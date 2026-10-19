"""Killable local-TTS synthesis worker (orb STOP aborts a piper/sherpa synth).

``piper``/``sherpa-onnx`` synthesis is a blocking C call: a Python thread
cannot interrupt it, so a STOP that lands inside ``synthesize`` used to let
that one wav finish server-side (the audio was discarded, but the C call and
the HTTP request thread were not released). This module moves the synth into
a child process the service OWNS, so a STOP can terminate it at once.

Shape:
  * ALL ``POST /api/audio/speak`` synthesis (keyed orb turns AND unkeyed
    dashboard calls) goes through this one worker, so the service process
    never builds/loads a TTS provider of its own. Total provider copies stay
    at ONE (the worker's) instead of the worker's plus the parent's warm
    in-process cache;
  * one warm worker process per config identity (the provider/model load is
    paid once, so normal replies keep their latency);
  * each request/response is one JSON line over the child's stdin/stdout;
  * the parent polls the caller's ``stopped`` predicate every ~20 ms while it
    waits, and on a stop sends SIGTERM then SIGKILL to the child's process
    group. The replacement worker is then pre-warmed in a background thread,
    so the next reply after a stop is warm instead of paying the model load
    (~1.7 s for piper/sherpa) a second time;
  * an idle worker is reaped after ``QUBIT_TTS_SYNTH_WORKER_IDLE_S`` seconds
    (default 300; ``0`` disables) so the extra model copy is not pinned in
    memory forever on an idle service. A reap only ever costs the next
    reply's load, never interruptibility or correctness;
  * ``warm_async`` lets the EARLIEST voice request that precedes a reply
    begin that load in the background — the keyed voice-edit ``begin`` /
    ``capture`` / ``transform`` / ``route`` hooks, the direct keyed ``ask``,
    and the unkeyed dashboard ``/api/audio/transcribe`` / ``/api/audio/speak``
    arrival — so the model loads while the mic, STT, router and brain work and
    a fast deterministic answer still finds a warm worker. It never blocks
    the turn, never raises, and never starts a second load.

Safety: the child gets the config DATA over a pipe (never argv/env, so no
secrets appear in the process list) and runs in its own session, so a stop
can kill the whole child group without touching the service. Set
``QUBIT_TTS_SYNTH_WORKER=0`` to fall back to the historical in-process synth
(not interruptible mid-C-call) if a host cannot spawn the worker.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

#: Interval between cancellation checks while the child synthesizes.
_POLL_S = 0.02


class SynthCancelled(RuntimeError):
    """The turn was stopped; the child was killed and the wav is unusable."""


class SynthWorkerUnavailable(RuntimeError):
    """The worker could not start/load the provider (typed TTS unavailable)."""


def worker_supported(cfg: Any) -> bool:
    """True when ``cfg`` can be handed to the worker (pure check).

    The service's ``Config`` carries its full ``data`` dict; test doubles and
    embedded configs without one keep the in-process path byte-identical.
    """
    try:
        if os.environ.get("QUBIT_TTS_SYNTH_WORKER", "1") == "0":
            return False
        return isinstance(getattr(cfg, "data", None), dict)
    except Exception:  # noqa: BLE001 - feature check only
        return False


def _cfg_key(cfg: Any) -> str:
    """Stable identity of the provider the child would build."""
    try:
        payload = {
            "data": cfg.data,
            "model_dir": str(cfg.model_dir()),
        }
        blob = json.dumps(payload, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - key only
        blob = repr(getattr(cfg, "data", ""))
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()


def _repo_root() -> Path:
    # .../<repo>/voicebridge/tts/synth_worker.py
    return Path(__file__).resolve().parents[2]


def _spawn_cmd() -> list:
    """Command that starts the child (a seam for hermetic tests)."""
    return [sys.executable, "-m", "voicebridge.tts.synth_worker", "--serve"]


class _Worker:
    """One live child process + its line protocol."""

    def __init__(self, cfg: Any) -> None:
        self.key = _cfg_key(cfg)
        self._cfg_data = cfg.data
        self.proc: Optional[subprocess.Popen] = None
        self._lines: "queue.Queue[Optional[str]]" = queue.Queue()
        self._seq = 0
        self.provider = ""

    # -- lifecycle ---------------------------------------------------------
    def start(self, *, stopped: Optional[Callable[[], bool]] = None,
              timeout: float = 60.0) -> None:
        root = _repo_root()
        env = dict(os.environ)
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(root) + ((os.pathsep + prev) if prev else "")
        try:
            self.proc = subprocess.Popen(
                _spawn_cmd(),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                cwd=str(root),
                env=env,
                start_new_session=True,
            )
        except Exception as exc:  # noqa: BLE001 - typed at the caller
            raise SynthWorkerUnavailable(
                f"could not start the synthesis worker: {exc}") from exc
        threading.Thread(target=self._pump, name="tts-synth-pump",
                         daemon=True).start()
        try:
            self._write({"config": self._cfg_data})
        except Exception as exc:  # noqa: BLE001
            self.kill()
            raise SynthWorkerUnavailable(
                f"could not configure the synthesis worker: {exc}") from exc
        try:
            ready = self._wait(stopped=stopped, timeout=timeout)
        except BaseException:
            # A stop (or any failure) during the model load must never leave
            # the just-spawned child behind.
            self.kill()
            raise
        if not ready.get("ready"):
            detail = str(ready.get("error", "") or "worker failed to load")
            self.kill()
            raise SynthWorkerUnavailable(detail[:300])
        self.provider = str(ready.get("provider", "") or "")

    def alive(self) -> bool:
        try:
            return self.proc is not None and self.proc.poll() is None
        except Exception:  # noqa: BLE001
            return False

    def kill(self) -> None:
        """Terminate the child (and its group) without blocking the caller."""
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except Exception:  # noqa: BLE001
                    proc.terminate()
                try:
                    proc.wait(timeout=0.5)
                except Exception:  # noqa: BLE001 - escalate to SIGKILL
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except Exception:  # noqa: BLE001
                        try:
                            proc.kill()
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        proc.wait(timeout=1.0)
                    except Exception:  # noqa: BLE001
                        pass
        except Exception:  # noqa: BLE001 - killing is best-effort
            pass
        finally:
            for stream in (proc.stdin, proc.stdout):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:  # noqa: BLE001
                    pass

    # -- protocol ----------------------------------------------------------
    def _pump(self) -> None:
        proc = self.proc
        try:
            if proc is not None and proc.stdout is not None:
                for line in proc.stdout:
                    self._lines.put(line)
        except Exception:  # noqa: BLE001 - EOF/crash reads as closed
            pass
        finally:
            self._lines.put(None)

    def _write(self, payload: Dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None:
            raise SynthWorkerUnavailable("synthesis worker is not running")
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()

    def _wait(self, *, stopped: Optional[Callable[[], bool]],
              timeout: float) -> Dict[str, Any]:
        """Wait for one reply line; poll ``stopped`` and the child's health."""
        end = time.monotonic() + max(0.0, float(timeout))
        while True:
            if stopped is not None:
                try:
                    if stopped():
                        raise SynthCancelled("the turn was stopped")
                except SynthCancelled:
                    raise
                except Exception:  # noqa: BLE001 - predicate is best-effort
                    pass
            try:
                line = self._lines.get(timeout=_POLL_S)
            except queue.Empty:
                line = ""
            if line == "":
                if time.monotonic() >= end:
                    raise SynthWorkerUnavailable(
                        "the synthesis worker timed out")
                if not self.alive() and self._lines.empty():
                    raise SynthWorkerUnavailable(
                        "the synthesis worker exited")
                continue
            if line is None:
                raise SynthWorkerUnavailable(
                    "the synthesis worker closed its output")
            try:
                msg = json.loads(line)
            except Exception:  # noqa: BLE001 - ignore non-protocol noise
                continue
            if isinstance(msg, dict):
                return msg
            continue

    def synthesize(self, text: str, out_path: Path,
                   *, stopped: Optional[Callable[[], bool]],
                   timeout: float) -> None:
        self._seq += 1
        req_id = self._seq
        self._write({"id": req_id, "text": text, "out": str(out_path)})
        msg = self._wait(stopped=stopped, timeout=timeout)
        if int(msg.get("id", -1) or -1) != req_id:
            raise SynthWorkerUnavailable("the synthesis worker replied out of order")
        if not msg.get("ok"):
            raise SynthWorkerUnavailable(
                str(msg.get("error", "") or "synthesis failed")[:300])


