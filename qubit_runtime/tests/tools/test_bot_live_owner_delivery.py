"""Durable mailbox invariants, using real disk and exec boundaries."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import bot_live_delivery as mailbox
def _owner(home):
    return dict(profile_home=str(Path(home).resolve()), session_id="local-bot",
                lease_id="authority-1", live_session_id="local-bot")


class _FakeAuthority:
    """Stands in for the profile authority's ``bot_relay.deliver`` RPC."""

    def __init__(self):
        self.calls = []
        self.records = {}

    def __call__(self, home, params):
        self.calls.append((Path(home).resolve(), dict(params)))
        record = self.records.get(params["id"])
        if record is None:
            record = self.records[params["id"]] = dict(
                status="queued", delivery_id=params["id"], profile_home=str(Path(home).resolve()),
                session_id="local-bot", message=params["message"], admission_id="adm-" + params["id"][:6],
                reply="", **({"author": params["author"]} if "author" in params else {}))
            with mailbox._locked(home) as root:
                mailbox._write(root / f"{params['id']}.json", record)
        elif (record["message"] != params["message"]
              or record.get("author") != params.get("author")):
            raise ValueError("admission_conflict")
        return {k: record[k] for k in ("status", "delivery_id", "profile_home", "session_id", "reply")}
@pytest.mark.xfail(strict=True, reason="wave-23 short-port: local deliver_to_live_owner keeps the legacy mailbox fallback instead of authority-only admission")
def test_cancel_queued_delivery_cancels_only_an_unadmitted_receipt(tmp_path, monkeypatch):
    """main's cancel fence on the owner seam: a legacy unadmitted queued receipt is cancelled
    once (idempotent); an authority admission is never cancelled from the mailbox."""
    key = "3" * 32
    with mailbox._locked(tmp_path) as root:
        mailbox._write(root / f"{key}.json", dict(
            delivery_id=key, id=key, status="queued", message="first", created_at=1, sequence=1,
            owner=_owner(tmp_path), **_owner(tmp_path)))
    cancelled = mailbox.cancel_queued_delivery(tmp_path, key, error="owner closed", reason="runtime_offline")
    assert cancelled["status"] == "cancelled" and cancelled["error"] == "owner closed"
    assert mailbox.cancel_queued_delivery(tmp_path, key, error="again", reason="runtime_offline") == cancelled

    authority = _FakeAuthority()
    monkeypatch.setattr(mailbox, "authority_delivery", authority)
    admitted = mailbox.deliver_to_live_owner(tmp_path, _owner(tmp_path), "second", delivery_id="4" * 32)
    kept = mailbox.cancel_queued_delivery(tmp_path, admitted["delivery_id"], error="x", reason="runtime_offline")
    assert kept["status"] != "cancelled"
    assert mailbox.read_delivery_result(tmp_path, admitted["delivery_id"])["status"] == "queued"
@pytest.mark.xfail(strict=True, reason="wave-23 short-port: local deliver_to_live_owner keeps the legacy mailbox fallback instead of authority-only admission")
def test_canonical_owner_is_the_authority_and_follows_compression(tmp_path, monkeypatch):
    from qubit_state import SessionDB
    import qubit_cli.gateway_runtime as runtime

    from types import SimpleNamespace

    monkeypatch.setattr(runtime, "discover_gateway_endpoint",
                        lambda home, timeout: SimpleNamespace(state="absent", endpoint=None))
    with pytest.raises(ValueError, match="authority"):
        mailbox.find_canonical_live_owner(tmp_path)

    monkeypatch.setattr(runtime, "discover_gateway_endpoint", _ready("authority-1"))
    assert mailbox.find_canonical_live_owner(tmp_path) is None  # no state.db yet
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session(session_id="scratch", source="cli")
        db.set_session_title("scratch", "Scratch")
        # No Bot Chat yet is still a deliverable owner: the authority creates the chat on
        # first delivery (create-if-missing), so discovery reports an empty tip, not None.
        assert mailbox.find_canonical_live_owner(tmp_path)["session_id"] == ""
        db.create_session(session_id="chat", source="cli")
        db.set_session_title("chat", "Bot Chat")
        owner = mailbox.find_canonical_live_owner(tmp_path)
        assert owner["canonical"] is True and owner["lease_id"] == "authority-1"
        assert owner["session_id"] == owner["live_session_id"] == "chat"
        db.end_session("chat", "compression")
        db.create_session(session_id="tip", source="cli", parent_session_id="chat")
        assert mailbox.find_canonical_live_owner(tmp_path)["session_id"] == "tip"
    finally:
        db.close()
