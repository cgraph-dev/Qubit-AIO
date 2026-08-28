#!/usr/bin/env python
"""Dashboard stress probes: web TEXT turn, web VOICE turn, Right-Ctrl PTT.

Modes
-----
  one-shot (default)   run all probes once, print JSON, exit 0/1
  --watch [interval]   run FOREVER every N seconds (default 60); appends each
                       cycle to <state>/dashboard_stress.jsonl; NEVER crashes;
                       after 3 consecutive rctrl failures best-effort restarts
                       the bridge service (self-healing watchdog).

Designed for "always run, never break": every probe is individually wrapped,
network/file/audio errors degrade that probe to FAIL without touching others.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

HOME = Path.home()
STATE = HOME / ".local/state/openclaw-voice-bridge"
EVENTS = STATE / "events.jsonl"
BRIDGE_UNIT = "openclaw-voice-bridge.service"


# --------------------------------------------------------------------- utils
def _post(base: str, path: str, body: bytes, token: str = "",
          ctype: str = "application/json", timeout: float = 10):
    req = urllib.request.Request(base + path, data=body,
                                 headers={"Content-Type": ctype,
                                          **({"X-Api-Token": token} if token else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(base: str, path: str, token: str = "", timeout: float = 8):
    req = urllib.request.Request(base + path,
                                 headers=({"X-Api-Token": token} if token else {}))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _events_since(offset: int):
    """Yield parsed event dicts appended after byte offset; return new offset."""
    try:
        size = EVENTS.stat().st_size
    except OSError:
        return offset, []
    if size < offset:            # truncated/rotated -> rescan tail
        offset = max(0, size - 65536)
    out = []
    with EVENTS.open("r", errors="replace") as fh:
        fh.seek(offset)
        for line in fh:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
        offset = fh.tell()
    return offset, out


def _wait_for(new_offset: int, pred, timeout_s: float):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        new_offset, evs = _events_since(new_offset)
        for ev in evs:
            if pred(ev):
                return True, ev, new_offset
        time.sleep(0.15)
    return False, None, new_offset


def _pcm_from_wav(path: Path, max_s: float = 12.0) -> bytes | None:
    """Any wav -> 16 kHz mono Int16 PCM bytes (what /api/talk expects)."""
    try:
        import numpy as np
        with wave.open(str(path), "rb") as wf:
            sr, nch = wf.getframerate(), wf.getnchannels()
            if wf.getsampwidth() != 2:
                return None
            raw = wf.readframes(int(sr * max_s))
        samples = np.frombuffer(raw, dtype=np.int16)
        if nch > 1:
            samples = samples[::nch]                      # first channel
        if sr != 16000 and len(samples):
            t = np.linspace(0.0, len(samples) / sr, num=len(samples))
            n_out = int(len(samples) * 16000 / sr)
            samples = np.interp(
                np.linspace(0.0, len(samples) / sr, num=n_out),
                t, samples.astype(np.float64)).astype(np.int16)
        return samples.tobytes()
    except Exception:
        return None


# -------------------------------------------------------------------- probes
def probe_text(base: str, token: str) -> dict:
    t0 = time.time()
    r = _post(base, "/api/type",
              json.dumps({"text": "what time is it?"}).encode(), token)
    rid = r["request_id"]
    while time.time() - t0 < 14:
        res = _get(base, f"/api/result/{rid}", token)
        if res.get("status") == "done":
            dt = round(time.time() - t0, 2)
            summary = (res.get("summary") or res.get("reply") or "").strip()
            return {"probe": "text", "ok": bool(summary),
                    "detail": f"{len(summary)}ch reply", "latency_s": dt}
        time.sleep(0.3)
    return {"probe": "text", "ok": False, "detail": "timeout waiting result"}


def _talk_wait(base: str, token: str, pcm: bytes, budget_s: float) -> tuple:
    """POST one utterance and wait for the result; returns (ok, latency, res)."""
    t0 = time.time()
    r = _post(base, "/api/talk", pcm, token, ctype="application/octet-stream")
    rid = r["request_id"]
    while time.time() - t0 < budget_s:
        res = _get(base, f"/api/result/{rid}", token)
        if res.get("status") == "done":
            return True, round(time.time() - t0, 2), res
        time.sleep(0.3)
    return False, round(time.time() - t0, 2), None


def probe_voice(base: str, token: str, wav: Path) -> dict:
    pcm = _pcm_from_wav(wav)
    if not pcm:
        return {"probe": "voice", "ok": False, "detail": f"no usable PCM from {wav}"}
    # Deterministic readiness: one unmeasured warm-up turn loads/proves the
    # ASR -> route -> TTS event-delivery path, so the measured turn is a WARM
    # local roundtrip (the <20s assertion is a warm-turn budget — a cold first
    # turn after a bridge restart can exceed it). The warm-up is bounded and
    # its failure is reported, never hidden.
    ok_warm, dt_warm, _ = _talk_wait(base, token, pcm, 40.0)
    if not ok_warm:
        return {"probe": "voice", "ok": False,
                "detail": f"warm-up turn did not complete ({dt_warm}s)"}
    ok, dt, res = _talk_wait(base, token, pcm, 18.0)
    if not ok:
        return {"probe": "voice", "ok": False, "detail": "timeout waiting result"}
    heard = (res.get("text") or "").strip()
    return {"probe": "voice", "ok": True,
            "detail": f"asr='{heard[:40]}' reply={len(res.get('summary') or '')}ch",
            "latency_s": dt}


def probe_rctrl(token: str) -> dict:
    off = EVENTS.stat().st_size if EVENTS.exists() else 0
    env = {"DISPLAY": __import__("os").environ.get("DISPLAY", ":0"),
           "XAUTHORITY": __import__("os").environ.get(
               "XAUTHORITY", "/run/user/1000/gdm/Xauthority"),
           "PATH": __import__("os").environ.get("PATH", "/usr/bin:/bin")}
    t0 = time.time()

    def _tap() -> tuple[bool, str]:
        errs = []
        for arg in ("keydown", "keyup"):
            p = subprocess.run(["xdotool", arg, "105"], env=env,
                               capture_output=True, text=True)
            if p.returncode != 0:
                errs.append((p.stderr or "").strip()[:80])
        return (not errs), "; ".join(errs)

    # Delivery signal: ptt_start/hotkey event proves the KEY works even when
    # the bridge is busy and the audible beep lags; feedback.beep is the bonus.
    def _delivered(e: dict) -> bool:
        return ((e.get("event") == "feedback.beep" and e.get("which") == "start")
                or e.get("event") == "hotkey")

    ok, ev, _ = _wait_for(off, _delivered, timeout_s=7)
    if not ok:                       # humans double-tap too
        _tap()
        ok, ev, _ = _wait_for(off, _delivered, timeout_s=5)
    # close out cleanly: our first tap TOGGLED listening on — a second tap
    # returns the bridge to idle so the user's own PTT state is untouched
    subprocess.run(["xdotool", "keydown", "105"], env=env, capture_output=True)
    time.sleep(0.25)
    subprocess.run(["xdotool", "keyup", "105"], env=env, capture_output=True)
    return {"probe": "rctrl", "ok": bool(ok),
            "detail": (f"delivery={'beep' if (ev or {}).get('which') == 'start' else 'hotkey'}"
                       if ok else "no event"),
            "latency_s": round(time.time() - t0, 2)}


# --------------------------------------------------------------------- modes
def run_cycle(base: str, token: str, wav: Path,
              lock_wait_s: float = 40.0) -> list[dict]:
    """Cross-process serialize (pytest vs systemd watchdog). The lock is
    acquired with a BOUNDED wait: the old unlimited LOCK_EX starved callers
    whenever the watchdog's cycle (~1 min) overlapped them."""
    import errno
    import fcntl
    results = []
    lf = open("/tmp/openclaw-dashboard-stress.lock", "w")
    deadline = time.time() + max(0.0, lock_wait_s)
    got = False
    while time.time() < deadline:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            got = True
            break
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES):
                raise
            time.sleep(0.5)
    if not got:
        lf.close()
        return [{"probe": "lock", "ok": False,
                 "detail": f"stress lock busy >{lock_wait_s}s (watchdog cycling?)"}]
    try:
        for name, fn in (("status", lambda: (lambda s: {
                "probe": "status", "ok": s.get("bridge") == "up",
                "detail": f"route={s.get('route')}"})(_get(base, "/api/status", token))),
                ("text", lambda: probe_text(base, token)),
                ("voice", lambda: probe_voice(base, token, wav)),
                ("rctrl", lambda: probe_rctrl(token))):
            try:
                results.append(fn())
            except Exception as exc:
                results.append({"probe": name, "ok": False,
                                "detail": f"{type(exc).__name__}: {str(exc)[:120]}"})
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()
    return results