#: Serializes real synthesis (the piper/espeak C library is not thread-safe).
_LOCK = threading.Lock()
#: Guards the cached worker and its bookkeeping. Never held across a synth.
_STATE = threading.Lock()
_WORKER: Optional[_Worker] = None
_LAST_USED = 0.0
_GENERATION = 0
_PREWARM_EVENTS: Dict[str, threading.Event] = {}
_REAPER: Optional[threading.Thread] = None

#: Idle grace before a loaded worker is released (seconds; 0 disables).
#: This is the memory/latency trade-off: the worker holds a provider/model
#: copy, but reaping it means the first reply after a long silence reloads.
#: A stopped worker is replaced eagerly, so stops are NOT penalised.
_DEFAULT_IDLE_S = 300.0
#: Reaper sleep between checks; tests shorten it.
_REAP_TICK_S = 5.0


def _idle_seconds() -> float:
    try:
        return max(0.0, float(os.environ.get(
            "QUBIT_TTS_SYNTH_WORKER_IDLE_S", _DEFAULT_IDLE_S)))
    except (TypeError, ValueError):
        return _DEFAULT_IDLE_S


def _ensure_reaper() -> None:
    """Start the idle reaper exactly once (daemon thread)."""
    global _REAPER
    with _STATE:
        if _REAPER is not None and _REAPER.is_alive():
            return
        thread = threading.Thread(target=_reaper_loop,
                                  name="tts-synth-reaper", daemon=True)
        _REAPER = thread
    thread.start()


