"""Final parity — the 8T-F matrix has zero code-side BLOCKED/REJECTED rows.

Every row must be IMPLEMENTED with a proof naming the real Qubit backend
and evidence test files that exist. Unsafe upstream variants stay refused
by typed errors + absence tests (test_rejected_matrix8tf.py) — full
parity is the safe equivalent fully implemented, not the unsafe behavior
copied.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _rows():
    m = json.loads((REPO / "docs/archive/2026-09-21" / "MATRIX_8T_F.json").read_text())
    assert m["upstreams"]["hermes-agent"]["commit"] == \
        "2237be355906fbe6065ce1815711eee52b2d646e"
    assert m["upstreams"]["openclaw"]["commit"] == \
        "3928bad9badfcb6c7d140530435e806fb8092190"
    return m["rows"]


def test_zero_blocked_rejected_deferred():
    rows = _rows()
    assert len(rows) == 35
    for row in rows:
        assert row["status"] == "IMPLEMENTED", row["id"]
        assert row["verdict"] in ("REUSE", "ADAPT", "MISSING"), row["id"]


def test_every_row_has_proof_evidence_owner_rollback():
    rows = _rows()
    for row in rows:
        for key in ("proof", "evidence", "owner", "rollback", "code",
                    "user_explanation", "capability", "upstream"):
            assert row.get(key), (row["id"], key)
        assert "cannot do this" not in row["user_explanation"].lower(), \
            row["id"]


def test_evidence_files_exist():
    import re

    rows = _rows()
    missing = []
    for row in rows:
        for path in re.findall(r"tests/test_[a-z0-9_]+\.py",
                               row.get("evidence", "")):
            if not (REPO / path).exists():
                missing.append((row["id"], path))
    assert not missing, missing


def test_proof_backends_exist():
    import re

    rows = _rows()
    missing = []
    for row in rows:
        for mod in re.findall(r"voicebridge/[a-z0-9_/]+\.py",
                              row.get("proof", "")):
            if not (REPO / mod).exists():
                missing.append((row["id"], mod))
    assert not missing, missing
