"""The installed ``qubit-gateway.service`` from ``scripts/install_gateway_unit.sh`` is a
``voicebridge.gateway_daemon`` unit, not the CLI-generated ``qubit gateway run`` unit.

Status used to compare the daemon unit to ``generate_systemd_unit()`` and call a healthy,
active install "outdated"; it also hashed the ``%h``-pinned live home into
``qubit-gateway-1ebdcf4f`` before ``%h`` expansion existed. Both are regressions pinned here.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import qubit_cli.gateway as gateway

pytestmark = pytest.mark.platforms("linux")

REPO_ROOT = Path(__file__).resolve().parents[3]
DAEMON_TEMPLATE = REPO_ROOT / "systemd" / "qubit-gateway.service"
LIVE_HOME_RELATIVE = Path(".local/state/qubit-voicebridge/runtime")
LIVE_HOME = Path("/home/trick") / LIVE_HOME_RELATIVE
PRE_FIX_HASH_TARGET = "qubit-gateway-1ebdcf4f"


def _daemon_unit(pinned_home: str | Path) -> str:
    """Render the shipped daemon template with its QUBIT_HOME pin replaced by *pinned_home*."""
    text = DAEMON_TEMPLATE.read_text(encoding="utf-8").replace(
        "__PROJECT_DIR__", "/opt/qubit"
    )
    lines = []
    for line in text.splitlines():
        if line.startswith("Environment=QUBIT_HOME="):
            line = f"Environment=QUBIT_HOME={pinned_home}"
        lines.append(line)
    return "\n".join(lines) + "\n"


@pytest.fixture
def live_daemon_unit(tmp_path, monkeypatch):
    """The live install shape: a bare user unit pinning ``%h/.local/state/.../runtime``."""
    account_home = tmp_path / "account"
    runtime_home = account_home / LIVE_HOME_RELATIVE
    runtime_home.mkdir(parents=True)
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit_path = unit_dir / "qubit-gateway.service"
    unit_path.write_text(
        _daemon_unit("%h/.local/state/qubit-voicebridge/runtime"), encoding="utf-8"
    )

    monkeypatch.setenv("HOME", str(account_home))
    monkeypatch.setenv("QUBIT_HOME", str(runtime_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(gateway, "is_linux", lambda: True)
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    return SimpleNamespace(
        account_home=account_home,
        runtime_home=runtime_home,
        unit_path=unit_path,
    )


def test_live_daemon_unit_owns_the_bare_name(live_daemon_unit):
    assert gateway._qubit_home_pinned_by_unit(live_daemon_unit.unit_path) == str(
        live_daemon_unit.runtime_home
    )
    assert gateway.get_service_name() == "qubit-gateway"
    assert gateway.get_systemd_unit_path(system=False) == live_daemon_unit.unit_path
    assert gateway.get_service_name() != PRE_FIX_HASH_TARGET


def test_live_daemon_unit_is_current_for_status(live_daemon_unit):
    """A shipped ``voicebridge.gateway_daemon`` unit is current by its own installer."""
    assert gateway._unit_launches_shipped_gateway_daemon(
        live_daemon_unit.unit_path.read_text(encoding="utf-8")
    )
    assert gateway.systemd_unit_is_current(system=False) is True


def test_live_home_never_resolves_to_the_pre_fix_hash(monkeypatch, tmp_path):
    """Regression: the actual live home string must never become ``qubit-gateway-1ebdcf4f``."""
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    unit_path = unit_dir / "qubit-gateway.service"
    # Absolute pin (no %h) so this test does not depend on the account home.
    unit_path.write_text(_daemon_unit(LIVE_HOME), encoding="utf-8")

    monkeypatch.setenv("HOME", "/home/trick")
    monkeypatch.setenv("QUBIT_HOME", str(LIVE_HOME))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setattr(gateway, "is_linux", lambda: True)
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)

    assert hashlib.sha256(str(LIVE_HOME).encode()).hexdigest()[:8] == "1ebdcf4f"
    assert gateway.get_service_name() != PRE_FIX_HASH_TARGET
    assert gateway.get_service_name() == "qubit-gateway"
    assert gateway.get_systemd_unit_path(system=False) == unit_path


def test_daemon_unit_pinning_another_home_is_not_current(tmp_path, monkeypatch):
    """Ownership is not weakened: a daemon unit for a foreign home is still not ours."""
    mine = tmp_path / "mine"
    foreign = tmp_path / "foreign"
    mine.mkdir()
    foreign.mkdir()
    unit_path = tmp_path / "qubit-gateway.service"
    unit_path.write_text(_daemon_unit(foreign), encoding="utf-8")

    monkeypatch.setenv("QUBIT_HOME", str(mine))
    monkeypatch.setattr(gateway, "get_systemd_unit_path", lambda system=False: unit_path)
    monkeypatch.setattr(gateway, "is_linux", lambda: True)
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)

    assert gateway._unit_launches_shipped_gateway_daemon(
        unit_path.read_text(encoding="utf-8")
    )
    assert gateway.systemd_unit_is_current(system=False) is False


def test_systemd_status_accepts_daemon_unit_and_never_targets_a_hash(
    live_daemon_unit, monkeypatch, capsys
):
    calls = []

    def fake_run_systemctl(args, **kwargs):
        calls.append(list(args))
        if args and args[0] == "is-active":
            return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gateway, "_select_systemd_scope", lambda system=False: False)
    monkeypatch.setattr(gateway, "has_conflicting_systemd_units", lambda: False)
    monkeypatch.setattr(gateway, "has_legacy_qubit_units", lambda: False)
    monkeypatch.setattr(gateway, "_run_systemctl", fake_run_systemctl)
    monkeypatch.setattr(gateway, "_read_systemd_unit_properties", lambda system=False: {})
    monkeypatch.setattr(gateway, "_print_runtime_health", lambda: None)
    monkeypatch.setattr(gateway, "get_systemd_linger_status", lambda: (True, ""))

    gateway.systemd_status(full=False)

    out = capsys.readouterr().out
    assert "outdated" not in out.lower()
    assert "User gateway service is running" in out
    joined = " ".join(" ".join(call) for call in calls)
    assert "qubit-gateway" in joined
    assert PRE_FIX_HASH_TARGET not in joined