def _restart_decision(fails: int, uptime: float, allow_restart: bool) -> bool:
    """Packet 7A: implicit watchdog/service restarts are PROHIBITED unless the
    operator passes --allow-restart. Without it the watchdog is monitor-only,
    even when the bridge looks stuck (5 straight failures after 3 min uptime)."""
    return bool(allow_restart) and fails >= 5 and uptime > 180


def watch(interval: int, base: str, token: str, wav: Path,
          allow_restart: bool = False) -> None:  # pragma: no cover
    fails = 0
    state = {"brain_bad": 0, "last_recycle": 0.0}
    out = STATE / "dashboard_stress.jsonl"
    print(f"[stress] watch mode: every {interval}s -> {out}", flush=True)
    time.sleep(45)   # let the bridge fully settle after OUR (re)start before
                     # the first cycle — otherwise we race its model reload
    while True:
        try:
            with open("/tmp/openclaw-dashboard-stress.lock", "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                results = run_cycle(base, token, wav)
            bad = [r for r in results if not r["ok"]]
            row = {"ts": time.time(), "pass": len(results) - len(bad),
                   "fail": len(bad), "results": results}
            try:
                STATE.mkdir(parents=True, exist_ok=True)
                with out.open("a") as fh:
                    fh.write(json.dumps(row) + "\n")
            except Exception:
                pass
            print(json.dumps(row), flush=True)
            fails = fails + 1 if any(r["probe"] == "rctrl" and not r["ok"]
                                     for r in results) else 0
            uptime = 999
            try:
                s = _get(base, "/api/status")
                uptime = float(s.get("uptime_s") or 999)
            except Exception:
                pass
            # VRAM self-heal: if a game/desktop app stole VRAM AFTER the brain
            # loaded, ollama never re-places it -> permanent slow partial-CPU
            # mode. Detect the split and recycle the model (max 1x / 30min).
            # Packet 7A: recycling the model is an implicit restart, so it is
            # monitor-only unless --allow-restart is set.
            try:
                ps_out = subprocess.run(["ollama", "ps"], capture_output=True,
                                        text=True, timeout=15).stdout
                for ln in ps_out.splitlines():
                    if "qubit-brain4" in ln and "CPU/GPU" in ln:
                        cpu_pct = int(ln.split("/")[0].strip().split()[-1]
                                      .rstrip("%"))
                        state["brain_bad"] = state.get("brain_bad", 0) + 1 \
                            if cpu_pct > 10 else 0
                        now_ts = time.time()
                        if allow_restart and (state.get("brain_bad", 0) >= 2
                                and now_ts - state.get("last_recycle", 0) > 1800):
                            print("[stress] brain4 partial-CPU -> recycling",
                                  flush=True)
                            subprocess.run(["ollama", "stop", "qubit-brain4"],
                                           capture_output=True)
                            time.sleep(3)
                            _post_chat_warmup()
                            state["last_recycle"] = now_ts
                            state["brain_bad"] = 0
            except Exception:
                pass

            # self-heal ONLY when clearly stuck: 5 straight failures AND the
            # bridge has been up long enough that we're not fighting another
            # (or our own previous) restart. Packet 7A: requires --allow-restart.
            if _restart_decision(fails, uptime, allow_restart):
                print("[stress] rctrl failed 5x -> restarting bridge (self-heal)",
                      flush=True)
                subprocess.run(["systemctl", "--user", "restart", BRIDGE_UNIT],
                               capture_output=True)
                fails = 0
                time.sleep(20)     # give the bridge time to come back
            elif fails >= 5 and uptime > 180 and not allow_restart:
                print("[stress] monitor-only: bridge looks stuck but restarts "
                      "are DISABLED (no --allow-restart). Alert, do not heal.",
                      flush=True)
        except Exception as exc:   # the loop itself must NEVER die
            print(f"[stress] cycle error (ignored): {exc}", flush=True)
        import random
        time.sleep(interval + random.uniform(0, 15))




def _ensure_probe_wav(path_str: str) -> Path:
    """1.2s quiet sine @16k mono — enough to exercise ASR->reply without a
    heavy pipeline run (the old 30s sample made every cycle intrusive)."""
    path = Path(path_str)
    if path.exists() and path.stat().st_size > 1000:
        return path
    try:
        import math
        import struct
        import wave as _wave
        path.parent.mkdir(parents=True, exist_ok=True)
        sr = 16000
        with _wave.open(str(path), "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
            frames = bytearray()
            for i in range(int(sr * 1.2)):
                v = int(0.05 * 32767 * math.sin(2 * math.pi * 300 * i / sr))
                frames += struct.pack("<h", v)
            wf.writeframes(bytes(frames))
    except Exception:
        pass
    return path


def _post_chat_warmup():
    """Reload qubit-brain4 resident after a recycle (Packet 4B contract)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from voicebridge.runner_contract import resolve
        rc = resolve()
    except Exception:
        class _Rc:
            alias = "qubit-brain4"
            url = "http://127.0.0.1:11434"
            keep_alive = -1
            num_ctx = 4096
        rc = _Rc()
    body = json.dumps({"model": rc.alias, "stream": False,
                       "keep_alive": rc.keep_alive,
                       "options": {"num_ctx": rc.num_ctx},
                       "messages": [{"role": "user", "content": "ready"}]}).encode()
    req = urllib.request.Request(rc.url + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=60).read()
    except Exception:
        pass


def main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8890")
    ap.add_argument("--token", default="")
    ap.add_argument("--watch", nargs="?", const=120, type=int, metavar="SEC")
    ap.add_argument("--allow-restart", action="store_true",
                    help="Packet 7A: enable implicit bridge/model restarts "
                         "(default is MONITOR-ONLY — restarts are prohibited)")
    ap.add_argument("--wav", default=str(Path("/tmp/openclaw-voice-bridge/stress-probe.wav")))
    args = ap.parse_args()
    wav = _ensure_probe_wav(args.wav)
    if args.watch:
        watch(args.watch, args.base, args.token, wav,
              allow_restart=args.allow_restart)
        return 0
    results = run_cycle(args.base, args.token, wav)
    print(json.dumps(results, indent=1))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
