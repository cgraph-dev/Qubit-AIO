#!/usr/bin/env python3
"""Packet 6B evaluation gate: held-out set, regression scoring, promotion gate.

Modes:
  --mode unit   offline, deterministic, no network/model (default). Runs the
                fixed synthetic base set and, when a state dir is given, the
                private held-out set derived from accepted/corrected tasks.
  --mode live   opt-in; adds real Ollama residency/VRAM + warm-generation
                latency sampling via the Packet 4A harness. A skipped or
                degraded live check is reported as DEGRADED, never PASS.

The gate compares the candidate run against --baseline PER CATEGORY and PER
FAMILY. Safety-critical categories are strict. The gate only issues a decision;
it never promotes a model, adapter, preference, or skill automatically.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from voicebridge.evaluation import (  # noqa: E402
    CATEGORY_FAMILY, NEEDS_REVIEW, PromotionDecision, PromotionGate,
    base_cases, coverage_policy, evaluate_cases, load_eval_set, run_poisoning,
    score_run, summarize_poisoning,
)

EXIT_OK = 0
EXIT_REJECTED = 2
EXIT_NEEDS_REVIEW = 3
EXIT_DEGRADED = 4
EXIT_FAILED = 5

SET_REQUIREMENTS = {
    "base": ("base",),
    "private": ("private",),
    "all": ("base", "private"),
}

SAFETY_CRITICAL_NOTE = (
    "safety is gated strictly per-category; no weighted average can hide a "
    "safety regression")

# Measurement units recorded in every baseline so a unit mismatch can never be
# mistaken for a real regression.
UNITS = {
    "size": "bytes",
    "size_vram": "bytes",
    "size_mib": "mebibytes",
    "size_vram_mib": "mebibytes",
    "latency_ms": "milliseconds",
    "warm_chat_ms": "milliseconds",
}

# Model-VRAM ceiling for the resident 4B model on the 8 GiB GPU. The plan's
# fast-tier gate is steady-state total GPU <= 6.5 GiB; the 4B model (~3 GiB)
# must stay below 5 GiB with the configured headroom.
MODEL_VRAM_CEILING_MIB = 5120  # 5 GiB


def _git_rev() -> str:
    try:
        import subprocess
        out = subprocess.run(["git", "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _git_dirty() -> bool:
    try:
        import subprocess
        out = subprocess.run(["git", "status", "--porcelain"],
                             capture_output=True, text=True, timeout=3)
        return bool(out.stdout.strip())
    except Exception:
        return True  # fail closed: cannot prove a clean tree


def _runner_settings(mode: str, live: dict | None) -> dict:
    """Runner settings for provenance. Live mode uses resolve() (real digest,
    network); unit mode is config-derived and offline (digest None)."""
    if mode == "live" and live and live.get("runner"):
        return live["runner"]
    try:
        from voicebridge.config import Config
        cfg = Config.load()
        alias = (cfg.get("intent", "runner", "alias", default=None)
                 or cfg.get("intent", "local_llm", "model",
                            default="qubit-brain4"))
        num_ctx = int(cfg.get("intent", "runner", "num_ctx", default=None)
                      or cfg.get("intent", "num_ctx", default=None)
                      or cfg.get("intent", "local_llm", "num_ctx",
                                 default=4096) or 4096)
        keep_alive = int(cfg.get("intent", "runner", "keep_alive",
                                 default=None)
                         or cfg.get("intent", "local_llm", "keep_alive",
                                    default=-1) or -1)
        url = str(cfg.get("intent", "local_llm", "url",
                          default="http://127.0.0.1:11434"))
        return {"alias": alias, "url": url, "num_ctx": num_ctx,
                "keep_alive": keep_alive, "digest": None,
                "note": "offline mode: digest not fetched"}
    except Exception as exc:
        return {"error": str(exc)[:120]}


def _write_report(out_dir: Path, obj: dict) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(out_dir, 0o700)
    except OSError:
        pass
    path = out_dir / f"eval_6b_{time.strftime('%Y%m%dT%H%M%S')}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, indent=2))
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def _run(comp, cases, set_name):
    rows = evaluate_cases(cases, comp=comp)
    scores = score_run(rows)
    poison = run_poisoning(cases=[c for c in cases
                                  if c.category in ("prompt_injection",
                                                    "secret_handling")],
                           classifier=comp.classifier, ledger=comp.ledger,
                           memory=comp.memory)
    return {
        "set": set_name,
        "rows": rows,
        "scores": scores,
        "poisoning": summarize_poisoning(poison),
        "n_cases": len(cases),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Packet 6B evaluation gate")
    ap.add_argument("--mode", choices=("unit", "live"), default="unit")
    ap.add_argument("--set", choices=("base", "private", "all"),
                    default="all")
    ap.add_argument("--state-dir", default=None,
                    help="state dir for the private set (default: config)")
    ap.add_argument("--baseline", default=None,
                    help="baseline score JSON to gate against")
    ap.add_argument("--save-baseline", default=None,
                    help="write this run's score as a baseline JSON")
    ap.add_argument("--out", default=None,
                    help="directory for the versioned report JSON")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    args = ap.parse_args(argv)

    state_dir = args.state_dir
    if state_dir is None:
        try:
            from voicebridge.config import Config
            state_dir = Config.load().state_dir()
        except Exception:
            state_dir = None

    from voicebridge.evaluation.runner import EvalComponents
    comp = EvalComponents()
    try:
        sets = {"base": base_cases()}
        if args.set in ("private", "all"):
            if state_dir:
                sets["private"] = load_eval_set(state_dir)["private"]
            else:
                print("WARN: no state dir; private set empty", file=sys.stderr)
                sets["private"] = []
        if args.set == "private":
            sets.pop("base", None)

        combined = []
        report_sets = {}
        for name, cases in sets.items():
            res = _run(comp, cases, name)
            combined.extend(res["rows"])
            fam_counts: dict = {}
            for cat, c in res["scores"]["per_category"].items():
                fam = CATEGORY_FAMILY.get(cat, "?")
                fam_counts[fam] = fam_counts.get(fam, 0) + c["n"]
            report_sets[name] = {
                "n_cases": res["n_cases"],
                "scores": res["scores"],
                "poisoning": res["poisoning"],
                "coverage": fam_counts,
            }

        candidate = score_run(combined)
        candidate["note"] = SAFETY_CRITICAL_NOTE
        candidate["sets_evaluated"] = {
            name: rs["n_cases"] for name, rs in report_sets.items()}
        required_sets = SET_REQUIREMENTS[args.set]

        report = {
            "packet": "6B",
            "mode": args.mode,
            "sets": report_sets,
            "candidate": candidate,
            "coverage_policy": coverage_policy(),
            "decision": None,
        }

        # --- live evidence (opt-in, honest DEGRADED on failure) ---------
        degraded = False
        live = None
        if args.mode == "live":
            live = _live_sample(args.ollama_url)
            report["live"] = live
            if live.get("status") == "degraded":
                degraded = True
            if live.get("warm_chat_ms"):
                # fold real model latency into the candidate so the saved
                # live baseline and the live-vs-live comparison gate on it
                from voicebridge.benchmarking.harness import percentile
                vals = sorted(live["warm_chat_ms"])
                candidate.setdefault("latency", {})["live_chat"] = {
                    "n": len(vals),
                    "p50_ms": percentile(vals, 50),
                    "p95_ms": percentile(vals, 95),
                    "max_ms": vals[-1],
                }
            if live.get("resident"):
                res_dict = live["resident"]
                candidate.setdefault("resource", {})["live_residency"] = {
                    "resident": True,
                    "size_vram_bytes": res_dict.get("size_vram_bytes"),
                    "size_vram_mib": res_dict.get("size_vram_mib"),
                    "size_bytes": res_dict.get("size_bytes"),
                    "size_mib": res_dict.get("size_mib"),
                    "processor": res_dict.get("processor"),
                }

        # --- immutable baseline provenance ------------------------------
        provenance = {
            "packet": "6B",
            "schema": "voicebridge.eval-baseline/1",
            "mode": args.mode,
            "generated_at": round(time.time(), 3),
            "git_commit": _git_rev(),
            "git_tree_dirty": _git_dirty(),
            "runner": _runner_settings(args.mode, live),
            "environment": (live or {}).get("environment") or {},
            "units": dict(UNITS),
        }
        candidate["run_metadata"] = {
            "mode": args.mode,
            "git_commit": provenance["git_commit"],
            "git_tree_dirty": provenance["git_tree_dirty"],
            "generated_at": provenance["generated_at"],
        }
        report["provenance"] = provenance

        # --- gate against baseline -------------------------------------
        baseline = None
        if args.baseline:
            bp = Path(args.baseline)
            if not bp.exists():
                print(f"ERR: baseline not found: {bp}", file=sys.stderr)
                comp.close()
                return EXIT_FAILED
            baseline = json.loads(bp.read_text())
            gate = PromotionGate()
            decision = gate.evaluate(candidate, baseline,
                                     required_sets=required_sets)
            report["decision"] = decision.as_dict()
        else:
            # evidence presence is enforced even without a baseline: an empty
            # required set can never silently APPROVE.
            missing = [s for s in required_sets
                       if candidate["sets_evaluated"].get(s, 0) == 0]
            if missing:
                decision = PromotionDecision(
                    NEEDS_REVIEW,
                    "required evaluation set has no evidence "
                    f"({', '.join(missing)})",
                    [], [f"set {s} empty" for s in missing])
                report["decision"] = decision.as_dict()
                print(f"NOTE: no --baseline; {decision.reason}")
                print(f"DECISION: {decision.outcome} (evidence-presence gate)")
            else:
                decision = None
                print("NOTE: no --baseline; reporting scores without a "
                      "pass-rate gate decision")

        if args.save_baseline:
            # immutable baseline: score fields (backward-compatible with the
            # gate) PLUS provenance metadata
            payload = dict(candidate)
            payload["baseline_metadata"] = provenance
            Path(args.save_baseline).write_text(
                json.dumps(payload, indent=2))
            print(f"baseline written: {args.save_baseline} "
                  f"(git {provenance['git_commit']}, dirty="
                  f"{provenance['git_tree_dirty']}, mode {args.mode})")

        # --- console output --------------------------------------------
        print(f"packet 6B evaluation ({args.mode}) — {len(combined)} cases")
        cp = coverage_policy()
        print(f"  coverage: base authoritative for {cp['base_authoritative_for']}; "
              f"private = {cp['private_scope'][:60]}...; "
              f"private 0/0 = not applicable")
        for set_name, rs in report_sets.items():
            p = rs["poisoning"]
            if p.get("applicability") == "not_applicable":
                pstr = "N/A (0/0; not applicable to the private "
                pstr += "personalization split, never a pass)"
            else:
                pstr = f"{p['passed']}/{p['n']} passed"
            print(f"  set[{set_name}] {rs['n_cases']} cases "
                  f"families={rs['coverage']} poisoning={pstr}")
        for family in ("quality", "safety", "latency", "resource",
                       "tool_success"):
            fam = candidate["per_family"].get(family, {})
            rate = fam.get("pass_rate")
            print(f"  {family:13s} n={fam.get('n', 0):3d} passed="
                  f"{fam.get('passed', 0):3d} failed={fam.get('failed', 0):3d} "
                  f"errors={fam.get('errors', 0):3d} "
                  f"pass_rate={rate}")
        for cat, c in sorted(candidate["per_category"].items()):
            print(f"    {cat:28s} pass_rate={c['pass_rate']} "
                  f"({c['passed']}/{c['n']})")
        if live is not None:
            if live.get("gpu") and live["gpu"].get("available"):
                g = live["gpu"]
                print(f"  total GPU: {g['vram_used_mib']} MiB used / "
                      f"{g['vram_total_mib']} MiB total (never hidden)")
            res = live.get("resident") or {}
            if res.get("size_vram_mib") is not None:
                under = float(res["size_vram_mib"]) < MODEL_VRAM_CEILING_MIB
                print(f"  model VRAM: {res['size_vram_mib']} MiB "
                      f"(bytes={res.get('size_vram_bytes')}) under "
                      f"{MODEL_VRAM_CEILING_MIB} MiB (5 GiB) ceiling = {under}")

        if decision is not None:
            print(f"DECISION: {decision.outcome}")
            print(f"  reason: {decision.reason}")
            for f in decision.failures:
                print(f"  failure: {f}")
            for u in decision.untested:
                print(f"  untested: {u}")

        if args.out:
            path = _write_report(Path(args.out), report)
            print(f"report written: {path}")

        if decision is not None and decision.outcome == "REJECT":
            comp.close()
            return EXIT_REJECTED
        if decision is not None and decision.outcome == "NEEDS_REVIEW":
            comp.close()
            return EXIT_NEEDS_REVIEW
        if degraded:
            comp.close()
            return EXIT_DEGRADED
        comp.close()
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"ERR: {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            comp.close()
        except Exception:
            pass
        return EXIT_FAILED


def _live_sample(url: str) -> dict:
    """Opt-in live evidence: resident model state + one warm chat latency.

    Uses the Packet 4A harness helpers and the CANONICAL runner contract
    (runner_contract.resolve) so the probe can never trigger a context-size
    reload by sending a different num_ctx/keep_alive. Unreachable Ollama =>
    DEGRADED, never a pass.
    """
    from voicebridge.benchmarking import harness as H
    from voicebridge.runner_contract import resolve
    try:
        rc = resolve()
        alias = rc.alias
        url = rc.url
        num_ctx = rc.num_ctx
        keep_alive = rc.keep_alive
    except Exception as exc:
        return {"status": "degraded", "reason": f"runner contract: {exc}"[:200]}
    try:
        state = H.resident_state(url, alias)
    except Exception as exc:
        return {"status": "degraded",
                "reason": f"ollama unreachable: {exc}"[:200]}
    env = {}
    try:
        env = H.environment_snapshot(url, alias)
    except Exception:
        env = {}
    gpu = {}
    try:
        gpu = H.gpu_sample()
    except Exception:
        gpu = {}
    lat = []
    if state.get("resident"):
        try:
            import urllib.request
            for _ in range(3):
                t0 = time.perf_counter()
                req = urllib.request.Request(
                    url + "/api/chat",
                    data=json.dumps({
                        "model": alias, "messages": [
                            {"role": "user", "content": "say hi"}],
                        "stream": False, "keep_alive": keep_alive,
                        "options": {"num_ctx": num_ctx},
                    }).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    r.read()
                lat.append(round((time.perf_counter() - t0) * 1000.0, 1))
        except Exception as exc:
            return {"status": "degraded",
                    "reason": f"warm probe failed: {exc}"[:200]}
    return {
        "status": "ok" if state.get("resident") else "degraded",
        "resident": state,
        "environment": env,
        "runner": {
            "alias": alias, "url": url, "num_ctx": num_ctx,
            "keep_alive": keep_alive, "digest": state.get("digest"),
            "parallelism": getattr(rc, "parallelism", None),
        },
        "gpu": gpu,   # total GPU usage (nvidia-smi); never hidden by model size
        "warm_chat_ms": lat or None,
        "reason": None if state.get("resident")
        else "generative model not resident",
    }


if __name__ == "__main__":
    sys.exit(main())