"""Packet 8T-F — foundation migration tests (hermetic; no 8U work).

Covers: identity constants, installer/service-template defaults,
singularity audit, server-side SecretRef, hermetic migration rehearsal
(apply/idempotent/rollback), and uninstall preservation. Live hardware,
service, browser, account, model, timer, and clean-machine proofs remain
operator-only and are never claimed here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

from voicebridge import foundation as F  # noqa: E402
from voicebridge.providers import credentials as C  # noqa: E402


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
def test_identity_constants():
    assert F.PRODUCT_NAME == "Qubit Agent"
    assert F.PUBLIC_IDENTITY == "Qubit Agent"
    assert F.VOICE_SUBSYSTEM == "Qubit VoiceBridge"
    assert F.INSTALL_IDENTITY == "qubit-voicebridge"
    assert F.IMPORT_PACKAGE == "voicebridge"
    assert F.LEGACY_IDENTITY == "openclaw-voice-bridge"
    assert F.AGENT_TURN_PATH == "voicebridge.agent.turn"
    assert F.AGENT_SESSION_PATH == "voicebridge.agent.run_session"
    receipt = F.identity_receipt()
    assert receipt["product"] == "Qubit Agent"
    assert receipt["agent_turn"] == "voicebridge.agent.turn"


def test_install_sh_canonical_identity():
    script = REPO_ROOT / "scripts" / "install.sh"
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr  # script still parses
    text = script.read_text()
    assert "Qubit Agent" in text
    assert "Install the OpenClaw Voice Bridge" not in text
    assert "qubit-voicebridge" in text


def test_legacy_template_marked_non_install():
    text = (REPO_ROOT / "systemd" / "qubit-voice-agent.service").read_text()
    assert "LEGACY REFERENCE" in text and "DO NOT INSTALL" in text
    assert "qubit-voicebridge.service" in text


def test_check_deployment_defaults_canonical():
    r = subprocess.run(
        [sys.executable, "scripts/check_deployment.py"],
        capture_output=True, text=True, timeout=120, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fail" not in r.stdout.split("deployment:")[-1].split("—")[0] \
        or "0 fail" in r.stdout


# ---------------------------------------------------------------------------
# Singularity audit
# ---------------------------------------------------------------------------
def test_singularity_all_single_ok():
    counts = F.collect_runtime_state(
        mic_open=True, hotkey_running=False, web_active_count=0,
        voice_session_count=1, tts_provider="piper",
        broker_count=1, audit_count=1, estop_count=1, agent_count=1)
    assert counts["listeners"] == 1
    report = F.singularity_report(counts)
    assert report["verdict"] == "OK"
    assert set(report["detail"].values()) == {"SINGLE"}
    F.assert_foundation(report)  # must not raise


def test_singularity_multiple_fails_closed():
    counts = F.collect_runtime_state(
        mic_open=True, hotkey_running=True, web_active_count=1,
        voice_session_count=1, tts_provider="piper",
        broker_count=1, audit_count=1, estop_count=1, agent_count=2)
    report = F.singularity_report(counts)
    assert report["verdict"] == "FAILED"
    assert report["detail"]["listeners"] == "MULTIPLE"
    assert report["detail"]["agent_authorities"] == "MULTIPLE"
    with pytest.raises(F.FoundationError):
        F.assert_foundation(report)


def test_singularity_unobserved_is_degraded_not_ok():
    report = F.singularity_report({})
    assert report["verdict"] == "DEGRADED"
    with pytest.raises(F.FoundationError):
        F.assert_foundation(report)
    partial = F.singularity_report({"brokers": 1})
    assert partial["verdict"] == "DEGRADED"
    assert partial["detail"]["brokers"] == "SINGLE"


def test_native_authorities_cover_every_concern():
    authorities = F.native_authorities()
    assert len(authorities) >= 5
    concerns = {entry["concern"] for entry in authorities}
    assert {"agent turns", "worker sessions"} <= concerns
    for entry in authorities:
        assert entry["authority"] and "hermes" not in entry["authority"].lower()
        assert "openclaw" not in entry["authority"].lower()
    assert F.verify_runtime_identity()["state"] == "VERIFIED"


# ---------------------------------------------------------------------------
# SecretRef (OpenClaw SecretRef shape, Qubit server-side policy)
# ---------------------------------------------------------------------------
def test_secret_ref_env_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_8TF_TEST_KEY", "s3cr3t-value")
    val, code = C.resolve_secret_ref(C.SecretRef(source="env", name="QUBIT_8TF_TEST_KEY"))
    assert (val, code) == ("s3cr3t-value", "")
    val, code = C.resolve_secret_ref({"source": "env", "name": "QUBIT_8TF_MISSING_XYZ"})
    assert (val, code) == ("", "AUTH_MISSING")
    assert C.redact_secret_ref(C.SecretRef(source="env", name="QUBIT_8TF_TEST_KEY")) == \
        "env:QUBIT_8TF_TEST_KEY"


def test_secret_ref_file_roundtrip(tmp_path):
    key_file = tmp_path / "api.key"
    key_file.write_text("file-secret-123\nsecond-line-ignored\n")
    val, code = C.resolve_secret_ref(C.SecretRef(source="file", name=str(key_file)))
    assert (val, code) == ("file-secret-123", "")
    assert C.redact_secret_ref(C.SecretRef(source="file", name=str(key_file))) == \
        "file:<redacted-path>"
    missing, code = C.resolve_secret_ref("file:/nonexistent-8tf-dir/key")
    assert (missing, code) == ("", "AUTH_MISSING")


def test_secret_ref_refuses_browser_and_private_db(tmp_path):
    for evil in ("~/.config/google-chrome/Default/Cookies",
                 "/home/u/snap/firefox/common/.mozilla/firefox/x/logins.json",
                 "~/.config/opencode/auth.json"):
        val, code = C.resolve_secret_ref(C.SecretRef(source="file", name=evil))
        assert (val, code) == ("", "AUTH_MISSING"), evil
    for bad in ("", "plaintext-key", "vault:prod/key",
                {"source": "exec", "name": "id"},
                C.SecretRef(source="env", name="")):
        assert C.resolve_secret_ref(bad) == ("", "AUTH_MISSING")
        assert "s3cr3t" not in C.redact_secret_ref(bad)


def test_secret_ref_unreadable_file_fails_closed(tmp_path):
    locked = tmp_path / "locked.key"
    locked.write_text("nope")
    locked.chmod(0o000)
    try:
        if os.geteuid() == 0:
            pytest.skip("root bypasses file modes; refusal proven by unit shape")
        assert C.resolve_secret_ref(f"file:{locked}") == ("", "AUTH_MISSING")
    finally:
        locked.chmod(0o600)


# ---------------------------------------------------------------------------
# Migration rehearsal (isolated HOME; synthetic fixture only)
# ---------------------------------------------------------------------------
def _isolated_env(tmp_path):
    home = tmp_path / "home"
    env = dict(os.environ, HOME=str(home),
               XDG_CONFIG_HOME=str(home / ".config"),
               XDG_DATA_HOME=str(home / ".local/share"),
               XDG_STATE_HOME=str(home / ".local/state"),
               XDG_CACHE_HOME=str(home / ".cache"))
    return env, home


def _run_bridge(args, env):
    return subprocess.run([sys.executable, "-m", "voicebridge", *args],
                          capture_output=True, text=True, timeout=300,
                          cwd=str(REPO_ROOT), env=env)


def test_migrate_dry_run_creates_nothing(tmp_path):
    env, home = _isolated_env(tmp_path)
    r = _run_bridge(["product", "migrate"], env)
    assert r.returncode == 0, r.stderr[-1500:]
    assert "FRESH" in r.stdout
    assert not (home / ".config" / "qubit-voicebridge").exists()


def test_migrate_apply_idempotent_and_rollback_restores(tmp_path):
    env, home = _isolated_env(tmp_path)
    legacy = home / ".config" / "openclaw-voice-bridge"
    legacy.mkdir(parents=True)
    (legacy / "notes.txt").write_text("keepme-8tf")
    backup = tmp_path / "backup"

    applied = _run_bridge(["product", "migrate", "--apply",
                           "--backup-dir", str(backup)], env)
    assert applied.returncode == 0, applied.stderr[-1500:]
    assert "OK" in applied.stdout
    canonical = home / ".config" / "qubit-voicebridge" / "notes.txt"
    assert canonical.read_text() == "keepme-8tf"

    rerun = _run_bridge(["product", "migrate", "--apply",
                         "--backup-dir", str(backup)], env)
    assert rerun.returncode == 0 and "FRESH" in rerun.stdout

    rolled = _run_bridge(["product", "migrate-rollback", "--apply",
                          "--backup-dir", str(backup)], env)
    assert rolled.returncode == 0, rolled.stderr[-1500:]
    assert (legacy / "notes.txt").read_text() == "keepme-8tf"


def test_uninstall_preserves_state_by_default():
    from voicebridge.distribution import uninstall_plan

    class _Cfg:
        pass

    plan = uninstall_plan(_Cfg())
    assert plan["requires_confirmation"] is True
    preserved = " ".join(plan["preserves_by_default"]).lower()
    assert "state" in preserved and "config" in preserved


def test_provenance_pins_match_matrix():
    matrix = json.loads((REPO_ROOT / "docs" / "FEATURE_MATRIX_8U.json").read_text())
    for key in ("hermes-agent", "openclaw"):
        pin = matrix["upstreams"][key]["commit"]
        assert len(pin) == 40 and all(c in "0123456789abcdef" for c in pin)
    record = (REPO_ROOT / "voicebridge" / "upstream" / "ATTRIBUTION.md").read_text()
    assert matrix["upstreams"]["hermes-agent"]["commit"] in record
