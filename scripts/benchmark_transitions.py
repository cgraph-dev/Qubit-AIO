#!/usr/bin/env python3
"""Packet 4A benchmark CLI: fixed-corpus transition measurements.

Modes (outputs are always written to separate directories):
  unit        offline corpus/L0 evaluation; never touches the network
  diagnostic  live Ollama measurements, warm-only; never changes residency
  live        opt-in cold phase: unload -> measure -> restore -> verify

This tool changes no model defaults, aliases, Modelfile, runner options, or
production runtime. The live mode temporarily unloads the resident model and
must restore it (typed report, rule 32).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.benchmarking.corpus import CorpusError, load_corpus  # noqa: E402
from voicebridge.benchmarking.harness import (  # noqa: E402
    BenchmarkError, OutputLock)
from voicebridge.benchmarking import harness as H  # noqa: E402
from voicebridge.benchmarking import unit_mode as UM  # noqa: E402
from voicebridge.config import Config  # noqa: E402

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_DEGRADED = 2


def _default_out() -> Path:
    base = Path.home() / ".local" / "state" / "voicebridge" / "benchmarks"
    return base


def build_tts_fn(cfg: Config, enabled: bool) -> Optional[Any]:
    """First-audio-ready probe: synthesize-only, never plays sound."""
    if not enabled:
        return None
    try:
        from voicebridge.tts.piper import PiperTTS
        import tempfile
        tts = PiperTTS(model_dir=cfg.model_dir())
        tmpdir = Path(tempfile.mkdtemp(prefix="vb-bench-tts-"))
        tts.load()

        def synth(text: str) -> float:
            t0 = time.monotonic()
            out = tmpdir / "first_audio_probe.wav"
            tts.synthesize(text[:200], out_path=out)
            if not out.exists():
                raise RuntimeError("synthesis produced no file")
            return (time.monotonic() - t0) * 1000.0

        return synth
    except Exception:
        return None


def run_unit(args: argparse.Namespace, corpus) -> tuple[int, Dict[str, Any]]:
    rows = UM.evaluate_offline(corpus["acceptance"] + corpus["holdout"])
    summary = UM.summarize_unit(rows)
    env = {"mode": "unit", "note": "offline; no network access performed"}
    with OutputLock(Path(args.out)):
        run_dir = H.write_run(Path(args.out), "unit", env,
                              rows, {"summary": summary})
    print(json.dumps({"run_dir": str(run_dir), **summary}, indent=2))
    degraded = summary["n_skipped_requires_model"] > 0 or summary["n_errors"] > 0
    return EXIT_DEGRADED if degraded else EXIT_OK


def _transition_cases(corpus) -> List[Dict[str, str]]:
    acc = [c for c in corpus["acceptance"]
           if c.category in ("short_chat", "ambiguous_routing")]
    hold = [c for c in corpus["holdout"]
            if c.category in ("short_chat", "ambiguous_routing")]
    pool = acc + hold
    cases: List[Dict[str, str]] = []
    for i, c in enumerate(pool):
        other = pool[(i + 1) % len(pool)]
        cases.append({
            "case_id": f"{c.case_id}+{other.case_id}",
            "classifier_a": c.text,
            "chat_a": c.text,
            "classifier_b": other.text,
            "chat_b": other.text,
        })
    if not cases:
        raise BenchmarkError("corpus produced no transition cases")
    return cases


def run_live_or_diagnostic(args: argparse.Namespace, corpus,
                           cfg: Config) -> tuple[int, Dict[str, Any]]:
    ccfg = H.resolve_caller_config(cfg)
    url = args.url or ccfg["url"]
    model = args.model or ccfg["chat_model"]
    ps0 = H.resident_state(url, model)
    env = H.environment_snapshot(url, model)
    env["caller_config"] = {**ccfg, "url": url, "effective_model": model}
    cases = _transition_cases(corpus)

    prior_resident = bool(ps0.get("resident"))
    prior_ctx = ps0.get("ctx")
    restore_report: Optional[Dict[str, Any]] = None
    cold_note: Optional[str] = None
    stat_start = H.read_proc_stat()

    recorder = H.TransportRecorder()
    unload_attempted = False

    if args.mode == "live":
        if prior_resident:
            print(f"[live] unloading resident {model} for cold measurement "
                  "(will restore afterwards)...", flush=True)
            try:
                H.unload_model(url, model)
                unload_attempted = True
                if not H.wait_until_unloaded(url, model):
                    cold_note = "model did not fully unload before measurement"
                    unload_attempted = False
            except Exception as exc:
                cold_note = f"unload failed: {str(exc)[:120]}"
                unload_attempted = False
        else:
            cold_note = "model was not resident before the run; first step is naturally cold"

    deadline = time.monotonic() + args.deadline_s
    with recorder:
        rows = H.run_transition_iterations(
            recorder, {**ccfg, "url": url},
            cases=cases,
            iterations=args.iterations,
            deadline_monotonic=deadline,
            tts_fn=build_tts_fn(cfg, not args.no_tts),
        )

    stat_end = H.read_proc_stat()
    drift = recorder.option_drift_events()
    summary = H.summarize_rows(rows)
    summary["option_drift_events"] = drift
    summary["option_drift_exposes_reload_risk"] = any(
        e.get("num_ctx_conflict") or e.get("keep_alive_conflict")
        for e in drift)
    summary["cpu_busy_fraction_during_run"] = H.cpu_busy_fraction(
        stat_start, stat_end)
    summary["gpu_after"] = H.gpu_sample()

    if args.mode == "live":
        if unload_attempted or prior_resident is False:
            restore_report = H.restore_residency(url, model, prior_ctx)
            summary["restore_report"] = restore_report

    mode_label = args.mode
    with OutputLock(Path(args.out)):
        run_dir = H.write_run(Path(args.out), mode_label, env, rows,
                              {"summary": summary})

    exit_code = EXIT_OK
    notes: List[str] = []
    if summary["total_errors"]:
        exit_code = max(exit_code, EXIT_DEGRADED)
        notes.append("measurement errors present (see transitions.jsonl)")
    if cold_note:
        exit_code = max(exit_code, EXIT_DEGRADED)
        notes.append(cold_note)
    if args.mode == "live":
        rr = summary.get("restore_report") or {}
        if not rr.get("restored"):
            notes.append("DEGRADED — RUNTIME STATE CHANGED: residency was "
                         "not verified restored")
            exit_code = EXIT_DEGRADED
        elif rr.get("state_changed_from_prior_context"):
            notes.append("DEGRADED — RUNTIME STATE CHANGED: restored context "
                         "differs from pre-run context")
            exit_code = EXIT_DEGRADED
    if not ps0.get("reachable"):
        exit_code = EXIT_DEGRADED
        notes.append("Ollama unreachable at start; measurements are failures")

    result = {
        "run_dir": str(run_dir),
        "mode": mode_label,
        "exit": exit_code,
        "notes": notes,
        "summary": {k: v for k, v in summary.items()
                    if k != "option_drift_events"},
        "option_drift_event_count": len(drift),
    }
    print(json.dumps(result, indent=2))
    return exit_code


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=("unit", "diagnostic", "live"),
                   required=True)
    p.add_argument("--iterations", type=int, default=5)
    p.add_argument("--deadline-s", type=float, default=600.0)
    p.add_argument("--corpus-dir", type=Path,
                   default=REPO_ROOT / "benchmarks" / "corpus")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--url", default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--no-tts", action="store_true",
                   help="skip first-audio synthesis probe")
    args = p.parse_args(argv)
    if args.out is None:
        args.out = _default_out() / args.mode
    if args.iterations < 1:
        p.error("--iterations must be >= 1")

    try:
        corpus = load_corpus(args.corpus_dir)
    except CorpusError as exc:
        print(f"CORPUS INVALID: {exc}", file=sys.stderr)
        return EXIT_FAILED

    cfg = Config.load()
    try:
        if args.mode == "unit":
            return run_unit(args, corpus)
        return run_live_or_diagnostic(args, corpus, cfg)
    except BenchmarkError as exc:
        print(f"BENCHMARK ERROR: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
