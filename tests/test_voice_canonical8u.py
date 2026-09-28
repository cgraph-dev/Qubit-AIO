"""Wave 17 / 8U — voice intents resolve to the SAME canonical backend op as
the CLI and dashboard (VOICE_BACKEND_MISMATCHES.md A16/A17/A18).

Each test drives the real ``voice_commands`` backend and proves it reaches the
canonical CLI/shared operation rather than the old parallel implementation:

* ``task_run``/``task_run_current`` -> orchestrator worker (never
  ``chat_transport.turn``), built headless so no audio hardware is touched;
* ``memory_search`` -> the canonical ``SemanticMemory`` adapter with the
  ``semantic.json`` enable/provider/health gate (never a bare ``MemoryStore``);
* ``completions`` -> the exact ``cli._cmd_completion`` scripts;
* ``browser_status`` -> ``cli._cmd_browser status`` (configured CDP
  precondition, not a hard-coded loopback probe);
* ``model`` -> ``cli._cmd_frontier status``; ``webui_status`` ->
  ``cli._cmd_webui --json``;
* ``status``/``mic_help`` -> typed, honest voice projections/refusals.
"""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path


def _cfg(tmp_path, monkeypatch, body: str = ""):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + body)
    return C.Config.load(p)


def _ctx(cfg, session="canon", source="user"):
    from voicebridge import voice_commands as VC

    return VC.make_ctx(cfg, session=session, source=source)


def _run(cfg, text, session="canon"):
    from voicebridge import voice_commands as VC

    ctx = _ctx(cfg, session=session)
    out = VC.execute(ctx, VC.resolve(text))
    if out.kind == VC.NEEDS_CONFIRM:
        out = VC.confirm_pending(ctx, out.confirmation_id)
    return out


def _cli_stdout(handler, *a, **kw):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = handler(*a, **kw)
    return rc, buf.getvalue()


# --------------------------------------------------------------- A17: worker
def test_task_run_dispatches_through_orchestrator_worker(tmp_path, monkeypatch):
    """A16/A17: voice task_run calls the orchestrator worker, never
    ``chat_transport.turn``, and the orchestrator is built headless (no
    audio-device resolution or sink volume mutation)."""
    from voicebridge import chat_transport as CT
    from voicebridge import orchestrator as OR
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    ctx = _ctx(cfg)

    def _no_chat(*a, **k):
        raise AssertionError("chat_transport.turn must not be used by voice")

    monkeypatch.setattr(CT, "turn", _no_chat)

    # Any hardware touch fails loudly.
    monkeypatch.setattr(OR.VoiceOrchestrator, "_apply_output_volume",
                        lambda self: (_ for _ in ()).throw(
                            AssertionError("headless must not touch audio")))
    monkeypatch.setattr(OR.Playback, "_resolve_device",
                        lambda self: (_ for _ in ()).throw(
                            AssertionError("headless must not probe audio")))

    seen = {}

    def _fake_worker(self, text, model=None, *, session=""):
        seen["headless"] = getattr(self, "headless", None)
        seen["text"] = text
        seen["session"] = session
        return {"state": "OK", "runtime": "orchestrator-worker",
                "reply": "done", "session": session,
                "receipt": {"runtime": "orchestrator-worker"}}

    monkeypatch.setattr(OR.VoiceOrchestrator, "run_worker_task", _fake_worker)

    staged = VC.execute(ctx, VC.resolve("qubit, run the task summarize notes"))
    assert staged.kind == VC.NEEDS_CONFIRM, staged.speech
    done = VC.confirm_pending(ctx, staged.confirmation_id)
    assert done.kind == VC.EXECUTED, done.speech
    task = done.receipt["result"]["task"]
    assert task["state"] == "OK" and task["runtime"] == "orchestrator-worker"
    assert seen["headless"] is True
    assert "summarize notes" in seen["text"]


def test_task_run_worker_failure_is_typed(tmp_path, monkeypatch):
    """The orchestrator worker's own typed denial (AGENT_DISABLED) is surfaced
    honestly, matching the CLI ``task`` path with agent.enabled=false."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch, "agent:\n  enabled: false\n")
    out = _run(cfg, "qubit, run the task summarize my notes")
    assert out.kind == VC.EXECUTED, out.speech
    task = out.receipt["result"]["task"]
    assert task["state"] == "UNAVAILABLE", task
    assert task["code"] == "AGENT_DISABLED", task


def test_task_run_confirmation_is_single_use_and_cancellable(tmp_path,
                                                            monkeypatch):
    """The shared ApprovalJournal semantics survive the backend swap: cancel
    settles once and a consumed/replayed id is refused."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch, "agent:\n  enabled: false\n")
    ctx = _ctx(cfg)
    staged = VC.execute(ctx, VC.resolve("qubit, run the task x"))
    cancelled = VC.cancel_pending(ctx, staged.confirmation_id)
    assert cancelled.kind == VC.EXECUTED, cancelled.speech
    gone = VC.confirm_pending(ctx, staged.confirmation_id)
    assert gone.kind == VC.FAILED, gone.speech

    staged2 = VC.execute(ctx, VC.resolve("qubit, run the task y"))
    first = VC.confirm_pending(ctx, staged2.confirmation_id)
    assert first.kind == VC.EXECUTED, first.speech
    replay = VC.confirm_pending(ctx, staged2.confirmation_id)
    assert replay.kind == VC.FAILED, replay.speech


