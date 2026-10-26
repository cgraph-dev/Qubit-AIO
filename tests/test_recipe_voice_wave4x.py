"""Phase 4 — the morning digest is voice-commandable.

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
    from recipes.morning_digest import MORNING_DIGEST
    from recipes.runner import RecipeRun
    from recipes.sections import available, unavailable

    return RecipeRun(
        recipe_id=MORNING_DIGEST.id, engine="native", brief=brief,
        sections=(
            available("weather", "Weather", ["Sunny 20C"]),
            unavailable("health", "Health", "connector_not_configured",
                        "no health connector is installed"),
        ),
        terminal="done" if not (refusal or failed) else "error",
        refusal=refusal, hint=hint, failed=failed)


def _patch_runner(monkeypatch, result):
    import recipes.runner as runner

    monkeypatch.setattr(runner, "run_recipe", lambda spec, **kw: result)


def test_aliases_resolve_to_the_morning_digest_entry():
    from voicebridge import voice_commands as VC

    for phrase in ("give me my morning digest", "morning digest",
                   "my morning digest", "daily digest",
                   "run my morning digest", "get my morning digest",
                   "qubit, give me my morning digest"):
        resolution = VC.resolve(phrase)
        assert resolution.kind == VC.MATCH, (phrase, resolution.kind, resolution.speech)
        assert resolution.entry is not None
        assert resolution.entry.id == "recipe_morning_digest", phrase
        assert resolution.entry.confirm is False


def test_entry_is_wired_to_the_recipe_backend():
    from voicebridge import voice_commands as VC

    entry = VC.CLI_BRIDGE_REGISTRY["recipe_morning_digest"]
    assert entry.backend == "recipe"
    assert VC._BACKENDS["recipe"] is VC._b_recipe
    assert VC._SUMMARIES["recipe"] is VC._sum_recipe
    assert dict(entry.fixed)["recipe"] == "morning_digest"


def test_spoken_execution_returns_and_speaks_the_brief(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _patch_runner(monkeypatch, _run("Good morning. Sunny, standup at 9."))
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w4x", source="cli")

    outcome = VC.execute(ctx, VC.resolve("give me my morning digest"))

    assert outcome.kind == VC.EXECUTED, (outcome.kind, outcome.speech)
    assert "Good morning" in outcome.speech
    result = outcome.receipt["result"]["recipe"]
    assert result["state"] == "OK"
    assert result["brief"] == "Good morning. Sunny, standup at 9."
    health = next(s for s in result["sections"] if s["id"] == "health")
    assert health["status"] == "unavailable"
    assert health["reason"] == "connector_not_configured"


def test_typed_unavailable_is_spoken_not_hidden(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _patch_runner(monkeypatch, _run(
        "", refusal="openjarvis_unavailable", hint="set the engine pin"))
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w4x", source="cli")

    outcome = VC.execute(ctx, VC.resolve("give me my morning digest"))
    assert outcome.kind == VC.EXECUTED
    assert "unavailable" in outcome.speech.lower()
    assert "openjarvis_unavailable" in outcome.speech
    assert outcome.receipt["result"]["recipe"]["state"] == "UNAVAILABLE"


def test_spoken_summary_never_leaks_a_secret(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _patch_runner(monkeypatch, _run("key sk-abcdefghijklmnop is visible"))
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w4x", source="cli")

    outcome = VC.execute(ctx, VC.resolve("give me my morning digest"))
    assert outcome.kind == VC.EXECUTED
    assert "sk-abcdefghijklmnop" not in outcome.speech
    assert "REDACTED" in outcome.speech
