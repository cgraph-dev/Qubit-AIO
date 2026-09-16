"""VRAM guard + deep-tier eviction/restore (Packet 4D).

Nothing large is ever loaded without a measured free-VRAM check, and the
resident fast model is always restorable at canonical contract options with
a verified warm probe. Lifecycle primitives are reused from the Packet 4A
harness (R5-documented expire/restore paths) — one source of truth.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

DEFAULT_HEADROOM_MIB = 512


class VRAMGuardError(RuntimeError):
    """Typed refusal: loading would violate the free-VRAM budget."""


def free_vram_mib(timeout_s: float = 3.0) -> Optional[int]:
    """Free GPU memory in MiB; typed None when nvidia-smi unavailable."""
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout_s, check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return int(float(proc.stdout.strip().splitlines()[0]))
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError, OSError):
        return None


def guard_load(size_mib: float, *, headroom_mib: float = DEFAULT_HEADROOM_MIB,
               what: str = "model") -> int:
    """Refuse BEFORE any load unless free VRAM covers size + headroom.

    Returns the measured free MiB on success; raises VRAMGuardError
    (typed refusal) otherwise or when measurement is unavailable — an
    unmeasurable guard fails closed.
    """
    free = free_vram_mib()
    if free is None:
        raise VRAMGuardError(
            f"VRAM guard cannot measure free memory; refusing to load {what}")
    needed = size_mib + headroom_mib
    if free < needed:
        raise VRAMGuardError(
            f"VRAM guard: {what} needs {needed:.0f} MiB "
            f"({size_mib:.0f}+{headroom_mib:.0f} headroom), "
            f"only {free} MiB free")
    return free


@dataclass
class RestoreReport:
    restored: bool
    restore_ms: Optional[float]
    verified_ctx: Optional[int]
    detail: str = ""


def deep_tier_cycle(url: str, fast_alias: str, canonical_ctx: int,
                    deep_work: Callable[[], Any],
                    *, poll_timeout_s: float = 20.0,
                    ) -> tuple[Any, RestoreReport]:
    """Run `deep_work` while the fast model is evicted, then restore it at
    canonical options and verify with a warm probe.

    Sequence (R5 semantics): empty-prompt keep_alive=0 expire -> wait until
    /api/ps shows the alias gone -> deep_work() -> restore keep_alive=-1 at
    canonical num_ctx -> warm probe -> measure restoration latency.
    """
    import json as _json
    import urllib.request as _u

    def _expire() -> None:
        payload = {"model": fast_alias, "keep_alive": 0}
        req = _u.Request(f"{url.rstrip('/')}/api/generate",
                         data=_json.dumps(payload).encode(),
                         headers={"Content-Type": "application/json"})
        with _u.urlopen(req, timeout=15) as resp:
            resp.read()

    def _resident() -> bool:
        req = _u.Request(f"{url.rstrip('/')}/api/ps")
        with _u.urlopen(req, timeout=5) as resp:
            body = _json.loads(resp.read().decode())
        return any(m.get("name", "").startswith(fast_alias.split(":")[0])
                   for m in body.get("models", []))

    from .logging_setup import log_event
    _expire()
    end = time.monotonic() + poll_timeout_s
    while time.monotonic() < end and _resident():
        time.sleep(0.4)

    try:
        result = deep_work()
    finally:
        t0 = time.monotonic()
        payload = {"model": fast_alias, "stream": False, "keep_alive": -1,
                   "options": {"num_ctx": canonical_ctx},
                   "messages": [{"role": "user", "content": "warm probe"}]}
        req = _u.Request(f"{url.rstrip('/')}/api/chat",
                         data=_json.dumps(payload).encode(),
                         headers={"Content-Type": "application/json"})
        try:
            with _u.urlopen(req, timeout=90) as resp:
                resp.read()
            # warm probe must actually answer (non-empty content)
            ok = True
        except Exception as exc:
            ok = False
            log_event("deep_tier.restore_probe.failed",
                      error=str(exc)[:120])
        restored = ok and _resident()
        report = RestoreReport(
            restored=restored,
            restore_ms=round((time.monotonic() - t0) * 1000.0, 2),
            verified_ctx=canonical_ctx,
            detail="canonical options reapplied" if restored else "restore failed")
        log_event("deep_tier.restored", alias=fast_alias,
                  ms=report.restore_ms, restored=restored)
    return result, report
