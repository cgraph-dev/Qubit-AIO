"""Phase 4 — monitor_operative is voice-commandable.

The spoken path reuses the one resolve/execute pipeline and the real
``_b_recipe`` backend. The model turn is faked here; routing, registry wiring,
summary redaction and the typed-unavailable path are the assertions.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    path = tmp_path / "voice.yaml"
    path.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(path)


def _run(brief, *, refusal="", hint="", failed=False):
    from recipes.runner import RecipeRun
    from recipes.sections import available, unavailable

    return RecipeRun(
        recipe_id="monitor_operative", engine="native", brief=brief,
        sections=(
            available("cron", "Cron", ["2 job(s): 1 scheduled, 1 paused."]),
            unavailable("kanban", "Kanban board", "connector_not_configured",
                        "no kanban board database exists yet"),
            available("memory", "Memory", ["Provider: honcho."]),
            available("compression", "Compression",
                      ["Session store: reachable (12 session(s))."]),
        ),
        terminal="done" if not (refusal or failed) else "error",
        refusal=refusal, hint=hint, failed=failed)


def _patch_runner(monkeypatch, result):
    import recipes.runner as runner

    monkeypatch.setattr(runner, "run_recipe", lambda spec, **kw: result)


def test_aliases_resolve_to_the_monitor_entry():
    from voicebridge import voice_commands as VC

    for phrase in ("what's my operative status", "operative status",
                   "monitor my work", "status of my work",
                   "how is my work doing", "monitor operative",
                   "qubit, what's my operative status"):
        resolution = VC.resolve(phrase)
        assert resolution.kind == VC.MATCH, (phrase, resolution.kind, resolution.speech)
        assert resolution.entry is not None
        assert resolution.entry.id == "recipe_monitor_operative", phrase
        assert resolution.entry.confirm is False


def test_entry_is_wired_to_the_recipe_backend():
    from voicebridge import voice_commands as VC

    entry = VC.CLI_BRIDGE_REGISTRY["recipe_monitor_operative"]
    assert entry.backend == "recipe"
    assert VC._BACKENDS["recipe"] is VC._b_recipe
    assert VC._SUMMARIES["recipe"] is VC._sum_recipe
    assert dict(entry.fixed)["recipe"] == "monitor_operative"


def test_spoken_execution_returns_and_speaks_the_brief(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _patch_runner(monkeypatch, _run(
        "Operational. Two cron jobs, one paused. Kanban is unavailable."))
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="p4b", source="cli")

    outcome = VC.execute(ctx, VC.resolve("what's my operative status"))

    assert outcome.kind == VC.EXECUTED, (outcome.kind, outcome.speech)
    assert "Operational" in outcome.speech
    result = outcome.receipt["result"]["recipe"]
    assert result["state"] == "OK"
    assert result["id"] == "monitor_operative"
    assert result["title"] == "Operative status"
    kanban = next(s for s in result["sections"] if s["id"] == "kanban")
    assert kanban["status"] == "unavailable"
    assert kanban["reason"] == "connector_not_configured"


def test_typed_unavailable_is_spoken_not_hidden(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _patch_runner(monkeypatch, _run(
        "", refusal="openjarvis_unavailable", hint="set the engine pin"))
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="p4b", source="cli")

    outcome = VC.execute(ctx, VC.resolve("status of my work"))
    assert outcome.kind == VC.EXECUTED
    assert "unavailable" in outcome.speech.lower()
    assert "openjarvis_unavailable" in outcome.speech
    assert outcome.receipt["result"]["recipe"]["state"] == "UNAVAILABLE"


def test_spoken_summary_never_leaks_a_secret(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _patch_runner(monkeypatch, _run("job key sk-abcdefghijklmnop is visible"))
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="p4b", source="cli")

    outcome = VC.execute(ctx, VC.resolve("monitor my work"))
    assert outcome.kind == VC.EXECUTED
    assert "sk-abcdefghijklmnop" not in outcome.speech
    assert "REDACTED" in outcome.speech
