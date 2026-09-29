# tests/test_v0215_class_a_repairs.py
"""Focused regressions for the v0.21.5 rebase class-(a) repairs.

Each test pins one root cause from ``prep/VERIFY_VENDORED_TRIAGE.md`` that the
rebase introduced and this workstream fixed. The vendored suite exercises the
same behavior end-to-end; these run in the hermetic rebase gate so a future
rename/merge cannot silently re-break it.

Repairs covered:

* A1/A9 — synthesized platform toolsets must use the registry's ``hermes-<platform>``
  name, not a ``qubit-`` spelling that resolves to no tools.
* A2/A6 — ``qubit_*`` first-party modules must be recognized as Qubit's own.
* A4 — the ``hermes`` launcher delegates to ``qubit_cli.main``.
* A8 — a legacy ``hermes pid=<pid>`` worktree lock is read-old as live.
* A3 — the update routes are mounted at the canonical ``/api/qubit/update*``.
"""
from __future__ import annotations

import os
import runpy
import sys
import types

from rebase_bucket_support import RUNTIME, runtime_on_path  # noqa: F401


def test_a1_platform_toolset_fallback_uses_registry_name(runtime_on_path):
    from qubit_cli.tools_config import _platform_default_toolset

    assert _platform_default_toolset("acp") == "hermes-acp"
    assert _platform_default_toolset("widget-plugin") == "hermes-widget-plugin"


def test_a1_toolset_validation_fallback_uses_registry_name(runtime_on_path):
    from qubit_cli.toolset_validation import _platform_default_toolset

    assert _platform_default_toolset("acp") == "hermes-acp"


def test_a2_a6_qubit_first_party_roots_are_recognized(runtime_on_path):
    from qubit_constants import is_first_party_module

    assert is_first_party_module("qubit_cli.config")
    assert is_first_party_module("qubit_constants")
    assert is_first_party_module("qubit_state")
    assert is_first_party_module("hermes_cli.config")  # read-old
    assert not is_first_party_module("agents")
    assert not is_first_party_module("agentops")


def test_a9_blank_slate_does_not_disable_platform_composites(runtime_on_path):
    from qubit_cli.setup_quick import _blank_slate_minimal_toolsets

    cfg: dict = {}
    _blank_slate_minimal_toolsets(cfg)
    disabled = cfg["agent"]["disabled_toolsets"]
    assert not any(ts.startswith("hermes-") for ts in disabled), disabled
    assert "hermes-acp" not in disabled


def test_a4_hermes_launcher_delegates_to_qubit_cli(runtime_on_path, monkeypatch):
    launcher = RUNTIME / "hermes"
    called: list[str] = []
    fake_main = types.ModuleType("qubit_cli.main")
    fake_main.main = lambda: called.append("qubit_cli.main")
    monkeypatch.setitem(sys.modules, "qubit_cli.main", fake_main)
    # A failing import of the removed legacy module must surface, not be masked.
    monkeypatch.setitem(sys.modules, "hermes_cli", None)
    monkeypatch.setattr(sys, "argv", [str(launcher)])

    runpy.run_path(str(launcher), run_name="__main__")

    assert called == ["qubit_cli.main"]


def test_a8_legacy_hermes_lock_reason_is_read_old(runtime_on_path, monkeypatch):
    from qubit_cli import worktree_ops

    listing = (
        "worktree /tmp/wt-a8\nHEAD deadbeef\nbranch refs/heads/x\n"
        f"locked hermes pid={os.getpid()}\n"
    )
    monkeypatch.setattr(worktree_ops, "_git_out", lambda *a, **k: listing)

    assert worktree_ops._worktree_lock_is_live("/repo", "/tmp/wt-a8") == "live"


def test_over_rebrand_keeps_openclaw_as_external_source(runtime_on_path, tmp_path):
    """The rebrand must not rewrite the external OpenClaw source into Qubit.

    ``claw`` migrates FROM OpenClaw and the residue probe detects the legacy
    ``~/.openclaw`` home; renaming those to ``.qubit`` desynced the migration
    from its real source (and would flag Qubit's own home as residue)."""
    from qubit_cli import claw
    from agent import onboarding

    assert ".openclaw" in claw._OPENCLAW_DIR_NAMES
    assert ".qubit" not in claw._OPENCLAW_DIR_NAMES

    (tmp_path / ".openclaw").mkdir()
    assert onboarding.detect_openclaw_residue(home=tmp_path) is True


def test_over_rebrand_keeps_external_model_ids(runtime_on_path):
    """Third-party model ids are wire values, not Qubit brand strings."""
    import importlib.util

    plugin = (
        RUNTIME
        / "plugins" / "model-providers" / "nebius-token-factory" / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location("_nebius_profile_a_guard", plugin)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    models = mod.nebius_token_factory.fallback_models
    assert "NousResearch/Hermes-4-70B" in models
    assert not any("Qubit" in m for m in models)


def test_installer_source_is_qubit_owned_and_configurable(runtime_on_path, monkeypatch):
    """Owner decision 2026-09-27: one configurable, Qubit-owned installer source."""
    from qubit_cli import install_source as s

    assert "cgraph-dev/Qubit-AIO" in s.DEFAULT_BASE_URL
    assert "hermes" not in s.base_url().lower()
    assert "openclaw" not in s.base_url().lower()
    assert s.install_sh_url().endswith("/raw/main/scripts/install.sh")
    assert "/Qubit-AIO/" in s.install_sh_url()
    assert s.releases_base_url() == f"{s.base_url()}/releases"

    # A single override changes every derived URL.
    monkeypatch.setenv("QUBIT_INSTALL_BASE_URL", "https://install.qubit.example")
    assert s.install_sh_url().startswith("https://install.qubit.example/raw/")
    assert s.reinstall_one_liner(windows=False).startswith(
        "curl -fsSL https://install.qubit.example/"
    )


def test_no_hardcoded_legacy_installer_url_in_shipped_cli(runtime_on_path):
    """No Hermes/OpenClaw installer URL may remain hardcoded in shipped CLI output."""
    legacy = (
        "hermes-agent.nousresearch.com/install",
        "nousresearch.com/install.sh",
        "nousresearch.com/install.ps1",
        "openclaw.com/install",
        "NousResearch/hermes-agent/releases",
    )
    offenders = []
    for path in (RUNTIME / "qubit_cli").rglob("*.py"):
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        text = path.read_text(errors="ignore")
        offenders += [(path.name, token) for token in legacy if token in text]
    assert not offenders, offenders


def test_a3_update_routes_are_mounted_at_canonical_path(runtime_on_path):
    from qubit_cli.web_routers import actions

    paths = {r.path for r in actions.router.routes}
    paths |= {r.path for r in actions.status_router.routes}
    assert "/api/qubit/update/check" in paths
    assert "/api/qubit/update" in paths
    assert "/api/qubit/update/receipt" in paths
