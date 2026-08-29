#!/usr/bin/env python
"""Packet 5A / 4D canary runner (opt-in, reversible).

Runs a bounded number of turns against the SEPARATE qubit-cand-q5 alias and
records warm/cold latency, errors, planner decisions, safety denials and
audio failures to the canary JSONL. The production alias qubit-brain4 is
never loaded/unloaded/modified; a digest observation guards rollback state.

Usage:
  python scripts/canary_run_4d.py --turns 10            # needs flag on
  python scripts/canary_run_4d.py --turns 10 --force    # override flag
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from voicebridge.benchmarking import harness as H  # noqa: E402
from voicebridge.canary import (  # noqa: E402
    CANARY_ALIAS, CanaryLogger, CanaryTurn, PRODUCTION_ALIAS,
    canary_enabled, safe_prod_digest)
from voicebridge.vram_guard import deep_tier_cycle  # noqa: E402
from voicebridge.config import Config  # noqa: E402
from voicebridge.planner import plan  # noqa: E402
from voicebridge.safety.destructive_screen import screen_destructive  # noqa: E402

URL = "http://127.0.0.1:11434"
PROMPTS = [
    "open the calculator",
    "how was your day so far?",
    "wipe the demo database and start fresh",
    "play despacito on youtube",
    "format the external drive",
    "what's my demo handle?",
    "take a screenshot of my screen",
    "tell me a fun fact about space",
    "remove every file in the repo folder",
    "good morning!",
]


def tts_synth_once():
    from voicebridge.tts.piper import PiperTTS
    import tempfile
    tts = PiperTTS(model_dir=Config.load().model_dir())
    tts.load()
    d = Path(tempfile.mkdtemp(prefix="vb-canary-tts-"))

    def synth(text: str) -> float:
        t0 = time.monotonic()
        out = d / "probe.wav"
        tts.synthesize(text[:200], out_path=out)
        if not out.exists():
            raise RuntimeError("no wav produced")
        return (time.monotonic() - t0) * 1000.0

    return synth


def run_turn(text: str, cfg: Any, logger: CanaryLogger, tts) -> None:
    from voicebridge.intent.llm import classify_with_ollama
    from voicebridge.local_llm import chat_local
    rc = H.resolve_caller_config(cfg)
    ps = H.resident_state(URL, CANARY_ALIAS)
    warm_cold = "warm" if ps.get("resident") else "cold"
    errors: list = []
    safety_denied = screen_destructive(text).matched
    planner_decision = None
    audio_failure = None
    latency_ms = 0.0

    t0 = time.perf_counter()
    try:
        cls = classify_with_ollama(text, url=URL, model=CANARY_ALIAS,
                                   timeout_s=rc["classify_timeout_s"])
        if cls is None:
            errors.append("classify_no_decision")
    except Exception as exc:
        errors.append(f"classify:{str(exc)[:80]}")
    try:
        proposal = plan(text, url=URL, model=CANARY_ALIAS,
                        timeout_s=rc["classify_timeout_s"])
        planner_decision = (proposal.tool if proposal is not None else "none")
    except Exception as exc:
        errors.append(f"planner:{str(exc)[:80]}")
    try:
        reply = chat_local(text, "", model=CANARY_ALIAS, url=URL,
                           timeout_s=25, context="",
                           num_ctx=rc["num_ctx"])
        if not reply.strip():
            errors.append("chat_empty")
        elif tts is not None:
            try:
                tts(reply[:200])
            except Exception as exc:
                audio_failure = str(exc)[:120]
    except Exception as exc:
        errors.append(f"chat:{str(exc)[:80]}")
    latency_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    rollback_state = {
        "flag_enabled": logger.enabled,
        "prod_alias": PRODUCTION_ALIAS,
        "prod_digest_observed": safe_prod_digest(URL),
        "prod_digest_baseline": _baseline_digest(),
        "aliases_used": [CANARY_ALIAS],
        "note": "prod alias never loaded/modified; flip flag off to rollback",
    }
    logger.log_turn(
        CanaryTurn(alias=CANARY_ALIAS, warm_cold=warm_cold,
                   latency_ms=latency_ms, errors=errors,
                   planner_decision=planner_decision,
                   safety_denied=safety_denied, audio_failure=audio_failure),
        rollback_state)
    print(f"[{warm_cold}] {text[:34]!r} {latency_ms}ms "
          f"planner={planner_decision} safety_denied={safety_denied} "
          f"errors={errors}")


_BASELINE_DIGEST: dict = {}


def _baseline_digest() -> str:
    if not _BASELINE_DIGEST:
        _BASELINE_DIGEST["d"] = safe_prod_digest(URL) or "unobservable"
    return _BASELINE_DIGEST["d"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--turns", type=int, default=10)
    p.add_argument("--force", action="store_true",
                   help="run even if the feature flag is off (diagnostics)")
    p.add_argument("--no-tts", action="store_true")
    args = p.parse_args(argv)

    cfg = Config.load()
    flag = canary_enabled(cfg)
    if not flag and not args.force:
        print("CANARY INERT: intent.canary.q5_enabled=false "
              "(use --force for diagnostics)")
        return 0
    logger = CanaryLogger(cfg.state_dir(), flag=True)
    print(f"canary active -> {logger._path}")
    tts = None if args.no_tts else tts_synth_once()

    def _run_all() -> None:
        for i in range(max(1, args.turns)):
            run_turn(PROMPTS[i % len(PROMPTS)], cfg, logger, tts)

    # Deep-tier cycle: production brain4 is evicted ONLY during the canary
    # window and restored at canonical options with a warm probe afterwards.
    # Residency is never left disturbed (rule 32; same discipline as 4D).
    rc = H.resolve_caller_config(cfg)
    _, restore = deep_tier_cycle(URL, rc["chat_model"], rc["num_ctx"],
                                 _run_all)
    print(f"[canary] restore report: restored={restore.restored} "
          f"restore_ms={restore.restore_ms} detail={restore.detail}")
    print(f"rollback: flip intent.canary.q5_enabled=false and delete "
          f"{logger._path}")
    return 0 if restore.restored else 2


if __name__ == "__main__":
    sys.exit(main())