def test_non_dict_ticket_fails_exact_id_reads_closed(tmp_path):
    """Malformed is not absent: an exact-id receipt read raises instead of reporting "no record"."""
    from tools import bot_live_delivery as mailbox

    bad = tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME / f"{'e' * 32}.json"
    bad.parent.mkdir(parents=True)
    bad.write_text('"oops"', encoding="utf-8")  # parses, but is not a record
    with pytest.raises(ValueError):
        mailbox.read_delivery_result(tmp_path, "e" * 32)
    assert bad.read_text(encoding="utf-8") == '"oops"'
def test_owner_from_another_home_or_malformed_id_is_refused_before_admission(tmp_path, monkeypatch):
    authority = _FakeAuthority()
    monkeypatch.setattr(mailbox, "authority_delivery", authority)
    foreign = dict(_owner(tmp_path), profile_home=str(tmp_path / "elsewhere"))
    with pytest.raises(ValueError, match="different profile home"):
        mailbox.deliver_to_live_owner(tmp_path, foreign, "hello", delivery_id="b" * 32)
    with pytest.raises(ValueError, match="delivery id"):
        mailbox.deliver_to_live_owner(tmp_path, _owner(tmp_path), "hello", delivery_id="../evil")
    assert authority.calls == []
def test_receipt_read_retains_the_immutable_author_without_replaying_admission(tmp_path, monkeypatch):
    """The owner-published local projection keeps the author; polling never replays admission."""
    authority = _FakeAuthority()
    monkeypatch.setattr(mailbox, "authority_delivery", authority)
    delivery_id = "d" * 32
    author = {"kind": "user", "id": "u-1", "display": "Ann"}
    queued = mailbox.deliver_to_live_owner(tmp_path, _owner(tmp_path), "hello", delivery_id=delivery_id, author=author)
    polled = mailbox.read_delivery_result(tmp_path, delivery_id)
    assert all(polled[key] == value for key, value in queued.items())
    assert polled['author'] == author
    assert [params.get("author") for _home, params in authority.calls] == [author]
def test_receipt_wait_polls_the_local_receipt_and_outlives_an_unreachable_owner(tmp_path, monkeypatch):
    """A sender's wait reads the receipt file the owner republishes; it never dials the authority
    (one fresh websocket per 0.5 s tick for up to 30 min) and an owner restart mid-wait is just a
    longer pending stretch, not an exception that abandons the reply."""
    import threading

    dials = []

    def unreachable(home, params):
        dials.append(params["id"])
        raise ValueError("profile authority is not ready")

    monkeypatch.setattr(mailbox, "authority_delivery", unreachable)
    monkeypatch.setattr(mailbox, "_POLL_SECONDS", 0.01)
    key = "f" * 32
    admitted = dict(delivery_id=key, profile_home=str(tmp_path.resolve()), session_id="bot", principal_id="owner",
                    message="hello", admission_id="adm-1", status="canonical")
    with mailbox._locked(tmp_path) as root:
        mailbox._write(root / f"{key}.json", admitted)
    pending = mailbox.await_delivery(tmp_path, key, 0.05)
    assert pending["status"] == "queued", pending  # admitted, outcome not yet published

    def publish():
        with mailbox._locked(tmp_path) as root:
            mailbox._write(root / f"{key}.json", dict(admitted, status="settled", reply="PONG"))
    threading.Timer(0.1, publish).start()
    settled = mailbox.await_delivery(tmp_path, key, 5)
    assert (settled["status"], settled["reply"]) == ("settled", "PONG")
    assert dials == []
