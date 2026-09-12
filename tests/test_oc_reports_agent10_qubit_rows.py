"""Qubit ledger rows (insights / reports_list) — real proving tests.

Exercises the production dashboard/voice backend the ledger points at:
``dashboard_ops.run_registry_op`` for ``reports_list`` (over a seeded
``ReportStore`` bundle) and ``insights`` (over the session-receipt reader).
Hermetic: isolated XDG state, no network, no model.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from voicebridge.communications.contracts import (
    CommunicationItem, EvidenceAssetRecord, EvidenceStatus, Platform,
    ReadState, ReportManifest, ReportStatus,
)
from voicebridge.communications.reports import ReportStore


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _seed_report(cfg, report_id="report_agent10"):
    store = ReportStore(Path(cfg.state_dir()) / "reports")
    item = CommunicationItem.create(
        platform=Platform.GMAIL, account_scope="a",
        sender="Synthetic Sender", subject="Synthetic subject",
        preview="preview", is_new=True, read_state=ReadState.UNREAD,
        safe_url="https://mail.google.com/mail/u/0/#inbox",
        source_item_id="m1", adapter="gmail", adapter_version="8b-gmail-2")
    manifest = ReportManifest.create(
        window_start=1_700_000_000.0, window_end=1_700_000_500.0,
        status=ReportStatus.SUCCESS, new_count=1, platforms=("gmail",),
        accounts=("a",), item_identities=(item.identity,),
        summary="Synthetic report", report_id=report_id)
    data = b"\x00" * 256
    asset = EvidenceAssetRecord.create(
        report_id=report_id, item_identity=item.identity,
        mime="image/webp", byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        redaction_status=EvidenceStatus.AVAILABLE)
    store.render(manifest, [item], [asset], asset_bytes={asset.asset_id: data})
    return store


def test_reports_list_backed_by_real_bundle(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as D

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_report(cfg, "report_agent10")
    out = D.run_registry_op(cfg, "reports_list", {}, actor="dashboard:user",
                            session="dashboard")
    assert out["state"] == "executed", out
    assert out["speech"]
    data = out.get("receipt", {}).get("result", {})
    text = repr(data)
    assert "report_agent10" in text


def test_insights_backed_by_receipt_reader(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as D

    cfg = _cfg(tmp_path, monkeypatch)
    out = D.run_registry_op(cfg, "insights", {}, actor="dashboard:user",
                            session="dashboard")
    assert out["state"] == "executed", out
    text = repr(out.get("data") or out.get("result") or out)
    assert "recent_sessions" in text or "states" in text
