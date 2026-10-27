"""Phase 4 — deep_research is voice-commandable.

The spoken path reuses the one resolve/execute pipeline and the real
``_b_recipe`` backend. Retrieval and the model turn are faked here; routing,
topic capture, registry wiring, summary redaction and the typed-unavailable
path are the assertions.
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
    from recipes.deep_research import DEEP_RESEARCH
    from recipes.runner import RecipeRun
    from recipes.sections import available, unavailable

    return RecipeRun(
        recipe_id=DEEP_RESEARCH.id, engine="native", brief=brief,
        sections=(
            available("search", "Web search", [
                "[1] Quantum error correction — https://example.com/qec"]),
            unavailable("sources", "Sources", "tool_error",
                        "no source could be fetched: timeout"),
        ),
        terminal="done" if not (refusal or failed) else "error",
        refusal=refusal, hint=hint, failed=failed)


def _patch_runner(monkeypatch, result, captured=None):
    import recipes.runner as runner

    def fake(spec, **kw):
        if captured is not None:
            captured.update(kw)
        return result

    monkeypatch.setattr(runner, "run_recipe", fake)


def test_aliases_resolve_to_the_deep_research_entry_with_a_topic():
    from voicebridge import voice_commands as VC

    cases = {
        "deep research quantum error correction": "quantum error correction",
        "do deep research on surface codes": "surface codes",
        "research this topic topological qubits": "topological qubits",
    }
    for phrase, topic in cases.items():
        resolution = VC.resolve(phrase)
        assert resolution.kind == VC.MATCH, (phrase, resolution.kind, resolution.speech)
        assert resolution.entry is not None
        assert resolution.entry.id == "recipe_deep_research", phrase
        assert resolution.args["topic"] == topic, (phrase, resolution.args)
        assert resolution.entry.confirm is False


def test_bare_alias_asks_for_a_topic_instead_of_researching_nothing():
    from voicebridge import voice_commands as VC

    resolution = VC.resolve("deep research")
    assert resolution.kind == VC.CLARIFY
    assert resolution.entry is not None
    assert resolution.entry.id == "recipe_deep_research"


def test_entry_is_wired_to_the_recipe_backend():
    from voicebridge import voice_commands as VC

    entry = VC.CLI_BRIDGE_REGISTRY["recipe_deep_research"]
    assert entry.backend == "recipe"
    assert VC._BACKENDS["recipe"] is VC._b_recipe
    assert VC._SUMMARIES["recipe"] is VC._sum_recipe
    assert dict(entry.fixed)["recipe"] == "deep_research"
    assert [arg.name for arg in entry.args] == ["topic"]


def test_spoken_execution_forwards_the_topic_and_speaks_the_brief(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    captured = {}
    _patch_runner(monkeypatch, _run("Quantum error correction improves as [1]."),
                  captured)
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w4x", source="cli")

    outcome = VC.execute(
        ctx, VC.resolve("deep research quantum error correction"))

    assert outcome.kind == VC.EXECUTED, (outcome.kind, outcome.speech)
    assert captured["inputs"] == {"topic": "quantum error correction"}
    assert "Quantum error correction" in outcome.speech
    result = outcome.receipt["result"]["recipe"]
    assert result["state"] == "OK"
    assert result["id"] == "deep_research"
    sources = next(s for s in result["sections"] if s["id"] == "sources")
    assert sources["status"] == "unavailable"
    assert sources["reason"] == "tool_error"


def test_typed_unavailable_is_spoken_not_hidden(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _patch_runner(monkeypatch, _run(
        "", refusal="openjarvis_unavailable", hint="set the engine pin"))
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w4x", source="cli")

    outcome = VC.execute(ctx, VC.resolve("deep research quantum error correction"))
    assert outcome.kind == VC.EXECUTED
    assert "unavailable" in outcome.speech.lower()
    assert "openjarvis_unavailable" in outcome.speech
    assert outcome.receipt["result"]["recipe"]["state"] == "UNAVAILABLE"


def test_spoken_summary_never_leaks_a_secret(tmp_path, monkeypatch):
    from voicebridge import voice_commands as VC

    _patch_runner(monkeypatch, _run("key sk-abcdefghijklmnop is visible [1]."))
    cfg = _cfg(tmp_path, monkeypatch)
    ctx = VC.make_ctx(cfg, session="w4x", source="cli")

    outcome = VC.execute(ctx, VC.resolve("deep research quantum error correction"))
    assert outcome.kind == VC.EXECUTED
    assert "sk-abcdefghijklmnop" not in outcome.speech
    assert "REDACTED" in outcome.speech
