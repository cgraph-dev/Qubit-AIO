"""OC-D16 surface: `qubit msg feishu-agent-*` through the real CLI.

Proves the dynamic-Feishu-agent routing records (backend-complete in
`voicebridge/messaging.py`) are reachable from the canonical `qubit` CLI, with
confirmation gating and honest typed failures when no account/transport is
configured. Hermetic: temp XDG dirs, no network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch) -> Path:
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return p


def _run(capsys, cfg: Path, *argv) -> dict:
    from voicebridge import cli as CLI

    rc = CLI.main(["--config", str(cfg), *argv])
    out = capsys.readouterr().out
    start = out.find("{")
    assert start >= 0, out
    payload = json.loads(out[start:])
    payload["_rc"] = rc
    return payload


def test_cli_feishu_agent_add_list_remove(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    added = _run(capsys, cfg, "msg", "feishu-agent-add", "studio",
                 "--app-id-ref", "env:FEISHU_APP_ID",
                 "--allowlist", "chat1,chat2", "--confirm")
    assert added["state"] == "OK"
    assert added["agent"]["id"] == "studio"
    assert added["agent"]["allowlist"] == ["chat1", "chat2"]
    # the reference kind is recorded; the value is never stored
    assert "FEISHU_APP_ID" not in json.dumps(added)
    listing = _run(capsys, cfg, "msg", "feishu-agent-list")
    assert [a["id"] for a in listing["agents"]] == ["studio"]
    removed = _run(capsys, cfg, "msg", "feishu-agent-remove", "studio",
                   "--confirm")
    assert removed["state"] == "OK" and removed["removed"] == "studio"


def test_cli_feishu_agent_add_requires_confirmation(tmp_path, monkeypatch,
                                                     capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    denied = _run(capsys, cfg, "msg", "feishu-agent-add", "x",
                  "--app-id-ref", "env:X")
    assert denied["state"] == "denied"
    assert denied["code"] == "MSG_CONFIRM_REQUIRED"


def test_cli_feishu_send_without_account_is_typed_failure(tmp_path,
                                                          monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _run(capsys, cfg, "msg", "feishu-agent-add", "studio",
         "--app-id-ref", "env:FEISHU_APP_ID", "--allowlist", "chat1",
         "--confirm")
    out = _run(capsys, cfg, "msg", "feishu-send-as-agent", "studio",
               "--account", "missing", "--to", "chat1", "--body", "hi",
               "--confirm")
    # no configured account/transport -> typed failure, never a fake success
    assert out["state"] != "OK"
    assert out.get("code")
