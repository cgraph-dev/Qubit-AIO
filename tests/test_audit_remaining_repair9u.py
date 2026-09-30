"""Audit-remaining repair wave — five internal-wiring NOT_READY items.

Focused regressions for the UI-audit follow-ups (2026-09-29) that remained
NOT_READY because they were internal wiring, not operator prerequisites:

* checkpoints list + real retention/policy prune (e-stop -> confirm -> audit);
* dashboard plugin visibility toggle persists AND invalidates the vendored
  hub cache;
* session search matches message CONTENT (not just ids/titles);
* binary file download/stream (``/api/files/read`` data URL + the
  ``/api/files/download|stream``, ``/api/fs/download`` routes) through the
  brokered, confined ``local.files_download`` capability.

Hermetic: isolated XDG/state, in-tree project root under ``tmp_path``, no
network, no live services.
"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import pytest

from voicebridge import qubit_compat as HC

pytestmark = pytest.mark.unit


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    monkeypatch.delenv("QUBIT_HOME", raising=False)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)], requested_by="t",
                        confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return created


# ------------------------------------------------------------------ item 2
def _ckpt_base(cfg) -> Path:
    from voicebridge import qubit_runtime as QR

    return Path(QR.runtime_home_for_cfg(cfg)) / "checkpoints"


def test_checkpoints_list_reads_real_store(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    base = _ckpt_base(cfg)
    (base / "session-a").mkdir(parents=True)
    (base / "session-a" / "0001.json").write_text("x" * 32)
    (base / "session-b").mkdir(parents=True)
    (base / "session-b" / "0001.json").write_text("y" * 16)
    (base / "session-b" / "0002.json").write_text("z" * 16)

    code, out = HC.h_ckpt(cfg, {}, {}, {})
    assert code == 200 and out["ok"] is True, out
    sessions = {s["session"]: s for s in out["sessions"]}
    assert sessions["session-a"]["files"] == 1
    assert sessions["session-a"]["bytes"] == 32
    assert sessions["session-b"]["files"] == 2
    assert out["total_bytes"] == 64


def test_checkpoints_prune_confirms_then_deletes_by_policy(tmp_path,
                                                           monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    base = _ckpt_base(cfg)
    legacy = base / "legacy-20200101-000000"
    legacy.mkdir(parents=True)
    (legacy / "pack-0.pack").write_bytes(b"Z" * 128)
    old = time.time() - 30 * 86400
    import os

    os.utime(legacy, (old, old))

    # Unconfirmed: staged 409, nothing deleted.
    code, staged = HC.h_checkpoints_prune(cfg, {}, {"retention_days": 1}, {})
    assert code == 409 and staged["code"] == "CONFIRM_REQUIRED", staged
    assert legacy.is_dir(), "unconfirmed prune must delete nothing"

    # Confirmed: real retention prune deletes the stale legacy archive.
    code, done = HC.h_checkpoints_prune(
        cfg, {}, {"retention_days": 1, "confirm": True}, {})
    assert code == 200 and done["ok"] is True, done
    assert done["result"]["deleted_stale"] >= 1, done
    assert not legacy.exists(), "stale archive must be deleted"

    # The poll route the SPA reads now reports the recorded terminal outcome.
    code, status = HC.h_action_status(cfg, {}, {},
                                      {"name": "checkpoints-prune"})
    assert code == 200 and status["state"] == "executed", status


def test_checkpoints_prune_refuses_under_estop(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setattr(HC, "_estop", lambda _cfg: True)
    code, out = HC.h_checkpoints_prune(cfg, {}, {"confirm": True}, {})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out


# ------------------------------------------------------------------ item 3
def test_plugin_visibility_persists_and_invalidates_hub_cache(tmp_path,
                                                              monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    from voicebridge import qubit_runtime as QR

    QR.ensure_path()
    from qubit_cli import web_server_dashboard as WSD

    calls: list = []
    monkeypatch.setattr(WSD, "_invalidate_plugins_hub_cache",
                        lambda: calls.append(1))

    code, out = HC.h_plugin_visibility(cfg, {}, {"hidden": True},
                                       {"id": "probe-plugin"})
    assert code == 200 and out["ok"] is True, out
    assert out["hidden"] is True and out["visible"] is False, out
    assert out["hidden_names"] == ["probe-plugin"], out
    assert calls, "the vendored plugins-hub cache must be invalidated"
    # Persisted to the ONE Qubit visibility store.
    persisted = json.loads(
        (HC._hidden_path(cfg)).read_text())["hidden"]
    assert "probe-plugin" in persisted, persisted

    code, out = HC.h_plugin_visibility(cfg, {}, {"hidden": False},
                                       {"id": "probe-plugin"})
    assert code == 200 and out["hidden"] is False, out
    persisted = json.loads(
        (HC._hidden_path(cfg)).read_text())["hidden"]
    assert "probe-plugin" not in persisted, persisted


# ------------------------------------------------------------------ item 4
def test_session_search_matches_message_content(tmp_path, monkeypatch):
    from voicebridge import qubit_session_store as QSS

    cfg = _cfg(tmp_path, monkeypatch)
    store = QSS.QubitSessionStore(cfg)
    first = store.create("Alpha")["session"]["id"]
    store.append(first, "user", "The shipment tracking number is zebra-9931")
    store.append(first, "assistant", "Noted.")
    second = store.create("Beta")["session"]["id"]
    store.append(second, "user", "just an unrelated title")

    # A content-only term must surface the session that contains it.
    res = store.list(query="zebra-9931")
    ids = {s["id"] for s in res["sessions"]}
    assert first in ids, res
    hit = next(s for s in res["sessions"] if s["id"] == first)
    assert hit.get("snippet"), hit
    assert second not in ids, res


def test_sessions_list_endpoint_returns_results_with_snippet(tmp_path,
                                                             monkeypatch):
    from voicebridge import qubit_session_store as QSS

    cfg = _cfg(tmp_path, monkeypatch)
    store = QSS.QubitSessionStore(cfg)
    sid = store.create("Travel notes")["session"]["id"]
    store.append(sid, "user", "My passport is in the blue folder")

    code, out = HC.h_sessions_list(cfg, {"q": "passport"}, {}, {})
    assert code == 200, out
    assert out["results"], out
    assert any(s["id"] == sid for s in out["results"]), out
    assert any(s.get("snippet") for s in out["results"]), out


# ------------------------------------------------------------------ item 5
BLOB = b"\x00\x01PNG\xff\xfe\x00binary\x80\x81payload"


def _binary_fixture(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir()
    _project(cfg, root)
    target = root / "blob.bin"
    target.write_bytes(BLOB)
    return cfg, target


def test_files_read_serves_binary_as_data_url(tmp_path, monkeypatch):
    cfg, target = _binary_fixture(tmp_path, monkeypatch)
    code, out = HC.h_files_read(cfg, {"path": str(target)}, {}, {})
    assert code == 200 and out["ok"] is True, out
    assert out["mime_type"] == "application/octet-stream", out
    assert out["data_url"].startswith("data:application/octet-stream;base64,")
    encoded = out["data_url"].split(",", 1)[1]
    assert base64.b64decode(encoded) == BLOB


def test_files_download_is_brokered_binary_stream(tmp_path, monkeypatch):
    cfg, target = _binary_fixture(tmp_path, monkeypatch)
    code, out = HC.h_files_download(cfg, {"path": str(target)}, {}, {})
    assert code == 200 and out["ok"] is True, out
    assert out["size"] == len(BLOB), out
    assert base64.b64decode(out["content_base64"]) == BLOB
    import hashlib

    assert out["sha256"] == hashlib.sha256(BLOB).hexdigest()


def test_files_download_refuses_outside_root(tmp_path, monkeypatch):
    cfg, _target = _binary_fixture(tmp_path, monkeypatch)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"nope")
    code, out = HC.h_files_download(cfg, {"path": str(outside)}, {}, {})
    assert code == 403, out
    assert out["code"] == "FILES_OUTSIDE_ROOT", out


def test_files_download_refuses_under_estop(tmp_path, monkeypatch):
    cfg, target = _binary_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(HC, "_estop", lambda _cfg: True)
    code, out = HC.h_files_download(cfg, {"path": str(target)}, {}, {})
    assert code == 403 and out["code"] == "EMERGENCY_STOP", out


# ------------------------------------------------------------------ item 1
def test_channel_config_bridges_into_vendor_gateway_store(tmp_path, monkeypatch):
    import yaml

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("MY_TG_TOKEN", "123456:abcdefghijklmnop")

    body = {"enabled": True,
            "env": {"TELEGRAM_BOT_TOKEN": "env:MY_TG_TOKEN"}}
    code, staged = HC.msg_update(cfg, {}, dict(body), {"id": "telegram"})
    assert code == 409 and staged["code"] == "CONFIRM_REQUIRED", staged

    code, done = HC.msg_update(cfg, {}, {**body, "confirm": True},
                               {"id": "telegram"})
    assert code == 200 and done["ok"] is True, done
    bridge = done["bridge"]
    assert bridge["state"] == "OK", bridge
    assert bridge["env_written"] == ["TELEGRAM_BOT_TOKEN"], bridge
    assert bridge["enabled_applied"] is True, bridge

    # The REAL vendored gateway store the daemon reads now reflects the channel
    # (this is the "consumed by a transport adapter" bridge): the credential env
    # var and the per-platform enabled flag.
    from voicebridge import qubit_runtime as QR

    home = QR.runtime_home_for_cfg(cfg)
    env_text = (home / ".env").read_text()
    assert "TELEGRAM_BOT_TOKEN=123456:abcdefghijklmnop" in env_text, env_text
    conf = yaml.safe_load((home / "config.yaml").read_text())
    assert conf["platforms"]["telegram"]["enabled"] is True, conf

    # And the vendored gateway config loader sees the platform enabled with the
    # resolved token (the exact shape the adapter consumes).
    QR.ensure_path()
    from gateway.config import load_gateway_config
    from gateway.config import Platform

    gw = load_gateway_config()
    assert Platform.TELEGRAM in gw.platforms, gw.platforms
    assert gw.platforms[Platform.TELEGRAM].enabled is True
    assert gw.platforms[Platform.TELEGRAM].token == "123456:abcdefghijklmnop"


def test_channel_bridge_never_seeds_unresolvable_token(tmp_path, monkeypatch):
    import yaml

    cfg = _cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("MISSING_TG", raising=False)
    code, done = HC.msg_update(
        cfg, {},
        {"enabled": True, "env": {"TELEGRAM_BOT_TOKEN": "env:MISSING_TG"},
         "confirm": True},
        {"id": "telegram"})
    assert code == 200 and done["bridge"]["state"] == "OK", done
    assert done["bridge"]["env_written"] == [], done
    assert done["bridge"]["unresolved"] == ["TELEGRAM_BOT_TOKEN"], done
    from voicebridge import qubit_runtime as QR

    home = QR.runtime_home_for_cfg(cfg)
    env_text = (home / ".env").read_text() if (home / ".env").exists() else ""
    assert "TELEGRAM_BOT_TOKEN=" not in env_text, env_text
    conf = yaml.safe_load((home / "config.yaml").read_text())
    assert conf["platforms"]["telegram"]["enabled"] is True, conf


def test_download_capability_registered_read_only():
    from voicebridge import broker as B
    from voicebridge import file_patch as FP
    from voicebridge.capabilities import Tier
    from voicebridge.power import power_control

    b = B.build_host_broker(power_control=power_control(None), cfg=None)
    registered = B.register_file_patch(b, cfg=None)
    assert FP.download_capability_name() in registered, registered
    binding = b.get(FP.download_capability_name())
    assert binding is not None and binding.spec.tier is Tier.READ_ONLY