def test_task_run_current_resolves_current_kanban_task(tmp_path, monkeypatch):
    from voicebridge import kanban as KB
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch, "agent:\n  enabled: false\n")
    KB.init(cfg)
    try:
        KB.boards_create(cfg, "main", requested_by="t", confirmed=True)
    except Exception:
        pass
    KB.create(cfg, "Summarize my notes", board="main",
              requested_by="t", confirmed=True)
    out = _run(cfg, "qubit, run this task")
    assert out.kind == VC.EXECUTED, out.speech
    task = out.receipt["result"]["task"]
    assert task["state"] == "UNAVAILABLE" and task["code"] == "AGENT_DISABLED"


# --------------------------------------------------------------- A16: memory
def test_memory_search_uses_semantic_state_gate(tmp_path, monkeypatch):
    """Voice memory_search reads the same ``semantic.json`` state the CLI
    ``memory`` family uses: provider/enable are honoured, never bypassed."""
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    remembered = _run(cfg, "qubit, remember the sky is blue")
    assert remembered.kind == VC.EXECUTED, remembered.speech

    base = Path(cfg.state_dir()) / "memory"
    # No semantic.json -> disabled baseline, typed health.
    res = _run(cfg, "qubit, what do you remember about sky").receipt["result"]
    assert res["provider"] == "none" and res["engine"] == "fts5"
    assert res["semantic"]["state"] == "ok"
    assert [r["text"] for r in res["results"]][:1] == ["the sky is blue"]

    # The canonical state file selects the provider (same file the CLI reads).
    (base / "semantic.json").write_text(json.dumps(
        {"schema_version": 1, "enabled": True, "provider": "hash"}))
    res = _run(cfg, "qubit, what do you remember about sky").receipt["result"]
    assert res["provider"] == "hash", res
    assert res["engine"] == "fts5+semantic", res

    # Corrupt state is surfaced as degraded (never silently reset), and the
    # read still fails safe to the FTS5 baseline.
    (base / "semantic.json").write_text("{not json")
    res = _run(cfg, "qubit, what do you remember about sky").receipt["result"]
    assert res["semantic"]["state"] == "degraded", res
    assert res["semantic"]["detail"], res


# ---------------------------------------------------------- A18: completions
def test_completions_emit_the_cli_canonical_scripts(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = _run(cfg, "qubit, shell completions").receipt["result"]["completions"]
    assert res["state"] == "OK" and res["count"] == 9, res
    by_key = {(s["cmd"], s["shell"]): s for s in res["scripts"]}
    for cmd in ("qubit", "voicebridge", "qubit-dev"):
        for shell in ("bash", "zsh", "fish"):
            _rc, text = _cli_stdout(CLI._cmd_completion, shell, cmd)
            assert by_key[(cmd, shell)]["script"] == text, (cmd, shell)
            assert by_key[(cmd, shell)]["sha256"]


# ------------------------------------------------------------- A18: browser
def test_browser_status_calls_the_cli_canonical_op(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    seen = {}

    def _fake_browser(_cfg, args):
        seen["command"] = args.browser_command
        print(json.dumps({
            "state": "BROWSER_RESTART_REQUIRED", "schema": "qubit-browser/1",
            "cdp": False, "detail": "restart required",
            "authority": "loopback CDP read-only (Packet 8B)",
        }, indent=2, sort_keys=True))
        return 0

    monkeypatch.setattr(CLI, "_cmd_browser", _fake_browser)
    res = _run(cfg, "qubit, browser status").receipt["result"]["browser"]
    assert seen["command"] == "status", seen
    assert res["canonical_state"] == "BROWSER_RESTART_REQUIRED", res
    assert res["state"] == "UNAVAILABLE"  # voice 2-state projection


def test_browser_close_stays_a_typed_refusal(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = _run(cfg, "qubit, close browser profile").receipt["result"]
    assert res["refused"]["code"] == "BROWSER_CLOSE_REFUSED", res


# ------------------------------------------------------------- A18: frontier
def test_model_matches_cli_frontier_status(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = _run(cfg, "qubit, which model").receipt["result"]["frontier"]
    _rc, text = _cli_stdout(CLI._cmd_frontier, cfg,
                            _ns(frontier_command="status"))
    assert res == json.loads(text), res


# ------------------------------------------------------------- A18: webui
def test_webui_status_matches_cli_cmd_webui(tmp_path, monkeypatch):
    from voicebridge import cli as CLI
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = _run(cfg, "qubit, dashboard status").receipt["result"]["dashboard"]
    _rc, text = _cli_stdout(CLI._cmd_webui, cfg,
                            _ns(port=None, open=False, json=True))
    cli_out = json.loads(text)
    assert res["url"] == cli_out["url"], res
    assert res["state"] == cli_out["state"], res
    assert res["token_in_url"] is False


# ------------------------------------------- SAFE_EQUIVALENT typed projections
def test_status_is_typed_inventory_projection(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = _run(cfg, "qubit, system status").receipt["result"]
    assert res["inventory"]["schema"] == "qubit-runtime-inventory/1", res
    assert "CLI-only" in res["authority"], res


def test_mic_help_is_typed_guidance_not_fake_hardware(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    cfg = _cfg(tmp_path, monkeypatch)
    res = _run(cfg, "qubit, check microphone").receipt["result"]
    assert res["code"] == "VOICE_CLI_HARDWARE_OP", res
    assert "qubit mic-test" in res["guidance"], res


def _ns(**kw):
    from types import SimpleNamespace

    return SimpleNamespace(**kw)