@pytest.mark.xfail(strict=True, reason="wave-23 short-port: local deliver_to_live_owner keeps the legacy mailbox fallback instead of authority-only admission")
def test_same_envelope_id_admits_once_and_conflicts_on_changed_payload(tmp_path, monkeypatch):
    authority = _FakeAuthority()
    monkeypatch.setattr(mailbox, "authority_delivery", authority)
    delivery_id = "a" * 32
    queued = mailbox.deliver_to_live_owner(tmp_path, _owner(tmp_path), "héllo 世界", delivery_id=delivery_id)
    assert queued["status"] == "queued" and queued["delivery_id"] == delivery_id
    # Receipts are written as plain UTF-8 and read back BOM-tolerantly (main 61dc26cd7d9).
    path = tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME / f"{delivery_id}.json"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    path.write_bytes(b"\xef\xbb\xbf" + raw)
    # Exact retry inspects the same admission; it never mints a second envelope.
    assert mailbox.deliver_to_live_owner(tmp_path, _owner(tmp_path), "héllo 世界", delivery_id=delivery_id) == queued
    assert [params["id"] for _home, params in authority.calls] == [delivery_id, delivery_id]
    assert len(authority.records) == 1
    with pytest.raises(ValueError):
        mailbox.deliver_to_live_owner(tmp_path, _owner(tmp_path), "different", delivery_id=delivery_id)
    # Reading the receipt resolves through the same admission, not a local guess.
    assert mailbox.read_delivery_result(tmp_path, delivery_id)["status"] == "queued"
    # The retired UI claim consumer never hands out work.
    assert mailbox.claim_pending_delivery(tmp_path, _owner(tmp_path)) is None
    if os.name != "nt":
        for path in (tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME).iterdir():
            assert path.stat().st_mode & 0o077 == 0
@pytest.mark.parametrize("terminal_status", ["settled", "failed", "cancelled"])
def test_terminal_receipt_is_immutable(tmp_path, terminal_status):
    delivery_id = "c" * 32
    with mailbox._locked(tmp_path) as root:
        mailbox._write(root / f"{delivery_id}.json", dict(delivery_id=delivery_id, status="claimed",
                                                          message="hello", created_at=1))
    receipt = mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="answer")
    assert mailbox.read_delivery_result(tmp_path, delivery_id) == receipt
    assert mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="answer") == receipt
    with pytest.raises(ValueError):
        mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="rewrite")
    with pytest.raises(ValueError):
        mailbox.complete_delivery(tmp_path, "d" * 32, status="not-terminal")


@pytest.fixture(autouse=True)
def _live_authority_for_synthetic_owners(monkeypatch):
    """``deliver_to_live_owner`` now needs a live canonical authority.

    Tests that build a real registry (a ``state.db``) keep using the real
    discovery, including its ``None`` for an incapable owner. Tests that use a
    synthetic owner on a bare tmp dir stand in a live authority so the mailbox
    mechanics they exercise remain reachable.
    """
    from tools import bot_live_delivery as mailbox

    real = mailbox.find_canonical_live_owner

    def _live(home):
        if (Path(home) / "state.db").exists():
            return real(home)
        return {"profile_home": str(Path(home).resolve()), "session_id": "chat",
                "lease_id": "lease", "live_session_id": "live"}

    monkeypatch.setattr(mailbox, "find_canonical_live_owner", _live)

