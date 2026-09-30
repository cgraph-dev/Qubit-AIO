"""Packet 8U Wave 3 — tri-surface wiring: voice + CLI + dashboard.

Proves the five new OpenClaw-delta backends are reachable through all three
Qubit surfaces on the SAME backend function:

* ``voicebridge.voice_commands`` (spoken registry: resolve -> execute/confirm);
* ``voicebridge.cli`` (``qubit transfers|recall|documents|ask|shares``);
* ``voicebridge.qubit_compat`` (dashboard HTTP compat routes), including the
  public ``GET /share/{id}`` view authenticated by the SHARE token alone.

Every assertion is a real read/mutation over isolated temp state: no skips,
no xfail, no network. The voice paths used in the fixtures are lowercase
because the spoken normalizer lowercases utterances (paths in tests are
lowercase tmp dirs).
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

from voicebridge import active_memory as AM
from voicebridge import cli as CLI
from voicebridge import doc_extract as DE
from voicebridge import file_transfer as FT
from voicebridge import qubit_compat as HC
from voicebridge import node_host as NH
from voicebridge import projects as PJ
from voicebridge import question_prompt as QP
from voicebridge import session_share as SS
from voicebridge import voice_commands as VC
from voicebridge.memory import MemoryStore


# ------------------------------------------------------------------ helpers
def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="wave3"):
    return VC.make_ctx(cfg, session=session, source="user")


def _voice(ctx, text, confirm=False):
    res = VC.resolve(text)
    assert res.kind == VC.MATCH, (text, res.kind, getattr(res.entry, "id", None))
    out = VC.execute(ctx, res)
    if confirm:
        assert out.kind == VC.NEEDS_CONFIRM, (text, out.kind, out.speech)
        out = VC.confirm_pending(ctx, out.confirmation_id)
    assert out.kind == VC.EXECUTED, (text, out.kind, out.speech)
    return out.receipt["result"]


def _cli(cfg, fn, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn(cfg, SimpleNamespace(**kw))
    assert rc == 0, (fn.__name__, rc, buf.getvalue()[:400])
    return json.loads(buf.getvalue())


def _hc(cfg, method, path, q=None, b=None):
    code, out = HC.dispatch(cfg, method, path, q or {}, b or {})
    assert code == 200, (method, path, code, out)
    assert out.get("ok") is True, (method, path, out)
    return out


def _strip(out, *keys):
    return {k: v for k, v in out.items() if k not in keys}


def _project_with_file(tmp_path, cfg):
    root = tmp_path / "wave3" / "site"
    root.mkdir(parents=True, exist_ok=True)
    (root / "notes.txt").write_text("hello wave3\n")
    created = PJ.create(cfg, "Site", [str(root)], requested_by="cli:user",
                        confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return root


def _local_node(cfg):
    out = NH.enroll(cfg, name="local", endpoint="http://127.0.0.1:8443/",
                    capabilities=["file.transfer"], confirmed=True)
    assert out.get("state") == "OK", out
    return out["node"]["id"]


def _seed_receipt(cfg, rid="rec-wave3"):
    from pathlib import Path

    state = Path(cfg.state_dir())
    state.mkdir(parents=True, exist_ok=True)
    (state / "actions.json").write_text(json.dumps(
        {"schema": 1,
         "receipts": {rid: {"receipt_id": rid, "action_id": "act.send",
                            "state": "OK", "code": "OK",
                            "runtime": "qubit-broker", "created_at": 1.0,
                            "detail": "wave3 invoice sent"}},
         "audit": []}))
    return rid


# ---------------------------------------------------------- file_transfer
def test_file_transfer_tri_surface(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = _project_with_file(tmp_path, cfg)
    node = _local_node(cfg)
    ctx = _ctx(cfg)

    direct = FT.list_directory(cfg, node, str(root))
    assert direct["state"] == "OK"

    voice = _voice(ctx, "qubit, list node files '" + str(root) + "'")
    assert voice["file_transfer"] == direct

    cli_out = _cli(cfg, CLI._cmd_transfers, transfers_command="list",
                   path=str(root), node=node, limit=200)
    assert cli_out == direct

    hc = _hc(cfg, "GET", "/api/transfers/list",
             {"path": str(root), "node": node})
    assert _strip(hc, "ok", "qubit_counterpart") == direct

    # confirm-tier fetch executes the same bounded chunk on all three surfaces
    direct_fetch = FT.fetch(cfg, node, str(root / "notes.txt"), confirmed=True)
    voice_fetch = _voice(ctx, "qubit, fetch file chunk '"
                         + str(root / "notes.txt") + "'", confirm=True)
    assert voice_fetch["file_transfer"] == direct_fetch
    cli_fetch = _cli(cfg, CLI._cmd_transfers, transfers_command="fetch",
                     path=str(root / "notes.txt"), node=node, offset=0,
                     max_bytes=None, confirm=True)
    assert cli_fetch == direct_fetch
    hc_fetch = _hc(cfg, "POST", "/api/transfers/fetch", {},
                   {"path": str(root / "notes.txt"), "node": node,
                    "confirm": True})
    assert _strip(hc_fetch, "ok", "qubit_counterpart") == direct_fetch

    # raw file bytes can never be spoken: a real typed refusal, no payload
    guard = _voice(ctx, "qubit, read raw file bytes")
    assert guard["state"] == "refused"
    assert guard["code"] == "VOICE_BYTES_UNSUPPORTED"
    assert "guidance" in guard


def test_file_transfer_plan_and_status_surfaces(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = _project_with_file(tmp_path, cfg)
    node = _local_node(cfg)
    ctx = _ctx(cfg)

    assert _voice(ctx, "qubit, transfer status")["file_transfer"] == \
        FT.status(cfg)
    assert _cli(cfg, CLI._cmd_transfers, transfers_command="status") == \
        FT.status(cfg)
    assert _strip(_hc(cfg, "GET", "/api/transfers/status"), "ok",
                  "qubit_counterpart") == FT.status(cfg)

    planned = _voice(ctx, "qubit, plan file transfer '"
                     + str(root / "notes.txt") + "'", confirm=True)
    assert planned["file_transfer"]["state"] == "OK"
    tid = planned["file_transfer"]["transfer_id"]
    resumed = _voice(ctx, "qubit, resume file transfer " + tid)
    assert resumed["file_transfer"]["state"] == "OK"


# ---------------------------------------------------------- active memory
def test_active_memory_tri_surface(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    enabled = AM.policy_set(cfg, enabled=True, min_score=0.0, max_items=5,
                            session_scoped=False, confirmed=True)
    assert enabled["state"] == "OK"
    store = MemoryStore(AM.memory_db_path(cfg))
    store.remember("wave3 invoice fact", scope="default", confidence=0.9)
    store.close()
    ctx = _ctx(cfg)

    direct = AM.status(cfg)
    assert _voice(ctx, "qubit, active recall status")["active_memory"] == direct
    assert _cli(cfg, CLI._cmd_recall, recall_command="status") == direct
    assert _strip(_hc(cfg, "GET", "/api/recall/status"), "ok") == direct

    direct_recall = AM.recall(cfg, "wave3 invoice")
    assert _voice(ctx, "qubit, recall memories wave3 invoice"
                 )["active_memory"] == direct_recall
    assert _strip(_hc(cfg, "GET", "/api/recall", {"query": "wave3 invoice"}),
                  "ok", "qubit_counterpart") == direct_recall

    # an honest EMPTY recall is a 200 read, never a fabricated failure/403
    empty = _hc(cfg, "GET", "/api/recall", {"query": "zzz-no-match-zzz"})
    assert empty["state"] == "EMPTY" and empty["ok"] is True


def test_active_memory_policy_and_escalate_tri_surface(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    assert AM.policy_set(cfg, enabled=True, confirmed=True)["state"] == "OK"
    store = MemoryStore(AM.memory_db_path(cfg))
    rec = store.remember("escalate me", scope="default", confidence=0.4,
                         tier="recall")
    store.close()
    ctx = _ctx(cfg)

    # confirm-tier policy update via CLI and dashboard
    cli_pol = _cli(cfg, CLI._cmd_recall, recall_command="policy-set",
                   enable=False, disable=False, min_score=None, max_items=3,
                   session_scoped=False, global_scope=True, confirm=True)
    assert cli_pol["state"] == "OK" and cli_pol["policy"]["max_items"] == 3
    hc_pol = _hc(cfg, "POST", "/api/recall/policy", {},
                 {"confirm": True, "enabled": True, "max_items": 4})
    assert hc_pol["state"] == "OK" and hc_pol["policy"]["max_items"] == 4

    # voice-escalate the same memory the module escalates
    voice_esc = _voice(ctx, f"qubit, escalate memory {rec['id']} reason important",
                       confirm=True)
    assert voice_esc["active_memory"]["state"] == "OK"
    assert voice_esc["active_memory"]["tier"] == "working"


# ---------------------------------------------------------- doc extract
def test_doc_extract_tri_surface(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    root = _project_with_file(tmp_path, cfg)
    target = str(root / "notes.txt")
    ctx = _ctx(cfg)

    direct_status = DE.status(cfg)
    assert _voice(ctx, "qubit, document status")["documents"] == direct_status
    assert _cli(cfg, CLI._cmd_documents,
                documents_command="status") == direct_status
    assert _strip(_hc(cfg, "GET", "/api/documents/status"), "ok") == \
        direct_status
    assert _cli(cfg, CLI._cmd_documents,
                documents_command="kinds") == DE.kinds(cfg)
    assert _strip(_hc(cfg, "GET", "/api/documents/kinds"), "ok") == \
        DE.kinds(cfg)

    direct = DE.extract(cfg, target, confirmed=True)
    voice = _voice(ctx, "qubit, extract document '" + target + "'",
                   confirm=True)
    assert voice["documents"] == direct
    cli_out = _cli(cfg, CLI._cmd_documents, documents_command="extract",
                   path=target, max_chars=None, page=None, confirm=True)
    assert cli_out == direct
    hc_out = _hc(cfg, "POST", "/api/documents/extract", {},
                 {"path": target, "confirm": True})
    assert _strip(hc_out, "ok", "qubit_counterpart") == direct


# ------------------------------------------------------- question prompts
def test_question_prompt_tri_surface(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)

    direct_status = QP.status(cfg)
    assert _voice(ctx, "qubit, question status")["questions"] == direct_status
    assert _cli(cfg, CLI._cmd_ask, ask_command="status") == direct_status
    assert _strip(_hc(cfg, "GET", "/api/ask/status"), "ok") == direct_status

    voice_ask = _voice(ctx, "qubit, ask a question which branch",
                       confirm=True)["questions"]
    assert voice_ask["state"] == "OK"
    assert voice_ask["prompt_id"].startswith("qp")
    assert voice_ask["questions"][0]["text"] == "which branch"

    # CLI + dashboard create the same contract shape
    cli_ask = _cli(cfg, CLI._cmd_ask, ask_command="ask",
                   question="deploy now", session="cli", confirm=True)
    assert cli_ask["state"] == "OK" and cli_ask["prompt_id"].startswith("qp")
    hc_ask = _hc(cfg, "POST", "/api/ask", {},
                 {"question": "hc question", "confirm": True})
    assert hc_ask["state"] == "OK" and hc_ask["prompt_id"].startswith("qp")

    pending = _cli(cfg, CLI._cmd_ask, ask_command="pending")
    assert len(pending["pending"]) == 3
    assert _strip(_hc(cfg, "GET", "/api/ask/pending"), "ok")["pending"]

    # the structured-answer op is impossible to speak (nested JSON)
    guard = _voice(ctx, "qubit, answer with structured answers")
    assert guard["state"] == "refused"
    assert guard["code"] == "VOICE_NESTED_UNSUPPORTED"

    # CLI answers the single free-text contract (q1) with real JSON
    answered = _cli(cfg, CLI._cmd_ask, ask_command="answer",
                    prompt_id=voice_ask["prompt_id"],
                    answers=json.dumps({"q1": "main"}), answers_file=None)
    assert answered["state"] == "OK"
    assert answered["answers"] == {"q1": "main"}


# ----------------------------------------------------------- session share
def test_session_share_tri_surface(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rid = _seed_receipt(cfg)
    ctx = _ctx(cfg)

    direct_status = SS.status(cfg)
    assert _voice(ctx, "qubit, share status")["session_share"] == direct_status
    assert _cli(cfg, CLI._cmd_shares, shares_command="status") == direct_status
    assert _strip(_hc(cfg, "GET", "/api/shares/status"), "ok",
                  "qubit_counterpart") == direct_status

    # voice publish (confirmed) -> token shown once
    voice_pub = _voice(ctx, f"qubit, share session {rid}", confirm=True)
    published = voice_pub["session_share"]
    assert published["state"] == "OK" and published["token"]
    share_id, token = published["share"]["id"], published["token"]

    # dashboard list (redacted) and public resolve via the SHARE token only
    hc_list = _hc(cfg, "GET", "/api/sessions/shares")
    assert share_id in [s["id"] for s in hc_list["shares"]]
    assert token not in json.dumps(hc_list)

    resolved = _hc(cfg, "GET", f"/share/{share_id}", {"token": token})
    assert resolved["state"] == "OK"
    assert resolved["transcript"]["session_id"] == rid
    assert token not in json.dumps(resolved)

    # CLI list sees the same record; CLI publish works through the module
    cli_list = _cli(cfg, CLI._cmd_shares, shares_command="list")
    assert share_id in [s["id"] for s in cli_list["shares"]]
    cli_pub = _cli(cfg, CLI._cmd_shares, shares_command="publish",
                   session_id=rid, ttl_s=120, confirm=True)
    assert cli_pub["state"] == "OK" and cli_pub["token"]

    # dashboard publish (dashboard-token auth lives at the webui layer)
    hc_pub = _hc(cfg, "POST", f"/api/sessions/{rid}/share", {},
                 {"confirm": True})
    assert hc_pub["share"]["id"].startswith("shr")


def test_session_share_revoke_purge_and_guards(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    rid = _seed_receipt(cfg, "rec-wave3b")
    ctx = _ctx(cfg)

    published = SS.publish(cfg, rid, ttl_s=60, confirmed=True)
    share_id, token = published["share"]["id"], published["token"]

    # revoke through voice (confirmed) and verify the public view dies typed
    revoked = _voice(ctx, "qubit, revoke share " + share_id, confirm=True)
    assert revoked["session_share"]["changed"] is True
    code, out = HC.dispatch(cfg, "GET", f"/share/{share_id}", {"token": token},
                            {})
    assert code == 403 and out["code"] == SS.REVOKED_CODE, (code, out)

    # purge via CLI requires confirmation, then runs the same module after
    # an expired record is published and time is advanced
    short = SS.publish(cfg, rid, ttl_s=1, confirmed=True)
    future = short["expires_at"] + 1
    monkey = SS._now
    try:
        SS._now = lambda: future
        cli_purge = _cli(cfg, CLI._cmd_shares, shares_command="purge",
                         confirm=True)
        assert cli_purge["state"] == "OK"
        assert cli_purge["purged"] >= 1
    finally:
        SS._now = monkey

    # the share-token resolve can never be spoken (bearer secret)
    guard = _voice(ctx, "qubit, open a shared session")
    assert guard["state"] == "refused"
    assert guard["code"] == "PASSPHRASE_NEVER_SPOKEN"
