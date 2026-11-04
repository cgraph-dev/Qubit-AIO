"""User systemd unit location and identity: the account home owns the unit dir (#98699) and a bare
``qubit-gateway.service`` pinning THIS home is this home's service (#109476)."""

import pytest

import qubit_cli.gateway as gateway

pytestmark = pytest.mark.platforms("linux")


def test_user_unit_dir_follows_the_account_home_not_a_profile_pinned_process_home(tmp_path, monkeypatch):
    # ``qubit -p x gateway install`` launched from a process whose HOME profile isolation already
    # pointed at the ACTIVE profile's ``{QUBIT_HOME}/home``; the unit must land where
    # ``systemctl --user`` looks — under the account home — not under that profile dir.
    account_home = tmp_path / "account"
    active_root = tmp_path / "active-profile-root"
    process_home = active_root / "home"
    process_home.mkdir(parents=True)
    monkeypatch.setenv("QUBIT_HOME", str(active_root))
    monkeypatch.setenv("HOME", str(process_home))
    monkeypatch.setenv("HERMES_REAL_HOME", str(account_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

    unit_path = gateway.get_systemd_unit_path(system=False)

    assert unit_path.parent == account_home / ".config" / "systemd" / "user"
    assert not unit_path.is_relative_to(process_home)


def test_bare_user_unit_pinning_this_custom_home_is_adopted_by_lifecycle_commands(tmp_path, monkeypatch):
    # A pre-#106611 install of a custom root left ``qubit-gateway.service`` (bare) pinning that root;
    # the recomputed ``qubit-gateway-<hash>`` name made it invisible and ``restart`` ran foreground.
    custom_root = tmp_path / "qubit"
    custom_root.mkdir()
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    monkeypatch.setenv("QUBIT_HOME", str(custom_root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(gateway, "is_linux", lambda: True)
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    legacy_unit = unit_dir / "qubit-gateway.service"

    # A bare unit that pins ANOTHER home is someone else's service: this home keeps its own name.
    legacy_unit.write_text(
        f'[Service]\nEnvironment="QUBIT_HOME={tmp_path / "other"}"\nExecStart=/usr/bin/qubit gateway run\n',
        encoding="utf-8",
    )
    assert gateway.get_service_name() != "qubit-gateway"
    assert not gateway._systemd_unit_installed()

    legacy_unit.write_text(
        f'[Service]\nEnvironment="QUBIT_HOME={custom_root}"\nRestart=always\nExecStart=/usr/bin/qubit gateway run\n',
        encoding="utf-8",
    )
    assert gateway.get_service_name() == "qubit-gateway"
    assert gateway.get_systemd_unit_path(system=False) == legacy_unit
    assert gateway._systemd_unit_installed()


def test_bare_unit_pinning_a_percent_h_home_owns_the_bare_service_name(tmp_path, monkeypatch):
    """Installed units pin ``QUBIT_HOME=%h/...``; ownership must expand ``%h``.

    Without expansion ``get_service_name()`` hashed the live home into
    ``qubit-gateway-1ebdcf4f`` while systemd owned the bare ``qubit-gateway``
    unit, so ``qubit agent gateway status`` said "manual" and lifecycle verbs
    targeted a unit that does not exist.
    """
    account_home = tmp_path / "account"
    custom_root = account_home / ".local" / "state" / "qubit-voicebridge" / "runtime"
    custom_root.mkdir(parents=True)
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(account_home))
    monkeypatch.setenv("QUBIT_HOME", str(custom_root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(gateway, "is_linux", lambda: True)
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    (unit_dir / "qubit-gateway.service").write_text(
        '[Service]\nEnvironment=QUBIT_HOME=%h/.local/state/qubit-voicebridge/runtime\n'
        'ExecStart=/usr/bin/qubit gateway run\n',
        encoding="utf-8",
    )

    assert gateway._qubit_home_pinned_by_unit(unit_dir / "qubit-gateway.service") == str(custom_root)
    assert gateway.get_service_name() == "qubit-gateway"
    assert gateway.get_systemd_unit_path(system=False) == unit_dir / "qubit-gateway.service"


def test_percent_h_expansion_keeps_an_escaped_percent_literal(tmp_path, monkeypatch):
    account_home = tmp_path / "account"
    account_home.mkdir()
    monkeypatch.setenv("HOME", str(account_home))
    monkeypatch.setattr(gateway, "is_linux", lambda: True)
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 1000)
    unit = tmp_path / "unit.service"
    unit.write_text(
        '[Service]\nEnvironment="QUBIT_HOME=%h/%%h-literal"\n', encoding="utf-8")
    assert gateway._qubit_home_pinned_by_unit(unit) == f"{account_home}/%h-literal"
