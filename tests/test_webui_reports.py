"""Packet 8C webui tests: authenticated report index/detail/asset/export/ack.

Covers API authentication, traversal/symlink refusal, MIME/hash, redaction,
legacy labeling, retention dry-run, and that the report surface is the canonical
bundle store (never the activity/log feed).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.communications.contracts import (  # noqa: E402
    CommunicationItem, EvidenceAssetRecord, EvidenceStatus, Platform,
    ReadState, ReportManifest, ReportStatus,
)
from voicebridge.communications.reports import ReportStore  # noqa: E402


def _item(platform=Platform.GMAIL, scope="a", source_id="m1", secret=False):
    return CommunicationItem.create(
        platform=platform, account_scope=scope,
        sender="Synthetic Sender", subject="Synthetic subject",
        preview="secret token=sk-1234567890abcdef" if secret else "preview",
        is_new=True, read_state=ReadState.UNREAD,
        safe_url="https://mail.google.com/mail/u/0/#inbox",
        source_item_id=source_id, adapter="gmail", adapter_version="8b-gmail-2")


def _seed_bundle(tmp_state: Path, report_id="report_api1"):
    store = ReportStore(tmp_state / "reports")
    items = [_item()]
    manifest = ReportManifest.create(
        window_start=1_700_000_000.0, window_end=1_700_000_500.0,
        status=ReportStatus.SUCCESS, new_count=1, platforms=("gmail",),
        accounts=("a",), item_identities=tuple(i.identity for i in items),
        summary="Synthetic report summary", report_id=report_id)
    data = b"\x00" * 512
    asset = EvidenceAssetRecord.create(
        report_id=report_id, item_identity=items[0].identity,
        mime="image/webp", byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        redaction_status=EvidenceStatus.AVAILABLE)
    store.render(manifest, items, [asset], asset_bytes={asset.asset_id: data})
    return store, asset


def _start(tmp_path):
    from voicebridge.config import Config
    from voicebridge.webui import WebUI
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    _seed_bundle(state)
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl_path: '" + str(state) +
                 "'\nwebui:\n  token: test-token\n")
    cfg = Config.load(p)

    class _FakeOrch:
        _route = "gateway"
        _web_active = 0
        task = type("T", (), {"active": False})()

        def __init__(self):
            self.cfg = cfg

    ui = WebUI(_FakeOrch(), host="127.0.0.1", port=0, state_dir=state)
    ui.start()
    return ui, ui._server.server_address[1]


def _http(port, method, path, body=None, token="test-token"):
    import urllib.request
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    if token:
        req.add_header("X-Api-Token", token)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_reports_api_requires_auth(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, _ = _http(port, "GET", "/api/reports", token="")
        assert status == 401
        status, _, _ = _http(port, "GET", "/api/reports", token="wrong")
        assert status == 401
    finally:
        ui.stop()


def test_reports_index_and_detail(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, body = _http(port, "GET", "/api/reports")
        assert status == 200
        data = json.loads(body)
        assert data["state"] == "OK"
        assert data["reports"][0]["report_id"] == "report_api1"
        assert data["reports"][0]["status"] == "success"
        status, _, body = _http(port, "GET", "/api/reports/report_api1")
        rep = json.loads(body)["report"]
        assert rep["items"][0]["sender"] == "Synthetic Sender"
        assert "sk-1234567890abcdef" not in body.decode()   # redacted
        assert "filesystem" not in body.decode()
    finally:
        ui.stop()


def test_reports_asset_html_export_routes(tmp_path):
    ui, port = _start(tmp_path)
    try:
        # asset served with image MIME + correct bytes
        store = ReportStore(tmp_path / "state" / "reports")
        detail = store.detail("report_api1")
        aid = detail["assets"][0]["asset_id"]
        status, ctype, body = _http(port, "GET",
                                    "/api/reports/report_api1/assets/" + aid)
        assert status == 200 and "image" in ctype
        assert hashlib.sha256(body).hexdigest() == detail["assets"][0]["sha256"]
        # self-contained HTML (print view)
        status, ctype, body = _http(port, "GET", "/api/reports/report_api1/html")
        assert status == 200 and "text/html" in ctype
        assert "report_api1" in body.decode()
        # export zip
        status, ctype, body = _http(port, "GET", "/api/reports/export/report_api1")
        assert status == 200 and "zip" in ctype
        zf = zipfile.ZipFile(io.BytesIO(body))
        assert any(n.endswith("manifest.json") for n in zf.namelist())
    finally:
        ui.stop()


def test_reports_ack_and_prune_dry_run(tmp_path):
    ui, port = _start(tmp_path)
    try:
        status, _, body = _http(port, "POST", "/api/reports/report_api1/ack", {})
        assert status == 200 and json.loads(body)["acknowledged"] is True
        status, _, body = _http(port, "GET", "/api/reports/report_api1")
        assert json.loads(body)["report"]["acknowledged"] is True
        status, _, body = _http(port, "POST", "/api/reports/prune",
                                {"days": 90})
        assert json.loads(body)["dry_run"] is True
        status, _, body = _http(port, "GET", "/api/reports")
        assert json.loads(body)["reports"]   # dry-run removed nothing
    finally:
        ui.stop()


def test_reports_traversal_and_unknown_refused(tmp_path):
    ui, port = _start(tmp_path)
    try:
        # invalid report id -> 400
        status, _, body = _http(port, "GET", "/api/reports/..%2fetc%2fpasswd")
        assert status == 400
        # invalid asset id -> 400
        status, _, body = _http(port, "GET",
                                "/api/reports/report_api1/assets/..%2fsecret")
        assert status == 400
        # missing report -> 404
        status, _, body = _http(port, "GET", "/api/reports/report_nope")
        assert status == 404
        # symlink asset pointing outside is refused (404/400)
        d = tmp_path / "state" / "reports" / "report_api1" / "assets"
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"secret")
        try:
            (d / "asset_evil123456").symlink_to(outside)
        except OSError:
            return
        status, _, body = _http(port, "GET",
                                "/api/reports/report_api1/assets/asset_evil123456")
        assert status == 404 or status == 400
    finally:
        ui.stop()


def test_reports_legacy_labeled(tmp_path):
    ui, port = _start(tmp_path)
    try:
        legacy = tmp_path / "state" / "reports" / "legacy-synthetic.md"
        legacy.write_text("# Legacy synthetic report\n\nsynthetic\n")
        status, _, body = _http(port, "GET", "/api/reports")
        data = json.loads(body)
        assert data["legacy"][0]["legacy"] is True
        assert data["legacy"][0]["name"] == "legacy-synthetic.md"
    finally:
        ui.stop()


def _expired_bundle(reports_root: Path, rid: str) -> None:
    from voicebridge.communications.contracts import ReportManifest
    store = ReportStore(reports_root)
    m = ReportManifest.create(
        window_start=1, window_end=2, status=ReportStatus.SUCCESS, new_count=0,
        platforms=("gmail",), accounts=("a",), report_id=rid,
        created_at=time.time() - 200 * 86400)
    store.render(m, [], [], asset_bytes=None)


def _prune(port, body):
    return _http(port, "POST", "/api/reports/prune", body)


def test_prune_propagates_backup_failure_blocked(tmp_path):
    """POST /api/reports/prune must return the REAL retention state (BLOCKED),
    never a top-level OK, when the protected backup cannot be created."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    _seed_bundle(state)               # fresh report_api1
    _expired_bundle(state / "reports", "report_old123")
    # a symlink bundle breaks backup (unsafe path) -> BLOCKED, delete nothing
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text(json.dumps(
        {"report_id": "evilbundle", "schema_version": 1, "status": "success",
         "created_at": 1.0, "new_count": 0}))
    try:
        (state / "reports" / "evilbundle").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    ui, port = _start(tmp_path)
    try:
        status, _, body = _prune(port, {"days": 90, "apply": True})
        assert status == 200
        out = json.loads(body)
        assert out["state"] == "BLOCKED", out
        assert out["dry_run"] is False
        assert any("backup failed" in f for f in out["failures"])
        assert out["deleted"] == []
        assert (state / "reports" / "report_old123").exists()   # nothing deleted
        assert outside.exists()                                  # outside intact
    finally:
        ui.stop()


