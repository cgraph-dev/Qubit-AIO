"""Packet 8G tests: operator-gated real-Chromium/CDP qualification harness.

Hermetic-only. Covers:
- the live gate failing closed (live work impossible by default);
- origin-only target redaction (never titles/queries/secrets);
- preflight state mapping (OK / BROWSER_RESTART_REQUIRED / UNAVAILABLE) and the
  never-probed profile-lock stance;
- target discovery states (found / ambiguous / hidden / unavailable /
  per-platform isolation);
- scan-state classification (selector drift, auth/login-2FA-CAPTCHA,
  unavailable, overflow, healthy);
- the isolated fixture qualification pipeline: baseline -> 8C bundle ->
  no-change rescan -> external-item (+1 new) -> silence, WITHOUT touching the
  production state dir;
- bounded evidence (byte cap, private modes, sha256) and the reconnect probe;
- redacted qualification summaries that never persist fixture bodies.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import voicebridge.communications.qualify as Q  # noqa: E402
from voicebridge.communications.adapters.base import (  # noqa: E402
    AdapterScan, ScanBounds,
)
from voicebridge.communications.contracts import (  # noqa: E402
    AccountHealthStatus, CommunicationItem, EvidenceAssetRecord,
    EvidenceStatus, ReadState, ScanStatus,
)
from voicebridge.communications.qualify import (  # noqa: E402
    EVIDENCE_MAX_BYTES, QUALIFY_ENV, deny_live, describe_targets,
    isolate_cfg, live_authorized, probe_reconnect, qualify_fixture,
    qualify_live_scan, qualify_preflight, qualify_targets,
    redact_target_url, run_qualification, scan_states,
)

FIX = REPO_ROOT / "tests" / "fixtures" / "communications"


def _cfg(tmp_path):
    from voicebridge.config import Config
    state = tmp_path / "production-state"
    state.mkdir(exist_ok=True)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) + "'\n")
    return Config.load(p)


# ---------------------------------------------------------------- live gate
def test_live_gate_fails_closed_by_default(monkeypatch):
    monkeypatch.delenv(QUALIFY_ENV, raising=False)
    assert live_authorized() is False
    denied = deny_live(True)
    assert denied is not None
    assert denied["state"] == "denied"
    assert denied["code"] == "LIVE_NOT_AUTHORIZED"
    # read-only (non-live) work is never denied
    assert deny_live(False) is None


def test_live_gate_opens_only_with_operator_env(monkeypatch):
    monkeypatch.setenv(QUALIFY_ENV, "1")
    assert live_authorized() is True
    assert deny_live(True) is None
    monkeypatch.setenv(QUALIFY_ENV, "0")
    assert deny_live(True) is not None


def test_cli_live_without_gate_exits_denied():
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "browser_qualify.py"),
         "--live"],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k != QUALIFY_ENV})
    assert proc.returncode == 2
    payload = json.loads(proc.stdout)
    assert payload["code"] == "LIVE_NOT_AUTHORIZED"


def test_cli_preflight_runs_without_browser_or_gate(tmp_path):
    cfg = _cfg(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "browser_qualify.py"),
         "--preflight", "--config", str(cfg.path)],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k != QUALIFY_ENV})
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["state"] in ("OK", "BROWSER_RESTART_REQUIRED", "UNAVAILABLE")
    assert payload["loopback_only"] is True


# ---------------------------------------------------------------- redaction
def test_redact_target_url_strips_query_and_fragment():
    assert redact_target_url(
        "https://mail.google.com/mail/u/0/#inbox?token=secret") \
        == "https://mail.google.com/mail"
    assert redact_target_url(
        "https://www.instagram.com/direct/inbox/?cookie=abc") \
        == "https://www.instagram.com/direct"
    assert redact_target_url("") == ""
    assert redact_target_url("not a url") == ""


def test_describe_targets_never_records_titles_or_bodies():
    targets = [
        {"type": "page", "url": "https://mail.google.com/?x=1",
         "title": "Private inbox title", "attached": True,
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/x"},
        {"type": "service_worker", "url": "chrome://serviceworker/",
         "title": "worker", "attached": False, "webSocketDebuggerUrl": ""},
    ]
    out = describe_targets(targets)
    assert len(out) == 2
    for rec in out:
        assert "title" not in rec
        assert "url" not in rec
        assert "?x=1" not in rec["origin"]
    assert out[0]["origin"] == "https://mail.google.com"
    assert out[0]["has_ws"] is True
    assert out[1]["has_ws"] is False


# ---------------------------------------------------------------- preflight
def test_preflight_maps_cdp_precondition_states(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)

    def _pre(state, detail=""):
        def inner(cfg):
            return {"state": state, "cdp": state == "OK", "detail": detail}
        return inner

    monkeypatch.setattr(Q, "cdp_precondition", _pre("OK", "ok"))
    monkeypatch.setattr(Q, "list_targets", lambda *a, **k: [])
    out = qualify_preflight(cfg)
    assert out["state"] == "OK" and out["cdp"] is True
    assert out["states"]["browser_restart_required"] is False
    assert out["states"]["profile_lock"] == "not_probed"
    assert out["targets"] == []

    monkeypatch.setattr(Q, "cdp_precondition",
                        _pre("BROWSER_RESTART_REQUIRED", "restart me"))
    out = qualify_preflight(cfg)
    assert out["states"]["browser_restart_required"] is True

    monkeypatch.setattr(Q, "cdp_precondition", _pre("UNAVAILABLE", "none"))
    out = qualify_preflight(cfg)
    assert out["states"]["cdp_unavailable"] is True


def test_preflight_target_discovery_is_redacted(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(Q, "cdp_precondition",
                        lambda cfg: {"state": "OK", "cdp": True})
    monkeypatch.setattr(
        Q, "list_targets",
        lambda *a, **k: [{"type": "page",
                          "url": "https://mail.google.com/?q=private",
                          "title": "Private", "attached": True,
                          "webSocketDebuggerUrl": "ws://127.0.0.1:9222/x"}])
    out = qualify_preflight(cfg)
    assert out["targets"][0]["origin"] == "https://mail.google.com"
    assert "private" not in json.dumps(out["targets"])
    assert "title" not in out["targets"][0]


# ------------------------------------------------------------- target states
def _target(url, *, ws=True, type="page"):
    t = {"type": type, "url": url, "attached": True}
    if ws:
        t["webSocketDebuggerUrl"] = "ws://127.0.0.1:9222/devtools/page/x"
    return t


def test_target_discovery_states(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    home = _target("https://www.instagram.com/")
    inbox = _target("https://www.instagram.com/direct/inbox/")

    # multiple wrong-route targets -> ambiguous, never guess
    monkeypatch.setattr(Q, "find_target_preferring",
                        lambda o, p, **k: (None, [home, home]))
    out = qualify_targets(cfg, ["instagram"])
    assert out["platforms"]["instagram"]["state"] == "ambiguous"

    # no targets -> unavailable
    monkeypatch.setattr(Q, "find_target_preferring",
                        lambda o, p, **k: (None, []))
    out = qualify_targets(cfg, ["gmail"])
    assert out["platforms"]["gmail"]["state"] == "unavailable"

    # a found, attachable target
    monkeypatch.setattr(Q, "find_target_preferring",
                        lambda o, p, **k: (inbox, [inbox]))
    out = qualify_targets(cfg, ["instagram"])
    assert out["platforms"]["instagram"]["state"] == "found"

    # hidden target: exists but no debugger websocket
    hidden = _target("https://mail.google.com/", ws=False)
    monkeypatch.setattr(Q, "find_target_preferring",
                        lambda o, p, **k: (hidden, [hidden]))
    out = qualify_targets(cfg, ["gmail"])
    assert out["platforms"]["gmail"]["state"] == "hidden"

    # read-only discovery without --live is never denied
    assert out["state"] == "OK"


def test_target_discovery_per_platform_isolation(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    inbox = _target("https://www.instagram.com/direct/inbox/")

    def _pick(origin, preferred, **k):
        if "instagram" in origin:
            return inbox, [inbox]
        raise RuntimeError("boom")

    monkeypatch.setattr(Q, "find_target_preferring", _pick)
    out = qualify_targets(cfg, ["instagram", "gmail"])
    assert out["platforms"]["instagram"]["state"] == "found"
    assert out["platforms"]["gmail"]["state"] == "failed"
    # one platform's failure must not poison the others
    assert out["state"] == "degraded"


# ------------------------------------------------------------ scan states
def test_scan_states_classification():
    def _scan(health, *, auth=None, overflow=False, reason=""):
        return AdapterScan(status=ScanStatus.DEGRADED, health_status=health,
                           auth_ok=auth, overflow=overflow, reason=reason)

    assert scan_states(_scan(AccountHealthStatus.DEGRADED,
                             reason="drift"))["selector_drift"] is True
    s = scan_states(_scan(AccountHealthStatus.AUTH_REQUIRED, auth=False))
    assert s["auth_required"] is True
    assert s["auth"] == "login_2fa_captcha"
    assert scan_states(_scan(AccountHealthStatus.UNAVAILABLE))["unavailable"] is True
    assert scan_states(_scan(AccountHealthStatus.HEALTHY, auth=True),
                       )["auth"] == "none"
    assert scan_states(_scan(AccountHealthStatus.HEALTHY, overflow=True)
                       )["overflow"] is True


# ------------------------------------------------------------- fixture pipeline
def _rows_with_screenshot(rows=None):
    rows = json.loads((FIX / "new_items.json").read_text())["rows"] \
        if rows is None else rows
    rows[0]["screenshot"] = base64.b64encode(b"\x00" * 1024).decode()
    return rows


def _write_fixture(tmp_path, rows, name="fx.json"):
    fx = tmp_path / name
    fx.write_text(json.dumps({"platform": "gmail", "rows": rows}))
    return fx


def test_fixture_qualification_full_pipeline(tmp_path):
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qualify"
    fx = _write_fixture(tmp_path, _rows_with_screenshot(), "baseline.json")
    external = _write_fixture(
        tmp_path, _rows_with_screenshot(
            json.loads((FIX / "qualify_external_item.json").read_text())
            ["rows"]), "external.json")
    out = qualify_fixture(cfg, "gmail", fixture=fx, external_fixture=external,
                          state_dir=state_dir)
    assert out["state"] == "OK"
    assert out["isolated"] is True
    checks = out["checks"]
    assert checks["baseline"]["state"] == "OK"
    assert checks["baseline"]["commit"] == "baseline"
    assert checks["bundle"]["state"] == "OK"
    assert sorted(checks["bundle"]["formats"]) == ["html", "json", "markdown"]
    assert checks["evidence"]["assets_available"] >= 1
    assert checks["no_change_rescan"]["state"] == "OK"
    assert checks["no_change_rescan"]["commit"] == "no-change"
    assert checks["external_item"]["state"] == "OK"
    assert checks["external_item"]["new_count"] == 1
    assert checks["external_item_no_change"]["state"] == "OK"


def test_fixture_qualification_never_touches_production_state(tmp_path):
    cfg = _cfg(tmp_path)
    prod = cfg.state_dir()
    state_dir = tmp_path / "qualify"
    fx = _write_fixture(tmp_path, _rows_with_screenshot())
    qualify_fixture(cfg, "gmail", fixture=fx, state_dir=state_dir)
    # production state root must be untouched (no store, no reports, no bundle)
    assert not (prod / "communications.db").exists()
    assert not (prod / "reports").exists()
    assert not (prod / "qualification").exists()
    # the isolated qualification state dir holds the real artifacts
    assert (state_dir / "communications.db").exists()
    assert (state_dir / "reports").exists()


# ---------------------------------------------------------------- live scan
def test_live_scan_requires_gate(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALIFY_ENV, raising=False)
    cfg = _cfg(tmp_path)
    out = qualify_live_scan(cfg, "gmail", state_dir=tmp_path / "q", live=True)
    assert out["state"] == "denied"
    assert out["code"] == "LIVE_NOT_AUTHORIZED"


def test_live_scan_without_browser_is_typed_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv(QUALIFY_ENV, "1")
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(Q, "find_target_preferring", lambda o, p, **k: (None, []))
    monkeypatch.setattr(Q, "find_target", lambda *a, **k: None)
    out = qualify_live_scan(cfg, "gmail", state_dir=tmp_path / "q", live=True)
    assert out["state"] in ("unavailable", "hidden")
    assert out["state"] != "denied"


class _FakeCdpClient:
    ALLOWED_METHODS = frozenset({"Runtime.evaluate", "Page.captureScreenshot"})

    def __init__(self, ws_url, *, timeout_s=15.0, allowed_expressions=()):
        self._ws_url = ws_url
        self.opened = 0

    def connect(self):
        self.opened += 1
        return self

    def close(self):
        return None


class _FakeGmailAdapter:
    adapter_name = "gmail"
    version = "8g-test"
    origin = "https://mail.google.com"
    preferred_route = ""
    extract_js = "(() => ({rows: [], health: {}}))()"

    def __init__(self, account_scope="default"):
        self.account_scope = account_scope

    def run(self, client, bounds, report_id=""):
        item = CommunicationItem.create(
            platform=Q.PLATFORM_NAMES["gmail"], account_scope=self.account_scope,
            sender="Synthetic Sender", subject="Synthetic",
            preview="Synthetic", is_new=True, read_state=ReadState.UNREAD,
            adapter="gmail", adapter_version="8g-test")
        blob = b"\x00" * 1024
        asset = EvidenceAssetRecord.create(
            report_id="qualify", item_identity=item.identity,
            mime="image/webp", byte_count=len(blob),
            sha256=hashlib.sha256(blob).hexdigest(),
            redaction_status=EvidenceStatus.AVAILABLE)
        return AdapterScan(items=[item], status=ScanStatus.NEW_ITEMS,
                           health_status=AccountHealthStatus.HEALTHY,
                           auth_ok=True, reason="",
                           assets=[asset], asset_bytes={asset.asset_id: blob})


def test_live_scan_persists_bounded_private_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv(QUALIFY_ENV, "1")
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(Q, "find_target_preferring",
                        lambda o, p, **k: (_target("https://mail.google.com/"),
                                           [_target("https://mail.google.com/")]))
    monkeypatch.setattr(Q, "find_target",
                        lambda *a, **k: _target("https://mail.google.com/"))
    monkeypatch.setattr(Q, "ADAPTERS", {Q.PLATFORM_NAMES["gmail"]: _FakeGmailAdapter})
    monkeypatch.setattr(Q, "CdpClient", _FakeCdpClient)

    state_dir = tmp_path / "qualify"
    out = qualify_live_scan(cfg, "gmail", state_dir=state_dir, live=True)
    assert out["state"] == "OK"
    assert out["states"]["healthy"] is True
    assert out["reconnect"]["state"] == "OK"
    assert out["evidence"]["state"] == "OK"
    assert out["evidence"]["assets"][0]["status"] == "stored"
    stored = list((state_dir / "qualify").rglob("*.webp"))
    assert len(stored) == 1
    assert stored[0].read_bytes() == b"\x00" * 1024
    assert (stored[0].stat().st_mode & 0o077) == 0
    # no sender/subject preview persisted anywhere
    for text in state_dir.rglob("*"):
        if text.is_file():
            raw = text.read_bytes()
            assert b"Synthetic Sender" not in raw
            assert b"Synthetic subject" not in raw


def test_oversized_evidence_refused_never_stored(tmp_path):
    state_dir = tmp_path / "qualify"
    big = b"\xff" * (EVIDENCE_MAX_BYTES + 1)
    asset = EvidenceAssetRecord.create(
        report_id="r", item_identity="i", mime="image/webp",
        byte_count=len(big),
        sha256=hashlib.sha256(big).hexdigest(),
        redaction_status=EvidenceStatus.AVAILABLE)
    recs = Q._write_live_evidence(state_dir, "run1", "gmail", [asset],
                                  {asset.asset_id: big})
    assert recs["assets"][0]["status"] == "oversized_refused"
    assert list((state_dir / "qualify" / "run1" / "gmail").rglob("*.webp")) == []
    recs2 = Q._write_live_evidence(state_dir, "run2", "gmail", [asset], {})
    assert recs2["assets"][0]["status"] == "no_bytes"


# ---------------------------------------------------------------- reconnect
def test_probe_reconnect_ok_and_fail():
    class _Fail:
        def __init__(self, ws_url, *, timeout_s=15.0):
            pass

        def connect(self):
            raise RuntimeError("ws refused")

        def close(self):
            pass

    assert probe_reconnect("ws://127.0.0.1:1/x",
                           client_cls=_FakeCdpClient)["state"] == "OK"
    out = probe_reconnect("ws://127.0.0.1:1/x", client_cls=_Fail)
    assert out["state"] == "failed"
    assert out["opened"] == 0


# ------------------------------------------------------------ orchestration
def test_run_qualification_redacted_summary_dry_run(tmp_path):
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qualify"
    fx = _write_fixture(tmp_path, _rows_with_screenshot())
    out = run_qualification(cfg, ["gmail"], state_dir=state_dir,
                            fixtures={"gmail": fx}, dry_run=True)
    assert out["state"] == "OK"
    # dry-run must not persist the summary record
    assert not list((state_dir / "qualify").rglob("summary.json"))
    blob = json.dumps(out)
    assert "Synthetic Sender" not in blob
    assert "preview" not in out["stages"]["scan:gmail"]["fixture"]["checks"]


def test_run_qualification_persists_redacted_summary(tmp_path):
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qualify"
    fx = _write_fixture(tmp_path, _rows_with_screenshot())
    out = run_qualification(cfg, ["gmail"], state_dir=state_dir,
                            fixtures={"gmail": fx}, dry_run=False)
    assert out["state"] == "OK"
    summary = state_dir / "qualify" / out["run_id"] / "summary.json"
    assert summary.exists()
    assert (summary.stat().st_mode & 0o077) == 0
    payload = json.loads(summary.read_text())
    blob = json.dumps(payload)
    for private in ("Synthetic Sender", "Synthetic subject", "preview"):
        assert private not in blob


def test_run_qualification_isolation(tmp_path):
    cfg = _cfg(tmp_path)
    prod = cfg.state_dir()
    state_dir = tmp_path / "qualify"
    fx = _write_fixture(tmp_path, _rows_with_screenshot())
    out = run_qualification(cfg, ["gmail"], state_dir=state_dir,
                            fixtures={"gmail": fx}, dry_run=False)
    assert out["state"] == "OK"
    assert not (prod / "communications.db").exists()
    assert not (prod / "reports").exists()


def test_run_qualification_live_without_gate_fails_closed(tmp_path, monkeypatch):
    monkeypatch.delenv(QUALIFY_ENV, raising=False)
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qualify"
    fx = _write_fixture(tmp_path, _rows_with_screenshot())
    out = run_qualification(cfg, ["gmail"], state_dir=state_dir,
                            fixtures={"gmail": fx}, live=True)
    # a requested-but-unauthorized live stage must never be reported as OK
    assert out["state"] == "failed"
    assert out["stages"]["targets"]["state"] == "denied"
    assert out["stages"]["scan:gmail"]["live_scan"]["state"] == "denied"


def test_run_qualification_live_no_browser_is_not_failed(tmp_path, monkeypatch):
    monkeypatch.setenv(QUALIFY_ENV, "1")
    monkeypatch.setattr(Q, "find_target_preferring", lambda o, p, **k: (None, []))
    monkeypatch.setattr(Q, "find_target", lambda *a, **k: None)
    cfg = _cfg(tmp_path)
    state_dir = tmp_path / "qualify"
    fx = _write_fixture(tmp_path, _rows_with_screenshot())
    out = run_qualification(cfg, ["gmail"], state_dir=state_dir,
                            fixtures={"gmail": fx}, live=True, dry_run=True)
    # hermetic fixture stage is the required gate; a missing browser is a typed
    # unavailable live state, not a fabricated pass or a denial
    assert out["stages"]["scan:gmail"]["live_scan"]["state"] == "unavailable"
    assert out["stages"]["scan:gmail"]["fixture"]["state"] == "OK"


def test_isolate_cfg_redirects_state(tmp_path):
    cfg = _cfg(tmp_path)
    iso = isolate_cfg(cfg, tmp_path / "isolated")
    assert iso.state_dir() == tmp_path / "isolated"
    assert cfg.state_dir() != tmp_path / "isolated"


def test_scan_bounds_caps():
    b = ScanBounds(max_items=60, max_rows=200, deadline_s=30.0)
    assert b.max_items == 60 and b.max_rows == 200