def _reaper_loop() -> None:
    """Release the cached worker after the configured idle grace."""
    global _WORKER
    while True:
        try:
            tick = float(_REAP_TICK_S)
        except (TypeError, ValueError):
            tick = 5.0
        time.sleep(max(0.01, tick))
        idle = _idle_seconds()
        if idle <= 0:
            continue
        # Never pull a worker out from under a synthesizing call.
        if not _LOCK.acquire(blocking=False):
            continue
        try:
            with _STATE:
                worker = _WORKER
                if worker is None or _PREWARM_EVENTS:
                    continue
                if time.monotonic() - _LAST_USED < idle:
                    continue
                _WORKER = None
            worker.kill()
        finally:
            _LOCK.release()


def _await_prewarm(key: str, timeout: float,
                   stopped: Optional[Callable[[], bool]] = None) -> None:
    """Let an in-flight pre-warm finish before spawning a duplicate.

    Polls ``stopped`` like the synth wait, so a STOP during the replacement's
    model load still returns promptly instead of waiting for the pre-warm.
    """
    with _STATE:
        event = _PREWARM_EVENTS.get(key)
    if event is None:
        return
    end = time.monotonic() + max(0.0, min(float(timeout), 60.0))
    while not event.is_set():
        if stopped is not None:
            try:
                if stopped():
                    raise SynthCancelled("the turn was stopped")
            except SynthCancelled:
                raise
            except Exception:  # noqa: BLE001 - predicate is best-effort
                pass
        if time.monotonic() >= end:
            return
        event.wait(_POLL_S)


def _start_prewarm(cfg: Any, key: str) -> None:
    """Warm a replacement in the background (never blocks the stopped turn)."""
    with _STATE:
        worker = _WORKER
        if worker is not None and worker.key == key and worker.alive():
            return
        if key in _PREWARM_EVENTS:
            return
        event = threading.Event()
        _PREWARM_EVENTS[key] = event
        generation = _GENERATION
    thread = threading.Thread(
        target=_prewarm_worker, args=(cfg, key, generation, event),
        name="tts-synth-prewarm", daemon=True)
    thread.start()


def _prewarm_worker(cfg: Any, key: str, generation: int,
                    event: threading.Event) -> None:
    """Load a replacement provider in the background; best-effort."""
    global _WORKER, _LAST_USED
    worker: Optional[_Worker] = None
    try:
        with _STATE:
            current = _WORKER
            if (generation != _GENERATION
                    or (current is not None and current.key == key
                        and current.alive())):
                return
        worker = _Worker(cfg)
        worker.start(timeout=60.0)
        discard: Optional[_Worker] = None
        installed = False
        with _STATE:
            current = _WORKER
            if generation != _GENERATION:
                pass  # shut down while loading: discard below
            elif current is not None and current.alive():
                # Some live worker (same key: a real call won the race;
                # another key: the slot belongs to it) owns the cache.
                pass
            else:
                discard = current
                _WORKER = worker
                _LAST_USED = time.monotonic()
                worker = None
                installed = True
        if discard is not None:
            discard.kill()
        if installed:
            _ensure_reaper()
    except BaseException:  # noqa: BLE001 - next real call is typed
        pass
    finally:
        with _STATE:
            _PREWARM_EVENTS.pop(key, None)
        if worker is not None:
            worker.kill()
        event.set()


def warm_async(cfg: Any) -> bool:
    """Start loading the worker in the background if it is cold.

    Returns ``True`` only when this call queued a load; ``False`` when a
    worker is already warm or loading, when the worker path is disabled
    (``QUBIT_TTS_SYNTH_WORKER=0`` or a config double without ``data``), or on
    any internal error. Never blocks the caller on the model load, never
    raises, and never starts a duplicate load: the loading state is tracked in
    ``_PREWARM_EVENTS`` and a real ``synthesize_cancellable`` joins it via
    ``_await_prewarm`` instead of spawning its own child.

    The parent process does not build a provider here (the load happens in the
    child), so the one-copy property is unchanged; the idle reaper still
    releases the worker if no reply follows.
    """
    try:
        if not worker_supported(cfg):
            return False
        key = _cfg_key(cfg)
        with _STATE:
            worker = _WORKER
            if worker is not None and worker.alive():
                # Same identity: already warm. Different identity: the live
                # worker owns the slot, so warming this one now would put a
                # second model copy in memory only to discard it (the next
                # real synth replaces it).
                return False
            if key in _PREWARM_EVENTS:
                return False
        _start_prewarm(cfg, key)
        with _STATE:
            return key in _PREWARM_EVENTS
    except Exception:  # noqa: BLE001 - warming must never break a turn
        return False


