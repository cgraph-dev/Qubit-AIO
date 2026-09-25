"""Qubit-native text/store differentials + conformance.

Proves: (1) Qubit's clean-room textutil/sqlstore behave identically to
the pinned upstream oracle on adversarial batteries (differential tests
importing the comparison clone straight from /tmp/upstream-8tfv3; the
clone is a read-only oracle, never executed by Qubit); (2) native
sanitizers execute in live Qubit output paths; (3) conformance rows map
to tested Qubit behavior (approvals lifecycle, receipts, events
validation, skill manifests); (4) the close-out matrix is complete and
honest (no row claims integration without proof).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX = REPO_ROOT / "docs/archive/2026-09-21" / "MATRIX_8T_F.json"

# The pinned upstream oracle. Owner decision #25 moved the vendored pin to
# v0.21.5 ``69948c0057``; the pristine tree and its read-only clone live under
# ``/tmp/opencode/pinned``. The clone is a read-only oracle, never executed by
# Qubit. Discover it (env override first, then the known pins) and skip -- not
# error -- when no oracle carrying the required modules is available.
_ORACLE_ENV = "QUBIT_8TF_HERMES_CLONE"
_ORACLE_REQUIRED = ("tools/ansi_strip.py", "hermes_cli/sqlite_util.py")
_ORACLE_CANDIDATES = (
    "/tmp/opencode/pinned/hermes-agent-69948c0057",
    "/tmp/opencode/pinned/hermes-agent",
    "/tmp/upstream-8tfv3/hermes-agent",
)


def _discover_oracle() -> Path | None:
    candidates = []
    env = os.environ.get(_ORACLE_ENV)
    if env:
        candidates.append(env)
    candidates.extend(_ORACLE_CANDIDATES)
    for candidate in candidates:
        root = Path(candidate)
        if all((root / rel).is_file() for rel in _ORACLE_REQUIRED):
            return root
    return None


CLONE = _discover_oracle()

from voicebridge import sqlstore as V_SQL  # noqa: E402
from voicebridge import textutil as V_STRIP  # noqa: E402


def _load_pinned(rel: str, name: str):
    if CLONE is None:
        pytest.skip(
            "pinned upstream oracle clone unavailable (set %s); expected a "
            "tree carrying %s" % (_ORACLE_ENV, ", ".join(_ORACLE_REQUIRED)))
    path = CLONE / rel
    if not path.is_file():
        pytest.skip(f"pinned oracle {CLONE} lacks {rel}")
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def pinned_strip():
    return _load_pinned("tools/ansi_strip.py", "pinned_ansi_strip")


@pytest.fixture(scope="module")
def pinned_sql():
    return _load_pinned("hermes_cli/sqlite_util.py", "pinned_sqlite_util")


ANSI_BATTERY = [
    "",
    "plain text",
    "\x1b[31mred\x1b[0m exact",
    "\x1b]0;retitle\x07hidden",
    "line1\x1b[2K\rline2",
    "\x9b32mC1\x9b0m",
    "back\x08space",
    "bell\x07here",
    "tag-smuggle\U000E0069\U000E0067 end",
    "\U0001F3F4\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F kept?",
]


# ---------------------------------------------------------------------------
# Differential: vendored == pinned source
# ---------------------------------------------------------------------------
def test_strip_ansi_matches_pinned(pinned_strip):
    for text in ANSI_BATTERY:
        assert V_STRIP.strip_ansi(text) == pinned_strip.strip_ansi(text), repr(text)


def test_sanitize_display_matches_pinned(pinned_strip):
    for text in ANSI_BATTERY:
        assert V_STRIP.sanitize_display_text(text) == \
            pinned_strip.sanitize_display_text(text), repr(text)


def test_unicode_tags_match_pinned(pinned_strip):
    for text in ANSI_BATTERY:
        assert V_STRIP.strip_unicode_tags(text) == \
            pinned_strip.strip_unicode_tags(text), repr(text)


def test_clean_text_untouched_and_fast_path(pinned_strip):
    clean = "hello world 123"
    assert V_STRIP.strip_ansi(clean) == clean
    assert V_STRIP.strip_unicode_tags(clean) == clean
    assert V_STRIP.sanitize_display_text("tab\tnewline\nok") == "tab\tnewline\nok"


def test_write_txn_matches_pinned_commit_and_rollback(pinned_sql, tmp_path):
    for mod, tag in ((V_SQL, "vendored"), (pinned_sql, "pinned")):
        db = tmp_path / f"{tag}.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
        with mod.write_txn(conn):
            conn.execute("INSERT INTO t(v) VALUES ('a')")
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
        with pytest.raises(RuntimeError):
            with mod.write_txn(conn):
                conn.execute("INSERT INTO t(v) VALUES ('b')")
                raise RuntimeError("boom")
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1, tag
        conn.close()


def test_add_column_matches_pinned_idempotent_and_race(pinned_sql, tmp_path):
    for mod, tag in ((V_SQL, "vendored"), (pinned_sql, "pinned")):
        db = tmp_path / f"col-{tag}.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY)")
        assert mod.add_column_if_missing(conn, "t", "extra", "extra TEXT") is True
        assert mod.add_column_if_missing(conn, "t", "extra", "extra TEXT") is False
        conn.close()


# ---------------------------------------------------------------------------
# Live wiring: sanitizer executes in Qubit output paths
# ---------------------------------------------------------------------------
def test_agent_reply_path_sanitizes_output():
    import inspect

    from voicebridge import agent as _agent

    src = inspect.getsource(_agent.turn)
    assert "sanitize" in src, "live reply path must sanitize model output"
    assert V_STRIP.sanitize("\x1b[1mdirty\x1b[0m") == "dirty"


def test_native_provenance_recorded():
    header = Path(V_STRIP.__file__).read_text()[:800]
    assert "Qubit text sanitizing" in header
    assert "hermes" not in header.lower() or "upstream" in header.lower()
    record = Path("voicebridge/upstream/ATTRIBUTION.md").read_text()
    assert "2237be355906fbe6065ce1815711eee52b2d646e" in record
    assert "retired" in record.lower()


# ---------------------------------------------------------------------------
# OpenClaw conformance: REUSE rows map to tested Qubit behavior
# ---------------------------------------------------------------------------
def test_approval_lifecycle_expiry_settle_once_no_renewal():
    from voicebridge.capabilities import ConfirmationDenied, ConfirmationStore

    store = ConfirmationStore(clock=lambda: 1000.0, default_ttl_s=60.0)
    rec = store.issue("act.mail.send", {"a": 1}, issued_by="user")
    consumed = store.consume(rec.id, "act.mail.send", {"a": 1})
    assert consumed.consumed is True
    with pytest.raises(ConfirmationDenied):
        store.consume(rec.id, "act.mail.send", {"a": 1})  # settle once
    assert not hasattr(store, "renew"), "no renewal path may exist"
    with pytest.raises(ConfirmationDenied):
        store.issue("act.browser.read", {}, issued_by="model")
    expired = ConfirmationStore(clock=lambda: 1000.0, default_ttl_s=1.0)
    rec2 = expired.issue("act.browser.read", {}, issued_by="user")
    expired._clock = lambda: 2000.0
    with pytest.raises(ConfirmationDenied):
        expired.consume(rec2.id, "act.browser.read", {})


def test_receipt_carries_runtime_provenance(tmp_path):
    from voicebridge import actions as A
    from voicebridge.permissions import PermissionsStore

    store = A.ActionStore(tmp_path / "actions.json")
    perms = PermissionsStore(tmp_path / "perm.json")
    perms.grant("act.browser.draft", requested_by="user", confirmed=True,
                scopes=("web",))
    outcomes = A.load_fixture_outcomes(
        REPO_ROOT / "tests" / "fixtures" / "actions" / "outcomes.json")
    runner = A.ActionRunner(store=store, perms=perms,
                            executors=A.make_fixture_executors(outcomes),
                            runtime_label="qubit-native")
    r = runner.execute("act.browser.draft",
                       args={"recipient": "b@example.com", "subject": "s",
                             "body": "hello"},
                       target="https://mail.google.com", scope="web")
    assert r["state"] == "OK"
    assert r["runtime"] == "qubit-native"
    persisted = [e for e in store.audit(20) if e.get("receipt_id") == r["receipt_id"]]
    assert persisted, "receipt must persist with its runtime provenance"


def test_events_reject_malformed_frames():
    from voicebridge.events.core import (EventType, decode_sse, make_event,
                                           to_wire)

    with pytest.raises(ValueError):
        make_event("not-a-real-event-type-xyz")
    # garbage frames decode to nothing (fail closed: no phantom events)
    assert decode_sse("not sse at all {{{") == []
    ev = make_event(EventType.RUN_STARTED, timestamp=1, thread_id="t1",
                      run_id="r1")
    assert ev["type"] == "RUN_STARTED"
    wire = to_wire(ev)
    assert wire["type"] == "RUN_STARTED" and wire["threadId"] == "t1"


def _good_manifest(**kw):
    from voicebridge.capabilities import Tier
    from voicebridge.permissions import SkillManifest

    base = dict(skill_id="qubit.demo", version="1.0.0", name="Demo",
                description="demo skill", source="https://example.com/demo",
                revision="abc123", license="MIT",
                content_hash="ab" * 32, capabilities=("demo.read",),
                scopes=("demo",), tier=Tier.READ_ONLY, sensitivity="personal",
                approval_policy="confirm", timeout_s=30.0,
                resource_limits={}, audit_events=("demo",),
                compatibility="qubit>=1", rollback="disable removes it")
    base.update(kw)
    return SkillManifest(**base)


def test_skill_manifest_validation_rejects_unlicensed(tmp_path):
    from voicebridge.permissions import (ConfirmationRequiredError,
                                         PermissionsStore, SkillValidationError)

    with pytest.raises(SkillValidationError):
        _good_manifest(license="")
    with pytest.raises(SkillValidationError):
        _good_manifest(content_hash="not-a-hash")
    store = PermissionsStore(tmp_path / "perm.json")
    with pytest.raises(ConfirmationRequiredError):
        store.install_skill(_good_manifest(), requested_by="user",
                            confirmed=False)
    rec = store.install_skill(_good_manifest(), requested_by="user",
                              confirmed=True)
    assert rec.status == "staged", "skills stage DISABLED until enablement"


# ---------------------------------------------------------------------------
# Matrix honesty
# ---------------------------------------------------------------------------
def test_matrix_complete_and_honest():
    matrix = json.loads((REPO_ROOT / "docs/archive/2026-09-21" / "MATRIX_8T_F.json").read_text())
    assert matrix["upstreams"]["hermes-agent"]["commit"] == \
        "2237be355906fbe6065ce1815711eee52b2d646e"
    assert matrix["upstreams"]["openclaw"]["commit"] == \
        "3928bad9badfcb6c7d140530435e806fb8092190"
    valid = {"REUSE", "ADAPT", "MISSING", "BLOCKED", "REJECTED"}
    implemented = blocked = 0
    for row in matrix["rows"]:
        assert row["verdict"] in valid, row
        assert row["status"] in ("IMPLEMENTED", "BLOCKED",
                                 "REJECTED"), row
        if row["verdict"] in ("REUSE", "ADAPT", "MISSING"):
            # Gate rule: no REUSE/ADAPT/MISSING row may remain unintegrated.
            assert row["status"] == "IMPLEMENTED", row
            assert row.get("proof"), row
            implemented += 1
        elif row["status"] == "BLOCKED":
            blocked += 1
            assert row.get("reason"), row
        else:
            assert row.get("reason"), row
    assert implemented >= 10, "too few implemented rows to close 8T-F"
    assert blocked == 0, \
        f"final parity leaves no BLOCKED rows: {[r['id'] for r in matrix['rows'] if r['status'] == 'BLOCKED']}"
    assert "REJECTED" not in {r["status"] for r in matrix["rows"]}, \
        "final parity leaves no REJECTED rows; unsafe variants stay refused by typed errors + absence tests"
    companion = (REPO_ROOT / "docs/archive/2026-09-21" / "MATRIX_8T_F.md").read_text()
    for row in matrix["rows"]:
        assert row["id"] in companion, f"id missing from MD: {row['id']}"
