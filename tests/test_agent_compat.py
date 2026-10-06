"""Tests for the foreign-agent adapter framework (``voicebridge.agent_compat``).

Two classes of test, deliberately separated:

* **Framework genericity** — proves a SECOND, synthetic adapter works without touching engine
  code. This is the guard against the framework quietly becoming a Hermes shim again.
* **Hermes conformance** — proves the real integration works, using an UNMODIFIED upstream
  plugin file read from the pinned repo as the fixture. A hand-written stand-in would pass
  while real plugins still failed.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "qubit_runtime"))

from voicebridge.agent_compat import framework as fw  # noqa: E402
from voicebridge.agent_compat.adapters import hermes  # noqa: E402

PIN = Path.home() / ".cache/qubit-pins/hermes-agent"


@pytest.fixture(autouse=True)
def _clean():
    """Every test starts from a stock interpreter and a re-seeded registry."""
    fw.uninstall()
    fw.registry._adapters = []
    fw.registry.register(hermes.ADAPTER)
    yield
    fw.uninstall()
    fw.registry._adapters = []


# ================================================================== framework genericity

# A SECOND agent, invented for the test. If supporting it required touching framework.py,
# the framework would not be a framework.
_FAKE = fw.ForeignAdapter(
    name="fakeagent",
    module_aliases={"fakeagent_cli": "qubit_cli"},
    prefix_aliases={"fakeagent_state_": "qubit_state_"},
    env_aliases={"FAKEAGENT_HOME": "QUBIT_HOME"},
    detection_markers=("fakeagent_cli",),
    entry_point_groups=("fakeagent.plugins",),
)


def test_second_adapter_registers_without_code_changes():
    """A brand-new agent is supported by DATA alone — no engine edits."""
    fw.registry.register(_FAKE)

    assert fw.registry.get("fakeagent") is _FAKE
    assert len(fw.active_adapters()) == 2


def test_second_adapter_module_alias_resolves():
    """The invented adapter's alias actually works through the same engine."""
    fw.registry.register(_FAKE)
    fw.install()

    import fakeagent_cli
    import qubit_cli

    assert sys.modules["fakeagent_cli"] is qubit_cli


def test_second_adapter_prefix_alias_resolves():
    """Prefix rules are adapter data too, not Hermes special-casing."""
    import importlib.util  # noqa: F401  (spec_from_file_location below needs it loaded)

    fw.registry.register(_FAKE)
    fw.install()

    import fakeagent_state_errors
    import qubit_state_errors

    assert sys.modules["fakeagent_state_errors"] is qubit_state_errors


def test_detection_picks_the_right_adapter(tmp_path):
    """Two registered adapters; detection returns the one whose markers are present."""
    fw.registry.register(_FAKE)

    d = tmp_path / "theirs"
    d.mkdir()
    (d / "__init__.py").write_text("from fakeagent_cli.config import load_config\n")

    assert fw.registry.detect(d) is _FAKE


def test_entry_point_groups_are_collected_from_all_adapters():
    """Qubit must keep accepting every registered agent's entry-point group."""
    fw.registry.register(_FAKE)

    groups = fw.registry.entry_point_groups()

    assert "hermes_agent.plugins" in groups
    assert "fakeagent.plugins" in groups


def test_reserved_names_are_never_aliased():
    """A reserved name must fall through even when a prefix rule would otherwise match."""
    # `hermes_agent` starts with no aliased prefix, but assert the guard directly.
    assert hermes.ADAPTER.is_reserved("hermes_agent")
    assert hermes.ADAPTER.is_reserved("hermes_version")

    fw.install()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("hermes_agent")


# ================================================================== Hermes conformance