def synthesize_cancellable(cfg: Any, text: str, out_path: Path | str, *,
                           stopped: Optional[Callable[[], bool]] = None,
                           timeout: float = 60.0) -> str:
    """Synthesize ``text`` in the killable child; return the provider name.

    Raises ``SynthCancelled`` when ``stopped`` trips. Serialized with a module
    lock (the piper/espeak C library is not thread-safe). A stop kills the
    child at once; a replacement is pre-warmed in the background so the next
    call finds a loaded worker instead of paying the model load again.
    """
    global _WORKER, _LAST_USED
    out = Path(out_path)
    key = _cfg_key(cfg)
    with _LOCK:
        for attempt in (0, 1):
            _await_prewarm(key, timeout, stopped)
            with _STATE:
                worker = _WORKER
                if worker is not None and (worker.key != key
                                           or not worker.alive()):
                    _WORKER = None
                    stale = worker
                else:
                    stale = None
            if stale is not None:
                stale.kill()
            if worker is None or worker.key != key or not worker.alive():
                if worker is not None:
                    worker.kill()
                worker = _Worker(cfg)
                worker.start(stopped=stopped, timeout=timeout)
                with _STATE:
                    _WORKER = worker
                    _LAST_USED = time.monotonic()
                _ensure_reaper()
            try:
                worker.synthesize(text, out, stopped=stopped, timeout=timeout)
                with _STATE:
                    if _WORKER is worker:
                        _LAST_USED = time.monotonic()
                return worker.provider
            except SynthCancelled:
                worker.kill()
                with _STATE:
                    if _WORKER is worker:
                        _WORKER = None
                _start_prewarm(cfg, key)
                raise
            except SynthWorkerUnavailable:
                worker.kill()
                with _STATE:
                    if _WORKER is worker:
                        _WORKER = None
                # A stop racing the child's death still wins as a stop.
                if stopped is not None:
                    try:
                        if stopped():
                            raise SynthCancelled("the turn was stopped")
                    except SynthCancelled:
                        raise
                    except Exception:  # noqa: BLE001
                        pass
                if attempt == 0:
                    continue
                raise


def shutdown() -> None:
    """Kill the cached worker (tests / orderly service shutdown)."""
    global _WORKER, _LAST_USED, _GENERATION
    with _STATE:
        _GENERATION += 1
        worker = _WORKER
        _WORKER = None
        _LAST_USED = 0.0
        events = list(_PREWARM_EVENTS.values())
    if worker is not None:
        worker.kill()
    # A pre-warm that was already loading must not re-install after shutdown.
    # Wait briefly so tests/orders do not leave an orphan child behind.
    for event in events:
        event.wait(timeout=5.0)


# ---------------------------------------------------------------------------
# child entry point
# ---------------------------------------------------------------------------
def _serve() -> int:
    """Read the config, build+load the provider once, then serve requests."""
    from ..config import Config

    def _emit(payload: Dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload) + "\n")
        sys.stdout.flush()

    try:
        first = sys.stdin.readline()
        req = json.loads(first) if first.strip() else {}
        data = req.get("config")
        if not isinstance(data, dict):
            raise ValueError("missing config data")
        cfg = Config(data=data)
        from .. import qubit_compat as QC
        tts = QC._tts_from_cfg(cfg)
        tts.load()
    except Exception as exc:  # noqa: BLE001 - typed to the parent
        _emit({"ready": False, "error": str(exc)[:300]})
        return 1
    _emit({"ready": True, "provider": str(getattr(tts, "name", "") or "")})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:  # noqa: BLE001 - ignore malformed input
            continue
        req_id = req.get("id")
        try:
            tts.synthesize(str(req.get("text", "")), Path(str(req.get("out"))))
            _emit({"id": req_id, "ok": True})
        except Exception as exc:  # noqa: BLE001 - typed to the parent
            _emit({"id": req_id, "ok": False, "error": str(exc)[:300]})
    return 0


def main(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--serve" in args:
        return _serve()
    print("usage: python -m voicebridge.tts.synth_worker --serve", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover - child entry
    raise SystemExit(main())
