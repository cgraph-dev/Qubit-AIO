# tests/test_rebase_driver.py
"""Task 6 unit gate: the bucket merge driver.

Tiny temp trees exercise :mod:`merge_v0215.driver` in isolation: the
classification shape table, the resolution rule (TAKE_OURS -> ours,
REAPPLY_THEIRS -> merge_file, CONFLICT -> keep theirs), and the writer.  No
product tree is read or written here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from merge_v0215 import driver as D  # noqa: E402


@pytest.fixture
def trees(tmp_path):
    out = {}
    for name in ("base", "ours", "theirs", "target"):
        (tmp_path / name).mkdir()
        out[name] = tmp_path / name
    return out


def _put(root: Path, rel: str, data) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if data is None:
        if p.exists():
            p.unlink()
    else:
        p.write_bytes(data)


def test_bucket_of():
    assert D.bucket_of("qubit_state_health.py") == "qubit_state*"
    assert D.bucket_of("agent/x.py") == "agent"
    assert D.bucket_of("gateway/x.py") == "gateway"
    assert D.bucket_of("plugins/a/b.py") == "plugins"
    assert D.bucket_of("tests/agent/x.py") == "remaining"
    assert D.bucket_of("README.md") == "remaining"
    # a nested qubit_state file is not the top-level state bucket
    assert D.bucket_of("sub/qubit_state_x.py") == "remaining"


def test_classify_shape_table(trees):
    b, o, t = trees["base"], trees["ours"], trees["theirs"]
    # identical
    for r in (b, o, t):
        _put(r, "id.py", b"same")
    assert D.classify(b, o, t, "id.py") == "IDENTICAL"
    # take ours (upstream-only modify)
    _put(b, "tu.py", b"v1"); _put(t, "tu.py", b"v1"); _put(o, "tu.py", b"v2")
    assert D.classify(b, o, t, "tu.py") == "TAKE_OURS"
    # take ours (new upstream file)
    _put(o, "new.py", b"n")
    assert D.classify(b, o, t, "new.py") == "TAKE_OURS"
    # take ours (delete-upstream)
    _put(b, "du.py", b"v1"); _put(t, "du.py", b"v1")
    assert D.classify(b, o, t, "du.py") == "TAKE_OURS"
    # reapply theirs (fork-only modify)
    _put(b, "rt.py", b"v1"); _put(o, "rt.py", b"v1"); _put(t, "rt.py", b"v2")
    assert D.classify(b, o, t, "rt.py") == "REAPPLY_THEIRS"
    # reapply theirs (fork-only add)
    _put(t, "af.py", b"f")
    assert D.classify(b, o, t, "af.py") == "REAPPLY_THEIRS"
    # reapply theirs (delete-fork)
    _put(b, "df.py", b"v1"); _put(o, "df.py", b"v1")
    assert D.classify(b, o, t, "df.py") == "REAPPLY_THEIRS"
    # conflict (all three differ)
    _put(b, "c.py", b"v1"); _put(o, "c.py", b"v2"); _put(t, "c.py", b"v3")
    assert D.classify(b, o, t, "c.py") == "CONFLICT"
    # both added, differing
    _put(o, "ba.py", b"o"); _put(t, "ba.py", b"t")
    assert D.classify(b, o, t, "ba.py") == "CONFLICT"
    # same change on both sides
    _put(b, "sc.py", b"v1"); _put(o, "sc.py", b"v2"); _put(t, "sc.py", b"v2")
    assert D.classify(b, o, t, "sc.py") == "SAME_CHANGE"
    # both deleted
    _put(b, "bd.py", b"v1")
    assert D.classify(b, o, t, "bd.py") == "BOTH_DELETED"


def test_resolve_take_ours_adds_and_deletes(trees):
    b, o, t = trees["base"], trees["ours"], trees["theirs"]
    _put(b, "n.py", b"v1"); _put(t, "n.py", b"v1"); _put(o, "n.py", b"v2")
    rec = D.resolve(b, o, t, "n.py", "TAKE_OURS")
    assert rec["resolution"] == D.RES_AUTO_OURS and rec["action"] == "write"
    assert rec["data"] == b"v2"
    _put(o, "brand_new.py", b"fresh")
    rec = D.resolve(b, o, t, "brand_new.py", "TAKE_OURS")
    assert rec["resolution"] == D.RES_ADDED and rec["action"] == "write"
    _put(b, "gone.py", b"v1"); _put(t, "gone.py", b"v1")
    rec = D.resolve(b, o, t, "gone.py", "TAKE_OURS")
    assert rec["resolution"] == D.RES_DELETED and rec["action"] == "delete"


def test_resolve_reapply_uses_merge_file_and_keeps_theirs_on_conflict(trees):
    b, o, t = trees["base"], trees["ours"], trees["theirs"]
    # fork-only modify: merge yields theirs
    _put(b, "rt.py", b"a\n"); _put(o, "rt.py", b"a\n"); _put(t, "rt.py", b"a\nb\n")
    rec = D.resolve(b, o, t, "rt.py", "REAPPLY_THEIRS")
    assert rec["resolution"] == D.RES_MERGE_FILE
    assert rec["data"] == b"a\nb\n"

    # delete-fork: the primitive returns ours (upstream file restored)
    _put(b, "df.py", b"a\n"); _put(o, "df.py", b"a\n")
    rec = D.resolve(b, o, t, "df.py", "REAPPLY_THEIRS")
    assert rec["resolution"] == D.RES_MERGE_FILE and rec["action"] == "write"
    assert rec["data"] == b"a\n"

    # a genuine overlapping merge keeps theirs and is recorded
    _put(b, "c.py", b"l1\nl2\nl3\n")
    _put(o, "c.py", b"L1\nl2\nl3\n")
    _put(t, "c.py", b"l1\nl2\nL3\n")
    rec = D.resolve(b, o, t, "c.py", "REAPPLY_THEIRS")
    # non-overlapping edits merge cleanly through the primitive
    assert rec["resolution"] == D.RES_MERGE_FILE


def test_resolve_conflict_keeps_theirs(trees):
    b, o, t = trees["base"], trees["ours"], trees["theirs"]
    _put(b, "c.py", b"v1"); _put(o, "c.py", b"v2"); _put(t, "c.py", b"v3")
    rec = D.resolve(b, o, t, "c.py", "CONFLICT")
    assert rec["resolution"] == D.RES_KEEP_CONFLICT
    assert rec["action"] == "noop" and rec["data"] == b"v3"
    assert rec["conflict"] is True


def test_run_bucket_writes_and_audits(trees):
    b, o, t, target = trees["base"], trees["ours"], trees["theirs"], trees["target"]
    # target starts as theirs
    _put(target, "agent/keep.py", b"k"); _put(t, "agent/keep.py", b"k")
    _put(target, "agent/up.py", b"old")
    _put(b, "agent/up.py", b"old"); _put(t, "agent/up.py", b"old")
    _put(o, "agent/up.py", b"new")
    _put(target, "agent/conf.py", b"t"); _put(b, "agent/conf.py", b"b")
    _put(o, "agent/conf.py", b"o"); _put(t, "agent/conf.py", b"t")

    recs = D.run_bucket(target, b, o, t, "agent")
    by_path = {r["path"]: r for r in recs}
    assert by_path["agent/up.py"]["resolution"] == D.RES_AUTO_OURS
    assert (target / "agent/up.py").read_bytes() == b"new"
    assert by_path["agent/conf.py"]["resolution"] == D.RES_KEEP_CONFLICT
    assert (target / "agent/conf.py").read_bytes() == b"t"

    audit = D.build_audit(recs, b, o, t)
    assert audit["schema"] == "qubit-v0215-task6-merge/1"
    assert audit["totals"]["by_resolution"]["AUTO_OURS"] == 1
    assert audit["totals"]["by_resolution"]["KEEP_THEIRS_CONFLICT"] == 1
    assert any(c["path"] == "agent/conf.py" for c in audit["conflicts"])