@pytest.mark.parametrize("terminal_status", ["settled", "failed", "cancelled"])
def test_delivery_is_idempotent_fenced_and_permanent(tmp_path, terminal_status):
    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    delivery_id = "a" * 32
    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "héllo 世界", delivery_id=delivery_id)
    assert queued["status"] == "queued"
    path = tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME / f"{delivery_id}.json"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    path.write_bytes(b"\xef\xbb\xbf" + raw)
    assert mailbox.deliver_to_live_owner(tmp_path, owner, "héllo 世界", delivery_id=delivery_id) == queued
    with pytest.raises(ValueError):
        mailbox.deliver_to_live_owner(tmp_path, owner, "different", delivery_id=delivery_id)
    assert mailbox.claim_pending_delivery(tmp_path, dict(owner, lease_id="other")) is None
    assert mailbox.claim_pending_delivery(tmp_path, dict(owner, live_session_id="other")) is None
    script = (
        "import json,sys; from tools.bot_live_delivery import claim_pending_delivery; "
        "print(json.dumps(claim_pending_delivery(sys.argv[1],json.loads(sys.argv[2]))))"
    )
    children = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path), json.dumps(owner)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True) for _ in range(2)]
    results = []
    for child in children:
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0, err
        results.append(json.loads(out))
    claims = [r for r in results if r is not None]
    assert len(claims) == 1 and claims[0]["message"] == "héllo 世界"
    assert mailbox.read_delivery_result(tmp_path, delivery_id)["status"] == "claimed"
    assert mailbox.claim_pending_delivery(tmp_path, owner) is None
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    receipt = mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="réponse 世界")
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    assert mailbox.read_delivery_result(tmp_path, delivery_id) == receipt
    assert mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="réponse 世界") == receipt
    with pytest.raises(ValueError):
        mailbox.complete_delivery(tmp_path, delivery_id, status=terminal_status, reply="rewrite")
    assert mailbox.deliver_to_live_owner(tmp_path, owner, "héllo 世界", delivery_id=delivery_id) == receipt
    assert mailbox.claim_pending_delivery(tmp_path, owner) is None
    if os.name != "nt":
        for path in (tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME).iterdir():
            assert path.stat().st_mode & 0o077 == 0


def test_cancel_queued_delivery_fences_claim_and_preserves_terminal_result(tmp_path):
    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "first", delivery_id="3" * 32)
    cancelled = mailbox.cancel_queued_delivery(
        tmp_path, queued["delivery_id"], error="owner closed", reason="runtime_offline")
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["error"] == "owner closed"
    assert mailbox.claim_pending_delivery(tmp_path, owner) is None
    assert mailbox.deliver_to_live_owner(tmp_path, owner, "first", delivery_id="3" * 32) == cancelled
    assert mailbox.cancel_queued_delivery(
        tmp_path, queued["delivery_id"], error="owner closed", reason="runtime_offline") == cancelled

    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "second", delivery_id="4" * 32)
    claimed = mailbox.claim_pending_delivery(tmp_path, owner)
    assert claimed is not None
    assert claimed["delivery_id"] == queued["delivery_id"]
    assert mailbox.cancel_queued_delivery(
        tmp_path, queued["delivery_id"], error="owner closed", reason="runtime_offline") == claimed
    settled = mailbox.complete_delivery(tmp_path, queued["delivery_id"], status="settled", reply="PONG")
    assert mailbox.cancel_queued_delivery(
        tmp_path, queued["delivery_id"], error="owner closed", reason="runtime_offline") == settled


