"""Release path contract: Qubit-owned names, with the pre-rebrand names read-old.

Focused coverage for the disconnected readers fixed in this lane:
- the launchd label must match the installed plist stem;
- fleet/legacy label derivation still cleans up the old label family;
- packaged desktop discovery prefers ``Qubit`` and falls back to ``Hermes``.
"""
from __future__ import annotations

import os
from pathlib import Path

from qubit_cli import gateway, gui_uninstall, main_desktop


def test_launchd_label_matches_installed_plist_stem(monkeypatch):
    monkeypatch.setattr(gateway, "_profile_suffix", lambda: "")

    label = gateway.get_launchd_label()

    assert label == "ai.qubit.gateway"
    assert gateway.get_launchd_plist_path().stem == label


def test_profiled_launchd_label_and_plist_stem_agree(monkeypatch):
    monkeypatch.setattr(gateway, "_profile_suffix", lambda: "work")

    label = gateway.get_launchd_label()

    assert label == "ai.qubit.gateway-work"
    assert gateway.get_launchd_plist_path().stem == label


def test_fleet_labels_derive_from_the_current_label_family(monkeypatch):
    from qubit_cli import profiles

    class _Profile:
        def __init__(self, name: str, is_default: bool = False):
            self.name = name
            self.is_default = is_default

    monkeypatch.setattr(
        profiles,
        "list_profiles",
        lambda: [_Profile("default", True), _Profile("work")],
    )

    assert gateway.launchd_gateway_labels_for_install() == [
        "ai.qubit.gateway",
        "ai.qubit.gateway-work",
    ]


def test_generated_launchd_plist_pins_qubit_home_and_has_no_voice_units(
    monkeypatch, tmp_path
):
    """The macOS core LaunchAgent is Qubit-owned and carries only the gateway.

    ``scripts/install_launchd_unit.sh`` delegates to this plist generator, so a
    regression here would silently point launchd at the wrong home or try to
    supervise a voice service that does not exist on macOS.
    """
    import plistlib

    monkeypatch.setattr(gateway, "_profile_suffix", lambda: "")
    monkeypatch.setattr(gateway, "get_qubit_home", lambda: tmp_path)
    monkeypatch.setattr(gateway, "_stable_service_working_dir", lambda: str(tmp_path))
    monkeypatch.setattr(gateway, "_build_service_path_dirs", lambda: ["/usr/bin", "/bin"])
    monkeypatch.setattr(gateway, "_append_node_dir_for_service", lambda _dirs: None)

    text = gateway.generate_launchd_plist()
    plist = plistlib.loads(text.encode("utf-8"))

    assert plist["Label"] == "ai.qubit.gateway"
    assert plist["EnvironmentVariables"]["QUBIT_HOME"] == str(tmp_path.resolve())
    assert plist["EnvironmentVariables"]["QUBIT_SUPERVISED_CHILD"] == "1"
    assert plist["RunAtLoad"] is True
    for voice_unit in (
        "qubit-voicebridge",
        "qubit-voice-orb",
        "qubit-voice-typing",
    ):
        assert voice_unit not in text


def _write_plist(agents: Path, label: str, home: Path) -> None:
    import plistlib

    (agents / f"{label}.plist").write_bytes(plistlib.dumps({
        "Label": label,
        "EnvironmentVariables": {"QUBIT_HOME": str(home)},
    }))


def test_legacy_cleanup_reads_both_label_families(monkeypatch, tmp_path):
    import qubit_constants

    monkeypatch.setattr(qubit_constants, "get_default_qubit_root", lambda: tmp_path / "root")
    import pwd
    import types

    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: types.SimpleNamespace(pw_dir=str(tmp_path)))
    agents = tmp_path / "Library" / "LaunchAgents"
    agents.mkdir(parents=True)
    _write_plist(agents, "ai.hermes.gateway-deadbeef", tmp_path / "root")
    _write_plist(agents, "ai.qubit.gateway-feedface", tmp_path / "root")

    assert gateway.legacy_launchd_labels_for_install() == [
        "ai.hermes.gateway-deadbeef",
        "ai.qubit.gateway-feedface",
    ]


def test_packaged_executable_prefers_qubit_on_each_platform(monkeypatch, tmp_path):
    release = tmp_path / "release"

    monkeypatch.setattr(main_desktop.sys, "platform", "linux")
    legacy = release / "linux-unpacked" / "hermes"
    canonical = release / "linux-unpacked" / "Qubit"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("", encoding="utf-8")
    canonical.write_text("", encoding="utf-8")
    os.utime(legacy, (2_000_000_000, 2_000_000_000))  # newer, must still lose
    assert main_desktop._desktop_packaged_executable_in(release) == canonical

    monkeypatch.setattr(main_desktop.sys, "platform", "win32")
    win_legacy = release / "win-unpacked" / "Hermes.exe"
    win_canonical = release / "win-unpacked" / "Qubit.exe"
    win_legacy.parent.mkdir(parents=True)
    win_legacy.write_text("", encoding="utf-8")
    win_canonical.write_text("", encoding="utf-8")
    os.utime(win_legacy, (2_000_000_000, 2_000_000_000))
    assert main_desktop._desktop_packaged_executable_in(release) == win_canonical

    monkeypatch.setattr(main_desktop.sys, "platform", "darwin")
    mac_canonical = release / "mac-arm64" / "Qubit.app" / "Contents" / "MacOS" / "Qubit"
    mac_legacy = release / "mac-arm64" / "Hermes.app" / "Contents" / "MacOS" / "Hermes"
    mac_canonical.parent.mkdir(parents=True)
    mac_legacy.parent.mkdir(parents=True)
    mac_canonical.write_text("", encoding="utf-8")
    mac_legacy.write_text("", encoding="utf-8")
    os.utime(mac_legacy, (2_000_000_000, 2_000_000_000))
    assert main_desktop._desktop_packaged_executable_in(release) == mac_canonical


def test_packaged_executable_falls_back_to_hermes(monkeypatch, tmp_path):
    release = tmp_path / "release"
    monkeypatch.setattr(main_desktop.sys, "platform", "darwin")

    legacy = release / "mac-arm64" / "Hermes.app" / "Contents" / "MacOS" / "Hermes"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("", encoding="utf-8")

    assert main_desktop._desktop_packaged_executable_in(release) == legacy


def test_packaged_gui_app_paths_list_qubit_before_hermes(monkeypatch):
    if gui_uninstall.sys.platform != "darwin":
        monkeypatch.setattr(gui_uninstall.sys, "platform", "darwin")

    names = [p.name for p in gui_uninstall.packaged_gui_app_paths()]

    assert names[:2] == ["Qubit.app", "Qubit.app"]
    assert "Hermes.app" in names
    assert names.index("Qubit.app") < names.index("Hermes.app")
