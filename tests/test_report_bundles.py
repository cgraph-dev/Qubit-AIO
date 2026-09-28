"""Packet 8C tests: report bundles (canonical JSON / Markdown / HTML).

Covers: JSON/Markdown/HTML consistency, atomic write + crash recovery, asset
MIME/hash/byte validation, safe links, screenshot evidence + missing-evidence
reasons, XSS/escaping, file modes, path-traversal + symlink refusal, secret
redaction, legacy indexing, acknowledgement, export, retention safety, and
rollback via canonical-store reconstruction.
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
from voicebridge.communications.reports import (  # noqa: E402
    ReportStore, ReportStoreError, ReportValidationError, safe_source_link,
)


def _items():
    return [
        CommunicationItem.create(
            platform=Platform.GMAIL, account_scope="a",
            sender="Synthetic <b>Sender</b>", subject="<script>alert(1)</script>",
            preview="secret token=sk-1234567890abcdef",
            is_new=True, read_state=ReadState.UNREAD,
            safe_url="https://mail.google.com/mail/u/0/#inbox",
            source_item_id="m1", adapter="gmail", adapter_version="8b-gmail-2"),
        CommunicationItem.create(
            platform=Platform.INSTAGRAM, account_scope="ig",
            sender="Synthetic IG", preview="ig preview",
            is_new=False, read_state=ReadState.UNKNOWN,
            safe_url="javascript:alert(1)", thread_id="t1",
            adapter="instagram", adapter_version="8b-instagram-3"),
        CommunicationItem.create(
            platform=Platform.MESSENGER, account_scope="ms",
            sender="Synthetic Messenger", preview="ms preview",
            is_new=False, read_state=ReadState.READ,
            safe_url="http://evil.example/x", thread_id="m1",
            adapter="messenger", adapter_version="8b-messenger-2"),
    ]


def _manifest(items, report_id="report_abc123", status=ReportStatus.SUCCESS,
              new_count=1, summary="Summary with <b>markup</b> and "
              "token=sk-abcdef0123456789") -> ReportManifest:
    return ReportManifest.create(
        window_start=1_700_000_000.0, window_end=1_700_000_500.0,
        status=status, new_count=new_count,
        platforms=tuple({i.platform.value for i in items}),
        accounts=tuple({i.account_scope for i in items}),
        item_identities=tuple(i.identity for i in items),
        summary=summary, report_id=report_id)


def _asset(item, data: bytes, report_id="report_abc123",
           status=EvidenceStatus.AVAILABLE) -> EvidenceAssetRecord:
    return EvidenceAssetRecord.create(
        report_id=report_id, item_identity=item.identity, mime="image/webp",
        byte_count=len(data), sha256=hashlib.sha256(data).hexdigest(),
        redaction_status=status)


def _store(tmp_path):
    return ReportStore(tmp_path / "reports")


def _render(tmp_path, *, asset_bytes=None, **kw):
    st = _store(tmp_path)
    items = _items()
    manifest = _manifest(items, **{k: v for k, v in kw.items()
                                   if k != "asset_bytes"})
    assets = []
    if asset_bytes:
        data = b"\x00" * 500
        assets.append(_asset(items[0], data))
        asset_bytes = {assets[0].asset_id: data}
    out = st.render(manifest, items, assets, asset_bytes=asset_bytes)
    return st, out, manifest, items, assets


# ------------------------------------------------------------ consistency
def test_bundle_consistency_json_md_html(tmp_path):
    st, out, manifest, items, _ = _render(tmp_path)
    d = st.bundle_dir("report_abc123")
    mf = json.loads((d / "manifest.json").read_text())
    md = (d / "report.md").read_text()
    html = (d / "index.html").read_text()
    assert mf["schema_version"] == 1
    assert mf["report_id"] == "report_abc123"
    assert mf["status"] == "success"
    assert mf["new_count"] == 1
    assert len(mf["items"]) == 3
    assert "report_abc123" in md and "Report report_abc123" in html
    assert "SUCCESS" in html
    # platform grouping present in markdown + html
    assert "gmail" in md and "instagram" in md


def test_bundle_file_modes(tmp_path):
    st, _, _, _, assets = _render(tmp_path, asset_bytes=True)
    d = st.bundle_dir("report_abc123")
    assert oct(d.stat().st_mode & 0o777) == "0o700"
    assert oct((d / "assets").stat().st_mode & 0o777) == "0o700"
    for name in ("manifest.json", "report.md", "index.html"):
        assert oct((d / name).stat().st_mode & 0o777) == "0o600"
    assert oct((d / "assets" / f"{assets[0].asset_id}.webp")
               .stat().st_mode & 0o777) == "0o600"


def test_atomic_render_and_stage_cleanup(tmp_path):
    st, out, _, _, _ = _render(tmp_path)
    assert not [p for p in st.bundle_dir("report_abc123").parent.iterdir()
                if p.name.startswith(".stage-")]
    # init cleans leftover stages
    leftover = tmp_path / "reports" / ".stage-deadbeef"
    leftover.mkdir()
    st2 = ReportStore(tmp_path / "reports")
    assert not leftover.exists()


def test_render_overwrites_existing_bundle_atomically(tmp_path):
    st, _, _, _, _ = _render(tmp_path)
    st, _, manifest, items, _ = _render(tmp_path, summary="changed summary")
    d = st.bundle_dir("report_abc123")
    mf = json.loads((d / "manifest.json").read_text())
    assert mf["summary"] == "changed summary"
    assert not [p for p in (tmp_path / "reports").iterdir()
                if p.name.endswith(".bak")]   # backup cleaned unless kept


# ------------------------------------------------------------ validation
def test_bundle_rejects_bad_asset_hash(tmp_path):
    st = _store(tmp_path)
    items = _items()
    manifest = _manifest(items)
    data = b"\x00" * 500
    asset = _asset(items[0], data)
    with pytest.raises(ReportValidationError, match="sha256"):
        st.render(manifest, items, [asset],
                  asset_bytes={asset.asset_id: b"\x01" * 500})


def test_bundle_rejects_bad_mime(tmp_path):
    st = _store(tmp_path)
    items = _items()
    manifest = _manifest(items)
    data = b"\x00" * 500
    asset = EvidenceAssetRecord.create(
        report_id="report_abc123", item_identity=items[0].identity,
        mime="text/html", byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        redaction_status=EvidenceStatus.AVAILABLE)
    with pytest.raises(ReportValidationError, match="MIME"):
        st.render(manifest, items, [asset], asset_bytes={asset.asset_id: data})


def test_bundle_rejects_bad_byte_count(tmp_path):
    st = _store(tmp_path)
    items = _items()
    manifest = _manifest(items)
    data = b"\x00" * 500
    asset = EvidenceAssetRecord.create(
        report_id="report_abc123", item_identity=items[0].identity,
        mime="image/webp", byte_count=999,
        sha256=hashlib.sha256(data).hexdigest(),
        redaction_status=EvidenceStatus.AVAILABLE)
    with pytest.raises(ReportValidationError, match="byte count"):
        st.render(manifest, items, [asset], asset_bytes={asset.asset_id: data})


# ------------------------------------------------------------ security
def test_xss_and_markdown_injection_escaped(tmp_path):
    _, _, _, _, _ = _render(tmp_path)
    d = (tmp_path / "reports" / "report_abc123")
    html_content = (d / "index.html").read_text()
    md = (d / "report.md").read_text()
    # raw script / html never present in HTML
    assert "<script>alert(1)</script>" not in html_content
    assert "<b>Sender</b>" not in html_content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_content
    # raw HTML never present in Markdown either
    assert "<script>alert" not in md
    assert "&lt;script&gt;" in md


def test_safe_source_links():
    assert safe_source_link("https://mail.google.com/mail/u/0/#inbox")
    assert safe_source_link("https://www.instagram.com/direct/inbox/")
    assert safe_source_link("https://www.messenger.com/t/1")
    assert safe_source_link("javascript:alert(1)") is None
    assert safe_source_link("http://evil.example/x") is None
    assert safe_source_link("https://attacker.example/x") is None
    assert safe_source_link("") is None


def test_secret_redaction_in_bundle(tmp_path):
    _, _, _, _, _ = _render(tmp_path)
    mf = json.loads((tmp_path / "reports" / "report_abc123" / "manifest.json")
                    .read_text())
    blob = json.dumps(mf)
    assert "sk-1234567890abcdef" not in blob
    assert "sk-abcdef0123456789" not in blob
    assert "[REDACTED]" in blob
    # secret-bearing unsafe URL dropped
    assert "evil.example" not in blob


def test_unsafe_links_and_missing_evidence_in_html(tmp_path):
    st, _, _, _, _ = _render(tmp_path, asset_bytes=False)
    html_content = (st.bundle_dir("report_abc123") / "index.html").read_text()
    # only the gmail safe link renders as a real <a href="https://mail.google.com"
    assert 'href="https://mail.google.com' in html_content
    assert "unsafe source" in html_content        # IG javascript: and MS http:
    assert "evidence not available" in html_content  # no asset bytes written


def test_screenshot_thumbnails_in_html(tmp_path):
    st, _, _, _, _ = _render(tmp_path, asset_bytes=True)
    html_content = (st.bundle_dir("report_abc123") / "index.html").read_text()
    assert "assets/" in html_content and ".webp" in html_content
    assert "scoped screenshot thumbnail" in html_content


# -------------------------------------------------------- missing-evidence
def test_missing_partial_degraded_no_change_states(tmp_path):
    st = _store(tmp_path)
    items = _items()
    for status in (ReportStatus.SUCCESS, ReportStatus.PARTIAL,
                   ReportStatus.DEGRADED):
        rid = f"report_{status.value}"
        manifest = _manifest(items, report_id=rid, status=status)
        st.render(manifest, items, [], asset_bytes=None)
        h = (st.bundle_dir(rid) / "index.html").read_text()
        assert status.value.upper() in h
    # no-change: empty items
    empty = _manifest([], report_id="report_nochange", status=ReportStatus.SUCCESS,
                      new_count=0)
    st.render(empty, [], [], asset_bytes=None)
    assert "No items in this report window" in \
        (st.bundle_dir("report_nochange") / "index.html").read_text()


# ------------------------------------------------------------- traversal
def test_traversal_and_symlink_refused(tmp_path):
    st, _, _, _, assets = _render(tmp_path, asset_bytes=True)
    with pytest.raises(ReportStoreError, match="invalid"):
        st.asset("report_abc123", "../../../etc/passwd")
    with pytest.raises(ReportStoreError, match="invalid"):
        st.asset("report_abc123", "..%2fsecret")
    # a valid-id asset that is a symlink pointing OUTSIDE the bundle is refused
    d = st.bundle_dir("report_abc123")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    evil = d / "assets" / "asset_evil123456"
    try:
        evil.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    got = st.asset("report_abc123", "asset_evil123456")
    assert got is None


# ---------------------------------------------------------------- legacy
def test_legacy_markdown_indexed_and_preserved(tmp_path):
    st = _store(tmp_path)
    legacy = tmp_path / "reports" / "legacy-synthetic-20260101.md"
    legacy.write_text("# Legacy synthetic report (Packet 8C fixture)\n\nsynthetic\n")
    _, _, _, _, _ = _render(tmp_path)
    idx = st.index()
    assert len(idx["legacy"]) == 1
    assert idx["legacy"][0]["legacy"] is True
    assert idx["legacy"][0]["name"].endswith(".md")
    # retention must NEVER delete legacy .md files
    r = st.retention(dry_run=False, retention_days=0, now=time.time() + 999999)
    assert legacy.exists()
    assert idx["legacy"][0]["name"] in [l["name"] for l in st.index()["legacy"]]


# --------------------------------------------------------- ack / export
def test_acknowledge_and_index(tmp_path):
    st, _, _, _, _ = _render(tmp_path)
    assert st.detail("report_abc123")["acknowledged"] is False
    assert st.acknowledge("report_abc123") is True
    assert st.detail("report_abc123")["acknowledged"] is True
    assert st.index()["reports"][0]["acknowledged"] is True
    assert st.acknowledge("nope") is False


def test_export_zip_roundtrip(tmp_path):
    st, _, _, _, assets = _render(tmp_path, asset_bytes=True)
    blob = st.export("report_abc123")
    assert blob
    zf = zipfile.ZipFile(io.BytesIO(blob))
    names = zf.namelist()
    assert any(n.endswith("manifest.json") for n in names)
    assert any(n.endswith("index.html") for n in names)
    assert any(a.asset_id in n for n in names for a in assets)
    assert st.export("missing") is None


# ------------------------------------------------------------- retention
def _expired_bundle(store: ReportStore, rid: str) -> None:
    from voicebridge.communications.contracts import ReportManifest
    m = ReportManifest.create(
        window_start=1, window_end=2, status=ReportStatus.SUCCESS,
        new_count=0, platforms=("gmail",), accounts=("a",), report_id=rid,
        created_at=time.time() - 200 * 86400)
    store.render(m, [], [], asset_bytes=None)


def test_retention_only_expired_bundles(tmp_path):
    st, _, _, _, _ = _render(tmp_path)   # report_abc123 (fresh, now)
    _expired_bundle(st, "report_old123")
    dry = st.retention(dry_run=True, retention_days=90)
    assert {r["report_id"] for r in dry["candidates"]} == {"report_old123"}
    assert dry["deleted"] == []
    done = st.retention(dry_run=False, retention_days=90)
    assert done["state"] == "OK"
    assert "report_old123" in done["deleted"]
    assert not st.bundle_dir("report_old123").exists()
    assert st.bundle_dir("report_abc123").exists()   # fresh untouched


def test_retention_fail_closed_on_backup_failure(tmp_path):
    st, _, _, _, _ = _render(tmp_path)
    _expired_bundle(st, "report_old123")
    blocked = tmp_path / "reports" / "not-a-dir"
    blocked.write_text("block backup path")
    r = st.retention(dry_run=False, retention_days=90, backup_dir=blocked)
    assert r["state"] == "BLOCKED"
    assert r["failures"] and "backup failed" in r["failures"][0]
    assert r["deleted"] == []
    # NOTHING was deleted, including the expired bundle
    assert st.bundle_dir("report_old123").exists()
    assert st.bundle_dir("report_abc123").exists()


def test_retention_fail_closed_on_index_failure(tmp_path):
    st, _, _, _, _ = _render(tmp_path)
    _expired_bundle(st, "report_old123")
    broken = tmp_path / "reports" / "report_broken"
    broken.mkdir()   # no manifest.json -> index integrity fails
    r = st.retention(dry_run=False, retention_days=90)
    assert r["state"] == "BLOCKED"
    assert r["index_check"] == "failed"
    assert r["deleted"] == []
    assert st.bundle_dir("report_old123").exists()   # nothing deleted


def test_retention_deletion_failure_reports_failed(tmp_path):
    st, _, _, _, _ = _render(tmp_path)
    _expired_bundle(st, "report_old123")
    d = st.bundle_dir("report_old123")
    os.chmod(d, 0o500)   # read-only: rmtree of children fails for non-root
    try:
        r = st.retention(dry_run=False, retention_days=90)
    finally:
        os.chmod(d, 0o700)
    assert r["state"] == "FAILED"
    assert any("delete failed" in f for f in r["failures"])
    assert st.bundle_dir("report_old123").exists()   # not silently deleted


def test_retention_symlink_and_traversal_refused(tmp_path):
    st, _, _, _, _ = _render(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text(json.dumps(
        {"report_id": "evilbundle", "schema_version": 1, "status": "success",
         "created_at": 1.0, "new_count": 0}))
    link = tmp_path / "reports" / "evilbundle"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    # a symlink bundle fails the index-integrity check -> BLOCKED, delete nothing
    r = st.retention(dry_run=False, retention_days=90)
    assert r["state"] == "BLOCKED"
    assert link.is_symlink() and outside.exists()   # outside untouched
    # traversal ids are structurally refused
    assert st._bundle_realpath("../etc/passwd") is None
    assert st._bundle_realpath("../../tmp") is None


def test_retention_backup_created_and_logged(tmp_path):
    st, _, _, _, _ = _render(tmp_path)
    _expired_bundle(st, "report_old123")
    r = st.retention(dry_run=False, retention_days=90)
    assert r["state"] == "OK" and r["backup_id"]
    backups = [p for p in (tmp_path / "reports").iterdir()
               if p.name.startswith(".backup-")]
    assert backups
    log = tmp_path / "reports" / "retention.log"
    assert log.exists()
    rec = json.loads(log.read_text().strip().splitlines()[-1])
    assert rec["state"] == "OK"
    assert rec["backup_id"] == r["backup_id"]
    assert rec["index_check"] == "ok"
    assert rec["deleted_count"] == 1
    assert rec["deleted"] == ["report_old123"]


def test_retention_backup_root_is_0700(tmp_path):
    """The retention backup ROOT directory must be private 0700 immediately
    after creation (never the permissive umask mode 0775)."""
    st, _, _, _, _ = _render(tmp_path)
    _expired_bundle(st, "report_old123")
    r = st.retention(dry_run=False, retention_days=90)
    assert r["state"] == "OK"
    backups = [p for p in (tmp_path / "reports").iterdir()
               if p.name.startswith(".backup-")]
    assert backups
    root = backups[0]
    assert oct(root.stat().st_mode & 0o777) == "0o700"
    # the copied per-bundle backup inside is also private
    (inner,) = list(root.iterdir())
    assert oct(inner.stat().st_mode & 0o777) == "0o700"
    assert oct((inner / "manifest.json").stat().st_mode & 0o777) == "0o600"


def test_retention_never_touches_legacy_or_fresh(tmp_path):
    st, _, _, _, _ = _render(tmp_path)
    _expired_bundle(st, "report_old123")
    legacy = tmp_path / "reports" / "legacy-report.md"
    legacy.write_text("# Legacy synthetic\n")
    r = st.retention(dry_run=False, retention_days=90)
    assert r["state"] == "OK"
    assert legacy.exists()
    assert st.bundle_dir("report_abc123").exists()
    assert not st.bundle_dir("report_old123").exists()


# ------------------------------------------------------------ oversized/secrets
def test_oversized_asset_rejected(tmp_path):
    from voicebridge.communications.reports import MAX_ASSET_BYTES
    st = _store(tmp_path)
    items = _items()
    manifest = _manifest(items)
    data = b"\x00" * (MAX_ASSET_BYTES + 1)
    asset = EvidenceAssetRecord.create(
        report_id="report_abc123", item_identity=items[0].identity,
        mime="image/webp", byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        redaction_status=EvidenceStatus.AVAILABLE)
    with pytest.raises(ReportValidationError, match="oversized"):
        st.render(manifest, items, [asset], asset_bytes={asset.asset_id: data})


def test_secret_bearing_url_rejected():
    assert safe_source_link("https://mail.google.com/mail/u/0/#inbox")
    assert safe_source_link("https://www.instagram.com/direct/inbox/")
    assert safe_source_link("https://mail.google.com/?token=sk-1234567890abcdef") \
        is None
    assert safe_source_link("https://mail.google.com/?access_token=abc123") \
        is None


# ------------------------------------------------------------ rollback/regen
def test_reconstruct_from_communications_store(tmp_path):
    from voicebridge.communications.store import CommunicationsStore
    st = _store(tmp_path)
    cstore = CommunicationsStore(tmp_path / "comm.db")
    items = _items()
    manifest = _manifest(items, report_id="report_rec1")
    scan = __import__("voicebridge.communications.contracts",
                      fromlist=["ScanRecord"]).ScanRecord.create(
        platform=Platform.GMAIL, account_scope="a",
        status=__import__("voicebridge.communications.contracts",
                          fromlist=["ScanStatus"]).ScanStatus.NO_CHANGE,
        adapter="gmail", adapter_version="1")
    cstore.commit_baseline(scan=scan, items=items, manifest=manifest)
    out = st.regen(cstore, "report_rec1")
    assert out is not None and out["items"] == 3
    mf = json.loads((st.bundle_dir("report_rec1") / "manifest.json").read_text())
    assert mf["report_id"] == "report_rec1"
    assert len(mf["items"]) == 3
    cstore.close()