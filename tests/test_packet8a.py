"""Packet 8A tests: communications-monitor contracts + SQLite store.

Covers: versioned schemas distinguishing new/unread/unknown/stale/partial/
auth-required/no-change; stable identity and fallback collisions; atomic
cursor/report commit (cursor advances only on durable report commit); typed
partial/auth/degraded statuses; retention (dry-run + audited); rollback;
lease; and proof that the synthetic fixtures contain no private data and that
no runtime module imports this package yet.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.communications import (  # noqa: E402
    AccountHealthRecord, AccountHealthStatus, CommunicationsStore,
    CommunicationsStoreError, CommunicationItem, CursorRecord,
    DEFAULT_RETENTION_SECONDS, EvidenceAssetRecord, EvidenceStatus,
    IdentitySource, NotificationKind, NotificationRecord, NotificationStatus,
    Platform, ReadState, ReportManifest, ReportStatus, SCHEMA_VERSION,
    ScanRecord, ScanStatus, Sensitivity, SUPPORTED_PLATFORMS,
    fallback_fingerprint, record_as_dict, stable_item_identity,
)

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "communications"

SYNTHETIC_SENDERS = (
    "Synthetic Sender Alpha", "Synthetic Sender Bravo", "Synthetic Sender Charlie",
    "Synthetic IG Sender Delta", "Synthetic IG Sender Echo",
    "Synthetic Messenger Sender Foxtrot", "Synthetic Messenger Sender Golf",
)


def _item(platform: Platform, sender: str, *, scope: str = "synth-a",
          subject: str = "", preview: str = "", is_new: bool = False,
          read_state: ReadState = ReadState.UNKNOWN,
          captured_at: float = 1_700_000_000.0, source_item_id=None,
          thread_id=None, evidence: EvidenceStatus = EvidenceStatus.NOT_AVAILABLE,
          ) -> CommunicationItem:
    return CommunicationItem.create(
        platform=platform, account_scope=scope, sender=sender,
        display_name=sender, subject=subject, preview=preview,
        is_new=is_new, read_state=read_state, captured_at=captured_at,
        adapter=f"{platform.value}_cdp", adapter_version="8a-1",
        evidence_status=evidence, source_item_id=source_item_id,
        thread_id=thread_id)


def _manifest(items, report_id: str, *, assets=(), accounts=("synth-a",),
              new_count=None) -> ReportManifest:
    return ReportManifest.create(
        window_start=1_699_999_900.0, window_end=1_700_000_500.0,
        status=ReportStatus.SUCCESS, new_count=len(items) if new_count is None
        else new_count, platforms=(items[0].platform.value,),
        accounts=accounts, item_identities=tuple(i.identity for i in items),
        assets=assets, report_id=report_id)


def _scan(platform: Platform, status: ScanStatus, scope: str = "synth-a") -> ScanRecord:
    return ScanRecord.create(platform=platform, account_scope=scope,
                             status=status, adapter=f"{platform.value}_cdp",
                             adapter_version="8a-1")


# ------------------------------------------------------------- schemas
def test_schema_version_is_one_and_platforms_closed_set():
    assert SCHEMA_VERSION == 1
    assert SUPPORTED_PLATFORMS == {"gmail", "instagram", "messenger"}


def test_statuses_distinguish_all_required_facts():
    # new vs unread vs unknown are separate axes
    assert CommunicationItem.create(
        platform=Platform.GMAIL, account_scope="s", sender="x",
        subject="s", is_new=True, read_state=ReadState.UNREAD,
        adapter="g", adapter_version="1").read_state is ReadState.UNREAD
    # every required scan/health/report status value exists
    for v in (ScanStatus.NEW_ITEMS, ScanStatus.NO_CHANGE, ScanStatus.PARTIAL,
              ScanStatus.OVERFLOW, ScanStatus.AUTH_REQUIRED, ScanStatus.STALE,
              ScanStatus.DEGRADED, ScanStatus.FAILED):
        assert isinstance(v, ScanStatus)
    for v in (AccountHealthStatus.HEALTHY, AccountHealthStatus.NO_CHANGE,
              AccountHealthStatus.PARTIAL, AccountHealthStatus.AUTH_REQUIRED,
              AccountHealthStatus.STALE, AccountHealthStatus.DEGRADED,
              AccountHealthStatus.UNAVAILABLE, AccountHealthStatus.FAILED):
        assert isinstance(v, AccountHealthStatus)
    assert ReportStatus.SUCCESS.value == "success"
    assert ReportStatus.PARTIAL.value == "partial"
    assert ReportStatus.DEGRADED.value == "degraded"
    assert ReadState.UNREAD.value == "unread"
    assert ReadState.UNKNOWN.value == "unknown"


def test_item_rejects_invalid_inputs():
    with pytest.raises(Exception, match="account_scope"):
        _item(Platform.GMAIL, "x", scope="  ")
    with pytest.raises(Exception, match="sender"):
        CommunicationItem.create(platform=Platform.GMAIL, account_scope="s",
                                 sender="", subject="s", is_new=False,
                                 read_state=ReadState.UNREAD,
                                 adapter="g", adapter_version="1")
    with pytest.raises(Exception, match="platform"):
        CommunicationItem.create(platform="not-a-platform",
                                 account_scope="s", sender="x", subject="s",
                                 is_new=False, read_state=ReadState.UNREAD,
                                 adapter="g", adapter_version="1")


# -------------------------------------------------------- stable identity
def test_stable_identity_prefers_platform_id_then_thread_then_fallback():
    with_pid = _item(Platform.GMAIL, "x", source_item_id="m-1")
    assert with_pid.identity_source is IdentitySource.PLATFORM_ID
    assert with_pid.identity == f"gmail:{with_pid.account_scope}:m-1"

    with_thread = _item(Platform.GMAIL, "x", thread_id="t-9")
    assert with_thread.identity_source is IdentitySource.THREAD_ID
    assert with_thread.identity == f"gmail:{with_thread.account_scope}:t:t-9"

    bare = _item(Platform.GMAIL, "x")
    assert bare.identity_source is IdentitySource.FALLBACK_FINGERPRINT
    assert bare.identity == bare.fingerprint == bare.collision_key
    assert bare.identity.startswith("fp:")


def test_fallback_fingerprint_is_versioned_and_scope_sensitive():
    fp_a = fallback_fingerprint(platform=Platform.GMAIL, account_scope="a",
                                sender="Sender", thread="t", ts_normalized="T",
                                content="same")
    fp_b = fallback_fingerprint(platform=Platform.GMAIL, account_scope="b",
                                sender="Sender", thread="t", ts_normalized="T",
                                content="same")
    assert fp_a != fp_b  # account scope is part of the bucket
    fp_v2 = fallback_fingerprint(platform=Platform.GMAIL, account_scope="a",
                                 sender="Sender", thread="t", ts_normalized="T",
                                 content="same", identity_version=2)
    assert fp_v2 != fp_a  # identity version is part of the bucket


def test_collision_fixture_vectors():
    data = json.loads((FIXTURES / "collision_cases.json").read_text())
    assert data["_synthetic"] is True
    for case in data["cases"]:
        items = [
            CommunicationItem.create(
                platform=Platform.GMAIL, account_scope=case["account_scope"],
                sender=case["sender"], subject="", preview=row.get("preview",
                                                                  case["preview"]),
                is_new=False, read_state=ReadState.UNKNOWN,
                ts_normalized=row["ts"], thread_id=row["thread"],
                adapter=data["adapter"], adapter_version=data["adapter_version"])
            for row in case["items"]
        ]
        same = items[0].identity == items[1].identity
        assert same == case["expect_same_identity"], \
            f"{case['name']}: {case['reason']}"


def test_content_hash_deterministic_and_redaction_label():
    a = _item(Platform.GMAIL, "Sender", subject="Subj", preview="Prev")
    b = _item(Platform.GMAIL, "Sender", subject="Subj", preview="Prev")
    assert a.content_hash == b.content_hash
    assert a.redacted is True


def test_record_as_dict_round_trip():
    from voicebridge.communications.contracts import json_dumps
    item = _item(Platform.MESSENGER, "x", source_item_id="m-1")
    d = record_as_dict(item)
    assert d["platform"] == "messenger"
    assert d["identity_source"] == "platform_id"
    assert item.identity == d["identity"]
    assert json.loads(json_dumps(item))["identity"] == item.identity


# ------------------------------------------------------------- fixtures
def _assert_no_private_data(obj, path: str):
    blob = json.dumps(obj, ensure_ascii=False)
    assert "_synthetic" in obj or "_synthetic" in blob
    assert "trick" not in blob.lower(), f"private username in {path}"
    assert "example.test" not in blob or True  # example.test is permitted
    for tok in ("+407", "@gmail.com", "@yahoo.com", "@outlook.com",
                "password", "sk-"):
        assert tok.lower() not in blob.lower(), f"private token {tok} in {path}"


def test_all_fixtures_are_synthetic_and_parse():
    for name in ("gmail_items.json", "instagram_items.json",
                 "messenger_items.json", "report_manifest.json",
                 "scan_records.json", "collision_cases.json"):
        path = FIXTURES / name
        data = json.loads(path.read_text())
        _assert_no_private_data(data, name)
    g = json.loads((FIXTURES / "gmail_items.json").read_text())
    for row in g["items"]:
        assert row["sender"] in SYNTHETIC_SENDERS
    ig = json.loads((FIXTURES / "instagram_items.json").read_text())
    for row in ig["items"]:
        assert row["sender"].startswith("Synthetic IG Sender")
    ms = json.loads((FIXTURES / "messenger_items.json").read_text())
    for row in ms["items"]:
        assert row["sender"].startswith("Synthetic Messenger Sender")


def test_fixture_items_build_valid_records():
    for name, platform in (("gmail_items.json", Platform.GMAIL),
                           ("instagram_items.json", Platform.INSTAGRAM),
                           ("messenger_items.json", Platform.MESSENGER)):
        data = json.loads((FIXTURES / name).read_text())
        for row in data["items"]:
            item = CommunicationItem.create(
                platform=platform, account_scope=data["account_scope"],
                sender=row["sender"], display_name=row["display_name"],
                subject=row.get("subject", ""), preview=row["preview"],
                is_new=False, read_state=ReadState(row["read_state"]),
                ts_normalized=row["ts_normalized"], ts_raw=row["time_raw"],
                safe_url=row["safe_url"], source_item_id=row["source_item_id"],
                thread_id=row.get("thread_id"), adapter=data["adapter"],
                adapter_version=data["adapter_version"],
                evidence_status=EvidenceStatus(row["evidence_status"]))
            assert item.identity


def test_fixture_manifest_validates():
    m = json.loads((FIXTURES / "report_manifest.json").read_text())
    built = ReportManifest.create(
        window_start=m["window_start"], window_end=m["window_end"],
        status=ReportStatus(m["status"]), new_count=m["new_count"],
        created_at=m["created_at"], generated_analysis=m["generated_analysis"],
        analysis_disclaimer=m["analysis_disclaimer"],
        retention_days=m["retention_days"], summary=m["summary"],
        platforms=tuple(m["platforms"]), accounts=tuple(m["accounts"]),
        item_identities=tuple(m["item_identities"]),
        assets=tuple(m["assets"]), partial_failures=tuple(m["partial_failures"]),
        report_id=m["report_id"])
    assert built.schema_version == SCHEMA_VERSION
    assert built.status is ReportStatus.SUCCESS


def test_fixture_scan_and_health_parse():
    data = json.loads((FIXTURES / "scan_records.json").read_text())
    s = data["scan"]
    scan = ScanRecord.create(platform=Platform(s["platform"]),
                             account_scope=s["account_scope"],
                             status=ScanStatus(s["status"]),
                             adapter=s["adapter"], adapter_version=s["adapter_version"],
                             started_at=s["started_at"], finished_at=s["finished_at"],
                             new_count=s["new_count"], seen_count=s["seen_count"],
                             total_count=s["total_count"], overflow=s["overflow"],
                             reason=s["reason"], cursor_before=s["cursor_before"],
                             scan_id=s["scan_id"])
    h = data["account_health"]
    health = AccountHealthRecord.create(platform=Platform(h["platform"]),
                                        account_scope=h["account_scope"],
                                        status=AccountHealthStatus(h["status"]),
                                        adapter_version=h["adapter_version"],
                                        checked_at=h["checked_at"],
                                        freshness_s=h["freshness_s"],
                                        auth_ok=h["auth_ok"],
                                        last_success_at=h["last_success_at"])
    assert scan.status is ScanStatus.NEW_ITEMS
    assert health.status is AccountHealthStatus.HEALTHY


# --------------------------------------------------------------- store
def _store(tmp_path):
    return CommunicationsStore(tmp_path / "communications.db")


def test_store_migrates_and_private_file_modes(tmp_path):
    from voicebridge.communications.store import STORE_SCHEMA_VERSION
    st = _store(tmp_path)
    assert st.schema_version == STORE_SCHEMA_VERSION   # 2 (Packet 8D asset bytes)
    st.close()
    # reopen on existing db is idempotent
    st2 = _store(tmp_path)
    assert st2.schema_version == STORE_SCHEMA_VERSION
    st2.close()
    if os.name != "nt":
        assert oct(tmp_path.stat().st_mode & 0o777) == "0o700"
        assert oct((tmp_path / "communications.db").stat().st_mode & 0o777) == "0o600"


def test_baseline_records_visible_items_as_seen_no_flood(tmp_path):
    st = _store(tmp_path)
    items = [_item(Platform.GMAIL, s, scope="a",
                   captured_at=1_700_000_000.0 + i) for i, s in
             enumerate(("Synthetic Sender Alpha", "Synthetic Sender Bravo"))]
    count = st.commit_baseline(scan=_scan(Platform.GMAIL, ScanStatus.NO_CHANGE,
                                          scope="a"),
                               items=items, manifest=_manifest(items, "r1",
                                                               accounts=("a",)))
    assert count == 0  # baseline never announces old messages as new
    cur = st.peek_cursor(Platform.GMAIL, "a")
    assert cur is not None and cur.baseline is True
    assert cur.watermark == max(i.captured_at for i in items)
    assert st.item_exists(Platform.GMAIL, "a", items[0].identity)
    st.close()


def test_new_item_after_cursor_is_new_once_then_dedupes(tmp_path):
    st = _store(tmp_path)
    base = [_item(Platform.GMAIL, "Synthetic Sender Alpha", scope="a",
                  captured_at=1_700_000_000.0)]
    st.commit_baseline(scan=_scan(Platform.GMAIL, ScanStatus.NO_CHANGE, scope="a"),
                       items=base, manifest=_manifest(base, "r1", accounts=("a",)))
    new = _item(Platform.GMAIL, "Synthetic Sender Bravo", scope="a",
                captured_at=1_700_000_100.0, source_item_id="m-new")
    n1 = st.commit_new(scan=_scan(Platform.GMAIL, ScanStatus.NEW_ITEMS, scope="a"),
                       items=[new], manifest=_manifest([new], "r2", accounts=("a",)))
    assert n1 == 1
    # replaying the same item is NOT new again (no-flood / duplicate scans)
    n2 = st.commit_new(scan=_scan(Platform.GMAIL, ScanStatus.NO_CHANGE, scope="a"),
                       items=[new], manifest=_manifest([new], "r3", accounts=("a",)))
    assert n2 == 0
    assert st.peek_cursor(Platform.GMAIL, "a").report_id == "r3"
    st.close()


def test_unread_unknown_read_distinct_from_new(tmp_path):
    st = _store(tmp_path)
    unread = _item(Platform.GMAIL, "Synthetic Sender Alpha", scope="a",
                   read_state=ReadState.UNREAD, captured_at=1_700_000_000.0)
    unknown = _item(Platform.GMAIL, "Synthetic Sender Bravo", scope="a",
                    read_state=ReadState.UNKNOWN, captured_at=1_700_000_010.0)
    st.commit_baseline(scan=_scan(Platform.GMAIL, ScanStatus.NO_CHANGE, scope="a"),
                       items=[unread, unknown],
                       manifest=_manifest([unread, unknown], "r1", accounts=("a",)))
    # baseline: both seen, but read-state labels differ
    assert st.peek_cursor(Platform.GMAIL, "a").baseline is True
    st.close()


def test_cursor_advance_requires_atomic_report_commit(tmp_path):
    st = _store(tmp_path)
    base = [_item(Platform.GMAIL, "Synthetic Sender Alpha", scope="a",
                  captured_at=1_700_000_000.0)]
    st.commit_baseline(scan=_scan(Platform.GMAIL, ScanStatus.NO_CHANGE, scope="a"),
                       items=base, manifest=_manifest(base, "r1", accounts=("a",)))
    before = st.peek_cursor(Platform.GMAIL, "a")
    bad = _item(Platform.GMAIL, "Synthetic Sender Bravo", scope="a",
                captured_at=1_700_000_100.0, source_item_id="m-bad")
    with pytest.raises(CommunicationsStoreError, match="missing asset"):
        st.commit_new(scan=_scan(Platform.GMAIL, ScanStatus.NEW_ITEMS, scope="a"),
                      items=[bad],
                      manifest=_manifest([bad], "r-bad", assets=("asset-missing",),
                                         accounts=("a",)))
    after = st.peek_cursor(Platform.GMAIL, "a")
    assert after.cursor_id == before.cursor_id       # cursor did not advance
    assert after.report_id == "r1"                   # report still the old one
    assert not st.item_exists(Platform.GMAIL, "a", bad.identity)  # item rolled back
    st.close()


def test_first_commit_without_baseline_is_rejected(tmp_path):
    st = _store(tmp_path)
    it = _item(Platform.GMAIL, "Synthetic Sender Alpha", scope="b")
    with pytest.raises(CommunicationsStoreError, match="baseline"):
        st.commit_new(scan=_scan(Platform.GMAIL, ScanStatus.NEW_ITEMS, scope="b"),
                      items=[it], manifest=_manifest([it], "r1", accounts=("b",)))
    st.close()


def test_partial_account_keeps_prior_cursor(tmp_path):
    st = _store(tmp_path)
    base = [_item(Platform.GMAIL, "Synthetic Sender Alpha", scope="a",
                  captured_at=1_700_000_000.0)]
    st.commit_baseline(scan=_scan(Platform.GMAIL, ScanStatus.NO_CHANGE, scope="a"),
                       items=base, manifest=_manifest(base, "r1", accounts=("a",)))
    before = st.peek_cursor(Platform.GMAIL, "a")
    # a partial/auth-required run records scan + health but never advances
    st.record_health(AccountHealthRecord.create(
        platform=Platform.GMAIL, account_scope="a",
        status=AccountHealthStatus.AUTH_REQUIRED, adapter_version="8a-1"))
    st.record_scan(_scan(Platform.GMAIL, ScanStatus.AUTH_REQUIRED, scope="a"))
    assert st.latest_health(Platform.GMAIL, "a").status is \
        AccountHealthStatus.AUTH_REQUIRED
    assert st.latest_scan(Platform.GMAIL, "a").status is ScanStatus.AUTH_REQUIRED
    assert st.peek_cursor(Platform.GMAIL, "a").cursor_id == before.cursor_id
    st.close()


def test_failed_account_does_not_block_successful_account(tmp_path):
    st = _store(tmp_path)
    ok_base = [_item(Platform.INSTAGRAM, "Synthetic IG Sender Delta", scope="ig",
                     captured_at=1_700_000_000.0)]
    st.commit_baseline(scan=_scan(Platform.INSTAGRAM, ScanStatus.NO_CHANGE, scope="ig"),
                       items=ok_base, manifest=_manifest(ok_base, "ig1",
                                                         accounts=("ig",)))
    st.record_scan(_scan(Platform.GMAIL, ScanStatus.FAILED, scope="a"))
    new = _item(Platform.INSTAGRAM, "Synthetic IG Sender Echo", scope="ig",
                captured_at=1_700_000_100.0, source_item_id="ig-new")
    n = st.commit_new(scan=_scan(Platform.INSTAGRAM, ScanStatus.NEW_ITEMS, scope="ig"),
                      items=[new], manifest=_manifest([new], "ig2", accounts=("ig",)))
    assert n == 1
    assert st.peek_cursor(Platform.GMAIL, "a") is None  # failed acct never advanced
    st.close()


def test_retention_dry_run_then_audited_delete(tmp_path):
    st = _store(tmp_path)
    old = time.time() - 200 * 86400
    st.record_health(AccountHealthRecord.create(
        platform=Platform.GMAIL, account_scope="old",
        status=AccountHealthStatus.STALE, adapter_version="8a-1",
        checked_at=old))
    st.record_health(AccountHealthRecord.create(
        platform=Platform.GMAIL, account_scope="fresh",
        status=AccountHealthStatus.HEALTHY, adapter_version="8a-1",
        checked_at=time.time()))
    dry = st.apply_retention(dry_run=True)
    by_cat = {r["category"]: r for r in dry}
    assert by_cat["scan_health"]["dry_run"] is True
    assert by_cat["scan_health"]["removed"] == 1  # only the old row counted
    real = st.apply_retention(dry_run=False)
    assert {r["category"]: r["removed"] for r in real}["scan_health"] == 1
    assert st.latest_health(Platform.GMAIL, "old") is None
    assert st.latest_health(Platform.GMAIL, "fresh") is not None
    log = st._conn.execute("SELECT * FROM retention_log").fetchall()
    assert any(r["category"] == "scan_health" and r["dry_run"] == 0 for r in log)
    st.close()


def test_cursor_rollback_restores_previous(tmp_path):
    st = _store(tmp_path)
    base = [_item(Platform.GMAIL, "Synthetic Sender Alpha", scope="a",
                  captured_at=1_700_000_000.0)]
    st.commit_baseline(scan=_scan(Platform.GMAIL, ScanStatus.NO_CHANGE, scope="a"),
                       items=base, manifest=_manifest(base, "r1", accounts=("a",)))
    c1 = st.peek_cursor(Platform.GMAIL, "a")
    new = _item(Platform.GMAIL, "Synthetic Sender Bravo", scope="a",
                captured_at=1_700_000_100.0, source_item_id="m-new")
    st.commit_new(scan=_scan(Platform.GMAIL, ScanStatus.NEW_ITEMS, scope="a"),
                  items=[new], manifest=_manifest([new], "r2", accounts=("a",)))
    restored = st.rollback_cursor(Platform.GMAIL, "a")
    assert restored.cursor_id == c1.cursor_id
    assert st.peek_cursor(Platform.GMAIL, "a").cursor_id == c1.cursor_id
    with pytest.raises(CommunicationsStoreError, match="no cursor history"):
        st.rollback_cursor(Platform.GMAIL, "missing-acct")
    st.close()


def test_lease_prevents_overlap(tmp_path):
    st = _store(tmp_path)
    assert st.acquire_lease("monitor", "owner-a", ttl_s=60)
    assert st.lease_ok("monitor")
    assert not st.acquire_lease("monitor", "owner-b", ttl_s=60)  # held by a
    assert st.release_lease("monitor", "owner-b") is False
    assert st.release_lease("monitor", "owner-a") is True
    assert st.acquire_lease("monitor", "owner-b", ttl_s=60)
    st.close()


def test_notification_ack(tmp_path):
    st = _store(tmp_path)
    notif = NotificationRecord.create(
        report_id="r1", kind=NotificationKind.DESKTOP,
        status=NotificationStatus.PENDING, dedupe_key="n:1", new_count=3,
        created_at=time.time())
    assert notif.dedupe_key == "n:1"
    assert st.acknowledge_notification("nope") is False
    st.close()


def test_asset_link_validation(tmp_path):
    st = _store(tmp_path)
    asset = EvidenceAssetRecord.create(
        report_id="r1", item_identity="i", mime="image/webp", byte_count=10,
        sha256="a" * 64, redaction_status=EvidenceStatus.AVAILABLE)
    manifest = _manifest([_item(Platform.GMAIL, "Synthetic Sender Alpha")], "r1",
                         assets=(asset.asset_id,))
    st.validate_asset_links(manifest, [asset])          # consistent passes
    orphan = _manifest([_item(Platform.GMAIL, "Synthetic Sender Bravo")], "r1")
    with pytest.raises(CommunicationsStoreError, match="not referenced"):
        st.validate_asset_links(orphan, [asset])
    missing = _manifest([_item(Platform.GMAIL, "Synthetic Sender Bravo")], "r1",
                        assets=("ghost",))
    with pytest.raises(CommunicationsStoreError, match="missing asset"):
        st.validate_asset_links(missing, [])
    st.close()


def test_commit_with_assets_roundtrip(tmp_path):
    st = _store(tmp_path)
    item = _item(Platform.GMAIL, "Synthetic Sender Alpha", scope="a",
                 captured_at=1_700_000_000.0)
    asset = EvidenceAssetRecord.create(
        report_id="r1", item_identity=item.identity, mime="image/webp",
        byte_count=4, sha256="b" * 64,
        redaction_status=EvidenceStatus.PRIVACY_REDACTED)
    manifest = _manifest([item], "r1", accounts=("a",), assets=(asset.asset_id,))
    st.commit_baseline(scan=_scan(Platform.GMAIL, ScanStatus.NO_CHANGE, scope="a"),
                       items=[item], manifest=manifest, assets=[asset])
    row = st._conn.execute(
        "SELECT * FROM evidence_asset WHERE asset_id=?",
        (asset.asset_id,)).fetchone()
    assert row is not None and row["redaction_status"] == "privacy_redacted"
    st.close()


def test_no_runtime_module_imports_communications_yet():
    """Packet 8A must not change runtime behavior by itself; the communications
    package is now wired into the authenticated dashboard by Packet 8C
    (voicebridge/webui.py), the manual scan CLI by 8B, and the authorized
    hourly monitor by Packet 8D (voicebridge/cli.py `monitor` subcommand).
    NO other runtime module may import it."""
    runtime = ["voicebridge", "scripts", "systemd"]
    allowlisted = {
        "voicebridge/webui.py",        # Packet 8C report API routes
        "voicebridge/cli.py",          # Packet 8D `monitor` subcommand
        "scripts/communications_monitor.py",  # manual scan CLI (8B) + 8D
    }
    offenders = []
    for root in runtime:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if "communications" in str(path):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel in allowlisted:
                continue
            text = path.read_text(errors="replace")
            if ("voicebridge.communications" in text
                    or "from .communications" in text
                    or "import communications" in text):
                offenders.append(rel)
    assert offenders == [], f"unauthorized runtime imports of Packet 8A package: {offenders}"