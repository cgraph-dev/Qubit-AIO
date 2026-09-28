"""Agent 4 — voice routes through the real production op dispatcher.

The owner directive requires CLI, dashboard, and voice to call the SAME
backend operation with the same args, confirmation, cancellation,
persistence, recovery, redaction, and audit. ``voice_say`` is the spoken
entry point: it resolves the utterance and dispatches by op id through
``dashboard_ops.run_op`` — the dispatcher the dashboard console and the
CLI share — so voice has no private execution path.

These tests pin the voice ledger rows whose only need is ``none``:

* read/guidance: ``commands_list``, ``console_help``, ``mic_help``,
  ``tui_help`` (and read-only ``wake_status`` parity);
* typed refusals: ``guard_actions_prepare``, ``guard_file_patch`` — real
  execution of the shared policy path that never binds an unsafe payload;
* confirm-tier ``kanban_create``: one durable confirmation settled exactly
  once by ``confirm_op`` and cancellable by ``cancel_op``, with the real
  backend mutation persisted;
* negatives: non-user sources and unresolved utterances never dispatch.

Genuine hardware rows (``audio_hw`` device tests, the wake model fetch)
stay open: their voice surface is guidance/status, never a claim of
hardware execution.
"""
from __future__ import annotations


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def _say(cfg, text, session="agent4"):
    from voicebridge import voice_commands as VC

    return VC.voice_say(cfg, text, actor="voice:user", session=session,
                        source="user")


def _run(cfg, op_id, args=None, session="agent4"):
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="voice:user",
                      session=session)


CLOSABLE_ROWS = {
    "commands_list": "qubit, what can you do",
    "console_help": "qubit, terminal help",
    "mic_help": "qubit, check microphone",
    "tui_help": "qubit, open dashboard",
    "guard_actions_prepare": "qubit, prepare a structured action",
    "guard_file_patch": "qubit, apply a file patch",
}

GUARD_ROWS = {
    "guard_actions_prepare": "qubit, prepare a structured action",
    "guard_file_patch": "qubit, apply a file patch",
}