def _real_plugin_source() -> str:
    """An UNMODIFIED upstream plugin that genuinely imports ``hermes_cli``.

    Read from the pinned commit so the test cannot drift with our tree. It is a lazy
    in-function import — the exact shape that silently broke the provider-fallback path
    during the vendor sync, which makes it the right fixture here.
    """
    out = subprocess.run(
        ["git", "show", "bd0affe5:plugins/web/xai/provider.py"],
        cwd=PIN, capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout:
        pytest.skip(f"pinned fixture unavailable at {PIN}")
    return out.stdout


def test_fixture_really_imports_the_foreign_module():
    """Guard the guard: a fixture that stopped importing hermes_cli would pass vacuously."""
    src = _real_plugin_source()
    assert "from hermes_cli" in src


def test_unmodified_foreign_plugin_executes(tmp_path):
    """THE core promise: an unmodified Hermes plugin file imports and runs under Qubit."""
    plugin = tmp_path / "xai_plugin"
    plugin.mkdir()
    (plugin / "__init__.py").write_text(_real_plugin_source())

    # Reject before install — proving the import really is the barrier.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("hermes_cli")

    matched = fw.install_for(plugin)
    assert matched is hermes.ADAPTER, "auto-detection must recognize the plugin"

    spec = importlib.util.spec_from_file_location("compat_plugin_under_test", plugin / "__init__.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["compat_plugin_under_test"] = mod
    spec.loader.exec_module(mod)  # raises if the foreign imports don't resolve

    assert mod.__name__ == "compat_plugin_under_test"


def test_module_identity_survives_both_import_orders():
    """Whichever name is imported first, both must resolve to ONE object.

    A duplicate module (same ``__name__``, different object) would give a foreign plugin a
    private registry and silently drop its registrations.
    """
    # Order A: alias first, then the real name.
    fw.install()
    import hermes_cli
    import qubit_cli
    assert sys.modules["hermes_cli"] is qubit_cli

    # Order B: real name first, then the alias. uninstall() drops the alias binding; the
    # real module legitimately stays in sys.modules, so only the alias key is cleared.
    fw.uninstall()
    sys.modules.pop("hermes_cli", None)
    fw.install()
    import qubit_cli as real_first  # noqa: F401
    import hermes_cli as alias_second

    assert sys.modules["hermes_cli"] is sys.modules["qubit_cli"]
    assert alias_second is real_first


def test_plugin_registration_through_alias_reaches_host():
    """The behaviour a plugin depends on: its registration must land in the HOST's object."""
    fw.install()
    import hermes_cli
    import qubit_cli

    hermes_cli.COMPAT_SENTINEL = "registered-by-plugin"
    try:
        assert getattr(qubit_cli, "COMPAT_SENTINEL", None) == "registered-by-plugin"
    finally:
        del qubit_cli.COMPAT_SENTINEL


def test_submodule_identity_holds():
    """Deep imports must share identity too, or `from hermes_cli.config import X` diverges."""
    fw.install()
    import hermes_cli.config as aliased
    import qubit_cli.config as real

    assert aliased is real


def test_symbol_alias_resolves_renamed_attribute():
    """A renamed ATTRIBUTE needs symbol aliasing; module aliasing cannot reach it."""
    fw.install()
    import tools.xai_http as xai

    assert hasattr(xai, "qubit_xai_user_agent")
    assert hasattr(xai, "hermes_xai_user_agent")
    assert xai.hermes_xai_user_agent is xai.qubit_xai_user_agent


def test_symbol_alias_never_overrides_existing():
    """If the foreign name genuinely exists, it must be left alone."""
    fw.install()
    import qubit_constants as kc

    before = kc.qubit_home_key
    fw.SymbolAliaser([hermes.ADAPTER]).apply()
    assert kc.qubit_home_key is before


def test_env_alias_mirrors_but_never_clobbers(monkeypatch):
    """HERMES_HOME reaches QUBIT_HOME; an explicit QUBIT_HOME always wins."""
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", "/tmp/foreign-home")
    applied = fw.EnvAliaser([hermes.ADAPTER]).apply()
    assert os.environ["QUBIT_HOME"] == "/tmp/foreign-home"
    assert "HERMES_HOME->QUBIT_HOME" in applied

    monkeypatch.setenv("HERMES_HOME", "/tmp/other")
    monkeypatch.setenv("QUBIT_HOME", "/tmp/explicit-profile")
    fw.EnvAliaser([hermes.ADAPTER]).apply()
    assert os.environ["QUBIT_HOME"] == "/tmp/explicit-profile"


def test_unresolvable_alias_fails_loudly():
    """An alias to a nonexistent target must raise a NAMED error, never return a stub."""
    bad = fw.ForeignAdapter(name="broken", module_aliases={"broken_cli": "no_such_module_xyz"})
    finder = fw.ImportAliaser([bad])

    with pytest.raises(fw.AgentCompatUnresolved):
        finder.find_spec("broken_cli")


def test_detection_ignores_prose(tmp_path):
    """A README mentioning the agent must NOT trigger an adapter."""
    d = tmp_path / "innocent"
    d.mkdir()
    (d / "__init__.py").write_text(
        '"""A plugin that mentions hermes in prose."""\n# not affiliated with hermes_cli.\nX = 1\n'
    )

    assert fw.registry.detect(d) is None


def test_install_is_idempotent():
    """Repeated installs must not stack finders."""
    a = fw.install()
    b = fw.install()

    assert a is b
    assert sys.meta_path.count(a) == 1


def test_adapters_carry_no_runtime_dependency_on_the_foreign_agent():
    """The adapter tables must not reference the foreign agent's on-disk tree.

    This is what makes 'delete Hermes' safe: the adapter describes NAME MAPPINGS, and the
    names it maps to are Qubit's own.
    """
    import inspect

    src = inspect.getsource(hermes)
    lowered = src.lower()

    assert "qubit-pins" not in lowered, "adapter must not read the pin repo at runtime"
    assert "hermes-agent" not in lowered, "adapter must not reference a Hermes checkout"
    assert "import hermes" not in lowered, "adapter must not import Hermes code"


def test_framework_is_not_used_in_tree():
    """EXTERNAL-ONLY invariant: no qubit_runtime module may import this package.

    If Qubit's own code starts depending on the compat layer, the clean rename silently rots.
    """
    offenders = []
    for py in (REPO / "qubit_runtime").rglob("*.py"):
        if "__pycache__" in py.parts or "/tests/" in str(py):
            continue
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        if "agent_compat" in text or "hermes_compat" in text:
            offenders.append(str(py.relative_to(REPO)))

    assert not offenders, "in-tree code must not import the compat layer: " + ", ".join(offenders)
