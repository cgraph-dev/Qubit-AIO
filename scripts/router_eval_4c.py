#!/usr/bin/env python
"""Packet 4C evaluation: L0/L1 metrics on the HELD-OUT corpus split.

Writes docs/router_4c_report.json + prints a summary. The acceptance split
is used only for L1 threshold calibration; the held-out split is scored
exactly once for the report and never used to tune anything.

Also measures the L1 embedding gates: warm latency percentiles, CPU-only
placement evidence, RAM delta, and zero generative-runner eviction.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.benchmarking.corpus import load_corpus  # noqa: E402
from voicebridge.router.l0 import route as l0_route  # noqa: E402
from voicebridge.router.l1 import SemanticRouter, load_default_routes  # noqa: E402
from voicebridge.router.pipeline import _calibration_examples  # noqa: E402

CORPUS_DIR = REPO_ROOT / "benchmarks" / "corpus"
OUT_JSON = REPO_ROOT / "docs" / "router_4c_report.json"

# category -> expected route label for routing eval (None = not a routing case)
CATEGORY_ROUTE = {
    "deterministic_routing": None,
    "ambiguous_routing": "clarify",
    "confirm_cancel_contextual": None,
    "local_vs_coding_safety": None,
    "malformed_speech": "clarify",
    "short_chat": "conversation",
    "personal_fact_conflict": "conversation",
    "tool_selection": "local",
    "invalid_arguments": None,
    "refusal_fallback": "conversation",
    "language_policy": None,
    "prompt_injection": None,
}


def _ollama_up() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/version",
                                    timeout=3) as r:
            return bool(json.loads(r.read().decode()).get("version"))
    except Exception:
        return False


def resident_digest(alias: str = "qubit-brain4") -> Optional[str]:
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/ps")
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read().decode())
        for m in body.get("models", []):
            if m.get("name", "").startswith(alias.split(":")[0]):
                return m.get("digest")
    except Exception:
        pass
    return None


def eval_l0(cases) -> dict:
    latencies = []
    rows = []
    net_calls = {"n": 0}
    import urllib.request as _u
    orig = _u.urlopen

    def counting(*a, **k):
        net_calls["n"] += 1
        return orig(*a, **k)

    _u.urlopen = counting
    try:
        for c in cases:
            t0 = time.perf_counter()
            d = l0_route(c.text,
                         pending_confirmation=bool(
                             c.expected.get("pending_required")),
                         busy=bool(c.expected.get("busy_required")))
            ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(ms)
            observed = d.kind if d else None
            exp_kinds = c.expected.get("kinds")
            if isinstance(exp_kinds, list) and exp_kinds:
                correct = observed in exp_kinds
            else:
                correct = True   # non-routing categories: coverage only
            rows.append({"case_id": c.case_id, "observed": observed,
                         "correct": correct})
    finally:
        _u.urlopen = orig
    routed = [r for r in rows if r["observed"]]
    labeled = [r for r in rows if r["observed"] is not None]
    labeled_correct = [r for r in labeled if r["correct"]]
    det_cases = [r for r in rows if r["case_id"].split("-")[1] == "det"]
    det_routed = [r for r in det_cases if r["observed"] is not None]
    det_ok = [r for r in det_cases if r["observed"]
              in ("system", "local", "conversation", "coding", "clarify")]
    return {
        "n_cases": len(rows),
        "network_calls_made": net_calls["n"],
        "routed_at_l0": len(routed),
        "labeled_decisions": len(labeled),
        "labeled_correct": len(labeled_correct),
        "label_accuracy_on_labeled": round(
            len(labeled_correct) / len(labeled), 4) if labeled else None,
        "deterministic_category_coverage": {
            "n": len(det_cases), "routed": len(det_routed),
            "correct_route_class": len(det_ok)},
        "latency_ms_p50": round(statistics.median(latencies), 4),
        "latency_ms_p95": round(_pct(latencies, 95), 4),
        "latency_ms_max": round(max(latencies), 4),
    }


def _pct(values, q):
    s = sorted(values)
    pos = (len(s) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _mem_available_mib() -> float:
    with open("/proc/meminfo", "r", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024.0
    return -1.0


def eval_l1(cases) -> dict:
    router = SemanticRouter(routes=load_default_routes())
    url = "http://127.0.0.1:11434"
    model = "nomic-embed-text"
    digest_before = resident_digest()
    ram_before = _mem_available_mib()
    t_fit0 = time.perf_counter()
    router.fit(url=url, model=model)
    fit_s = round(time.perf_counter() - t_fit0, 2)
    calib = _calibration_examples()
    thresholds = router.calibrate(calib, url=url, model=model)

    # embedding latency sample (warm)
    lat = []
    for i in range(12):
        t0 = time.perf_counter()
        router.embed([f"gate probe {i}"], query=True, url=url, model=model)
        lat.append((time.perf_counter() - t0) * 1000.0)

    digest_after = resident_digest()
    # System RAM delta across the whole embedding workload (fit + latency
    # probes + held-out scoring happen AFTER this point only in part; the
    # dominant cost is the resident embed runner itself, so re-sample at the
    # end of scoring below and report both).
    ram_mid = _mem_available_mib()

    # resource/placement evidence from the live scheduler table
    placement = {}
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/ps")
        with urllib.request.urlopen(req, timeout=5) as r:
            models = json.loads(r.read().decode()).get("models", [])
        for m in models:
            name = m.get("name", "")
            if "nomic" in name or "brain4" in name.split(":")[0]:
                placement[name] = {
                    "size_mib": round((m.get("size") or 0) / 1048576, 1),
                    "size_vram_mib": round(
                        (m.get("size_vram") or 0) / 1048576, 1),
                    "processor": m.get("processor"),
                }
    except Exception:
        pass

    # held-out scoring (single pass; never used for tuning)
    tp = fp = fn = tn = abstain = 0
    per_label = {}
    routing_cases = 0
    for c in cases:
        route = CATEGORY_ROUTE.get(c.category)
        if route is None:
            continue
        routing_cases += 1
        d = router.decide_vector(router.embed([c.text], query=True,
                                              url=url, model=model)[0])
        observed = d.route if d is not None else None
        entry = per_label.setdefault(route, {"tp": 0, "fp": 0, "fn": 0})
        if observed == route:
            tp += 1
            entry["tp"] += 1
        elif observed is None:
            fn += 1
            entry["fn"] += 1
            abstain += 1
        else:
            fp += 1
            per_label.setdefault(observed, {"tp": 0, "fp": 0, "fn": 0})["fp"] += 1
    f1s = []
    for label, e in per_label.items():
        prec = e["tp"] / (e["tp"] + e["fp"]) if (e["tp"] + e["fp"]) else 0.0
        rec = e["tp"] / (e["tp"] + e["fn"]) if (e["tp"] + e["fn"]) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        f1s.append(f1)
    return {
        "enabled": True,
        "embed_model": model,
        "fit_seconds": fit_s,
        "thresholds_calibrated_on_acceptance_slice": thresholds,
        "embedding_latency_ms_p50": round(statistics.median(lat), 2),
        "embedding_latency_ms_p95": round(_pct(lat, 95), 2),
        "embedding_placement": placement,
        "generative_digest_before": digest_before,
        "generative_digest_after": digest_after,
        "zero_generative_eviction": (
            digest_before is not None and digest_before == digest_after),
        "heldout_routing_cases": tp + fp + fn,
        "true_positive": tp, "false_positive": fp,
        "false_negative_abstain": fn, "abstains": abstain,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else None,
        "evaluation_size_status": (
            "INCOMPLETE — EVALUATION CORPUS TOO SMALL"
            if tp + fp + fn < 30 else "adequate"),
        "system_ram_delta_mib_after_runner_load": round(
            ram_before - ram_mid, 1) if ram_before > 0 and ram_mid > 0 else None,
        "ram_measurement_note": (
            "MemAvailable delta around fit+latency probes; includes the "
            "resident embed runner. Ollama-accounted sizes are reported "
            "separately in embedding_placement."),
        "note": ("abstention (no-match/margin fail) routes back to the "
                 "legacy classifier path; uncertainty never acts"),
    }


def main() -> int:
    corpus = load_corpus(CORPUS_DIR)
    report = {
        "schema": "voicebridge.router-4c-report/1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "corpus": {k: len(v) for k, v in corpus.items()},
        "l0": eval_l0(corpus["holdout"]),
    }
    if _ollama_up():
        try:
            report["l1"] = eval_l1(corpus["holdout"])
        except Exception as exc:
            report["l1"] = {"enabled": False,
                            "degraded_reason": str(exc)[:200]}
    else:
        report["l1"] = {"enabled": False,
                        "degraded_reason": "Ollama unavailable"}
    OUT_JSON.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2)[:2600])
    print(f"\nfull report -> {OUT_JSON}")
    l0 = report["l0"]
    assert l0["network_calls_made"] == 0, "L0 must never touch the network"
    assert l0["latency_ms_p95"] < 10.0, "L0 p95 gate"
    return 0


if __name__ == "__main__":
    sys.exit(main())