@pytest.mark.parametrize("intent_state", ["new", "existing", "raced"])
def test_live_dm_bom_readers_preserve_pinned_intent(tmp_path, monkeypatch, intent_state):
    from pathlib import Path

    from qubit_cli.active_sessions import try_acquire_active_session
    from qubit_state import SessionDB
    from tools import bot_live_delivery as mailbox, bot_mode_dm

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="chat", source="cli")
    db.set_session_title("chat", "Bot Chat")
    lease, refusal = try_acquire_active_session(
        session_id="chat", surface="desktop", config={}, registry_home=tmp_path,
        metadata=dict(live_session_id="live", bot_live_delivery_consumer=True))
    assert refusal is None and lease is not None
    try:
        owner = mailbox.find_canonical_live_owner(tmp_path)
        assert owner is not None
        payload = tmp_path / "message.txt"
        payload.write_bytes("héllo 世界".encode("utf-8-sig"))
        intent_path = Path(str(payload) + ".live.json")
        author = {"id": "bot:coder", "name": "Renée", "is_bot": True}
        intent = dict(owner=owner, message="pinned 世界", delivery_id="d" * 32, author=author)
        encoded = json.dumps(intent, ensure_ascii=False).encode("utf-8-sig")
        if intent_state == "existing":
            intent_path.write_bytes(encoded)
        elif intent_state == "raced":
            real_open = os.open

            def competing_intent(path, flags, *args, **kwargs):
                if Path(path) == intent_path:
                    intent_path.write_bytes(encoded)
                return real_open(path, flags, *args, **kwargs)

            monkeypatch.setattr(os, "open", competing_intent)
        record = bot_mode_dm._admit_live_dm(tmp_path, str(payload), author)
        assert record is not None
        assert record["message"] == ("héllo 世界" if intent_state == "new" else "pinned 世界")
        assert record["owner"] == owner and record["author"] == author
        if intent_state == "new":
            assert not intent_path.read_bytes().startswith(b"\xef\xbb\xbf")
            intent_path.write_bytes(b"\xef\xbb\xbf" + intent_path.read_bytes())
        payload.write_text("must not replace pinned payload", encoding="utf-8")
        assert bot_mode_dm._admit_live_dm(None, str(payload)) == record
        claim = mailbox.claim_pending_delivery(tmp_path, owner)
        assert claim is not None and claim["delivery_id"] == record["delivery_id"]
        assert mailbox.claim_pending_delivery(tmp_path, owner) is None
        intent_path.write_bytes(b"\xef\xbb\xbf{broken")
        assert bot_mode_dm._run_delivery([], str(payload), stdin_file=False, profile_home=tmp_path) == 1
        assert payload.exists()  # Ambiguous admission must retain its evidence, not retry a transport.
    finally:
        lease.release()
        db.close()

def test_fifo_survives_clock_rollback(tmp_path, monkeypatch):
    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    for timestamp, message in ((100, "first"), (90, "second")):
        monkeypatch.setattr(mailbox.time, "time_ns", lambda: timestamp)
        mailbox.deliver_to_live_owner(tmp_path, owner, message)
    assert mailbox.claim_pending_delivery(tmp_path, owner)["message"] == "first"
    assert mailbox.claim_pending_delivery(tmp_path, owner)["message"] == "second"

@pytest.mark.parametrize("capable", [True, False])
def test_only_canonical_capable_owner_receives_across_compression(tmp_path, capable):
    from qubit_state import SessionDB
    from qubit_cli.active_sessions import try_acquire_active_session, transfer_active_session
    from tools import bot_live_delivery as mailbox

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session(session_id="chat", source="cli")
    db.set_session_title("chat", "Bot Chat")
    meta = dict(live_session_id="live", bot_live_delivery_consumer=capable)
    lease, refusal = try_acquire_active_session(session_id="chat", surface="desktop", config={},
                                               registry_home=tmp_path, metadata=meta)
    assert refusal is None
    try:
        owner = mailbox.find_canonical_live_owner(tmp_path)
        if not capable:
            assert owner is None
            return
        assert owner["lease_id"] == lease.lease_id
        queued = mailbox.deliver_to_live_owner(tmp_path, owner, "before compression")
        db.end_session("chat", "compression")
        db.create_session(session_id="tip", source="cli", parent_session_id="chat")
        assert transfer_active_session(lease, session_id="tip", metadata=meta)
        current = mailbox.find_canonical_live_owner(tmp_path)
        assert current["session_id"] == "tip"
        claim = mailbox.claim_pending_delivery(tmp_path, current)
        assert claim["delivery_id"] == queued["delivery_id"]
        assert claim["session_id"] == "chat"
        assert mailbox.claim_pending_delivery(tmp_path, current) is None
    finally:
        lease.release()
        db.close()

