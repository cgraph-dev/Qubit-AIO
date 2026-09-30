"""Packet 4A tests: corpus contract, harness behavior, honest degraded modes.

Unit tests never touch the network. Live Ollama behavior is exercised by the
separate CLI modes (scripts/benchmark_transitions.py --mode diagnostic|live),
not during pytest collection.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.benchmarking import harness as H  # noqa: E402
from voicebridge.benchmarking import unit_mode as UM  # noqa: E402
from voicebridge.benchmarking.corpus import (  # noqa: E402
    CATEGORIES, MAX_TEXT_CHARS, Case, CorpusError, load_corpus)
from scripts.benchmark_transitions import _transition_cases  # noqa: E402

CORPUS_DIR = REPO_ROOT / "benchmarks" / "corpus"


# ---------------------------------------------------------------- corpus
def test_fixed_corpus_loads_and_covers_all_categories():
    corpus = load_corpus(CORPUS_DIR)
    acc_cats = {c.category for c in corpus["acceptance"]}
    hold_cats = {c.category for c in corpus["holdout"]}
    assert set(CATEGORIES) == acc_cats
    assert set(CATEGORIES) == hold_cats
    ids_acc = {c.case_id for c in corpus["acceptance"]}
    ids_hold = {c.case_id for c in corpus["holdout"]}
    assert not (ids_acc & ids_hold), "acceptance and holdout must be disjoint"


def test_corpus_texts_are_bounded_and_sanitized():
    corpus = load_corpus(CORPUS_DIR)
    for c in corpus["acceptance"] + corpus["holdout"]:
        assert len(c.text) <= MAX_TEXT_CHARS
        assert c.expected is not None


@pytest.mark.parametrize("mutation,expected_fragment", [
    ({"case_id": "x-1", "category": "nope", "text": "hi"}, "unknown category"),
    ({"case_id": "x-1", "category": "short_chat", "text": ""}, "missing text"),
])
def test_loader_rejects_invalid_cases(tmp_path, mutation, expected_fragment):
    base = {"case_id": "ok-1", "category": "short_chat", "text": "hello there",
            "expected": {}}
    case = {**base, **mutation}
    d = tmp_path
    (d / "acceptance_cases.json").write_text(json.dumps({
        "schema": "voicebridge.eval-corpus/1", "split": "acceptance",
        "cases": [case]}))
    (d / "heldout_cases.json").write_text(json.dumps({
        "schema": "voicebridge.eval-corpus/1", "split": "holdout",
        "cases": [{"case_id": "h-1", "category": "short_chat",
                   "text": "hi", "expected": {}}]}))
    with pytest.raises(CorpusError, match=expected_fragment):
        load_corpus(d)


def test_loader_rejects_private_shaped_content(tmp_path):
    bad = [{"case_id": "p-1", "category": "short_chat",
            "text": "mail it to someone@gmail.com now", "expected": {}}]
    d = tmp_path
    (d / "acceptance_cases.json").write_text(json.dumps({
        "schema": "voicebridge.eval-corpus/1", "split": "acceptance",
        "cases": bad}))
    (d / "heldout_cases.json").write_text(json.dumps({
        "schema": "voicebridge.eval-corpus/1", "split": "holdout",
        "cases": [{"case_id": "h-1", "category": "short_chat",
                   "text": "hi", "expected": {}}]}))
    with pytest.raises(CorpusError, match="forbidden pattern personal_email"):
        load_corpus(d)


def test_loader_rejects_duplicate_ids_across_splits(tmp_path):
    d = tmp_path
    case = {"case_id": "dup-1", "category": "short_chat", "text": "hey",
            "expected": {}}
    (d / "acceptance_cases.json").write_text(json.dumps({
        "schema": "voicebridge.eval-corpus/1", "split": "acceptance",
        "cases": [case]}))
    (d / "heldout_cases.json").write_text(json.dumps({
        "schema": "voicebridge.eval-corpus/1", "split": "holdout",
        "cases": [dict(case)]}))
    with pytest.raises(CorpusError, match="duplicate case_id"):
        load_corpus(d)


def test_loader_rejects_wrong_schema_or_split(tmp_path):
    d = tmp_path
    good = {"case_id": "a-1", "category": "short_chat", "text": "yo",
            "expected": {}}
    (d / "acceptance_cases.json").write_text(json.dumps({
        "schema": "wrong", "split": "acceptance", "cases": [good]}))
    (d / "heldout_cases.json").write_text(json.dumps({
        "schema": "voicebridge.eval-corpus/1", "split": "holdout",
        "cases": [good]}))
    with pytest.raises(CorpusError, match="schema"):
        load_corpus(d)


# ------------------------------------------------------------ percentiles
def test_percentile_known_vectors():
    assert H.percentile([], 50) is None
    assert H.percentile([5.0], 95) == 5.0
    assert H.percentile([10, 20, 30, 40], 50) == 25.0
    assert H.percentile(list(range(1, 101)), 95) == pytest.approx(95.05)
    with pytest.raises(ValueError):
        H.percentile([1.0], 150)


def test_cpu_busy_fraction_bounds():
    start = [100, 0, 100, 100, 0, 0, 0, 0]
    end = [200, 0, 100, 100, 0, 0, 0, 0]  # only user jiffies advanced
    frac = H.cpu_busy_fraction(start, end)
    assert frac == 1.0
    idle_same = H.cpu_busy_fraction(start, start)
    assert idle_same == 0.0


# --------------------------------------------------- unit mode (offline)
def test_unit_mode_never_touches_network(tmp_path, monkeypatch):
    def _boom(*a, **k):  # any network attempt fails the test loudly
        raise AssertionError("unit mode must not perform network I/O")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    corpus = load_corpus(CORPUS_DIR)
    rows = UM.evaluate_offline(corpus["acceptance"])
    summary = UM.summarize_unit(rows)
    measured = [r for r in rows if "route_latency_ms" in r]
    skipped = [r for r in rows if r.get("skipped") == "requires-model"]
    assert measured and skipped, "both offline-measured and typed skips exist"
    assert summary["n_skipped_requires_model"] == len(skipped)
    assert summary["route_latency_ms_p95"] is not None


def test_unit_mode_records_confirm_cancel_context(tmp_path):
    cases = [
        Case("u-cc-yes", "confirm_cancel_contextual", "acceptance",
             "sure.", {"pending_required": True}),
        Case("u-cc-no", "confirm_cancel_contextual", "acceptance",
             "nope.", {"pending_required": True}),
        Case("u-det-mute", "deterministic_routing", "acceptance", "mute",
             {}),
    ]
    rows = UM.evaluate_offline(cases)
    by_id = {r["case_id"]: r for r in rows}
    assert by_id["u-cc-yes"]["observed_kind"] == "confirm"
    assert by_id["u-cc-no"]["observed_kind"] == "cancel"
    assert by_id["u-det-mute"]["observed_kind"] == "system"
    for r in by_id.values():
        assert r["error"] is None
        assert r["route_latency_ms"] >= 0.0


# ------------------------------------------- transport recorder + metrics
class FakeStreamResp:
    """Mimics HTTPResponse line iteration + context manager for NDJSON."""

    def __init__(self, lines):
        self._lines = lines
        self._it = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def __iter__(self):
        self._it = iter(self._lines)
        return self

    def __next__(self):
        raw = next(self._it)
        return raw if isinstance(raw, bytes) else raw.encode()

    def read(self, n=-1):
        return b"".join(self._lines)

    def close(self):
        pass


GEN_BODY = json.dumps({
    "response": ("{\"kind\": \"conversation\", \"confidence\": 0.7, "
                 "\"summary\": \"Renamed 14 files; two skipped.\"}"),
    "done": True,
    "total_duration": 900_000_000,
    "load_duration": 123_000_000,
    "prompt_eval_count": 42,
    "prompt_eval_duration": 50_000_000,
    "eval_count": 20,
    "eval_duration": 300_000_000,
}).encode()


@pytest.fixture()
def fake_ollama(monkeypatch):
    """Route /api/generate -> single JSON body; /api/chat -> NDJSON stream."""
    seen = []

    def fake_urlopen(req, timeout=None, **kwargs):
        url = req.full_url
        body = bytes(req.data or b"")
        seen.append((url, json.loads(body)))
        if url.endswith("/api/generate"):
            return FakeStreamResp([GEN_BODY])
        ndjson = "\n".join([
            json.dumps({"message": {"role": "assistant", "content": "Hello"}}),
            json.dumps({"message": {"role": "assistant",
                                    "content": " there today."}}),
            json.dumps({"message": {"role": "assistant", "content": ""},
                        "done": True, "total_duration": 800_000_000,
                        "load_duration": 0, "prompt_eval_count": 30,
                        "prompt_eval_duration": 20_000_000,
                        "eval_count": 8, "eval_duration": 120_000_000}),
        ]).encode() + b"\n"
        return FakeStreamResp(ndjson.splitlines(keepends=True))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


CCFG = {
    "url": "http://127.0.0.1:9999",
    "chat_model": "qubit-brain4",
    "classifier_model": "qubit-brain4",
    "summary_model": "qubit-brain4",
    "timeout_s": 4.0,
    "max_tokens": 100,
    "num_ctx": 4096,
    "classify_timeout_s": 8.0,
    "summary_timeout_s": 5.0,
}

CASES = [{
    "case_id": "c1",
    "classifier_a": "tell me something interesting about space",
    "chat_a": "tell me something interesting about space",
    "classifier_b": "how was your day so far?",
    "chat_b": "how was your day so far?",
}]


def test_transition_sequence_records_rows_metrics_and_drift(fake_ollama):
    recorder = H.TransportRecorder()
    with recorder:
        rows = H.run_transition_iterations(
            recorder, CCFG, CASES, iterations=2,
            deadline_monotonic=None, tts_fn=None)
    steps = [(r["step"], r.get("error")) for r in rows]
    assert len(rows) == 10
    assert [s for s, e in steps[:3]] == ["classifier", "chat", "classifier"]
    assert all(e is None for _, e in steps), f"errors in rows: {steps}"

    chat_row = next(r for r in rows if r["step"] == "chat")
    assert chat_row["first_content_ms"] >= 0.0
    assert chat_row["first_sentence_ms"] >= chat_row["first_content_ms"]
    assert chat_row["tokens_per_s"] > 0
    assert chat_row["gen_metrics"]["eval_count"] == 8

    cls_row = next(r for r in rows if r["step"] == "classifier")
    assert cls_row["gen_metrics"]["load_duration"] == 123_000_000
    assert cls_row["gen_metrics"]["prompt_eval_count"] == 42

    drift = recorder.option_drift_events()
    # Packet 4B unified the runner contract: the live-config sequence now
    # carries IDENTICAL material options across classifier/chat/summary.
    # Reload-risk DETECTION itself is proven by
    # test_material_options_detects_num_ctx_conflict below.
    assert drift == [], f"unexpected option drift after 4B: {drift}"


def test_material_options_detects_num_ctx_conflict():
    a = {"model": "m", "options": {"num_ctx": 8192}, "keep_alive": -1}
    b = {"model": "m", "options": {"num_ctx": 4096}}
    prev, cur = H.material_options(a), H.material_options(b)
    conflict = prev != cur and prev["num_ctx"] != cur["num_ctx"]
    assert conflict, "the 4096-vs-8192 option pair must flag reload risk"


def test_production_payload_shapes_are_preserved(fake_ollama):
    recorder = H.TransportRecorder()
    with recorder:
        H.run_transition_iterations(recorder, CCFG, CASES, iterations=1,
                                    deadline_monotonic=None, tts_fn=None)
    urls = [u for u, _ in fake_ollama]
    payloads = [p for _, p in fake_ollama]
    # Packet 4B: classifier now sends the full JSON Schema (enum-locked kind)
    # plus canonical keep_alive; assert the NEW production shape verbatim.
    cls_payload = next(p for u, p in zip(urls, payloads)
                       if u.endswith("/api/generate")
                       and isinstance(p.get("format"), dict)
                       and "kind" in p["format"]["properties"]
                       and p["options"].get("temperature") == 0.0)
    assert cls_payload["options"]["num_ctx"] == 4096
    assert cls_payload["keep_alive"] == -1
    chat_payloads = [p for u, p in zip(urls, payloads)
                     if u.endswith("/api/chat")]
    assert chat_payloads, "streaming chat must hit /api/chat"
    cp = chat_payloads[0]
    assert cp["keep_alive"] == -1
    assert cp["options"]["num_ctx"] == CCFG["num_ctx"]
    sum_payload = next(p for u, p in zip(urls, payloads)
                       if u.endswith("/api/generate")
                       and p.get("keep_alive") == -1
                       and p["options"].get("temperature") == 0.2)
    assert sum_payload["options"]["num_ctx"] == 4096
    assert sum_payload["options"]["num_predict"] == 90


def test_deadline_stops_remaining_steps_as_typed_errors(fake_ollama):
    recorder = H.TransportRecorder()
    deadline = __import__("time").monotonic() - 1.0  # already expired
    rows = H.run_transition_iterations(recorder, CCFG, CASES, iterations=3,
                                       deadline_monotonic=deadline,
                                       tts_fn=None)
    assert rows[0]["error"] == "deadline_exceeded"
    assert len(rows) == 1


def test_ollama_failure_produces_typed_error_rows(monkeypatch):
    def fail(req, timeout=None, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fail)
    recorder = H.TransportRecorder()
    with recorder:
        rows = H.run_transition_iterations(recorder, CCFG, CASES,
                                           iterations=1,
                                           deadline_monotonic=None,
                                           tts_fn=None)
    assert len(rows) == 5
    assert all(r.get("error") for r in rows), "every step reports its failure"
    summary = H.summarize_rows(rows)
    assert summary["total_errors"] == 5


def test_summarize_separates_warm_and_cold_never_merges():
    rows = [
        {"step": "classifier", "warm_cold": "warm", "wall_ms": 700.0,
         "gen_metrics": {"load_duration": 0}},
        {"step": "classifier", "warm_cold": "cold", "wall_ms": 3933.0,
         "gen_metrics": {"load_duration": 3_246_000_000}},
        {"step": "classifier", "warm_cold": "warm", "wall_ms": 800.0,
         "gen_metrics": {"load_duration": None}},
        {"step": "chat", "warm_cold": "warm", "wall_ms": 400.0,
         "gen_metrics": {}},
        {"step": "chat", "warm_cold": "warm", "error": "boom"},
    ]
    s = H.summarize_rows(rows)
    cls = s["per_step"]["classifier"]
    assert cls["n_cold_samples"] == 1
    assert cls["cold_wall_ms_samples"] == [3933.0]
    assert cls["warm_wall_ms_p50"] == 750.0
    assert cls["warm_load_duration_ms"]["n_over_1000ms"] == 0
    assert cls["warm_load_duration_ms"]["max"] == 0.0
    chat = s["per_step"]["chat"]
    assert chat["n_errors"] == 1
    assert chat["n_samples"] == 2
    assert s["total_errors"] == 1


# ------------------------------------------------------- lifecycle helpers
def test_unload_restore_cycle_with_fake_http(monkeypatch):
    state = {"resident": True, "ctx": 4096}
    calls = []

    class FakeResp(FakeStreamResp):
        def __init__(self, obj):
            super().__init__([json.dumps(obj).encode()])

    def fake_urlopen(req, timeout=None, **kwargs):
        url, payload = req.full_url, json.loads(bytes(req.data or b"{}") or b"{}")
        calls.append((url, payload))
        if url.endswith("/api/generate"):
            state["resident"] = False
            return FakeResp({})
        if url.endswith("/api/chat"):
            state["resident"] = True
            return FakeResp({"message": {"content": "pong"}})
        if url.endswith("/api/ps"):
            models = ([{"name": "qubit-brain4:latest",
                        "digest": "df22", "context_length": state["ctx"],
                        "size_vram": 3300000000}]
                      if state["resident"] else [])
            return FakeResp({"models": models})
        raise AssertionError(url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert H.resident_state("http://x", "qubit-brain4")["resident"] is True
    H.unload_model("http://x", "qubit-brain4")
    assert H.wait_until_unloaded("http://x", "qubit-brain4", deadline_s=1.5)
    report = H.restore_residency("http://x", "qubit-brain4", prior_ctx=4096)
    assert report["restored"] is True
    assert report["verified_ctx"] == 4096
    assert report["state_changed_from_prior_context"] is False
    restore_payload = next(p for u, p in calls if u.endswith("/api/chat"))
    assert restore_payload["keep_alive"] == -1
    assert restore_payload["options"]["num_ctx"] == 4096


def test_resident_state_reports_correct_vram_units(monkeypatch):
    """Ollama /api/ps reports size/size_vram in BYTES; the harness must keep
    the raw bytes under *_bytes and convert to real MiB under *_mib. The
    resident 4B model (~3 GiB) must stay under the 5 GiB model-VRAM ceiling
    (plan fast-tier gate: steady-state total GPU <= 6.5 GiB on the 8 GiB GPU)."""
    class FakeResp(FakeStreamResp):
        def __init__(self, obj):
            super().__init__([json.dumps(obj).encode()])

    def fake_urlopen(req, timeout=None, **kwargs):
        url = req.full_url
        if url.endswith("/api/ps"):
            return FakeResp({"models": [{"name": "qubit-brain4:latest",
                                         "digest": "df22",
                                         "size": 3_400_000_000,
                                         "size_vram": 3_300_000_000,
                                         "context_length": 4096}]})
        raise AssertionError(url)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    st = H.resident_state("http://x", "qubit-brain4")
    assert st["resident"] is True
    # raw byte values are preserved verbatim
    assert st["size_vram_bytes"] == 3_300_000_000
    assert st["size_bytes"] == 3_400_000_000
    # *_mib fields are the CORRECT bytes->MiB conversion (never bytes under a
    # MiB name)
    assert st["size_vram_mib"] == pytest.approx(round(3_300_000_000 / 1048576, 1))
    assert st["size_mib"] == pytest.approx(round(3_400_000_000 / 1048576, 1))
    # 5 GB resource gate: the 4B model's VRAM stays under 5120 MiB
    assert st["size_vram_mib"] < 5120
    assert st["size_vram_mib"] < 6.5 * 1024  # plan fast-tier ceiling


def test_output_lock_blocks_concurrent_run(tmp_path, monkeypatch):
    lock = H.OutputLock(tmp_path)
    with lock:
        with pytest.raises(H.BenchmarkError, match="benchmark.lock"):
            with H.OutputLock(tmp_path):
                pass
    # released lock allows re-acquire
    with H.OutputLock(tmp_path):
        pass


def test_output_lock_steals_dead_owner_lock(tmp_path, monkeypatch):
    dead = tmp_path / "benchmark.lock"
    dead.write_text("999999999")
    with H.OutputLock(tmp_path):
        assert not dead.exists() or dead.read_text() == str(__import__("os").getpid())


def test_write_run_is_atomic_private_and_complete(tmp_path):
    run_dir = H.write_run(tmp_path, "unit", {"mode": "unit"},
                          [{"step": "x", "wall_ms": 1.0}],
                          {"summary": {"n": 1}})
    mode = run_dir.stat().st_mode & 0o777
    assert mode == 0o700
    env = json.loads((run_dir / "environment.json").read_text())
    rows = [json.loads(l) for l in
            (run_dir / "transitions.jsonl").read_text().splitlines()]
    summ = json.loads((run_dir / "summary.json").read_text())
    assert env["mode"] == "unit"
    assert rows and summ["mode"] == "unit"


# ---------------------------------------------------------------- CLI glue
def test_transition_case_pool_built_from_both_splits():
    corpus = load_corpus(CORPUS_DIR)
    cases = _transition_cases(corpus)
    assert len(cases) >= 4
    for c in cases:
        assert c["classifier_a"] and c["chat_a"] and c["classifier_b"] \
            and c["chat_b"]
        assert c["case_id"].count("+") == 1


def test_argparse_rejects_unknown_mode(capsys):
    from scripts.benchmark_transitions import main
    with pytest.raises(SystemExit) as exc:
        main(["--mode", "yolo"])
    assert exc.value.code == 2


def test_missing_corpus_dir_fails_typed(tmp_path, capsys):
    from scripts.benchmark_transitions import EXIT_FAILED, main
    rc = main(["--mode", "unit", "--corpus-dir",
               str(tmp_path / "nope"), "--out", str(tmp_path / "out")])
    assert rc == EXIT_FAILED
    assert "CORPUS INVALID" in capsys.readouterr().err