def test_voice_say_dispatches_through_run_op(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    real = OPS.run_op
    seen = {}

    def spy(cfg_arg, op_id, args, *, actor="dashboard:user",
            session="dashboard"):
        seen["op_id"] = op_id
        seen["args"] = dict(args)
        seen["actor"] = actor
        seen["session"] = session
        return real(cfg_arg, op_id, args, actor=actor, session=session)

    monkeypatch.setattr(OPS, "run_op", spy)
    out = VC.voice_say(cfg, "qubit, list tasks", actor="voice:user",
                       session="spy", source="user")
    assert seen["op_id"] == "kanban_list", seen
    assert seen["actor"] == "voice:user"
    assert seen["session"] == "spy"
    assert out["route"] == "kanban_list", out


def test_closable_rows_route_through_production_op(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for entry_id, utterance in CLOSABLE_ROWS.items():
        said = _say(cfg, utterance, session=f"say-{entry_id}")
        assert said["state"] == "executed", (entry_id, said)
        assert said["route"] == entry_id, (entry_id, said)
        direct = _run(cfg, entry_id, {}, session=f"run-{entry_id}")
        assert direct["state"] == "executed", (entry_id, direct)
        assert said["receipt"]["result"] == direct["receipt"]["result"], \
            entry_id


def test_commands_list_is_the_live_registry(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    said = _say(cfg, "qubit, what can you do")
    rows = said["receipt"]["result"]["commands"]
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids)), "registry ids must be unique"
    assert {"commands_list", "tui_help", "guard_actions_prepare"} <= set(ids)
    for row in rows:
        assert set(row) == {"id", "title", "aliases", "confirm", "help",
                            "example"}, row
        blob = str(row)
        assert "passphrase" not in blob.lower()
        assert "hunter2" not in blob


def test_guidance_rows_return_guidance_not_fake_execution(tmp_path,
                                                          monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for entry_id, utterance in CLOSABLE_ROWS.items():
        if not entry_id.startswith(("tui_", "console_", "mic_")):
            continue
        said = _say(cfg, utterance)
        result = said["receipt"]["result"]
        assert result.get("guidance"), (entry_id, result)
        assert len(result["guidance"]) > 20, (entry_id, result)


def test_confirm_op_settles_voice_confirmation_once(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    said = _say(cfg, "qubit, create task buy milk", session="v-conf")
    assert said["state"] == "needs-confirm", said
    cid = said["confirmation_id"]
    assert cid
    done = OPS.confirm_op(cfg, cid, actor="voice:user", session="v-conf")
    assert done["state"] == "executed", done
    tasks = KB.list_tasks(cfg, board="main").get("tasks", [])
    assert any("buy milk" in str(t.get("title", "")) for t in tasks), tasks
    replay = OPS.confirm_op(cfg, cid, actor="voice:user", session="v-conf")
    assert replay["state"] == "failed", replay
    assert replay.get("code") in ("NO_CONFIRMATION", "ALREADY_DONE"), replay


def test_cancel_op_cancels_voice_confirmation(tmp_path, monkeypatch):
    from voicebridge import dashboard_ops as OPS
    from voicebridge import kanban as KB

    cfg = _cfg(tmp_path, monkeypatch)
    said = _say(cfg, "qubit, create task toss this", session="v-cancel")
    assert said["state"] == "needs-confirm", said
    cid = said["confirmation_id"]
    assert cid
    cancelled = OPS.cancel_op(cfg, cid, actor="voice:user",
                              session="v-cancel")
    assert cancelled["state"] == "executed", cancelled
    gone = OPS.confirm_op(cfg, cid, actor="voice:user", session="v-cancel")
    assert gone["state"] == "failed", gone
    tasks = KB.list_tasks(cfg, board="main").get("tasks", [])
    assert not any("toss this" in str(t.get("title", "")) for t in tasks), tasks


def test_guard_refusals_route_through_run_op_and_bind_nothing(tmp_path,
                                                              monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    for entry_id, utterance in GUARD_ROWS.items():
        said = _say(cfg, utterance, session=f"g-{entry_id}")
        assert said["state"] == "executed", (entry_id, said)
        result = said["receipt"]["result"]
        assert result["state"] == "refused", (entry_id, result)
        assert result["code"], (entry_id, result)
        assert len(result["guidance"]) > 20, (entry_id, result)
        assert not said.get("confirmation_id"), (entry_id, said)
        direct = _run(cfg, entry_id, {}, session=f"gr-{entry_id}")
        assert direct["receipt"]["result"] == result, entry_id
        entry = VC.REGISTRY[entry_id]
        assert not entry.args, entry_id
        assert set(dict(entry.fixed or ())) <= {"op", "guidance", "code"}, \
            entry_id


def test_every_voice_guard_routes_through_run_op(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    for op_id, spec in VC.VOICE_GUARDS.items():
        said = _say(cfg, "qubit, " + spec["aliases"][0], session="all-guards")
        assert said["state"] == "executed", (op_id, said)
        result = said["receipt"]["result"]
        assert result["state"] == "refused", (op_id, result)
        assert result["code"] == spec["code"], (op_id, result)
        assert not said.get("confirmation_id"), op_id


def test_wake_status_read_is_read_only_parity(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    said = _say(cfg, "qubit, is wake word ready", session="wake")
    assert said["state"] == "executed", said
    direct = _run(cfg, "wake_status", {}, session="wake-run")
    assert said["receipt"]["result"]["wake"] == \
        direct["receipt"]["result"]["wake"]


def test_voice_say_never_executes_non_user_sources(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    for source in ("model", "page", "tool", "system", ""):
        out = VC.voice_say(cfg, "qubit, list tasks", actor="model",
                           session="nope", source=source)
        assert out["state"] == "refused", (source, out)
        assert out["code"] == "SOURCE_REFUSED", (source, out)
        assert "did not come from you" in out["speech"], (source, out)


def test_voice_say_unknown_never_dispatches(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path, monkeypatch)
    for utterance in ("qubit, xyzzy", "tasks", "qubit tasks",
                      "qubit, do the thing"):
        out = _say(cfg, utterance)
        assert out["state"] == "unknown", (utterance, out)
        assert not out.get("receipt"), (utterance, out)
        assert not out.get("confirmation_id"), (utterance, out)