def test_delivery_keeps_the_sender_and_refuses_a_different_one_under_the_same_id(tmp_path):
    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat", lease_id="lease", live_session_id="live")
    author = {"id": "bot:coder", "name": "coder", "is_bot": True}
    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "héllo 世界", delivery_id="b" * 32, author=author)
    assert queued["author"] == author
    assert mailbox.deliver_to_live_owner(tmp_path, owner, "héllo 世界", delivery_id="b" * 32, author=author) == queued
    with pytest.raises(ValueError):
        mailbox.deliver_to_live_owner(tmp_path, owner, "héllo 世界", delivery_id="b" * 32, author={**author, "id": "bot:other"})
    assert "author" not in mailbox.deliver_to_live_owner(tmp_path, owner, "no sender", delivery_id="c" * 32)

@pytest.mark.platforms("posix")
@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0, reason='needs POSIX file permissions for an unreadable ticket')
def test_unreadable_ticket_does_not_wedge_bulk_scans(tmp_path, caplog):
    import logging

    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "readable", delivery_id="d" * 32)
    root = tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME
    # A real admission that later turns unreadable: its sequence must survive the skip.
    hidden = mailbox.deliver_to_live_owner(tmp_path, owner, "hidden", delivery_id="e" * 32)
    (root / f"{'e' * 32}.json").chmod(0)
    corrupt = root / f"{'1' * 32}.json"
    corrupt.write_text("{not json", encoding="utf-8")
    (root / f"{'2' * 32}.json").write_bytes(b"\xff\xfe\x00garbage")  # invalid UTF-8, not just bad JSON
    with caplog.at_level(logging.WARNING, logger="tools.bot_live_delivery"):
        # Sender side: admission of a fresh id must survive the sequence sweep.
        admitted = mailbox.deliver_to_live_owner(tmp_path, owner, "second", delivery_id="f" * 32)
        # Receiver side: every readable queued ticket must still be claimed, in order.
        assert mailbox.claim_pending_delivery(tmp_path, owner)["delivery_id"] == queued["delivery_id"]
        assert mailbox.claim_pending_delivery(tmp_path, owner)["delivery_id"] == admitted["delivery_id"]
        for _ in range(10):  # the idle poller rescans twice a second
            assert mailbox.claim_pending_delivery(tmp_path, owner) is None
    assert admitted["status"] == "queued"
    assert admitted["sequence"] > hidden["sequence"] > queued["sequence"]
    denied = [record for record in caplog.records
              if record.message.startswith(f"bot_live_delivery: skipping unreadable ticket {'e' * 32}.json")
              and "Permission denied" in record.message]
    assert len(denied) == 1, "one persistent bad ticket must warn once per process, not per scan"

@pytest.mark.platforms("posix")
@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0, reason='needs POSIX file permissions for an unreadable ticket')
def test_unreadable_ticket_keeps_exact_id_reads_fail_closed(tmp_path):
    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    unreadable = tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME / f"{'e' * 32}.json"
    unreadable.parent.mkdir(parents=True, exist_ok=True)
    unreadable.write_text('{"status": "queued"}', encoding="utf-8")
    unreadable.chmod(0)
    # Uninspectable is not absent: an exact-id retry must fail closed instead of
    # minting a fresh receipt that overwrites the possibly-live one (#109820).
    with pytest.raises(PermissionError):
        mailbox.deliver_to_live_owner(tmp_path, owner, "same id", delivery_id="e" * 32)
    with pytest.raises(PermissionError):
        mailbox.read_delivery_result(tmp_path, "e" * 32)

