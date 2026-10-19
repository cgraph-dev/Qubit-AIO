"""Killable local-TTS synthesis worker (orb STOP aborts a piper/sherpa synth).

``piper``/``sherpa-onnx`` synthesis is a blocking C call: a Python thread
cannot interrupt it, so a STOP that lands inside ``synthesize`` used to let
that one wav finish server-side (the audio was discarded, but the C call and
the HTTP request thread were not released). This module moves the synth into
a child process the service OWNS, so a STOP can terminate it at once.

Shape:
  * one warm worker process per config identity (the provider/model load is
    paid once, exactly like the in-process provider cache, so normal replies
    keep their latency);
  * each request/response is one JSON line over the child's stdin/stdout;
  * the parent polls the caller's ``stopped`` predicate every ~20 ms while it
    waits, and on a stop sends SIGTERM then SIGKILL to the child's process
    group. The next reply after a stop starts a fresh worker (only then is
    the load cost paid again).

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


_LOCK = threading.Lock()
_WORKER: Optional[_Worker] = None


def synthesize_cancellable(cfg: Any, text: str, out_path: Path | str, *,
                           stopped: Optional[Callable[[], bool]] = None,
                           timeout: float = 60.0) -> str:
    """Synthesize ``text`` in the killable child; return the provider name.

    Raises ``SynthCancelled`` when ``stopped`` trips. Serialized with a module
    lock (the piper/espeak C library is not thread-safe). A stop kills the
    child at once; the next call starts a new one (paying the model load again
    only after a stop).
    """
    global _WORKER
    out = Path(out_path)
    with _LOCK:
        for attempt in (0, 1):
            worker = _WORKER
            key = _cfg_key(cfg)
            if worker is None or worker.key != key or not worker.alive():
                if worker is not None:
                    worker.kill()
                worker = _Worker(cfg)
                worker.start(stopped=stopped, timeout=timeout)
                _WORKER = worker
            try:
                worker.synthesize(text, out, stopped=stopped, timeout=timeout)
                return worker.provider
            except SynthCancelled:
                worker.kill()
                _WORKER = None
                raise
            except SynthWorkerUnavailable:
                worker.kill()
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
    global _WORKER
    with _LOCK:
        if _WORKER is not None:
            _WORKER.kill()
            _WORKER = None


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
