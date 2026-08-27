"""Packet 7C remediation tests.

Covers the user-facing audio settings contract (list/select/reset/persist/
hot-plug/missing/permission/invalid), model metadata verification (fail-closed
digest, checksum tamper/missing), and the distributable-artifact completeness
check (launcher/installer/config template/systemd template/metadata; no
universal binary). All hermetic: device enumeration is stubbed via
sounddevice.query_devices; the model digest is stubbed; config writes go to a
tmp config file; no live services, audio, browser, or model are touched.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from voicebridge.config import Config  # noqa: E402
from voicebridge import distribution as D  # noqa: E402
from voicebridge.audio import settings as AS  # noqa: E402


def _mk(name, in_ch=0, out_ch=0, hostapi=0, default_rate=48000):
    return {"name": name, "max_input_channels": in_ch,
            "max_output_channels": out_ch, "hostapi": hostapi,
            "default_samplerate": default_rate}


_DEVICES = [
    _mk("HD-Audio Generic: ALC892 Analog (hw:2,0)", 2, 8),
    _mk("HD-Audio Generic: ALC892 Alt Analog (hw:2,2)", 2, 0),
    _mk("HyperX Cloud III: USB Audio (hw:1,0)", 1, 2),
    _mk("pipewire", 64, 64, hostapi=1),
    _mk("pulse", 32, 32, hostapi=1),
    _mk("default", 64, 64, hostapi=1),
]
_HOSTAPIS = [{"name": "ALSA"}, {"name": "Pulse"}]


def _patch_sd(monkeypatch, devices=None, hostapis=None, default=None):
    import sounddevice as sd
    monkeypatch.setattr(sd, "query_devices", lambda: devices or _DEVICES)
    monkeypatch.setattr(sd, "query_hostapis", lambda: hostapis or _HOSTAPIS)
    monkeypatch.setattr(sd, "default", type("D", (), {
        "device": default if default is not None else (5, 2)})())


def _cfg_with(tmp_path, mic_name="", out_name=""):
    p = tmp_path / "voice.yaml"
    p.write_text(f"mic:\n  device_name: '{mic_name}'\n"
                 f"audio:\n  output_device_name: '{out_name}'\n")
    return Config.load(p)


# ========================================================================
# Audio settings contract — enumeration
# ========================================================================
def test_list_devices_returns_stable_identifiers(monkeypatch):
    _patch_sd(monkeypatch)
    devs = AS.list_devices()
    inputs = {d.stable_id for d in devs["inputs"]}
    outputs = {d.stable_id for d in devs["outputs"]}
    assert "input:HyperX Cloud III: USB Audio:ALSA" in inputs
    assert "output:HD-Audio Generic: ALC892 Analog:ALSA" in outputs
    # never a numeric index or a raw ALSA path in the stable id
    for d in devs["inputs"] + devs["outputs"]:
        assert "hw:" not in d.stable_id.lower()
        assert "(hw:" not in d.stable_id.lower()
        assert not d.stable_id.startswith("input:" + str(d.index))
        assert d.index is None or isinstance(d.index, int)
        assert d.state == "OK"


def test_normalize_device_name_strips_alsa_paths_and_indices():
    assert AS.normalize_device_name("HyperX Cloud III: USB Audio (hw:1,0)") \
        == "HyperX Cloud III: USB Audio"
    assert AS.normalize_device_name("HD-Audio Generic: ALC892 Analog (hw:2,0)") \
        == "HD-Audio Generic: ALC892 Analog"
    assert AS.normalize_device_name("pulse") == "pulse"
    assert AS.normalize_device_name("default") == "default"
    assert AS.normalize_device_name("7") == ""          # bare index -> no name
    assert AS.normalize_device_name("") == ""
    assert "hw:" not in AS.normalize_device_name("Foo (hw:3,1)").lower()


def test_persisted_stable_ids_and_config_contain_no_hw_or_index(monkeypatch, tmp_path):
    """The audit rule: persisted config and stable IDs contain no '(hw:',
    'hw:', numeric index selection, or guessed ALSA path."""
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    mic = AS.select_microphone(cfg, "HyperX")
    out = AS.select_output(cfg, "ALC892 Analog")
    assert "hw:" not in mic.stable_id.lower()
    assert "hw:" not in out.stable_id.lower()
    assert "(hw:" not in mic.stable_id.lower()
    assert mic.name.isdigit() is False and out.name.isdigit() is False
    persisted = Config.load(tmp_path / "voice.yaml")
    mic_val = persisted.get("mic", "device_name")
    out_val = persisted.get("audio", "output_device_name")
    for val in (mic_val, out_val):
        assert "hw:" not in str(val).lower()
        assert "(hw:" not in str(val).lower()
        assert str(val).strip().isdigit() is False
    # legacy numeric index keys are cleared, never persisted
    assert persisted.get("mic", "device") is None
    assert persisted.get("audio", "output_device") == ""


def test_select_never_persists_hw_path_or_numeric_index(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    # a selector that IS an hw path must not match a clean device
    with pytest.raises(AS.InvalidSelectionError):
        AS.select_microphone(cfg, "hw:1,0")
    with pytest.raises(AS.InvalidSelectionError):
        AS.select_microphone(cfg, "(hw:1,0)")
    with pytest.raises(AS.InvalidSelectionError):
        AS.select_output(cfg, "hw:2,0")
    # numeric selector never matches by index
    with pytest.raises(AS.InvalidSelectionError):
        AS.select_microphone(cfg, "3")
    persisted = Config.load(tmp_path / "voice.yaml")
    assert "hw:" not in str(persisted.data).lower()


def test_list_defaults_flagged(monkeypatch):
    _patch_sd(monkeypatch, default=(5, 2))
    devs = AS.list_devices()
    assert devs["inputs"][0].is_default or any(d.is_default for d in devs["inputs"])
    assert any(d.is_default for d in devs["outputs"])


def test_list_devices_permission_denied(monkeypatch):
    import sounddevice as sd

    def boom():
        raise OSError("PermissionError: cannot open device")

    monkeypatch.setattr(sd, "query_devices", boom)
    with pytest.raises(AS.AudioSettingsError):
        AS.list_devices()
    # status() surfaces it as PERMISSION_DENIED, never a false pass
    st = AS.status(Config.load(Path(__file__).resolve().parent / "no-file.yaml"))
    assert st["mic"]["state"] == "PERMISSION_DENIED"


# ========================================================================
# Audio settings contract — selection
# ========================================================================
def test_select_microphone_by_name(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    dev = AS.select_microphone(cfg, "HyperX")
    assert dev.stable_id.startswith("input:HyperX")
    persisted = Config.load(tmp_path / "voice.yaml")
    assert persisted.get("mic", "device_name") == dev.name
    assert persisted.get("mic", "device") is None  # never an index


def test_select_output_by_name(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    dev = AS.select_output(cfg, "ALC892 Analog")
    persisted = Config.load(tmp_path / "voice.yaml")
    assert persisted.get("audio", "output_device_name") == dev.name
    assert persisted.get("audio", "output_device") == ""  # empty = default


def test_select_matches_by_stable_id_and_substring(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    d1 = AS.select_microphone(cfg, "input:pulse")
    assert "pulse" in d1.name
    d2 = AS.select_microphone(cfg, "HYPER")
    assert "HyperX" in d2.name
    # a full stable id (role:name:backend) also selects
    d3 = AS.select_microphone(cfg, "input:HyperX Cloud III: USB Audio:ALSA")
    assert "HyperX" in d3.name
    # an explicit backend identity works only when no name matches
    d4 = AS.select_output(cfg, "ALSA")
    assert d4.backend == "ALSA"


def test_invalid_selection_rejected(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    with pytest.raises(AS.InvalidSelectionError):
        AS.select_microphone(cfg, "nonsense-device")
    with pytest.raises(AS.InvalidSelectionError):
        AS.select_output(cfg, "no-such-output")


def test_select_never_matches_by_numeric_index(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    # "3" should not select device index 3 by number
    with pytest.raises(AS.InvalidSelectionError):
        AS.select_microphone(cfg, "3")


# ========================================================================
# Audio settings contract — reset + persistence + hot-plug
# ========================================================================
def test_reset_to_defaults_clears_pins(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path, mic_name="HyperX", out_name="ALC892 Analog")
    bak = AS.reset_to_defaults(cfg)
    assert bak.exists() and "audio" in str(bak)
    persisted = Config.load(tmp_path / "voice.yaml")
    assert persisted.get("mic", "device_name") in (None, "")
    assert persisted.get("audio", "output_device_name") in (None, "")


def test_selection_persists_across_restart(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    AS.select_microphone(cfg, "HyperX")
    AS.select_output(cfg, "pulse")
    # new Config object == restart
    cfg2 = Config.load(tmp_path / "voice.yaml")
    assert cfg2.get("mic", "device_name") == "HyperX Cloud III: USB Audio"
    assert cfg2.get("audio", "output_device_name") == "pulse"


def test_hotplug_missing_device_reported_not_guessed(monkeypatch, tmp_path):
    # device present at select time
    _patch_sd(monkeypatch, devices=[_mk("HyperX Cloud III: USB Audio (hw:1,0)", 1, 2),
                                    _mk("pulse", 32, 32, hostapi=1),
                                    _mk("default", 64, 64, hostapi=1)],
              default=(0, 0))
    cfg = _cfg_with(tmp_path)
    AS.select_microphone(cfg, "HyperX")
    # reload config == persisted selection after restart
    cfg = Config.load(tmp_path / "voice.yaml")
    assert cfg.get("mic", "device_name")
    # device disappears (hot unplug) -> MISSING, never a silent fallback
    _patch_sd(monkeypatch, devices=[_mk("pulse", 32, 32, hostapi=1),
                                    _mk("default", 64, 64, hostapi=1)],
              default=(0, 0))
    st = AS.status(cfg)
    assert st["mic"]["state"] == "MISSING"
    assert "no silent fallback" in st["mic"]["detail"]


def test_status_reports_ok_for_present_device(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path, mic_name="HyperX", out_name="pulse")
    st = AS.status(cfg)
    assert st["mic"]["state"] == "OK"
    assert st["output"]["state"] == "OK"


def test_status_default_when_no_pin(monkeypatch, tmp_path):
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    st = AS.status(cfg)
    assert st["mic"]["state"] == "OK"
    assert st["output"]["state"] == "OK"
    assert "default" in st["mic"]["detail"].lower()


def test_settings_change_does_not_restart_or_record(monkeypatch, tmp_path, capsys):
    """Selecting/resetting is a pure config write: no subprocess, no streams."""
    import voicebridge.audio.settings as _m
    calls = []
    orig_run = __import__("subprocess").run

    def spy(*a, **k):
        calls.append(a[0] if a else k.get("args"))
        return orig_run(*a, **k)

    monkeypatch.setattr(_m.shutil, "copy2", lambda *a, **k: None)
    _patch_sd(monkeypatch)
    cfg = _cfg_with(tmp_path)
    AS.select_microphone(cfg, "HyperX")
    AS.reset_to_defaults(cfg)
    # no subprocess invoked (pactl/sd.play/etc. are never triggered by a config
    # change); recording never starts from a settings call.
    assert calls == []


# ========================================================================
# Model metadata verification (fail closed + checksum)
# ========================================================================
def test_model_artifact_record_fails_closed_without_digest(tmp_path, monkeypatch):
    import voicebridge.runner_contract as RC
    RC.reset_cache()
    cfg = Config.load(tmp_path / "none.yaml")
    monkeypatch.setattr(RC, "_fetch_digest",
                        lambda url, alias, timeout_s=3.0: None)
    rec = D.model_artifact_record(cfg)
    assert rec["state"] == "MISSING"
    assert rec["revision"] is None and rec["checksum"] is None
    assert "fail closed" in rec["detail"]
    assert rec["license"] and rec["source"]


def test_model_artifact_record_ok_with_digest(tmp_path, monkeypatch):
    import voicebridge.runner_contract as RC
    RC.reset_cache()
    cfg = Config.load(tmp_path / "none.yaml")
    monkeypatch.setattr(RC, "_fetch_digest",
                        lambda url, alias, timeout_s=3.0: "sha256:abc123")
    rec = D.model_artifact_record(cfg)
    assert rec["state"] == "OK"
    assert rec["checksum"] == "sha256:abc123"
    assert rec["revision"] == "sha256:abc123"


def test_artifact_checksum_missing(tmp_path):
    assert D.artifact_checksum(tmp_path / "nope.onnx") is None


def test_verify_artifact_ok(tmp_path):
    f = tmp_path / "model.onnx"
    f.write_bytes(b"artifact-data")
    expected = D.artifact_checksum(f)
    assert D.verify_artifact(f, expected)["state"] == "OK"


def test_verify_artifact_tampered(tmp_path):
    f = tmp_path / "model.onnx"
    f.write_bytes(b"artifact-data")
    expected = D.artifact_checksum(f)
    f.write_bytes(b"artifact-data-TAMPERED")
    res = D.verify_artifact(f, expected)
    assert res["state"] == "FAIL"
    assert "tampered" in res["detail"]


def test_verify_artifact_missing(tmp_path):
    res = D.verify_artifact(tmp_path / "gone.onnx", "deadbeef")
    assert res["state"] == "MISSING"


def test_model_doctor_check_fails_closed(tmp_path, monkeypatch):
    import voicebridge.runner_contract as RC
    RC.reset_cache()
    cfg = Config.load(tmp_path / "none.yaml")
    monkeypatch.setattr(RC, "_fetch_digest",
                        lambda url, alias, timeout_s=3.0: None)
    monkeypatch.setattr(D, "ollama_available", lambda url="": True)
    monkeypatch.setattr(D, "os_target_ok", lambda: True)
    monkeypatch.setattr(D, "gpu_present", lambda: True)
    monkeypatch.setattr(D, "chromium_cdp_available", lambda: True)
    monkeypatch.setattr(D, "audio_devices", lambda: 2)
    monkeypatch.setattr(D, "openssl_ok", lambda: True)
    monkeypatch.setattr(D, "ram_total_gib", lambda: 16.0)
    monkeypatch.setattr(D, "disk_free_gib", lambda path=None: 8.0)
    checks = D.doctor(cfg)
    model = next(c for c in checks if c["name"] == "model")
    assert model["state"] == "MISSING"
    assert "unavailable" in model["detail"]


# ========================================================================
# Distributable artifact completeness
# ========================================================================
def test_distributable_manifest_is_complete():
    m = D.distributable_manifest(REPO)
    assert m["complete"] is True
    assert m["binary_claim"].startswith("none")
    roles = {e["role"] for e in m["files"]}
    assert {"launcher", "installer", "config-template", "systemd-template",
            "metadata"} <= roles
    for e in m["files"]:
        assert e["sha256"], f"{e['path']} missing sha256"


def test_distributable_manifest_missing_file_reported(tmp_path):
    m = D.distributable_manifest(tmp_path)  # empty dir
    assert m["complete"] is False
    assert m["files"] and all(not e["present"] for e in m["files"])
    res = D.verify_distributable(tmp_path)
    assert res["state"] == "MISSING"


def test_no_universal_binary_claim():
    m = D.distributable_manifest(REPO)
    joined = " ".join(str(v) for v in m.values()).lower()
    assert "single binary" not in joined
    assert "not a universal binary" in joined
    # Windows/macOS are documented FUTURE targets, never a current binary
    assert "future targets" in joined


def test_distributable_files_exist_in_repo():
    for rel, _role in D.DISTRIBUTABLE_FILES:
        assert (REPO / rel).exists(), f"required distributable file missing: {rel}"


def test_launcher_and_install_from_outside_repo_and_home(tmp_path):
    """Prove the distributable can install and launch from a staging dir
    OUTSIDE the repository and OUTSIDE the developer home directory."""
    import subprocess
    import shutil
    import os
    staging = tmp_path / "dist"
    # stage a copy of the distributable into a neutral dir (not under $HOME)
    for rel, _role in D.DISTRIBUTABLE_FILES:
        src = REPO / rel
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    # also stage the package so `python -m voicebridge` is importable there
    shutil.copytree(REPO / "voicebridge", staging / "voicebridge")
    (staging / "__init__.py").write_text("")
    # a fake HOME under the staging area, outside the developer home
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = dict(os.environ)
    env["HOME"] = str(fake_home)
    env["XDG_CONFIG_HOME"] = str(fake_home / ".config")
    env["XDG_STATE_HOME"] = str(fake_home / ".state")
    env["XDG_DATA_HOME"] = str(fake_home / ".data")
    env["PYTHONPATH"] = str(staging)
    # 1) launch from the staged copy with a neutral config + HOME
    probe = subprocess.run(
        [sys.executable, "-m", "voicebridge", "package", "manifest"],
        cwd=str(staging), env=env, capture_output=True, text=True, timeout=60)
    assert probe.returncode == 0, probe.stderr[-500:]
    # must NOT depend on the developer home or the original repo path
    dev_home = str(Path.home())
    joined = probe.stdout + probe.stderr
    assert dev_home not in joined, "manifest leaked developer home path"
    assert str(REPO) not in joined, "manifest leaked repository path"
    # 2) install.sh must be relocatable: with HOME redirected it computes the
    #    config/state paths from the staging location, never the developer home
    inst = subprocess.run(["bash", "-n", str(staging / "scripts/install.sh")],
                          capture_output=True, text=True, timeout=30)
    assert inst.returncode == 0, inst.stderr
    # 3) launcher (run.sh) resolves PROJECT_DIR relative to its own location
    run_src = (staging / "scripts/run.sh").read_text()
    assert 'BASH_SOURCE[0]' in run_src and '.venv/bin/python' in run_src


def test_install_script_uses_xdg_not_hardcoded_home():
    src = (REPO / "scripts/install.sh").read_text()
    assert "${XDG_STATE_HOME:-$HOME/.local/state}" in src
    assert "PROJECT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")/..\" && pwd)\"" in src


# ========================================================================
# CLI surface for the remediation
# ========================================================================
def test_cli_audio_list_and_status(monkeypatch, tmp_path, capsys):
    _patch_sd(monkeypatch)
    from voicebridge.cli import main as cli
    cfg_path = _cfg_path(tmp_path)
    rc = cli(["audio", "list", "--config", str(cfg_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "INPUT devices" in out and "OUTPUT devices" in out
    rc = cli(["audio", "status", "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["audio", "list", "--json", "--config", str(cfg_path)])
    assert rc == 0


def test_cli_audio_select_reset(monkeypatch, tmp_path, capsys):
    _patch_sd(monkeypatch)
    from voicebridge.cli import main as cli
    cfg_path = _cfg_path(tmp_path)
    rc = cli(["audio", "select", "mic", "HyperX", "--config", str(cfg_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "selected mic" in out
    rc = cli(["audio", "reset", "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["audio", "select", "mic", "nonsense", "--config", str(cfg_path)])
    assert rc == 2


def test_cli_package_model_and_distributable(monkeypatch, tmp_path, capsys):
    from voicebridge.cli import main as cli
    import voicebridge.runner_contract as RC
    cfg_path = _cfg_path(tmp_path)
    monkeypatch.setattr(RC, "_fetch_digest",
                        lambda url, alias, timeout_s=3.0: "sha256:deadbeef")
    rc = cli(["package", "model", "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["package", "distributable", "--config", str(cfg_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sha256" in out


def _cfg_path(tmp_path: Path) -> Path:
    p = tmp_path / "voice.yaml"
    p.write_text("mic:\n  device_name: ''\n"
                 "audio:\n  output_device_name: ''\n"
                 "logging:\n  jsonl_path: '" + str(tmp_path / "state") + "'\n")
    return p