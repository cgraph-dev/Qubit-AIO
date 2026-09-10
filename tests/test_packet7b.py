"""Packet 7B tests: feature flags, rollback drills, smoke coverage, change
reports, and final runtime/documentation reconciliation.

Everything is hermetic: flag writes happen on throwaway configs, drills seed
throwaway state, smoke runs against the WebUI fake-orch path, and the doctor
reconciliation is read-only. No live config, service, model, or audio device
is touched.
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


# ========================================================================
# Feature flags: registry + typed defaults + resolution
# ========================================================================
def test_feature_flag_registry_is_complete_and_typed():
    from voicebridge import feature_flags as FF
    names = set(FF.names())
    for want in ("broker", "compiler_5c", "canary_q5", "router_l0",
                 "router_l1", "llm_classifier"):
        assert want in names, f"flag {want} missing from registry"
    for f in FF.FLAGS:
        assert f.name and f.config_path and f.category
        assert f.default is not None
        assert f.rollback_value is not None


def test_flag_defaults_agree_with_config_defaults():
    from voicebridge import feature_flags as FF
    from voicebridge.config import DEFAULTS
    for f in FF.FLAGS:
        node: object = DEFAULTS
        for seg in f.config_path:
            node = node.get(seg) if isinstance(node, dict) else None
        assert node == f.default, \
            f"{f.name}: config DEFAULTS {node!r} != registry {f.default!r}"


def test_flag_effective_resolves_from_config(tmp_path, monkeypatch):
    from voicebridge import feature_flags as FF
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("safety:\n  tool_broker: false\n")
    monkeypatch.setenv("VOICE_BRIDGE_CONFIG", str(cfg_path))
    from voicebridge.config import Config
    cfg = Config.load(cfg_path)
    assert FF.effective(cfg, "broker") is False
    assert FF.effective(cfg, "compiler_5c") == "5c"   # default
    assert FF.effective(cfg, "canary_q5") is False
    assert FF.effective(cfg, "llm_classifier") is True
    eff = FF.effective_all(cfg)
    assert set(eff) == set(FF.names())


def test_flag_env_override_key_format():
    from voicebridge import feature_flags as FF
    f = FF._by_name("broker")
    key = FF.env_key(f)
    assert key == "VOICE_BRIDGE__SAFETY__TOOL_BROKER"
    assert FF.to_env(f, False) == {key: "false"}


# ========================================================================
# Flag writes are atomic + backup; never touch state dir
# ========================================================================
def test_flag_write_creates_backup_and_applies(tmp_path):
    from voicebridge import feature_flags as FF
    from voicebridge.config import Config
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("intent:\n  compiler:\n    mode: 4b\n")
    state = tmp_path / "state"
    state.mkdir()
    (state / "memory.db").write_bytes(b"MEM")
    before = FF.state_snapshot(state)
    bak = FF.apply_flag(cfg_path, "compiler_5c", "5c")
    assert bak.exists() and "compiler" not in str(bak)
    cfg = Config.load(cfg_path)
    assert FF.effective(cfg, "compiler_5c") == "5c"
    assert FF.state_snapshot(state) == before  # state untouched
    # rollback restores the previous file content (config only)
    FF.rollback_flag(cfg_path, "compiler_5c")
    cfg2 = Config.load(cfg_path)
    assert FF.effective(cfg2, "compiler_5c") == "4b"


def test_flag_write_requires_existing_config(tmp_path):
    from voicebridge import feature_flags as FF
    with pytest.raises(FileNotFoundError):
        FF.apply_flag(tmp_path / "nope.yaml", "broker", True)


# ========================================================================
# Rollback drill: every flag flips and restores, user data preserved
# ========================================================================
def test_rollback_drill_script_passes(tmp_path):
    from scripts import rollback_drill as RD
    report = RD.run_drill()
    assert report["user_data_preserved"] is True
    assert report["all_ok"] is True
    assert len(report["flags"]) == 6
    for r in report["flags"]:
        assert r["ok"], r


def test_rollback_drill_cli_exit_code():
    from scripts import rollback_drill as RD
    assert RD.main(["--json"]) == 0


# ========================================================================
# Smoke coverage: startup/dashboard/task/cancellation/voice
# ========================================================================
def test_smoke_category_registered_in_run_tests():
    from scripts import run_tests as RT
    assert "smoke" in RT.CATEGORIES
    assert any("test_smoke.py" in a for a in RT.CATEGORIES["smoke"])


def test_reconcile_category_routes_to_doctor():
    from scripts import run_tests as RT
    assert "reconcile" not in RT.CATEGORIES  # handled as a command, not pytest
    src = (REPO / "scripts" / "run_tests.py").read_text()
    assert '"reconcile"' in src
    assert "--reconcile" in src
    # the category choice accepts it alongside health
    assert '"health", "reconcile"' in src or "['health', 'reconcile']" in src


def test_smoke_marker_registered_in_pytest_ini():
    ini = (REPO / "pytest.ini").read_text()
    assert "smoke:" in ini


def test_smoke_test_file_covers_all_five_paths():
    src = (REPO / "tests" / "test_smoke.py").read_text()
    for probe in ("startup", "dashboard", "task", "cancellation", "voice"):
        assert f"test_{probe}" in src, f"smoke missing {probe} path"


def test_smoke_never_touches_live_services():
    src = (REPO / "tests" / "test_smoke.py").read_text()
    assert "systemctl" not in src
    assert "11434" not in src            # no Ollama
    assert "8890" not in src             # no live bridge port


# ========================================================================
# Change reports
# ========================================================================
def test_change_report_generates_markdown(tmp_path, capsys):
    from scripts import change_report as CR
    out = tmp_path / "report.md"
    rc = CR.main(["--title", "Packet 7B test", "--tests", "unit green",
                  "--bench", "drill ok", "--risks", "none",
                  "--rollback", "restore .bak", "--out", str(out)])
    assert rc == 0
    md = out.read_text()
    for section in ("Files changed", "Diff summary", "Tests",
                    "Benchmarks / measurements", "Risks", "Rollback"):
        assert f"## {section}" in md, f"missing {section}"
    assert "Packet 7B test" in md


def test_change_report_never_contains_secrets(tmp_path):
    from scripts import change_report as CR
    out = tmp_path / "r.md"
    CR.main(["--title", "t", "--tests", "ok", "--out", str(out)])
    md = out.read_text()
    for secret in ("webui.token", "API_KEY", "token=", "password", "Bearer "):
        assert secret.lower() not in md.lower()


# ========================================================================
# Final runtime/documentation reconciliation (doctor --reconcile)
# ========================================================================
def test_doctor_reconcile_passes(tmp_path, monkeypatch):
    import urllib.request
    import socket

    def _no_net(*a, **k):
        raise ConnectionRefusedError("no network in unit test")

    monkeypatch.setattr(urllib.request, "urlopen", _no_net)
    monkeypatch.setattr(socket, "create_connection", _no_net)

    # hermetic: an explicit working config so the check is machine-independent
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text(
        "mic:\n  channels: 1\n  gain: 3.0\n"
        "audio:\n  output_device: \"\"\n  output_device_name: \"\"\n"
        "intent:\n  local_llm:\n    model: qubit-brain4\n")
    from scripts import config_doctor as DOC
    cfg = DOC._load_config(str(cfg_path))
    results: list = []
    DOC.check_reconcile(cfg, results)
    by = {r[0]: r[1] for r in results}
    for key in ("reconcile.flags_defaults", "reconcile.flags_resolve",
                "reconcile.model_alias", "reconcile.audio_rules",
                "reconcile.ops_categories", "reconcile.migrations"):
        assert key in by, f"{key} missing from reconcile"
        assert by[key] != "FAIL", f"{key} FAILED: {by}"
    assert DOC.main(["--reconcile", "--config", str(cfg_path)]) == 0


# ========================================================================
# CLI surface
# ========================================================================
def test_flags_cli_list_and_get(tmp_path, capsys):
    from voicebridge.cli import main as cli
    rc = cli(["flags", "list", "--config", str(tmp_path / "nope.yaml")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "broker" in out and "rollback" in out
    rc = cli(["flags", "get", "broker", "--config", str(tmp_path / "nope.yaml")])
    assert rc == 0


def test_flags_cli_set_is_dry_run_by_default(tmp_path, capsys):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("safety:\n  tool_broker: true\n")
    rc = cli(["flags", "set", "broker", "false",
              "--config", str(cfg_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[dry-run]" in out
    # config unchanged
    assert "tool_broker: true" in cfg_path.read_text()
    assert not list(tmp_path.glob("*.bak"))


def test_flags_cli_set_apply_writes_backup(tmp_path):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("safety:\n  tool_broker: true\n")
    rc = cli(["flags", "set", "broker", "false", "--apply",
              "--config", str(cfg_path)])
    assert rc == 0
    bak = list(tmp_path.glob("*.bak"))
    assert len(bak) == 1
    assert "tool_broker: false" in cfg_path.read_text()


def test_flag_drill_cli_passes(tmp_path):
    from voicebridge.cli import main as cli
    cfg_path = tmp_path / "voice.yaml"
    cfg_path.write_text("safety:\n  tool_broker: true\n")
    rc = cli(["flag-drill", "--config", str(cfg_path)])
    assert rc == 0


def test_flag_drill_redacts_live_token():
    """The drill serializes a config for a throwaway file; secret-shaped keys
    must be masked so the live token never lands on disk."""
    from voicebridge import feature_flags as FF
    from voicebridge.config import Config
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cfg_path = Path(td) / "voice.yaml"
        cfg_path.write_text("webui:\n  token: supersecret-live-token-abc\n"
                            "agent:\n  api_key: super-secret-agent-key\n")
        cfg = Config.load(cfg_path)
        yaml_text = FF.redact_config_yaml(cfg)
        assert "supersecret-live-token-abc" not in yaml_text
        assert "[REDACTED]" in yaml_text
        assert "super-secret-agent-key" not in yaml_text


def test_log_event_never_crashes_on_non_serializable_fields(tmp_path, monkeypatch):
    """Order-dependent full-suite regression: once a prior test has set the
    global journal, log_event with a non-JSON value (a mocked provider) must
    write a visible typed marker, never raise TypeError. No blanket default=str.
    """
    from voicebridge import logging_setup as ls
    from unittest import mock
    import json as _json
    jf = tmp_path / "events.jsonl"
    monkeypatch.setattr(ls, "_EVENT_FILE", jf)
    monkeypatch.setattr(ls, "_LOGGER", None)
    mocked_tts = mock.MagicMock()
    ls.log_event("tts.speak", text="hello there", provider=mocked_tts.name)
    line = jf.read_text().strip().splitlines()[-1]
    rec = _json.loads(line)
    assert isinstance(rec["provider"], str)
    assert "MagicMock" in rec["provider"] or "<" in rec["provider"]
    assert rec["text"] == "hello there"
    # raw piped object (bytes/Path) also becomes a visible typed string
    ls.log_event("probe", blob=b"\x00\x01", path=Path("/tmp/x.wav"))
    line2 = jf.read_text().strip().splitlines()[-1]
    rec2 = _json.loads(line2)
    assert isinstance(rec2["blob"], str) and "bytes" in rec2["blob"]
    assert isinstance(rec2["path"], str) and "PosixPath" in rec2["path"]


def test_log_event_journal_write_is_still_survivable(tmp_path, monkeypatch):
    """A permission-denied journal must still degrade, not crash the caller."""
    from voicebridge import logging_setup as ls
    import json as _json
    jf = tmp_path / "events.jsonl"
    monkeypatch.setattr(ls, "_EVENT_FILE", jf)
    monkeypatch.setattr(ls, "_LOGGER", None)
    ls.log_event("probe", value=1)
    monkeypatch.setattr(ls, "_EVENT_FILE", tmp_path / "missing" / "nested" / "x.jsonl")
    ls.log_event("probe", value=2)  # write fails -> OSError swallowed