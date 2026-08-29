"""Packet 4B tests: canonical runner contract, sanitized artifact,
bounded context compilation, compact typed decisions, zero reload drift.

Network-touching tests skip (typed) when Ollama is unavailable; they never
unload the resident model.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.context_compiler import (  # noqa: E402
    CHARS_PER_TOKEN, CompiledContext, compile_context, fits_budget,
    load_core_facts, output_reserve_tokens)
from voicebridge.runner_contract import (  # noqa: E402
    DEFAULT_ALIAS, DEFAULT_NUM_CTX, KEEP_ALIVE, RunnerContract, material_options,
    resolve)
import voicebridge.intent.llm as LLM  # noqa: E402
from voicebridge.local_llm import _chat_payload  # noqa: E402
from voicebridge.summarizer import summarize_ollama  # noqa: E402

MODEFILE = REPO_ROOT / "Modelfile.qubit4"


class _Cfg:
    def __init__(self, data):
        self.data = data

    def get(self, *path, default=None):
        node = self.data
        for p in path:
            if not isinstance(node, dict) or p not in node:
                return default
            node = node[p]
        return node


# ------------------------------------------------------------ contract
def test_contract_defaults_without_config(monkeypatch):
    def _down(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(urllib.request, "urlopen", _down)
    from voicebridge import runner_contract as RC
    RC.reset_cache()
    rc = resolve(None)
    assert rc.alias == DEFAULT_ALIAS
    assert rc.num_ctx == DEFAULT_NUM_CTX
    assert rc.keep_alive == KEEP_ALIVE == -1
    assert rc.digest is None          # typed degraded, never a crash
    assert rc.placement_policy


def test_contract_config_precedence(monkeypatch):
    def _down(*a, **k):
        raise OSError("down")
    monkeypatch.setattr(urllib.request, "urlopen", _down)
    from voicebridge import runner_contract as RC
    RC.reset_cache()
    legacy = _Cfg({"intent": {"num_ctx": 8192}})
    assert resolve(legacy).num_ctx == 8192
    canonical = _Cfg({"intent": {"runner": {"num_ctx": 3072},
                                 "num_ctx": 8192}})
    assert resolve(canonical).num_ctx == 3072
    alias_unified = _Cfg({"intent": {
        "ollama_model": "alias-a", "local_llm": {"model": "alias-b"}}})
    assert resolve(alias_unified).alias == "alias-a"
    # one resolution is stable and shared (cached per key)
    assert resolve(canonical) == resolve(canonical)


def test_material_options_projection():
    p = {"model": "m", "keep_alive": -1,
         "options": {"temperature": 0.4, "num_ctx": 4096}}
    assert material_options(p) == {"num_ctx": 4096, "keep_alive_present": True}


# ------------------------------------- cross-caller payload agreement
class _FakeResp:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def read(self, n=-1):
        return self._body

    def close(self):
        pass


def test_all_hot_path_callers_share_material_options(monkeypatch):
    """classifier + chat + summary payloads MUST agree on the reload key."""
    seen = []
    rc = resolve(None)

    def fake_urlopen(req, timeout=None, **kwargs):
        seen.append(json.loads(bytes(req.data or b"{}")))
        return _FakeResp(b'{"response": "{\\"kind\\": \\"conversation\\"}"}')

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    LLM.classify_with_ollama("how are you?")
    chat_payload = _chat_payload("hi there friend", "", rc.alias, 100, "",
                                 stream=False)
    summarize_ollama("demo agent output text")

    payloads = seen + [chat_payload]
    assert len(payloads) == 3
    projections = [material_options(p) for p in payloads]
    assert len({json.dumps(x, sort_keys=True) for x in projections}) == 1, \
        f"material options drifted across callers: {projections}"
    assert projections[0]["num_ctx"] == rc.num_ctx
    assert projections[0]["keep_alive_present"] is True
    models = {p["model"] for p in payloads}
    assert models == {rc.alias}


def test_orchestrator_paths_use_the_contract():
    src = (REPO_ROOT / "voicebridge" / "orchestrator.py").read_text()
    assert "resolve(self.cfg)" in src
    assert "_rc.num_ctx" in src and "_rc.keep_alive" in src
    assert 'llm_cfg.get("num_ctx"' not in src, \
        "per-caller num_ctx fallback must be gone"


# ------------------------------------------------- compact typed decisions
def _fake_generate(monkeypatch, response_text):
    def fake_urlopen(req, timeout=None, **kwargs):
        return _FakeResp(json.dumps({"response": response_text}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_compact_decision_synthesizes_task_and_reason(monkeypatch):
    _fake_generate(monkeypatch, '{"kind": "conversation", "confidence": 0.91}')
    out = LLM.classify_with_ollama("tell me a joke")
    assert out["kind"] == "conversation"
    assert out["task"] == ""                      # no free-text generation
    assert out["reason"] == "conversation:schema"
    assert out["confidence"] == 0.91


def test_coding_decision_keeps_task_text(monkeypatch):
    _fake_generate(monkeypatch, '{"kind": "coding"}')
    out = LLM.classify_with_ollama("fix the failing unit test")
    assert out["task"] == "fix the failing unit test"
    assert out["confidence"] == 0.6               # bounded default


def test_out_of_enum_kind_fails_closed(monkeypatch):
    _fake_generate(monkeypatch, '{"kind": "destroy_everything"}')
    assert LLM.classify_with_ollama("do anything") is None


def test_non_dict_and_garbage_fail_closed(monkeypatch):
    _fake_generate(monkeypatch, "[1,2]")
    assert LLM.classify_with_ollama("x") is None
    _fake_generate(monkeypatch, "not json at all")
    assert LLM.classify_with_ollama("x") is None


def test_schema_locked_to_valid_kinds():
    kinds = set(LLM.DECISION_SCHEMA["properties"]["kind"]["enum"])
    assert kinds == set(LLM.VALID_KINDS)
    assert LLM.DECISION_SCHEMA["required"] == ["kind"]
    assert LLM.DECISION_SCHEMA["additionalProperties"] is False


# ------------------------------------------------------- context compiler
def test_compiler_precedence_drops_lowest_first():
    out = compile_context(persona="P", active="A" * 100,
                          core_facts="C" * 100, recall="R" * 100,
                          char_budget=260)
    assert "persona" in out.sections_included
    assert "active" in out.sections_included
    assert "core_facts" in out.sections_included
    assert "recall" in out.sections_omitted      # lowest priority dropped
    assert "<about-the-user>" in out.text


def test_compiler_tight_budget_starves_lowest_tiers_first():
    out = compile_context(persona="P", active="A" * 100,
                          core_facts="C" * 100, recall="R" * 100,
                          char_budget=len("P") + 150)
    # active outranks the rest: it fills the remainder by hard clipping
    assert "active" in out.sections_included
    assert set(out.sections_omitted) == {"core_facts", "recall"}
    assert "<about-the-user>" not in out.text


def test_compiler_persona_never_truncated_reports_omissions():
    persona = "P" * 50
    out = compile_context(persona=persona, active="", core_facts="C" * 5000,
                          recall="R" * 5000, char_budget=60)
    assert out.text == "" or "<about-the-user>" not in out.text
    assert set(out.sections_omitted) == {"core_facts", "recall"}
    assert out.est_tokens >= 0


def test_budget_fits_at_canonical_context():
    rc = resolve(None)
    compiled = compile_context(
        persona="x" * 2400,           # ~SYSTEM_PROMPT + dialogs worst case
        active="y" * 1600,            # ACTIVE_MAX_CHARS
        core_facts="z" * 600,         # CORE_FACTS_MAX_CHARS
        recall="")
    assert fits_budget(rc.num_ctx, 100, compiled)
    tiny = RunnerContract(alias="m", url="u", num_ctx=512)
    assert not fits_budget(tiny.num_ctx, 100, compiled)


def test_output_reserve_math():
    assert output_reserve_tokens(4096, 200, 3000) == 896
    assert output_reserve_tokens(4096, 4000, 3000) == 0


def test_core_facts_loader_strips_secret_shaped_lines(tmp_path):
    f = tmp_path / "pk.md"
    f.write_text("- Name: Demo\n- API key: sk-demo-12345678901234567890\n"
                 "- City: Bucharest-demo\n")
    text = load_core_facts(str(f))
    assert "API key" not in text and "sk-demo" not in text
    assert "City: Bucharest-demo" in text


def test_core_facts_loader_bounded():
    text = load_core_facts()   # real user file may exist; must stay bounded
    assert len(text) <= 1600


# ------------------------------------------------------ artifact hygiene
def test_modelfile_is_sanitized_minimal_artifact():
    mf = MODEFILE.read_text()
    for forbidden in ("+40", "744863501217955940", "8153897846", "tr.cky",
                      "cgraph", "RankValut", "/home/", "BAKED-MEMORY",
                      "instagram", "whatsapp", "discord", "telegram"):
        assert forbidden.lower() not in mf.lower(), forbidden
    # pinned no-think mechanism and doc params survive
    assert "TEMPLATE" in mf and "/no_think" in mf
    assert f"PARAMETER num_ctx {DEFAULT_NUM_CTX}" in mf


def test_modelfile_system_in_stability_basin():
    """Measured Packet 4B gotcha: this abliterated Q4 quant emits instant-EOS
    (1-token empty replies) when the baked SYSTEM is too short (~<1KB).
    The generic persona must stay above the measured stable basin AND end
    with a newline before the closing quotes."""
    import re
    mf = MODEFILE.read_text()
    sys_text = re.search(r'SYSTEM """(.*?)"""', mf, re.S).group(1)
    assert len(sys_text) >= 1800, \
        f"SYSTEM fell below the measured EOS-stability basin ({len(sys_text)} chars)"
    assert len(sys_text) < 6000, "persona must stay bounded (~550 tokens max)"
    assert sys_text.endswith("\n"), \
        "SYSTEM must end with a newline (measured tokenization seam)"


def test_bake_script_refuses_memory_markers_and_private_probes():
    src = (REPO_ROOT / "scripts" / "bake_brain.py").read_text()
    assert "BAKED-MEMORY" in src                    # refusal guard present
    assert "build_memory_block" not in src
    assert "tr.cky" not in src                      # no private probe needles


def test_diagnostic_callers_aligned_to_contract():
    stress = (REPO_ROOT / "scripts" / "dashboard_stress.py").read_text()
    assert "runner_contract import resolve" in stress
    assert '"options": {"num_ctx": rc.num_ctx}' in stress
    airlive = (REPO_ROOT / "tests" / "test_steal_list_strict.py").read_text()
    assert "_rc.num_ctx" in airlive


# --------------------------------------------------- zero-reload proof
def _ollama_up() -> bool:
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/version")
        with urllib.request.urlopen(req, timeout=2) as r:
            return bool(json.loads(r.read().decode()).get("version"))
    except Exception:
        return False


@pytest.mark.skipif(not _ollama_up(), reason="Ollama unavailable (DEGRADED)")
def test_repeated_mixed_transitions_have_zero_reload_drift():
    """Live proof on the real runner: mixed classifier/chat/summary
    transitions carry identical material options and produce no >1s load.
    Full 100-transition gate runs offline via scripts/benchmark_transitions.py."""
    from voicebridge.benchmarking import harness as H
    corpus_dir = REPO_ROOT / "benchmarks" / "corpus"
    from voicebridge.benchmarking.corpus import load_corpus
    corpus = load_corpus(corpus_dir)
    acc = [c for c in corpus["acceptance"]
           if c.category in ("short_chat", "ambiguous_routing")]
    cases = [{
        "case_id": c.case_id, "classifier_a": c.text, "chat_a": c.text,
        "classifier_b": c.text, "chat_b": c.text} for c in acc[:3]]
    ccfg = {**H.resolve_caller_config(_Cfg({"intent": {}}))}
    recorder = H.TransportRecorder()
    with recorder:
        rows = H.run_transition_iterations(recorder, ccfg, cases,
                                           iterations=3,
                                           deadline_monotonic=None,
                                           tts_fn=None)
    assert rows, "no measurements recorded"
    drift = recorder.option_drift_events()
    assert drift == [], f"option drift present -> reload risk: {drift}"
    warm_over_1s = [r for r in rows if r.get("warm_cold") == "warm"
                    and (r.get("gen_metrics", {}).get("load_duration") or 0)
                    > 1_000_000_000]
    assert warm_over_1s == [], \
        f"warm steps reloaded the runner: {[r['wall_ms'] for r in warm_over_1s]}"
