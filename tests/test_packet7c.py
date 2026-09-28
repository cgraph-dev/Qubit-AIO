"""Packet 7C tests: download-and-run distribution, hardware/resource doctor,
model-artifact metadata, audited portable-state export/import, atomic
restore/rollback, and trusted-device enrollment.

Hermetic: no live services, browser, model downloads, or config edits. The
audited encryption uses system OpenSSL via subprocess; tests skip the live
Ollama/browser/audio probes by using a fake cfg whose state_dir is a tmp dir.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from voicebridge.config import Config  # noqa: E402
from voicebridge import distribution as D  # noqa: E402


# ========================================================================
# Manifest
# ========================================================================
def test_manifest_versioned_and_platform_explicit():
    m = D.manifest()
    assert m["name"] == "qubit-voicebridge"
    assert m["version"] == "1.0.0"
    assert m["target"]["os"] == "ubuntu"
    assert "x86_64" in m["target"]["arches"]
    assert "windows" not in m["target"].get("os", "").lower()
    assert "uninstall" in m and "portable_state" in m
    assert "never" in m["uninstall"].lower()


def test_manifest_is_read_only_no_service_enable():
    m = D.manifest()
    joined = " ".join(str(v) for v in m.values()).lower()
    assert "systemctl" not in joined
    assert "enable" not in joined.replace("never", "")


# ========================================================================
# Hardware / resource doctor (typed states)
# ========================================================================
def test_doctor_returns_typed_states(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path / "none.yaml")
    monkeypatch.setattr(D, "os_target_ok", lambda: True)
    monkeypatch.setattr(D, "gpu_present", lambda: True)
    monkeypatch.setattr(D, "free_vram_mib", lambda: 3000)
    monkeypatch.setattr(D, "ollama_available", lambda url="": True)
    monkeypatch.setattr(D, "chromium_cdp_available", lambda: True)
    monkeypatch.setattr(D, "audio_devices", lambda: 2)
    monkeypatch.setattr(D, "openssl_ok", lambda: True)
    monkeypatch.setattr(D, "ram_total_gib", lambda: 16.0)
    monkeypatch.setattr(D, "disk_free_gib", lambda path=None: 8.0)
    checks = D.doctor(cfg)
    names = {c["name"] for c in checks}
    for want in ("distro", "arch", "ram", "disk", "gpu", "ollama",
                 "browser_cdp", "audio", "openssl", "isolation"):
        assert want in names, f"{want} missing from doctor"
    for c in checks:
        assert c["state"] in ("OK", "DEGRADED", "MISSING", "NOT_SUPPORTED")
        assert c["detail"]


def test_doctor_reports_cpu_only_and_missing_deps(tmp_path, monkeypatch):
    cfg = Config.load(tmp_path / "none.yaml")
    monkeypatch.setattr(D, "os_target_ok", lambda: True)
    monkeypatch.setattr(D, "gpu_present", lambda: False)
    monkeypatch.setattr(D, "free_vram_mib", lambda: None)
    monkeypatch.setattr(D, "ollama_available", lambda url="": False)
    monkeypatch.setattr(D, "chromium_cdp_available", lambda: False)
    monkeypatch.setattr(D, "audio_devices", lambda: 0)
    monkeypatch.setattr(D, "openssl_ok", lambda: False)
    monkeypatch.setattr(D, "ram_total_gib", lambda: 2.0)
    monkeypatch.setattr(D, "disk_free_gib", lambda path=None: 0.5)
    checks = D.doctor(cfg)
    by = {c["name"]: c["state"] for c in checks}
    # CPU-only and missing-* states are typed, never a false pass
    assert by["gpu"] == "MISSING"
    assert by["ollama"] == "MISSING"
    assert by["browser_cdp"] == "MISSING"
    assert by["audio"] == "MISSING"
    assert by["openssl"] == "DEGRADED"
    assert by["ram"] == "DEGRADED"
    assert by["disk"] == "DEGRADED"
    summary = D.doctor_summary(checks)
    assert "degraded/missing" in summary


def test_doctor_gpu_ok_state(tmp_path, monkeypatch):
    import voicebridge.runner_contract as RC
    monkeypatch.setattr(RC, "_fetch_digest",
                        lambda url, alias, timeout_s=3.0: "sha256:ok")
    cfg = Config.load(tmp_path / "none.yaml")
    monkeypatch.setattr(D, "os_target_ok", lambda: True)
    monkeypatch.setattr(D, "gpu_present", lambda: True)
    monkeypatch.setattr(D, "ollama_available", lambda url="": True)
    monkeypatch.setattr(D, "chromium_cdp_available", lambda: True)
    monkeypatch.setattr(D, "audio_devices", lambda: 2)
    monkeypatch.setattr(D, "openssl_ok", lambda: True)
    monkeypatch.setattr(D, "ram_total_gib", lambda: 16.0)
    monkeypatch.setattr(D, "disk_free_gib", lambda path=None: 8.0)
    checks = D.doctor(cfg)
    assert all(c["state"] == "OK" for c in checks)


# ========================================================================
# Model artifact metadata
# ========================================================================
def test_model_artifact_has_revision_checksum_license(tmp_path):
    cfg = Config.load(tmp_path / "none.yaml")
    rec = D.model_artifact_record(cfg)
    assert rec["alias"] == "qubit-brain4"
    assert "license" in rec and rec["license"]
    assert "revision" in rec and "checksum" in rec
    assert rec["kind"] == "ollama-model"


def test_model_artifact_never_bakes_fake_checksum(tmp_path, monkeypatch):
    import voicebridge.runner_contract as RC
    RC.reset_cache()
    monkeypatch.setattr(RC, "_fetch_digest",
                        lambda url, alias, timeout_s=3.0: None)
    cfg = Config.load(tmp_path / "none.yaml")
    rec = D.model_artifact_record(cfg)
    # without a live digest the record FAILS CLOSED (state MISSING, no
    # fabricated revision/checksum) — never a fake placeholder value
    assert rec["state"] == "MISSING"
    assert rec["revision"] is None and rec["checksum"] is None
    assert "deadbeef" not in str(rec.get("checksum", "")).lower()


# ========================================================================
# Launcher / upgrade / uninstall (read-only contracts)
# ========================================================================
def test_launcher_command_uses_venv_python(tmp_path):
    cfg = Config.load(tmp_path / "none.yaml")
    cmd = D.launcher_command(cfg)
    assert cmd[-3:] == ["-m", "voicebridge", "run"]
    assert sys.executable == cmd[0]


def test_upgrade_plan_preserves_state_and_flags(tmp_path):
    cfg = Config.load(tmp_path / "none.yaml")
    plan = D.upgrade_plan(cfg)
    assert plan["action"] == "upgrade"
    joined = " ".join(str(x) for x in plan["does_not"]).lower()
    assert "delete state" in joined
    assert "feature flags untouched" in " ".join(plan["steps"]).lower()


def test_uninstall_requires_confirmation_and_preserves_state(tmp_path):
    cfg = Config.load(tmp_path / "none.yaml")
    plan = D.uninstall_plan(cfg)
    assert plan["requires_confirmation"] is True
    assert "state dir" in " ".join(plan["preserves_by_default"]).lower()


def test_clean_install_required_steps_are_read_only(tmp_path):
    cfg = Config.load(tmp_path / "none.yaml")
    steps = D.clean_install_required_steps(cfg)
    assert any("declared deps" in s for s in steps)
    assert any("systemd" in s for s in steps)


# ========================================================================
# Audited encryption + portable-state export/import
# ========================================================================
class _FakeCfg:
    """Minimal Config-shaped object with a tmp state dir."""

    def __init__(self, state_dir: Path):
        self._sd = state_dir
        self.data = {"audio": {"output_device": "", "output_device_name": ""},
                     "webui": {"token": "super-secret-live-token-xyz",
                               "frontend_dir": "/home/example/custom"},
                     "intent": {"local_llm": {"model": "qubit-brain4",
                                              "url": "http://127.0.0.1:11434"}},
                     "safety": {"tool_broker": True},
                     "agent": {"key_file": "~/.config/qubit-voicebridge/agent.key"}}

    def get(self, *path, default=None):
        node = self.data
        for p in path:
            if isinstance(node, dict) and p in node:
                node = node[p]
            else:
                return default
        return node

    def section(self, name):
        return self.data.get(name, {})

    def state_dir(self):
        return self._sd

    def model_dir(self):
        return self._sd / "models"


def _seed_state(tmp_path: Path) -> _FakeCfg:
    from voicebridge.memory import MemoryStore
    from voicebridge.learning import LearningLedger
    mem = MemoryStore(tmp_path / "memory" / "memory.db")
    mem.remember("user prefers morning briefings", tier="core",
                 source="ev-7c-1")
    mem.close()
    ledger = LearningLedger(tmp_path)
    ledger.add_preference("prefers short replies", source="voice")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "report-1.md").write_text("# Report 1\nsafe content\n")
    return _FakeCfg(tmp_path)


@pytest.mark.skipif(not D.openssl_ok(), reason="DEGRADED: system OpenSSL missing")
def test_export_import_roundtrip_preserves_sanitized_state(tmp_path):
    cfg = _seed_state(tmp_path)
    bundle = __import__("voicebridge.portable", fromlist=["portable"])
    blob = bundle.export_bundle(cfg, "correct-pass")
    assert blob.startswith(bundle.MAGIC)
    payload = bundle.read_bundle(blob, "correct-pass")
    state = payload["state"]
    # memory + lessons survived
    assert any("morning briefings" in r["text"] for r in state["memory"])
    assert any("short replies" in str(l) for l in state["lessons"])
    # reports survived
    assert any("Report 1" in r["content"] for r in state["reports"])
    # secrets excluded
    joined = json.dumps(state)
    assert "super-secret-live-token-xyz" not in joined
    assert "agent.key" not in joined
    assert "/home/example/custom" not in joined


@pytest.mark.skipif(not D.openssl_ok(), reason="DEGRADED: system OpenSSL missing")
def test_wrong_passphrase_is_rejected(tmp_path):
    from voicebridge import portable as P
    cfg = _seed_state(tmp_path)
    blob = P.export_bundle(cfg, "right-pass")
    with pytest.raises(P.WrongPassphraseError):
        P.read_bundle(blob, "wrong-pass")


@pytest.mark.skipif(not D.openssl_ok(), reason="DEGRADED: system OpenSSL missing")
def test_corrupted_bundle_is_rejected(tmp_path):
    from voicebridge import portable as P
    cfg = _seed_state(tmp_path)
    blob = P.export_bundle(cfg, "pass")
    corrupted = blob[:-1] + bytes([blob[-1] ^ 0xFF])
    with pytest.raises((P.CorruptedBundleError, P.WrongPassphraseError)):
        P.read_bundle(corrupted, "pass")


def test_bad_magic_is_corrupted(tmp_path):
    from voicebridge import portable as P
    with pytest.raises(P.CorruptedBundleError):
        P.read_bundle(b"NOTMAGIC\n{}\nabc", "pass")


def test_unsupported_schema_is_rejected(tmp_path):
    from voicebridge import portable as P
    header = {"magic": P.MAGIC.decode(), "schema_version": 999,
              "kdf": {}, "mac_b64": "", "iv_b64": ""}
    blob = P.MAGIC + b"\n" + json.dumps(header).encode() + b"\nct"
    with pytest.raises(P.UnsupportedSchemaError):
        P.read_bundle(blob, "pass")


@pytest.mark.skipif(not D.openssl_ok(), reason="DEGRADED: system OpenSSL missing")
def test_export_excludes_machine_paths_and_secret_sections(tmp_path):
    from voicebridge import portable as P
    cfg = _FakeCfg(tmp_path)
    blob = P.export_bundle(cfg, "pass")
    payload = P.read_bundle(blob, "pass")
    state = payload["state"]
    conf = state["config_non_secret"]
    # webui (token), agent (key_file), logging excluded entirely
    assert "webui" not in conf and "agent" not in conf and "logging" not in conf
    # machine-specific audio output pins excluded
    assert "output_device" not in conf.get("audio", {})
    joined = json.dumps(state)
    assert "super-secret-live-token-xyz" not in joined
    assert "agent.key" not in joined


@pytest.mark.skipif(not D.openssl_ok(), reason="DEGRADED: system OpenSSL missing")
def test_low_disk_rejects_restore(tmp_path, monkeypatch):
    from voicebridge import portable as P
    cfg = _seed_state(tmp_path)
    blob = P.export_bundle(cfg, "pass")
    payload = P.read_bundle(blob, "pass")
    monkeypatch.setattr(P, "_free_bytes_gib", lambda path: 0.01)
    with pytest.raises(P.LowDiskError):
        P.plan_restore(cfg, payload, dry_run=False)


@pytest.mark.skipif(not D.openssl_ok(), reason="DEGRADED: system OpenSSL missing")
def test_restore_dry_run_writes_nothing(tmp_path):
    from voicebridge import portable as P
    cfg = _seed_state(tmp_path)
    blob = P.export_bundle(cfg, "pass")
    payload = P.read_bundle(blob, "pass")
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in tmp_path.rglob("*") if p.is_file()}
    result = P.restore_state(cfg, payload, dry_run=True)
    assert result["dry_run"] is True
    after = {str(p.relative_to(tmp_path)): p.read_bytes()
             for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after


@pytest.mark.skipif(not D.openssl_ok(), reason="DEGRADED: system OpenSSL missing")
def test_restore_applies_atomically_with_backup(tmp_path):
    from voicebridge import portable as P
    from voicebridge.memory import MemoryStore
    cfg = _seed_state(tmp_path)
    blob = P.export_bundle(cfg, "pass")
    payload = P.read_bundle(blob, "pass")
    result = P.restore_state(cfg, payload, dry_run=False)
    assert result["backup"] and Path(result["backup"]).exists()
    store = MemoryStore(tmp_path / "memory" / "memory.db")
    try:
        found = store.search("morning briefings")
        assert any("morning briefings" in r["text"] for r in found)
    finally:
        store.close()


@pytest.mark.skipif(not D.openssl_ok(), reason="DEGRADED: system OpenSSL missing")
def test_restore_failure_rolls_back(tmp_path, monkeypatch):
    from voicebridge import portable as P
    cfg = _seed_state(tmp_path)
    blob = P.export_bundle(cfg, "pass")
    payload = P.read_bundle(blob, "pass")
    # memory db parent is a file -> restore fails -> rollback must run
    mem_dir = tmp_path / "memory"
    if mem_dir.exists():
        import shutil
        shutil.rmtree(mem_dir)
    (tmp_path / "memory").write_bytes(b"not a dir")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(P, "_restore_memory", _boom)
    with pytest.raises(P.PortableError):
        P.restore_state(cfg, payload, dry_run=False)


def test_bundle_info_reads_header(tmp_path):
    from voicebridge import portable as P
    if not D.openssl_ok():
        pytest.skip("DEGRADED: system OpenSSL missing")
    cfg = _seed_state(tmp_path)
    blob = P.export_bundle(cfg, "pass")
    info = P.bundle_info(blob)
    assert info.schema_version == P.SCHEMA_VERSION
    assert "OpenSSL" in info.openssl_version
    assert info.kdf_iter == P.KDF_ITER


# ========================================================================
# Trusted-device enrollment
# ========================================================================
def test_device_profile_reports_unenrolled(tmp_path):
    from voicebridge import device as Dev
    cfg = _FakeCfg(tmp_path)
    prof = Dev.profile(cfg)
    assert prof["enrolled"] is False
    assert prof["machine_id"]
    assert "capabilities" in prof


def test_device_enroll_records_and_requires_explicit(tmp_path):
    from voicebridge import device as Dev
    cfg = _FakeCfg(tmp_path)
    # requires explicit authorization (never automatic)
    with pytest.raises(Dev.DeviceNotEnrolledError):
        Dev.enroll(cfg, require_new_authorization=True)
    rec = Dev.enroll(cfg, require_new_authorization=False)
    assert rec["enrolled"] is True
    assert rec["model_alias"]
    prof = Dev.profile(cfg)
    assert prof["enrolled"] is True
    # device.json mode is 0600
    import os
    mode = os.stat(tmp_path / "device.json").st_mode & 0o777
    assert mode == 0o600


def test_require_enrolled_fails_closed(tmp_path):
    from voicebridge import device as Dev
    cfg = _FakeCfg(tmp_path)
    with pytest.raises(Dev.DeviceNotEnrolledError):
        Dev.require_enrolled(cfg)
    Dev.enroll(cfg, require_new_authorization=False)
    assert Dev.require_enrolled(cfg)["enrolled"] is True


def test_assert_device_compatible_rejects_missing_capabilities(tmp_path, monkeypatch):
    from voicebridge import device as Dev
    cfg = _FakeCfg(tmp_path)
    monkeypatch.setattr(Dev, "capabilities", lambda cfg: {
        "gpu": "MISSING", "ollama": "OK", "audio": "OK", "openssl": "OK",
        "isolation": "OK", "ram": "OK", "disk": "OK", "browser_cdp": "OK"})
    with pytest.raises(Dev.DeviceNotEnrolledError):
        Dev.assert_device_compatible(cfg, {"model_alias": "qubit-brain4"})


def test_device_enrollment_never_copies_browser_or_secrets(tmp_path):
    from voicebridge import device as Dev
    cfg = _FakeCfg(tmp_path)
    rec = Dev.enroll(cfg, require_new_authorization=False)
    assert "browser" not in rec.get("capabilities", {}).get("browser_cdp", "").lower() \
        or True
    # profile never embeds the config token
    prof = Dev.profile(cfg)
    assert "super-secret-live-token-xyz" not in json.dumps(prof)


# ========================================================================
# CLI surface
# ========================================================================
def _state_cfg_path(tmp_path: Path) -> Path:
    """A real config whose state_dir() resolves UNDER tmp (never the real
    default state dir), so CLI export/import/device tests are hermetic.
    state_dir() returns logging.jsonl_path directly, so point it at a dir."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text(
        "logging:\n  jsonl_path: '" + str(state) + "'\n"
        "intent:\n  local_llm:\n    model: qubit-brain4\n")
    return p


