"""Task 11 — config/environment alignment (rebased v0.21.5).

Guards the four alignment decisions for the Qubit-owned runtime surface:

1. **QUBIT_HOME on both units.** ``qubit-voicebridge.service`` and
   ``qubit-gateway.service`` must each set an explicit ``Environment=QUBIT_HOME=``
   so both processes resolve the SAME canonical runtime home / one ``state.db``
   (the single-authority rule).
2. **Registered toolset name.** The portable example config's
   ``agent_runtime.toolsets`` must name real toolsets. The capital-Q
   ``Qubit-cli`` spelling (produced by a careless string rebrand) is not a
   registry key and resolved to zero tools; the Qubit-owned ``qubit-cli`` alias
   and ``kanban`` do resolve.
3. **Accepted journal mode.** ``agent_runtime.database.journal_mode`` must be the
   documented working choice (``delete``) — the live single ``state.db`` is
   opened by two Qubit processes and the linked SQLite cannot apply WAL safely.
   The vendored ``resolve_journal_mode()`` must agree.
4. **``platform_toolsets`` resolution.** Explicit per-platform maps are NOT
   required for parity: the vendored ``_get_platform_tools()`` derives each
   platform's default from the static bundles, so an absent/empty map still
   resolves a real tool set. If a map IS present every name must be valid.

Hermetic: throwaway ``HOME``/``XDG_*``/``QUBIT_HOME`` only; no network, no live
services, no writes to the reference home.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

UNITS = (
    REPO_ROOT / "systemd" / "qubit-voicebridge.service",
    REPO_ROOT / "systemd" / "qubit-gateway.service",
)
EXAMPLE = REPO_ROOT / "config" / "voice.example.yaml"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Throwaway HOME/XDG/QUBIT_HOME so nothing here resolves the live install."""
    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME",
                 "XDG_CACHE_HOME"):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "qhome"))
    (tmp_path / "qhome").mkdir(parents=True, exist_ok=True)
    yield tmp_path


def _unit_environment(path: Path) -> dict[str, str]:
    """Every ``Environment=KEY=VALUE`` directive in a systemd unit, split on the
    first ``=`` after the key."""
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line.startswith("Environment="):
            continue
        body = line[len("Environment="):]
        if "=" not in body:
            continue
        key, _, value = body.partition("=")
        env[key.strip()] = value.strip()
    return env


def _example() -> dict:
    return yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1. Both units pin an explicit QUBIT_HOME
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("unit", UNITS, ids=[u.name for u in UNITS])
def test_unit_pins_an_explicit_qubit_home(unit):
    env = _unit_environment(unit)
    assert "QUBIT_HOME" in env, f"{unit.name} must set Environment=QUBIT_HOME=..."
    assert env["QUBIT_HOME"].endswith("/qubit-voicebridge/runtime"), env["QUBIT_HOME"]


def test_units_pin_the_same_canonical_home():
    homes = {_unit_environment(u)["QUBIT_HOME"] for u in UNITS}
    assert len(homes) == 1, f"units disagree on QUBIT_HOME: {homes}"


# --------------------------------------------------------------------------- #
# 2. Toolset names are real registry keys
# --------------------------------------------------------------------------- #
def test_example_toolsets_are_registered_names():
    from voicebridge import qubit_asgi as QA

    names = _example()["agent_runtime"]["toolsets"]
    assert names, "agent_runtime.toolsets must not be empty"
    # The regression: a capital-Q spelling is not a registry key.
    assert "Qubit-cli" not in names, "capital-Q 'Qubit-cli' is not a registry key"

    QA._overlay_applied = False
    QA._apply_qubit_config_overlay()

    import toolsets as ts

    for name in names:
        assert ts.validate_toolset(name), f"{name!r} is not a registered toolset"
        assert ts.resolve_toolset(name), f"{name!r} resolves to zero tools"


def test_qubit_cli_shares_the_canonical_bundle():
    """The Qubit-owned id must resolve to the same bundle as the canonical CLI
    toolset (read-old source), so the rebrand does not silently drop tools."""
    from voicebridge import qubit_asgi as QA

    QA._overlay_applied = False
    QA._apply_qubit_config_overlay()

    import toolsets as ts

    assert ts.resolve_toolset("qubit-cli") == ts.resolve_toolset("hermes-cli")


# --------------------------------------------------------------------------- #
# 3. Journal mode matches the accepted runtime state
# --------------------------------------------------------------------------- #
def test_example_journal_mode_is_the_accepted_choice():
    mode = _example()["agent_runtime"]["database"]["journal_mode"]
    assert mode == "delete", (
        "the accepted working choice is DELETE (the live state.db is opened by "
        "two Qubit processes and WAL cannot apply safely on this host)")


def test_vendored_runtime_resolves_the_aligned_journal_mode(tmp_path):
    """End-to-end: materialise ``<QUBIT_HOME>/config.yaml`` from the example's
    ``agent_runtime`` block and prove the vendored resolver agrees, in a fresh
    interpreter so no config cache can mask a drift."""
    block = _example()["agent_runtime"]
    runtime_cfg = dict(block)
    runtime_cfg["database"] = dict(block["database"])
    (tmp_path / "qhome" / "config.yaml").write_text(
        yaml.safe_dump(runtime_cfg, sort_keys=False), encoding="utf-8")

    env = dict(os.environ)
    env.update({
        "HOME": str(tmp_path / "home"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "QUBIT_HOME": str(tmp_path / "qhome"),
        "PYTHONPATH": str(REPO_ROOT),
    })
    script = (
        "from voicebridge import qubit_runtime\n"
        "qubit_runtime.ensure_path()\n"
        "from qubit_state_wal import resolve_journal_mode\n"
        "print(resolve_journal_mode())\n"
    )
    proc = subprocess.run([sys.executable, "-c", script], env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip().splitlines()[-1] == "delete", proc.stdout


# --------------------------------------------------------------------------- #
# 4. platform_toolsets parity resolution
# --------------------------------------------------------------------------- #
def test_platform_toolsets_absent_still_resolves_real_tools():
    """Resolution of item 4: explicit per-platform maps are NOT required. The
    vendored resolver derives each platform's default from the static bundles,
    so an empty map still yields a non-empty tool set (reference parity via the
    defaults)."""
    from voicebridge import qubit_asgi as QA
    from voicebridge import qubit_runtime

    QA._overlay_applied = False
    QA._apply_qubit_config_overlay()
    qubit_runtime.ensure_path()

    from qubit_cli.tools_config import _get_platform_tools, PLATFORMS

    cfg = dict(_example()["agent_runtime"])
    assert isinstance(cfg.get("platform_toolsets"), dict)
    assert cfg["platform_toolsets"] == {}, "resolution: maps are opt-in overrides"

    assert "cli" in PLATFORMS and "telegram" in PLATFORMS
    for platform in ("cli", "telegram"):
        enabled = _get_platform_tools(cfg, platform)
        assert enabled, f"platform {platform!r} resolved no toolsets"


def test_any_explicit_platform_toolset_name_is_valid():
    from voicebridge import qubit_asgi as QA

    QA._overlay_applied = False
    QA._apply_qubit_config_overlay()

    import toolsets as ts

    mapping = _example()["agent_runtime"].get("platform_toolsets") or {}
    for platform, names in mapping.items():
        for name in names:
            assert ts.validate_toolset(name), f"{platform}: {name!r} is not registered"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
