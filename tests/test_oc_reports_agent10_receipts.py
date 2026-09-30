"""OC-52 — immutable execution-identity receipt tests.

Hermetic; proves content-addressed identities, hash-chained receipts, tamper
and reorder/deletion detection, and that receipts carry digests only.
"""
from __future__ import annotations

import pytest

from voicebridge.evaluation import receipts as R


def _chain():
    ident = R.ExecutionIdentity.create(run_id="run-1", actor="alice",
                                       workspace="/w", tool="media_gen",
                                       started_at=1000.0)
    chain = R.ReceiptChain(ident)
    chain.append(status="PREPARED", inputs_digest="in1", ts=1001.0)
    chain.append(status="OK", outputs_digest="out1", approval_id="ap1",
                 ts=1002.0)
    return ident, chain


def test_identity_is_content_addressed():
    a = R.ExecutionIdentity.create(run_id="r", actor="u", started_at=1.0)
    b = R.ExecutionIdentity.create(run_id="r", actor="u", started_at=1.0)
    c = R.ExecutionIdentity.create(run_id="r", actor="v", started_at=1.0)
    assert a.identity_id == b.identity_id
    assert a.identity_id != c.identity_id
    assert len(a.identity_id) == 64


def test_chain_links_and_verifies():
    ident, chain = _chain()
    receipts = chain.receipts()
    assert receipts[0]["prev_hash"] == R.GENESIS
    assert receipts[1]["prev_hash"] == receipts[0]["content_hash"]
    assert chain.verify() == {"valid": True, "broken_at": -1, "length": 2,
                              "head": receipts[1]["content_hash"]}


def test_tampered_receipt_is_detected():
    ident, chain = _chain()
    chain._receipts[0].status = "TAMPERED"
    result = chain.verify()
    assert result["valid"] is False and result["broken_at"] == 0
    assert "tampered" in result["reason"]


def test_deleted_receipt_breaks_linkage():
    ident, chain = _chain()
    del chain._receipts[0]
    result = chain.verify()
    assert result["valid"] is False and result["broken_at"] == 0
    assert "prev_hash" in result["reason"]


def test_serialized_chain_verification_and_tamper():
    ident, chain = _chain()
    ok = R.verify_receipts(chain.receipts())
    assert ok["valid"] is True
    bad = chain.receipts()
    bad[1]["status"] = "FORGED"
    broken = R.verify_receipts(bad)
    assert broken["valid"] is False and broken["broken_at"] == 1


def test_chain_requires_identity():
    with pytest.raises(R.ReceiptError) as ei:
        R.ReceiptChain({"not": "identity"})
    assert ei.value.code == "RECEIPT_BAD_IDENTITY"


def test_receipts_carry_digests_not_contents():
    ident, chain = _chain()
    blob = repr(chain.receipts())
    assert "in1" in blob and "out1" in blob
    assert "password" not in blob
