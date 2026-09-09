"""Packet 8T-F — voice command registry: full pipeline behavioral tests.

Proves every implemented capability is voice-addressable (resolution +
execution through policy gates), ambiguous/incomplete/unknown inputs are
handled honestly, confirmations are single-use expiring shared-journal
records, e-stop/source/redaction/cancellation hold, and the orchestrator
branch never hijacks non-command turns.
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


# ------------------------------------------------- coverage + schema
def test_registry_covers_every_implemented_row():
    from voicebridge import voice_commands as VC

    par = json.loads((Path(VC.__file__).resolve().parents[1] / "docs" /
                      "PARITY_HERMES.json").read_text())
    mat = json.loads((Path(VC.__file__).resolve().parents[1] / "docs" /
                      "MATRIX_8T_F.json").read_text())
    covered = set()
    for entry in VC.REGISTRY.values():
        covered.update(entry.parity)
    need_par = {r["cmd"] for r in par["rows"] if r["verdict"] == "IMPLEMENTED"}
    assert (need_par - covered) <= set(VC.FALLBACK_ROWS), \
        f"uncovered parity rows: {sorted((need_par - covered) - set(VC.FALLBACK_ROWS))}"
    need_mat = {r["id"] for r in mat["rows"]
                if r.get("verdict") in ("REUSE", "ADAPT")}
    assert need_mat <= set(VC.MATRIX_COVERAGE), \
        f"unmapped matrix rows: {sorted(need_mat - set(VC.MATRIX_COVERAGE))}"
    for matrix_id, entry_id in VC.MATRIX_COVERAGE.items():
        assert entry_id in VC.REGISTRY, f"{matrix_id} -> missing {entry_id}"


def test_fallback_rows_are_all_chat_flags():
    from voicebridge import voice_commands as VC

    assert "chat" in VC.FALLBACK_ROWS
    assert all(r.startswith(("-", "--")) or r == "worktree/--worktree"
               for r in VC.FALLBACK_ROWS if r != "chat")


def test_entry_schema_hygiene():
    from voicebridge import voice_commands as VC

    assert len(VC.REGISTRY) >= 80, "registry must span every capability"
    for entry in VC.REGISTRY.values():
        assert entry.backend in VC._BACKENDS, f"{entry.id}: unwired backend"
        assert entry.aliases and entry.examples and entry.help, entry.id
        assert entry.scope, entry.id
        assert isinstance(entry.confirm, bool)
        for alias in entry.aliases:
            assert alias == alias.lower().strip(), (entry.id, alias)


def test_no_secret_shaped_arguments():
    from voicebridge import voice_commands as VC

    banned = ("passphrase", "password", "secret", "token", "api_key", "cookie")
    for entry in VC.REGISTRY.values():
        for arg in entry.args:
            assert arg.name.lower() not in banned, (entry.id, arg.name)


MUTATING_ACTIONS = {
    "kanban": {"create", "claim", "complete", "comment"},
    "projects": {"create", "use", "archive"},
    "skills": {"enable", "disable"},
    "checkpoints": {"create", "prune"},
    "hooks": {"fire"},
    "themes": {"set"},
    "memory": {"remember", "forget"},
    "pairing": {"create", "approve", "revoke"},
    "msg": {"draft", "approve", "send"},
    "peer": {"dm", "run"},
    "vision_describe": set(),
    "plugins_install": set(),
    "worktree": set(),
    "monitor": set(),
    "pause": set(),
    "resume": set(),
}


def test_mutating_backends_need_confirmation():
    from voicebridge import voice_commands as VC

    for entry in VC.REGISTRY.values():
        fixed_action = dict(entry.fixed).get("action")
        mutating = MUTATING_ACTIONS.get(entry.backend, set())
        if entry.backend in ("vision_describe", "plugins_install", "worktree",
                             "monitor", "pause", "resume"):
            assert entry.confirm, f"{entry.id}: heavy op must confirm"
        elif fixed_action in mutating:
            assert entry.confirm, f"{entry.id}: mutating action must confirm"


# ------------------------------------------------- resolution table
COMPLETE_EXAMPLES = {
    "status": "qubit, system status",
    "pause": "qubit, pause everything",
    "resume": "qubit, resume work",
    "doctor": "qubit, run diagnostics",
    "setup_check": "qubit, setup status",
    "verify_release": "qubit, verify release",
    "security": "qubit, security status",
    "diagnostics": "qubit, show diagnostics",
    "backup_info": "qubit, how do i back up",
    "sessions": "qubit, list sessions",
    "capability": "qubit, is whatsapp available",
    "checkpoints_list": "qubit, list checkpoints",
    "checkpoints_write": "qubit, create checkpoint deploy",
    "auth_status": "qubit, credential status",
    "auth_login": "qubit, how do i log in xai",
    "auth_verify": "qubit, is xai configured",
    "auth_logout": "qubit, logout check",
    "mcp": "qubit, list mcp servers",
    "claw": "qubit, claw status",
    "memory_search": "qubit, what do you remember about invoices",
    "memory_remember": "qubit, remember the gate code",
    "memory_forget": "qubit, forget memory 12",
    "skills_list": "qubit, list skills",
    "skills_show": "qubit, describe skill weather",
    "skills_enable": "qubit, enable skill weather",
    "skills_disable": "qubit, disable skill weather",
    "projects_list": "qubit, list projects",
    "projects_show": "qubit, show project shop",
    "projects_create": "qubit, create project shop",
    "projects_use": "qubit, switch to project shop",
    "projects_archive": "qubit, archive project shop",
    "kanban_boards": "qubit, list boards",
    "kanban_list": "qubit, list tasks",
    "kanban_show": "qubit, show task 5",
    "kanban_create": "qubit, create task buy milk",
    "kanban_claim": "qubit, claim task 5",
    "kanban_complete": "qubit, complete task 5",
    "kanban_comment": "qubit, comment on task 5 looks good",
    "themes": "qubit, what theme",
    "themes_set": "qubit, set theme light",
    "proxy": "qubit, proxy status",
    "import_inspect": "qubit, inspect foreign setup at /tmp/x",
    "hooks_list": "qubit, list hooks",
    "hooks_show": "qubit, show hook deploy",
    "hooks_fire": "qubit, fire hook deploy",
    "sync_list": "qubit, sync status",
    "vision_status": "qubit, is vision ready",
    "vision_describe": "qubit, describe image at /tmp/pic.png",
    "pairing_list": "qubit, list pairing codes",
    "pairing_create": "qubit, pair a device",
    "pairing_approve": "qubit, pair with code AB12CD34",
    "pairing_revoke": "qubit, revoke pairing abc",
    "webhooks": "qubit, list webhooks",
    "msg_accounts": "qubit, list messaging accounts",
    "msg_link": "qubit, check account team",
    "msg_inbox": "qubit, check messages",
    "msg_show": "qubit, show message",
    "msg_draft": "qubit, text sam saying running late",
    "msg_send": "qubit, send draft",
    "msg_approve": "qubit, approve draft",
    "msg_summarize": "qubit, read message",
    "gateway": "qubit, list gateways",
    "peer_list": "qubit, list peers",
    "peer_dm": "qubit, tell peer relay hello",
    "peer_run": "qubit, run on peer relay status",
    "brain": "qubit, list profiles",
    "permissions": "qubit, permission mode",
    "monitor": "qubit, run monitor check",
    "model": "qubit, which model",
    "audio": "qubit, list audio devices",
    "device": "qubit, device status",
    "flags": "qubit, list flags",
    "logs": "qubit, show logs",
    "insights": "qubit, activity summary",
    "prompt_budget": "qubit, prompt budget",
    "reasoning": "qubit, reasoning levels",
    "approvals": "qubit, pending approvals",
    "tools": "qubit, list tools",
    "secrets": "qubit, secret status",
    "worktree": "qubit, clean worktrees",
    "completions": "qubit, shell completions",
    "tui_help": "qubit, open dashboard",
    "console_help": "qubit, terminal help",
    "mic_help": "qubit, check microphone",
    "acp": "qubit, editor check",
    "lsp": "qubit, language server check",
    "browser_status": "qubit, browser status",
    "computer_status": "qubit, desktop status",
    "plugins_install": "qubit, install plugin from manifest",
    "curator": "qubit, learning summary",
    "update_status": "qubit, update status",
    "config_profiles": "qubit, list config profiles",
    "migrate_status": "qubit, migration status",
}


def test_every_entry_resolves_its_example():
    from voicebridge import voice_commands as VC

    assert set(COMPLETE_EXAMPLES) == set(VC.REGISTRY), \
        f"example table drift: {sorted(set(VC.REGISTRY) ^ set(COMPLETE_EXAMPLES))}"
    for entry_id, text in COMPLETE_EXAMPLES.items():
        res = VC.resolve(text)
        assert res.kind == VC.MATCH, (entry_id, text, res.kind, res.speech)
        assert res.entry is not None and res.entry.id == entry_id, \
            (entry_id, text, getattr(res.entry, "id", None))


def test_single_word_and_unknown_fall_through():
    from voicebridge import voice_commands as VC

    for text in ("status", "list", "help", "stop", "do the thing",
                 "blargle wibble zzz", ""):
        assert VC.resolve(text).kind == VC.UNKNOWN, text
    res = VC.resolve("qubit status")
    assert res.kind == VC.MATCH and res.entry.id == "status"


def test_clarify_and_ambiguous():
    from voicebridge import voice_commands as VC

    res = VC.resolve("qubit, claim task")
    assert res.kind == VC.CLARIFY and "task" in res.speech.lower()
    res = VC.resolve("qubit, text sam")
    assert res.kind == VC.CLARIFY, "to-saying without saying clarifies"
    a = VC.VoiceCommand(id="__ta", title="A", aliases=("toggle mode",),
                        backend="status", parity=())
    b = VC.VoiceCommand(id="__tb", title="B", aliases=("toggle mode",),
                        backend="status", parity=())
    VC.REGISTRY["__ta"] = a
    VC.REGISTRY["__tb"] = b
    try:
        res = VC.resolve("qubit, toggle mode")
        assert res.kind == VC.AMBIGUOUS, res.kind
        assert "Did you mean" in res.speech
    finally:
        del VC.REGISTRY["__ta"]
        del VC.REGISTRY["__tb"]


def test_arg_splits():
    from voicebridge import voice_commands as VC

    res = VC.resolve("qubit, text sam saying running late")
    assert res.kind == VC.MATCH and res.entry.id == "msg_draft"
    assert res.args == {"to": "sam", "body": "running late"}, res.args
    res = VC.resolve("qubit, tell peer relay hello there")
    assert res.entry.id == "peer_dm"
    assert res.args == {"peer_id": "relay", "text": "hello there"}, res.args
    res = VC.resolve("qubit, comment on task 5 looks good today")
    assert res.args == {"task_id": 5, "body": "looks good today"}, res.args
    res = VC.resolve("qubit, describe image at /tmp/pic.png")
    assert res.args["image"] == "/tmp/pic.png", res.args
    res = VC.resolve("qubit, show task five")
    assert res.kind == VC.CLARIFY, "words are not numbers"


# ------------------------------------------------- execution
def test_read_executes_with_receipt(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    out = VC.execute(ctx, VC.resolve("qubit, list skills"))
    assert out.kind == VC.EXECUTED
    assert out.receipt["entry"] == "skills_list"
    assert out.speech, "speech always present"


def test_confirm_flow_single_use_and_shared_journal(tmp_path, monkeypatch):
    from voicebridge import approvals as AP
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    out = VC.execute(ctx, VC.resolve("qubit, create task buy milk"))
    assert out.kind == VC.NEEDS_CONFIRM and out.confirmation_id
    assert "yes to confirm" in out.speech.lower()
    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl")
    pending = [a for a in journal.pending()
               if a.capability == "voice.kanban_create"]
    assert len(pending) == 1, "dashboard reads the same journal"
    done = VC.confirm_pending(ctx, out.confirmation_id)
    assert done.kind == VC.EXECUTED
    assert "buy milk" in done.speech or "Task" in done.speech
    again = VC.confirm_pending(ctx, out.confirmation_id)
    assert again.kind == VC.FAILED and "gone" in again.speech.lower()
    latest = VC.confirm_pending(ctx)
    assert latest.kind == VC.FAILED, "nothing left pending"


def test_confirm_expiry(tmp_path, monkeypatch):
    import time as _time

    from voicebridge import approvals as AP
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    journal = AP.ApprovalJournal(Path(cfg.state_dir()) / "approvals.jsonl",
                                 default_ttl_s=1.0)
    item = journal.create("voice.test", "test", ttl_s=1.0, session="voice")
    _time.sleep(1.15)
    assert item.id not in {a.id for a in journal.pending()}, "expired"


def test_cancel_denies_pending(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    out = VC.execute(ctx, VC.resolve("qubit, create task buy milk"))
    assert out.kind == VC.NEEDS_CONFIRM
    cancelled = VC.cancel_pending(ctx)
    assert cancelled.kind == VC.EXECUTED and "Cancelled 1" in cancelled.speech
    gone = VC.confirm_pending(ctx, out.confirmation_id)
    assert gone.kind == VC.FAILED


def test_estop_refuses_mutations_not_reads(tmp_path, monkeypatch):
    from voicebridge import power as PW
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)
    PW.power_control_store(cfg).set(emergency_stop=True, global_disable=False,
                                    issued_by="cli:user")
    try:
        out = VC.execute(ctx, VC.resolve("qubit, create task buy milk"))
        assert out.kind == VC.REFUSED and "latched" in out.speech.lower()
        out = VC.execute(ctx, VC.resolve("qubit, list skills"))
        assert out.kind == VC.EXECUTED, "reads stay available"
    finally:
        PW.power_control_store(cfg).set(emergency_stop=False,
                                        global_disable=False,
                                        issued_by="cli:user")


def test_source_gate_refuses_model_text(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg, source="model")
    out = VC.execute(ctx, VC.resolve("qubit, list skills"))
    assert out.kind == VC.REFUSED
    assert out.receipt == {}, "refused work leaves no receipt"


def test_redaction_in_summary_and_audit(tmp_path, monkeypatch):
    from voicebridge import logging_setup as LS
    from voicebridge import voice_commands as VC
    from voicebridge.logging_setup import setup

    cfg = _cfg(tmp_path, monkeypatch)
    setup(level="INFO", jsonl=True, state_dir=cfg.state_dir())
    try:
        ctx = _ctx(cfg)
        out = VC.execute(ctx, VC.resolve("qubit, remember token=abc123secret"))
        assert out.kind == VC.NEEDS_CONFIRM
        blob = (Path(cfg.state_dir()) / "events.jsonl").read_text()
        assert "abc123secret" not in blob
        assert "voice.needs_confirm" in blob
        speech = VC.summarize(out.entry, {"state": "OK", "x": "sk-live-1"})
        assert "sk-live-1" not in speech
    finally:
        LS._EVENT_FILE = None


# ------------------------------------------------- orchestrator + CLI
class _FakeOrch:
    def __init__(self, cfg):
        self.cfg = cfg
        self.spoken = []
        self.feed = []

    def speak(self, text, *a, **k):
        self.spoken.append(text)

    def feed_push(self, item):
        self.feed.append(item)


def test_orchestrator_branch_executes_and_falls_through(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    orch = _FakeOrch(cfg)
    assert VC.handle_turn(orch, "conversation", "qubit, list skills") is True
    assert orch.spoken, "reads speak immediately"
    assert orch.feed and orch.feed[0]["route"] == "voice:skills_list"
    orch2 = _FakeOrch(cfg)
    assert VC.handle_turn(orch2, "conversation", "do the thing") is False
    assert orch2.spoken == [], "non-commands never speak"
    assert VC.handle_turn(orch2, "confirm", "qubit, list skills") is False, \
        "confirm turns never enter the registry"
    assert VC.handle_turn(orch2, "cancel", "qubit, list skills") is False


def test_orchestrator_clarify_speaks_question(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    orch = _FakeOrch(cfg)
    assert VC.handle_turn(orch, "conversation", "qubit, claim task") is True
    assert "task" in orch.spoken[0].lower()


def test_orchestrator_confirm_and_cancel_loop(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    orch = _FakeOrch(cfg)
    assert VC.handle_turn(orch, "conversation", "qubit, create task buy milk") is True
    assert "yes to confirm" in orch.spoken[0].lower()
    assert VC.confirm_voice_turn(orch) is True
    assert orch.spoken[-1], "execution speaks a receipt summary"
    assert VC.confirm_voice_turn(orch) is False, "nothing left"
    VC.handle_turn(orch, "conversation", "qubit, create task second")
    VC.cancel_voice_turn(orch)
    assert VC.confirm_voice_turn(orch) is False, "cancelled is gone"


def test_cli_voice_group(tmp_path, monkeypatch):
    from voicebridge import cli as CLI

    cfg = _cfg(tmp_path, monkeypatch)

    class _A:
        def __init__(self, **kw):
            self.voice_command = kw.get("cmd", "commands")
            self.__dict__.update(kw)

    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_voice(cfg, _A(cmd="commands")) == 0
    body = json.loads(buf.getvalue())
    assert len(body["commands"]) >= 80
    assert all("aliases" in c and "help" in c for c in body["commands"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_voice(cfg, _A(cmd="say", text=["list", "skills"],
                                      session="cli", json=False)) == 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_voice(cfg, _A(cmd="say", text=["blargle", "zzz"],
                                    session="cli", json=True))
    assert rc == 1
    assert json.loads(buf.getvalue())["state"] == "unknown"
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = CLI._cmd_voice(cfg, _A(cmd="say", text=["create", "task", "x"],
                                    session="cli", json=True))
    assert rc == 0
    cid = json.loads(buf.getvalue())["confirmation_id"]
    assert cid, "CLI say stages shared-journal confirmations"
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_voice(cfg, _A(cmd="confirm", confirmation_id=cid,
                                      session="cli")) == 0
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert CLI._cmd_voice(cfg, _A(cmd="cancel", session="cli")) == 2, \
            "nothing left to cancel"
    assert "voice" in CLI.build_parser().format_help()
