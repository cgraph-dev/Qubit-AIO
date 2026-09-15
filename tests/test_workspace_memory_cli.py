"""OC-48 local surface: `qubit memory workspace-*` through the real CLI.

Proves the local markdown workspace memory + dreaming backend is reachable from
the canonical `qubit` CLI (previously backend-only), with confirmation gating
and the local-only provider refusal. Hermetic: temp XDG dirs, no network.
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

    rc_holder = {"rc": CLI.main(["--config", str(cfg), *argv])}
    out = capsys.readouterr().out
    start = out.find("{")
    assert start >= 0, out
    payload = json.loads(out[start:])
    payload["_rc"] = rc_holder["rc"]
    return payload


def test_cli_workspace_write_list_show_roundtrip(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    write = _run(capsys, cfg, "memory", "workspace-write", "status",
                 "--body", "line one", "--tag", "plan", "--confirm")
    assert write["state"] == "OK" and write["created"] is True
    listing = _run(capsys, cfg, "memory", "workspace-list")
    assert listing["state"] == "OK"
    assert [n["name"] for n in listing["notes"]] == ["status"]
    shown = _run(capsys, cfg, "memory", "workspace-show", "status")
    assert shown["state"] == "OK"
    assert shown["note"]["body"] == "line one"


def test_cli_workspace_write_requires_confirmation(tmp_path, monkeypatch,
                                                   capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    denied = _run(capsys, cfg, "memory", "workspace-write", "nope",
                  "--body", "x")
    assert denied["state"] == "denied"
    assert denied["code"] == "MEMORY_WORKSPACE_CONFIRM_REQUIRED"


def test_cli_workspace_providers_are_local_only(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    out = _run(capsys, cfg, "memory", "workspace-providers")
    assert out["state"] == "OK"
    by_name = {p["name"]: p["state"] for p in out["providers"]}
    assert by_name["markdown-workspace"] == "AVAILABLE"
    for external in ("lancedb", "wiki", "honcho", "cloud-sync"):
        assert by_name[external] == "UNAVAILABLE"


def test_cli_workspace_dream_is_offline_and_confirmed(tmp_path, monkeypatch,
                                                      capsys):
    cfg = _cfg(tmp_path, monkeypatch)
    _run(capsys, cfg, "memory", "workspace-write", "a", "--body", "x",
         "--confirm")
    out = _run(capsys, cfg, "memory", "workspace-dream", "--confirm")
    assert out["state"] == "OK"