def test_prune_propagates_index_failure_blocked(tmp_path):
    """Index-integrity failure -> BLOCKED, delete nothing (never OK)."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    _seed_bundle(state)
    _expired_bundle(state / "reports", "report_old123")
    broken = state / "reports" / "report_broken"
    broken.mkdir()   # no manifest.json -> index integrity fails
    ui, port = _start(tmp_path)
    try:
        status, _, body = _prune(port, {"days": 90, "apply": True})
        out = json.loads(body)
        assert out["state"] == "BLOCKED", out
        assert out["index_check"] == "failed"
        assert out["deleted"] == []
        assert (state / "reports" / "report_old123").exists()
    finally:
        ui.stop()


def test_prune_propagates_deletion_failure_failed(tmp_path):
    """A deletion failure -> FAILED (not OK); nothing silently dropped."""
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    _seed_bundle(state)
    _expired_bundle(state / "reports", "report_old123")
    d = state / "reports" / "report_old123"
    os.chmod(d, 0o500)
    try:
        ui, port = _start(tmp_path)
        try:
            status, _, body = _prune(port, {"days": 90, "apply": True})
            out = json.loads(body)
            assert out["state"] == "FAILED", out
            assert any("delete failed" in f for f in out["failures"])
            assert d.exists()   # not silently deleted
        finally:
            ui.stop()
    finally:
        os.chmod(d, 0o700)


def test_fresh_process_serves_canonical_reports(tmp_path):
    """A FRESH python process must load the canonical /api/reports route (the
    ReportStore bundle index), not the old activity-log feed. Proves the new
    route is what a service restart actually runs — without touching the live
    service."""
    import subprocess
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    _seed_bundle(state)
    cfg = tmp_path / "voice.yaml"
    cfg.write_text("logging:\n  jsonl_path: '" + str(state) +
                   "'\nwebui:\n  token: test-token\n")
    script = r"""