def test_non_dict_ticket_is_skipped_by_scans_and_fails_exact_id_reads_closed(tmp_path, caplog):
    import logging

    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "readable", delivery_id="d" * 32)
    bad = tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME / f"{'e' * 32}.json"
    bad.write_text('"oops"', encoding="utf-8")  # parses, but is not a record
    with caplog.at_level(logging.WARNING, logger="tools.bot_live_delivery"):
        admitted = mailbox.deliver_to_live_owner(tmp_path, owner, "second", delivery_id="f" * 32)
        assert mailbox.claim_pending_delivery(tmp_path, owner)["delivery_id"] == queued["delivery_id"]
        assert mailbox.claim_pending_delivery(tmp_path, owner)["delivery_id"] == admitted["delivery_id"]
        assert mailbox.claim_pending_delivery(tmp_path, owner) is None
    assert sum(r.message.startswith(f"bot_live_delivery: skipping unreadable ticket {'e' * 32}.json")
               for r in caplog.records) == 1
    # Malformed is not absent: exact-id reads fail closed rather than overwrite the receipt.
    with pytest.raises(ValueError):
        mailbox.deliver_to_live_owner(tmp_path, owner, "same id", delivery_id="e" * 32)
    with pytest.raises(ValueError):
        mailbox.read_delivery_result(tmp_path, "e" * 32)
    assert bad.read_text(encoding="utf-8") == '"oops"'

def test_schema_damaged_ticket_does_not_wedge_bulk_scans(tmp_path, caplog):
    """Valid JSON that lost a field must degrade like corrupt JSON: skipped, warned once, never raised."""
    import logging

    from tools import bot_live_delivery as mailbox

    owner = dict(profile_home=str(tmp_path.resolve()), session_id="chat",
                 lease_id="lease", live_session_id="live")
    queued = mailbox.deliver_to_live_owner(tmp_path, owner, "healthy", delivery_id="d" * 32)
    root = tmp_path / "runtime" / mailbox.DELIVERY_DIR_NAME
    damaged = {
        root / f"{'a' * 32}.json": "{}",
        root / f"{'b' * 32}.json": json.dumps(dict(
            delivery_id="b" * 32, id="b" * 32, status="queued", created_at=1,
            sequence=1, owner=None, message="owner lost")),
        root / f"{'c' * 32}.json": json.dumps(dict(
            delivery_id="c" * 32, id="c" * 32, status="queued", created_at=2,
            sequence="old", owner=owner, message="sequence lost", **owner)),
        root / f"{'e' * 32}.json": json.dumps(dict(
            delivery_id="../wrong", id="../wrong", status="queued", created_at=3,
            sequence=3, owner=owner, message="id lost", **owner)),
        root / f"{'1' * 32}.json": json.dumps(dict(
            delivery_id="1" * 32, id="1" * 32, status=[], created_at=4,
            sequence=4, owner=owner, message="status lost", **owner)),
    }
    for path, contents in damaged.items():
        path.write_text(contents, encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="tools.bot_live_delivery"):
        admitted = mailbox.deliver_to_live_owner(tmp_path, owner, "also healthy", delivery_id="f" * 32)
        assert mailbox.claim_pending_delivery(tmp_path, owner)["delivery_id"] == queued["delivery_id"]
        assert mailbox.claim_pending_delivery(tmp_path, owner)["delivery_id"] == admitted["delivery_id"]
        for _ in range(3):
            assert mailbox.claim_pending_delivery(tmp_path, owner) is None
    assert admitted["sequence"] == queued["sequence"] + 1
    assert {path: path.read_text(encoding="utf-8") for path in damaged} == damaged
    skipped = [r.message for r in caplog.records if r.message.startswith("bot_live_delivery: skipping unreadable ticket")]
    assert len(skipped) == len(damaged), "each damaged ticket warns once per process, not per scan"