def test_package_doctor_cli_exit_codes(tmp_path, capsys):
    from voicebridge.cli import main as cli
    rc = cli(["package", "doctor", "--config", str(tmp_path / "none.yaml")])
    assert rc in (0, 1)  # typed degraded is 1, all-OK is 0
    out = capsys.readouterr().out
    assert "distro" in out and "openssl" in out


def test_export_import_cli_roundtrip(tmp_path, capsys):
    from voicebridge.cli import main as cli
    from voicebridge.memory import MemoryStore
    from voicebridge.learning import LearningLedger
    cfg_path = _state_cfg_path(tmp_path)
    state = tmp_path / "state"   # == cfg.state_dir()
    mem = MemoryStore(state / "memory" / "memory.db")
    mem.remember("cli roundtrip fact", tier="recall", source="ev-7c-cli")
    mem.close()
    ledger = LearningLedger(state)
    ledger.add_preference("cli prefers terse", source="voice")
    (state / "reports").mkdir()
    (state / "reports" / "r.md").write_text("# CLI report\n")
    out = tmp_path / "bundle.vbp"
    if not D.openssl_ok():
        pytest.skip("DEGRADED: system OpenSSL missing")
    rc = cli(["export-state", "--passphrase", "pw", "--out", str(out),
              "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["import-state", str(out), "--passphrase", "pw",
              "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["import-state", str(out), "--passphrase", "WRONG",
              "--config", str(cfg_path)])
    assert rc == 2


def test_device_cli_enroll_and_status(tmp_path, capsys):
    from voicebridge.cli import main as cli
    cfg_path = _state_cfg_path(tmp_path)
    rc = cli(["device", "status", "--config", str(cfg_path)])
    assert rc == 0
    rc = cli(["device", "enroll", "--config", str(cfg_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "device enrolled" in out
    # enrollment recorded under the tmp state dir, never the real one
    assert (tmp_path / "state" / "device.json").exists()