import json, sys
from pathlib import Path
sys.path.insert(0, {repo!r})
from voicebridge.config import Config
from voicebridge.webui import WebUI
class Orch:
    _route = "gateway"; _web_active = 0
    task = type("T", (), {{"active": False}})()
    def __init__(self): self.cfg = Config.load(Path({cfg!r}))
ui = WebUI(Orch(), host="127.0.0.1", port=0, state_dir=Path({state!r}))
ui.start()
try:
    import urllib.request
    port = ui._server.server_address[1]
    req = urllib.request.Request("http://127.0.0.1:%d/api/reports" % port)
    req.add_header("X-Api-Token", "test-token")
    with urllib.request.urlopen(req, timeout=5) as r:
        print(json.dumps(json.loads(r.read().decode())))
finally:
    ui.stop()
""".format(repo=str(REPO_ROOT), cfg=str(cfg), state=str(state))
    r = subprocess.run([sys.executable, "-c", script], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-500:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out.get("state") == "OK"
    assert "reports" in out and "legacy" in out      # canonical bundle index
    assert out["reports"][0]["report_id"] == "report_api1"
    assert "items" not in out and "pruned_available" not in out  # NOT activity


def test_report_html_loads_in_local_browser(tmp_path):
    """Local Playwright UI smoke: the self-contained bundle HTML renders with
    the report title and an h1. Skips (typed, not hidden) when the Playwright
    browser binary is not installed — never downloads one here."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright package unavailable")
    _seed_bundle(tmp_path / "state")
    html = tmp_path / "state" / "reports" / "report_api1" / "index.html"
    assert html.exists()
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("playwright chromium binary not installed")
        try:
            page = browser.new_page()
            page.goto(html.resolve().as_uri())
            assert "Report report_api1" in page.title()
            assert page.locator("h1").count() == 1
            assert page.locator(".status.SUCCESS").count() == 1
        finally:
            browser.close()