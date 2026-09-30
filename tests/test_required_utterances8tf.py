"""Required natural-language utterances run the real pipeline.

Every example the product brief mandates resolves through the genuine
voice path (resolve -> execute -> the same backend module the CLI and
dashboard use), confirm/cancel dialogue acts classify through the real
intent classifier, and each new voice action has CLI + dashboard Tier-1
parity. Hermetic: isolated XDG/state, no network, no audio, no model.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _ctx(cfg, session="voice", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


def _seed_project(cfg, tmp_path, name="Website Redesign"):
    from voicebridge import projects as PJ

    folder = tmp_path / "work" / "site"
    folder.mkdir(parents=True, exist_ok=True)
    created = PJ.create(cfg, name, [str(folder)], requested_by="cli:user",
                        confirmed=True)
    slug = created["project"]["slug"]
    PJ.use(cfg, slug, requested_by="cli:user", confirmed=True)
    return slug


def _seed_task(cfg, title="Summarize my notes", board="main"):
    from voicebridge import kanban as KB

    KB.init(cfg)
    try:
        KB.boards_create(cfg, board, requested_by="cli:user", confirmed=True)
    except Exception:
        pass
    return KB.create(cfg, title, board=board, requested_by="cli:user",
                     confirmed=True)


def _seed_report(cfg, report_id="report_voice1"):
    import hashlib

    from voicebridge.communications.contracts import (
        CommunicationItem, EvidenceAssetRecord, EvidenceStatus, Platform,
        ReadState, ReportManifest, ReportStatus,
    )
    from voicebridge.communications.reports import ReportStore

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
        summary="Synthetic voice report", report_id=report_id)
    data = b"\x00" * 512
    asset = EvidenceAssetRecord.create(
        report_id=report_id, item_identity=item.identity,
        mime="image/webp", byte_count=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        redaction_status=EvidenceStatus.AVAILABLE)
    store.render(manifest, [item], [asset],
                 asset_bytes={asset.asset_id: data})
    return store


# ------------------------------------------------------- resolution (9/9)
REQUIRED = [
    ("Qubit, show my projects.", "projects_list"),
    ("Qubit, open my active project.", "projects_active"),
    ("Qubit, list my sessions.", "sessions"),
    ("Qubit, inspect the current task.", "kanban_current"),
    ("Qubit, run this task.", "task_run_current"),
    ("Qubit, show my reports.", "reports_list"),
    ("Qubit, check the browser.", "browser_status"),
    ("Qubit, show available capabilities.", "capabilities_list"),
    ("Qubit, open the dashboard.", "webui_status"),
]


@pytest.mark.parametrize("text,entry_id", REQUIRED)
def test_required_utterance_resolves(text, entry_id):
    from voicebridge import voice_commands as VC

    res = VC.resolve(text)
    assert res.kind == VC.MATCH, (text, res.kind, res.speech)
    assert res.entry.id == entry_id, (text, res.entry.id)


def test_new_entries_examples_resolve_to_self():
    from voicebridge import voice_commands as VC

    for entry_id in ("projects_active", "kanban_current",
                     "task_run_current", "capabilities_list",
                     "reports_list"):
        entry = VC.REGISTRY[entry_id]
        assert entry.examples, entry_id
        for example in entry.examples:
            res = VC.resolve(example)
            assert res.kind == VC.MATCH, (example, res.kind)
            assert res.entry.id == entry_id, (example, res.entry.id)


# ------------------------------------------------------------- execution
def test_open_my_active_project_executes(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    slug = _seed_project(cfg, tmp_path)
    out = VC.execute(_ctx(cfg), VC.resolve("Qubit, open my active project."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert out.receipt["result"]["project"]["slug"] == slug
    assert "Website Redesign" in out.speech


def test_open_my_active_project_empty(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    out = VC.execute(_ctx(cfg), VC.resolve("Qubit, open my active project."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert out.receipt["result"]["state"] == "EMPTY"
    assert out.receipt["result"]["code"] == "NO_ACTIVE_PROJECT"
    assert "no active project" in out.speech.lower()


def test_inspect_current_task_executes(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    created = _seed_task(cfg)
    out = VC.execute(_ctx(cfg),
                     VC.resolve("Qubit, inspect the current task."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert out.receipt["result"]["task"]["id"] == created["task"]["id"]
    assert "Summarize my notes" in out.speech


def test_inspect_current_task_empty(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    out = VC.execute(_ctx(cfg),
                     VC.resolve("Qubit, inspect the current task."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert out.receipt["result"]["code"] == "NO_OPEN_TASKS"
    assert "no open tasks" in out.speech.lower()


def test_show_available_capabilities_executes(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    out = VC.execute(
        _ctx(cfg), VC.resolve("Qubit, show available capabilities."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    caps = out.receipt["result"]["capabilities"]
    assert caps["total"] > 0 and caps["implemented"] > 0
    assert "chat" in caps["available"]
    assert "capabilities available" in out.speech


def test_check_browser_executes_typed(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    out = VC.execute(_ctx(cfg), VC.resolve("Qubit, check the browser."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    state = out.receipt["result"]["browser"]["state"]
    assert state in ("OK", "UNAVAILABLE"), state


def test_open_dashboard_executes_typed(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    out = VC.execute(_ctx(cfg), VC.resolve("Qubit, open the dashboard."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    dash = out.receipt["result"]["dashboard"]
    assert dash["state"] in ("READY", "UNAVAILABLE"), dash
    assert dash["url"].startswith("http://127.0.0.1:")
    assert dash["token_in_url"] is False


def test_show_my_reports_executes(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_report(cfg)
    out = VC.execute(_ctx(cfg), VC.resolve("Qubit, show my reports."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    body = out.receipt["result"]["reports"]
    assert body["total"] == 1
    assert body["reports"][0]["report_id"] == "report_voice1"
    assert "report_voice1" in out.speech


def test_show_my_reports_empty(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    out = VC.execute(_ctx(cfg), VC.resolve("Qubit, show my reports."))
    assert out.kind == VC.EXECUTED, (out.kind, out.speech)
    assert out.receipt["result"]["reports"]["total"] == 0
    assert "no reports" in out.speech.lower()


def test_reports_voice_matches_store_index(tmp_path, monkeypatch):
    """Differential: the voice projection lists the same bundle ids in
    the same newest-first order as ReportStore.index()."""
    from voicebridge import voice_commands as VC
    from voicebridge.communications.reports import ReportStore

    cfg = _cfg(tmp_path, monkeypatch)
    store = _seed_report(cfg, report_id="report_aaa")
    _seed_report(cfg, report_id="report_zzz")
    expected = [e["report_id"]
                for e in store.index()["reports"]]
    assert len(expected) == 2
    out = VC.execute(_ctx(cfg), VC.resolve("qubit, show my reports"))
    got = [e["report_id"]
           for e in out.receipt["result"]["reports"]["reports"]]
    assert got == expected, (got, expected)
    assert set(got) == {"report_aaa", "report_zzz"}


def test_run_this_task_stages_confirmation(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_task(cfg)
    res = VC.resolve("Qubit, run this task.")
    assert res.kind == VC.MATCH and res.entry.id == "task_run_current"
    out = VC.execute(_ctx(cfg), res)
    assert out.kind == VC.NEEDS_CONFIRM, (out.kind, out.speech)
    assert out.confirmation_id
    # Agent explicitly disabled hermetic: typed unavailable, never fake OK.
    cfg.data.setdefault("agent", {})["enabled"] = False
    out2 = VC.confirm_pending(_ctx(cfg), out.confirmation_id)
    assert out2.kind == VC.EXECUTED, (out2.kind, out2.speech)
    task = out2.receipt["result"]["task"]
    assert task["state"] == "UNAVAILABLE", task
    assert task["code"] in ("AGENT_DISABLED", "EMERGENCY_STOP"), task


def test_run_this_task_empty_is_typed(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    out = VC.execute(_ctx(cfg), VC.resolve("Qubit, run this task."))
    assert out.kind == VC.NEEDS_CONFIRM
    out2 = VC.confirm_pending(_ctx(cfg), out.confirmation_id)
    assert out2.kind == VC.EXECUTED
    assert out2.receipt["result"]["task"]["code"] == "NO_OPEN_TASKS"


def test_run_this_task_cancel_denies(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_task(cfg)
    out = VC.execute(_ctx(cfg), VC.resolve("Qubit, run this task."))
    assert out.kind == VC.NEEDS_CONFIRM
    denied = VC.cancel_pending(_ctx(cfg), out.confirmation_id)
    assert denied.kind == VC.EXECUTED  # cancel itself executes
    # The single-use journal record is gone: confirming is refused.
    gone = VC.confirm_pending(_ctx(cfg), out.confirmation_id)
    assert gone.kind == VC.FAILED
    assert "gone" in gone.speech
    again = VC.confirm_pending(_ctx(cfg))
    assert again.speech == "There's nothing to confirm."


# ------------------------------------------------- confirm/cancel acts
def _classifier(pending=False, busy=False):
    from voicebridge.intent.classifier import Classifier
    from voicebridge.intent.state import ConfirmationState, ConversationState

    cfg = {"llm_classifier": False, "classify_timeout_s": 1.0}
    confirmations = ConfirmationState()
    if pending:
        confirmations.request("do x", {"capability": "voice.x"}, "test")
    return Classifier(cfg, confirmations, ConversationState(max_turns=4),
                      busy_fn=(lambda: True) if busy else None)


def test_confirm_act_with_pending():
    assert _classifier(pending=True).classify("Qubit, confirm.").kind \
        == "confirm"


def test_cancel_act_with_pending():
    assert _classifier(pending=True).classify("Qubit, cancel that.").kind \
        == "cancel"


def test_cancel_act_while_busy():
    assert _classifier(busy=True).classify("Qubit, cancel that.").kind \
        == "cancel"


def test_confirm_cancel_idle_are_not_dialogue_acts():
    # Nothing pending and nothing running: honestly not confirm/cancel
    # (the turns stay conversational instead of faking a cancellation).
    idle = _classifier()
    assert idle.classify("Qubit, confirm.").kind == "conversation"
    assert idle.classify("Qubit, cancel that.").kind in (
        "conversation", "clarify")


# ------------------------------------------------------------- CLI parity
def test_cli_project_active(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)
    slug = _seed_project(cfg, tmp_path)

    class _A:
        project_command = "active"

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_project(cfg, _A()) == 0
    body = json.loads(buf.getvalue())
    assert body["project"]["slug"] == slug


def test_cli_kanban_current(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)
    created = _seed_task(cfg)

    class _A:
        kanban_command = "current"
        confirm = False
        dry_run = False

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_kanban(cfg, _A()) == 0
    body = json.loads(buf.getvalue())
    assert body["task"]["id"] == created["task"]["id"]
    assert body["current"] is True


def test_cli_capability_list(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_capability("list", None, None) == 0
    assert "chat" in buf.getvalue().split()


def test_cli_resolve_task_text(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, **kw):
            self.__dict__.update(**{"text": [], "current": False, **kw})

    text, err, rc = CLI._resolve_task_text(cfg, _A(text=["do", "x"]))
    assert (text, err, rc) == ("do x", None, 0)
    _, err, rc = CLI._resolve_task_text(cfg, _A())
    assert rc == 2 and err["code"] == "BAD_ARGS"
    _, err, rc = CLI._resolve_task_text(
        cfg, _A(text=["x"], current=True))
    assert rc == 2 and err["code"] == "BAD_ARGS"
    _, err, rc = CLI._resolve_task_text(cfg, _A(current=True))
    assert rc == 2 and err["code"] == "NO_OPEN_TASKS"
    _seed_task(cfg, title="Current thing")
    text, err, rc = CLI._resolve_task_text(cfg, _A(current=True))
    assert rc == 0 and err is None
    assert "Current thing" in text


def test_cli_task_parser_accepts_current():
    from voicebridge.cli import build_parser

    args = build_parser().parse_args(["task", "--current"])
    assert args.current is True and args.text == []


# ------------------------------------------------------- dashboard parity
@pytest.mark.parametrize("op_id", ["projects_active", "kanban_current",
                                   "capabilities_list", "reports_list",
                                   "browser_status", "webui_status"])
def test_dashboard_tier1_runs_new_ops(tmp_path, monkeypatch, op_id):
    from voicebridge import dashboard_ops as D

    cfg = _cfg(tmp_path, monkeypatch)
    if op_id == "projects_active":
        _seed_project(cfg, tmp_path)
    if op_id == "kanban_current":
        _seed_task(cfg)
    if op_id == "reports_list":
        _seed_report(cfg)
    out = D.run_registry_op(cfg, op_id, {}, actor="dashboard:user",
                            session="dashboard")
    assert out["state"] == "executed", (op_id, out)
    assert out["speech"], op_id


def test_dashboard_run_this_task_confirms(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as D

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_task(cfg)
    cfg.data.setdefault("agent", {})["enabled"] = False
    out = D.run_registry_op(cfg, "task_run_current", {},
                            actor="dashboard:user", session="dashboard")
    assert out["state"] == "needs-confirm", out
    assert out["confirmation_id"], out
    confirmed = D.confirm_registry_op(cfg, out["confirmation_id"],
                                      actor="dashboard:user",
                                      session="dashboard")
    assert confirmed["state"] == "executed", confirmed
    task = confirmed["receipt"]["result"]["task"]
    assert task["state"] == "UNAVAILABLE", task


# ------------------------------------------------- architecture pin
def test_voice_commands_respect_8a_boundary():
    text = (Path(__file__).resolve().parents[1] / "voicebridge" /
            "voice_commands.py").read_text()
    assert "voicebridge.communications" not in text
    assert "from .communications" not in text
    assert "from voicebridge.communications" not in text
