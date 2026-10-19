#!/usr/bin/env python3
"""LIVE-capable STOP harness for the desktop helper's playback path.

Two modes:

**Demo mode (default).** Best-effort, on a machine with a working player: a wav
is played through the SAME ``DesktopHelper._speak`` path the orb uses, a STOP is
delivered (SIGUSR2, exactly like the orb's stop press), and the evidence is
recorded:

  * the helper's own player subprocess ends promptly after the stop;
  * the journal carries the canonical triple in order —
    ``voice_edit.talk.interrupt`` -> ``voice_edit.talk.playback.stopped`` ->
    ``voice_edit.talk.response.done``;
  * ``evidence.json`` + ``timeline.txt`` are written under ``--out``.

This mode exercises a PRIVATE helper instance inside the harness process. It
proves the code path, not the actually-running service.

**Speak-test mode (``--speak-test TEXT``).** Opt-in, double-gated
(``--speak-test`` AND ``QUBIT_ORB_LIVE_ALLOW_SPEAK_TEST=1``). When the live
helper is idle there is normally no reply to stop, and starting a turn needs
the mic. This mode instead asks the RUNNING service for real TTS
(``POST /agent/api/audio/speak`` — the one provider chain, no model, no mic),
plays the returned WAV through the host's REAL player (``paplay``/``aplay``,
the same player the helper uses), then delivers the orb STOP: the service's
``/api/interrupt`` plus SIGUSR2 to the validated live helper, and terminates
the player. It asserts the player was really playing before the stop and
ended promptly after it, that the live helper survived, and — when the helper
owns playback (an open ``playback.started``) — the canonical
``interrupt``/``playback.stopped``/``response.done`` triple. A missing
provider/player/helper degrades honestly; it never fabricates a PASS.

**Live mode (``--live-helper``).** Inspects/verifies the RUNNING helper
(``qubit-voice-typing.service``) and its journal, not a private copy:

  * resolves the helper pid from the heartbeat file with the same validation
    the orb uses (fresh heartbeat + ``/proc`` cmdline naming
    ``voicebridge.desktop_helper``);
  * reads ``SigCgt`` from ``/proc/<pid>/status`` to prove the SIGUSR1/SIGUSR2
    handlers are installed BEFORE any signal could be sent;
  * tails the live journal for an open ``playback.started`` (no matching
    ``playback.stopped``/``response.done``).

Read-only by default. Any signal is gated behind BOTH ``--live-helper`` and
``--live-signal``. When the helper is idle and idle in the heartbeat, the
explicit signal is a no-op survival probe (handler installed); when it is
speaking, the signal is the real orb STOP and the harness asserts the player
halts and the canonical triple lands. It never starts/stops a service, never
opens the mic, and never sends synthetic input. Not speaking -> honest
``DEGRADED`` (never a fabricated PASS).

Safety by default:
  * NO synthetic input (no xdotool, no clicks, no keys);
  * NO microphone (barge-in is forced off; the real ``_speak`` path is used);
  * NO running-service mutation (``request_id`` is empty, so no cancel POST);
  * no service is ever started or stopped;
  * a missing display/audio/helper degrades with a clear ``DEGRADED`` verdict
    instead of failing hard;
  * it writes only its output dir (and an isolated journal) unless an explicit
    ``--journal`` is supplied.

Audio source (demo mode): a generated 6 s low-volume tone by default
(deterministic, no service needed). ``--use-service-tts`` asks the live
voice-edit service to synthesize ``--text`` instead (read-only HTTP), padding
it to the requested duration so the stop always lands mid-playback.

Run::

    scripts/verify_orb_stop_live.py
    scripts/verify_orb_stop_live.py --duration 8 --out /tmp/orbstop --json
    scripts/verify_orb_stop_live.py --use-service-tts --text "Testing stop."
    scripts/verify_orb_stop_live.py --live-helper --json
    scripts/verify_orb_stop_live.py --live-signal --json   # permits one SIGUSR2
    QUBIT_ORB_LIVE_ALLOW_SPEAK_TEST=1 \\
        scripts/verify_orb_stop_live.py --speak-test "Qubit live stop test."

Exit codes: 0 PASS, 1 FAIL, 2 BLOCKED (gate not opened), 3 DEGRADED
(environment cannot demonstrate it).
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import shutil
import signal
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT = "output/orb_capture/stop_live"
TALK_PREFIX = "voice_edit.talk."
STOP_TRIPLE = ("interrupt", "playback.stopped", "response.done")

#: Opt-in gate for ``--speak-test`` (mirrors the synthetic-input gate spirit:
#: an explicit env flag AND the explicit CLI flag are both required).
SPEAK_TEST_ENV = "QUBIT_ORB_LIVE_ALLOW_SPEAK_TEST"

#: The live service endpoints used by speak-test. The TTS authority is mounted
#: under ``/agent`` (``HelperClient``'s prefix); ``/api/interrupt`` is the
#: webui dashboard stop route (no ``/agent`` prefix).
SERVICE_TTS_PATH = "/agent/api/audio/speak"
SERVICE_INTERRUPT_PATH = "/api/interrupt"
#: Journal row emitted by the service dashboard interrupt (``had_playback``
#: is the proof the service's own player was live when the stop arrived).
SERVICE_STOP_EVENT = "interrupt.request"

#: The live helper's cmdline marker (mirrors ``voicebridge.orb.client``).
HELPER_CMDLINE_MARK = "voicebridge.desktop_helper"
#: How much of the (large) live journal to scan for the current playback state.
LIVE_TAIL_BYTES = 16 * 1024 * 1024
#: An "open" ``playback.started`` older than this is treated as stale, not live.
LIVE_STALE_AFTER_S = 120.0


# ---------------------------------------------------------------------------
# pure journal parsing / evidence reduction
# ---------------------------------------------------------------------------
def classify_talk_row(row: Any) -> Optional[Dict[str, Any]]:
    """Normalize one journal row to a talk boundary, or ``None``.

    Only ``voice_edit.talk.*`` rows are evidence here (a stop harness needs the
    helper's own playback boundaries, not unrelated service logs).
    """
    try:
        if not isinstance(row, dict):
            return None
        name = row.get("event")
        if not isinstance(name, str) or not name.startswith(TALK_PREFIX):
            return None
        ts = row.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            ts = None
        return {
            "ts": ts,
            "event": name,
            "type": name[len(TALK_PREFIX):],
            "turn_id": row.get("turn_id"),
            "seq": row.get("seq"),
            "pid": row.get("pid"),
        }
    except Exception:  # noqa: BLE001 - parsing is fail-soft
        return None


def read_talk_rows(path: Path, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    """Read completed journal lines from ``offset``; return ``(events, end)``."""
    events: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(max(0, int(offset)))
            while True:
                pos = fh.tell()
                line = fh.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    fh.seek(pos)  # a concurrent writer is mid-line
                    break
                line = line.strip()
                if not line or not line.startswith("{"):
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                ev = classify_talk_row(row)
                if ev is not None:
                    events.append(ev)
            return events, fh.tell()
    except Exception:  # noqa: BLE001 - missing/unreadable journal
        return events, offset


def _first_index(types: List[str], wanted: str) -> Optional[int]:
    try:
        return types.index(wanted)
    except ValueError:
        return None


def summarize(events: List[Dict[str, Any]], *, t_stop: Optional[float] = None,
              player_started: bool = False,
              player_exited: Optional[bool] = None,
              playback_live_before_stop: Optional[bool] = None,
              stop_action: str = "signal",
              degraded: Optional[List[str]] = None) -> Dict[str, Any]:
    """Reduce journal events + player observation to a PASS/FAIL verdict.

    Pure and hermetic-testable. ``triple_ordered`` requires all three canonical
    boundaries to be present in that relative order; ``stop_latency_s`` is the
    first ``interrupt`` timestamp minus ``t_stop`` when both are known.
    """
    degraded = list(degraded or [])
    types = [str(e.get("type", "")) for e in events]
    indices = {name: _first_index(types, name) for name in STOP_TRIPLE}
    missing = [name for name, idx in indices.items() if idx is None]
    triple_ordered = (not missing and
                      indices["interrupt"] < indices["playback.stopped"]
                      < indices["response.done"])
    latency: Optional[float] = None
    if t_stop is not None:
        for ev in events:
            if ev.get("type") == "interrupt" and isinstance(ev.get("ts"),
                                                            (int, float)):
                latency = round(float(ev["ts"]) - float(t_stop), 4)
                break
    if not player_started:
        verdict = "DEGRADED"
        reason = "player never started (no paplay/aplay or no audio server)"
    elif playback_live_before_stop is False:
        verdict = "DEGRADED"
        reason = "player exited before the stop (no audio server/device?)"
    elif player_exited is False:
        verdict = "FAIL"
        reason = "player process was still alive after the stop"
    elif missing:
        verdict = "FAIL"
        reason = "missing stop boundaries: " + ", ".join(missing)
    elif not triple_ordered:
        verdict = "FAIL"
        reason = "stop boundaries present but out of order"
    else:
        verdict = "PASS"
        reason = "player ended and the canonical stop triple is journaled"
    return {
        "verdict": verdict,
        "reason": reason,
        "stop_action": stop_action,
        "player_started": bool(player_started),
        "player_exited": player_exited,
        "playback_live_before_stop": playback_live_before_stop,
        "triple": list(STOP_TRIPLE),
        "triple_present": not missing,
        "triple_ordered": bool(triple_ordered),
        "missing": missing,
        "stop_latency_s": latency,
        "event_count": len(events),
        "degraded": degraded,
    }


def build_timeline(events: List[Dict[str, Any]], t0: float,
                   header: Optional[List[str]] = None) -> str:
    """Render the talk timeline relative to ``t0`` (stable and ordered)."""
    def key(ev: Dict[str, Any]) -> Tuple[int, float, int]:
        ts = ev.get("ts")
        seq = ev.get("seq")
        return ((1, 0.0, 0) if ts is None else (0, float(ts), 0)
                if not isinstance(seq, int) else
                (0, float(ts), int(seq)))

    lines: List[str] = list(header or [])
    for ev in sorted(events, key=key):
        ts = ev.get("ts")
        rel = "  ?    " if ts is None else f"{float(ts) - t0:+8.3f}"
        turn = ev.get("turn_id") or "-"
        seq = ev.get("seq")
        seq_s = f"seq={seq}" if seq is not None else "seq=-"
        lines.append(f"{rel}  {ev.get('type', ''):<22} turn={turn:<9} {seq_s}")
    return "\n".join(lines) + "\n"


def ordered_triple(events: List[Dict[str, Any]],
                   *, t_stop: Optional[float] = None) -> Dict[str, Any]:
    """Canonical stop-boundary status for ``events`` (pure).

    ``ordered`` requires ``interrupt`` -> ``playback.stopped`` ->
    ``response.done`` in that relative order; ``latency_s`` is the first
    ``interrupt`` timestamp minus ``t_stop`` when both are known.
    """
    types = [str(e.get("type", "")) for e in events]
    indices = {name: _first_index(types, name) for name in STOP_TRIPLE}
    missing = [name for name, idx in indices.items() if idx is None]
    present = not missing
    ordered = bool(
        present
        and indices["interrupt"] < indices["playback.stopped"]
        < indices["response.done"])
    latency: Optional[float] = None
    if t_stop is not None:
        for ev in events:
            if ev.get("type") == "interrupt" and isinstance(
                    ev.get("ts"), (int, float)) and not isinstance(
                        ev.get("ts"), bool):
                latency = round(float(ev["ts"]) - float(t_stop), 4)
                break
    return {"triple": list(STOP_TRIPLE), "present": present,
            "ordered": ordered, "missing": missing, "latency_s": latency}


# ---------------------------------------------------------------------------
# live helper inspection (read-only)
# ---------------------------------------------------------------------------
def read_proc_cmdline(pid: int, proc_root: Path = Path("/proc")) -> str:
    """One process's cmdline as text ("" when unreadable). Never raises."""
    try:
        raw = (Path(proc_root) / str(int(pid)) / "cmdline").read_bytes()
        return raw.replace(b"\0", b" ").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


def read_proc_signal_mask(pid: int,
                          proc_root: Path = Path("/proc")) -> Optional[int]:
    """``SigCgt`` (caught-signal bitmask) for ``pid``; ``None`` if unreadable.

    Bit ``signum - 1`` is set when a handler is installed for that signal.
    Reading this is how the harness proves a SIGUSR2 handler exists BEFORE it
    would ever signal the live helper (sending SIGUSR2 to a helper without a
    handler terminates it).
    """
    try:
        text = (Path(proc_root) / str(int(pid)) / "status").read_text(
            encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None
    for line in text.splitlines():
        if line.startswith("SigCgt:"):
            try:
                return int(line.split()[1], 16)
            except (IndexError, ValueError):
                return None
    return None


def handler_installed(mask: Optional[int], signum: int) -> bool:
    """True when the caught-signal ``mask`` covers ``signum`` (pure)."""
    try:
        return mask is not None and bool(
            (int(mask) >> (int(signum) - 1)) & 1)
    except Exception:  # noqa: BLE001
        return False


def resolve_live_helper(*, proc_root: Path = Path("/proc"),
                        live_fn: Optional[Callable[[], bool]] = None,
                        read_fn: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Resolve the RUNNING helper pid the same validated way the orb does.

    Requires a fresh heartbeat (``heartbeat.live``) AND a ``/proc`` cmdline
    that names ``voicebridge.desktop_helper``, so a stale heartbeat naming a
    recycled/foreign pid is refused. Never raises.
    """
    out: Dict[str, Any] = {"ok": False, "pid": None, "heartbeat": None,
                           "cmdline": "", "reason": ""}
    try:
        if live_fn is None or read_fn is None:
            from voicebridge.voice_edit import heartbeat as _hb
            live_fn = live_fn or _hb.live
            read_fn = read_fn or _hb.read
        if not live_fn():
            out["reason"] = "helper heartbeat is missing/stale (helper not live)"
            return out
        hb = read_fn()
        if not isinstance(hb, dict):
            out["reason"] = "helper heartbeat is unreadable"
            return out
        out["heartbeat"] = hb
        try:
            pid = int(hb.get("pid", 0) or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            out["reason"] = "helper heartbeat carries no pid"
            return out
        cmdline = read_proc_cmdline(pid, proc_root)
        out["cmdline"] = cmdline
        if HELPER_CMDLINE_MARK not in cmdline:
            out["reason"] = ("heartbeat pid is not a live desktop helper "
                             "(cmdline validation)")
            return out
        out["pid"] = pid
        out["ok"] = True
        return out
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"helper resolution failed: {exc}"
        return out


def read_tail_talk_rows(path: Path,
                        max_bytes: int = LIVE_TAIL_BYTES
                        ) -> Tuple[List[Dict[str, Any]], int]:
    """Read the last ``max_bytes`` of a journal; return ``(events, size)``.

    The journal is large (hundreds of MB); only the tail is parsed and only
    lines containing ``voice_edit.talk.`` are decoded. A partial first line
    (the file is read mid-write) is dropped. ``size`` is the file size (the
    append offset for a subsequent stop observation); ``0`` when unreadable.
    """
    try:
        with Path(path).open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            start = max(0, size - max(0, int(max_bytes)))
            prev = b""
            if start > 0:
                fh.seek(start - 1)
                prev = fh.read(1)
            fh.seek(start)
            data = fh.read()
    except Exception:  # noqa: BLE001
        return [], 0
    lines = data.decode("utf-8", "replace").splitlines()
    if start > 0 and prev != b"\n" and lines:
        lines = lines[1:]  # a concurrent writer is mid-line at the boundary
    events: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line or TALK_PREFIX not in line:
            continue
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        ev = classify_talk_row(row)
        if ev is not None:
            events.append(ev)
    return events, size


def classify_service_row(row: Any) -> Optional[Dict[str, Any]]:
    """Normalize one journal row to a service stop boundary, or ``None``.

    Only the dashboard interrupt row is service evidence here; it carries the
    decisive ``had_playback`` flag (True = a real player was live when the
    stop arrived, False = idle/no-op).
    """
    try:
        if not isinstance(row, dict):
            return None
        name = row.get("event")
        if name != SERVICE_STOP_EVENT:
            return None
        ts = row.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            ts = None
        return {
            "ts": ts,
            "event": name,
            "type": SERVICE_STOP_EVENT,
            "had_playback": row.get("had_playback"),
            "announce": row.get("announce"),
            "pid": row.get("pid"),
        }
    except Exception:  # noqa: BLE001 - parsing is fail-soft
        return None


def read_service_rows(path: Path,
                      offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    """Read completed service-stop journal lines from ``offset`` (append-safe)."""
    events: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            fh.seek(max(0, int(offset)))
            while True:
                pos = fh.tell()
                line = fh.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    fh.seek(pos)
                    break
                line = line.strip()
                if not line or SERVICE_STOP_EVENT not in line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                ev = classify_service_row(row)
                if ev is not None:
                    events.append(ev)
            return events, fh.tell()
    except Exception:  # noqa: BLE001
        return events, offset


def analyze_service_stop(events: List[Dict[str, Any]], *,
                         t_stop: Optional[float] = None) -> Dict[str, Any]:
    """Reduce service ``interrupt.request`` rows to stop evidence (pure).

    Only rows at/after ``t_stop`` count (an older interrupt cannot make this
    stop look successful). ``had_playback`` is the newest relevant row's
    flag, so a stop that found the service player live is distinguishable
    from an idle no-op.
    """
    relevant: List[Dict[str, Any]] = []
    for ev in events:
        ts = ev.get("ts")
        if t_stop is not None:
            if not isinstance(ts, (int, float)) or isinstance(ts, bool):
                continue
            if float(ts) < float(t_stop):
                continue
        relevant.append(ev)
    last = relevant[-1] if relevant else None
    return {
        "stop_seen": bool(relevant),
        "count": len(relevant),
        "had_playback": (None if last is None
                         else bool(last.get("had_playback"))),
        "ts": None if last is None else last.get("ts"),
    }


def analyze_playback(events: List[Dict[str, Any]], *,
                     now: Optional[float] = None,
                     stale_after: float = LIVE_STALE_AFTER_S) -> Dict[str, Any]:
    """Reduce the journal tail to the helper's current playback state (pure).

    ``speaking`` is true when the newest ``playback.started`` has no later
    ``playback.stopped``/``response.done``. An open start older than
    ``stale_after`` is reported as ``stale`` and NOT as speaking (a crashed
    writer must not fabricate a live reply).
    """
    now = float(now if now is not None else time.time())
    last: Dict[str, Tuple[int, Dict[str, Any]]] = {}
    for i, ev in enumerate(events):
        t = ev.get("type")
        if t in ("playback.started", "interrupt", "playback.stopped",
                 "response.done"):
            last[str(t)] = (i, ev)
    started = last.get("playback.started")
    stopped = last.get("playback.stopped")
    done = last.get("response.done")
    open_ = False
    if started is not None:
        open_ = stopped is None or started[0] > stopped[0]
        if (done is not None and done[0] > started[0]
                and (stopped is None or done[0] > stopped[0])):
            open_ = False  # response.done closes an error/empty turn
    started_ts: Optional[float] = None
    age: Optional[float] = None
    if started is not None:
        raw = started[1].get("ts")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            started_ts = float(raw)
            age = max(0.0, now - started_ts)
    stale = bool(open_ and age is not None and age > float(stale_after))
    speaking = bool(open_ and not stale)
    last_boundary = None
    if last:
        idx, name = max((v[0], k) for k, v in last.items())
        last_boundary = {"type": name, "ts": last[name][1].get("ts"),
                         "turn_id": last[name][1].get("turn_id")}
    return {
        "speaking": speaking,
        "open": open_,
        "stale": stale,
        "started_ts": started_ts,
        "started_age_s": None if age is None else round(age, 3),
        "turn_id": started[1].get("turn_id") if started else None,
        "last_boundary": last_boundary,
        "event_count": len(events),
    }


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:  # noqa: BLE001
        return False


def live_check(*, allow_signal: bool = False,
               journal: Optional[Path] = None,
               stale_after: float = LIVE_STALE_AFTER_S,
               timeout: float = 6.0,
               tail_bytes: int = LIVE_TAIL_BYTES,
               proc_root: Path = Path("/proc"),
               now_fn: Callable[[], float] = time.time,
               sleep_fn: Callable[[float], None] = time.sleep,
               kill_fn: Callable[[int, int], Any] = os.kill,
               alive_fn: Optional[Callable[[int], bool]] = None,
               live_fn: Optional[Callable[[], bool]] = None,
               read_fn: Optional[Callable[[], Any]] = None) -> Dict[str, Any]:
    """Verify the RUNNING helper's stop path. Read-only unless allowed.

    Never raises. Signals the live helper only when ``allow_signal`` is true,
    the SIGUSR2 handler is proven installed, the pid re-validates immediately
    before the signal, and (for an idle probe) the heartbeat state is
    ``idle`` so an in-flight turn is never cancelled.
    """
    alive_fn = alive_fn or _pid_alive
    result: Dict[str, Any] = {
        "mode": "live",
        "verdict": "DEGRADED",
        "reason": "",
        "degraded": [],
        "helper_pid": None,
        "helper_state": None,
        "heartbeat": None,
        "sigusr1_handler": None,
        "sigusr2_handler": None,
        "signal_allowed": bool(allow_signal),
        "signal_sent": False,
        "survives_sigusr2": None,
        "helper_alive_after": None,
        "stop_at": None,
        "journal": str(journal) if journal is not None else None,
        "playback": None,
        "triple_present": None,
        "triple_ordered": None,
        "missing": [],
        "stop_latency_s": None,
        "events": [],
        "events_after_stop": [],
    }
    if journal is None:
        try:
            from voicebridge.config import canonical_state_dir
            journal = canonical_state_dir() / "events.jsonl"
        except Exception:  # noqa: BLE001
            journal = (Path.home() / ".local" / "state" / "qubit-voicebridge"
                       / "events.jsonl")
        result["journal"] = str(journal)

    helper = resolve_live_helper(proc_root=proc_root, live_fn=live_fn,
                                 read_fn=read_fn)
    result["helper_pid"] = helper.get("pid")
    result["heartbeat"] = helper.get("heartbeat")
    hb = helper.get("heartbeat") or {}
    state = str(hb.get("state") or "") if isinstance(hb, dict) else ""
    result["helper_state"] = state or None
    if not helper.get("ok"):
        reason = str(helper.get("reason") or "no live helper")
        result["reason"] = reason
        result["degraded"].append(reason)
        return result

    pid = int(helper["pid"])
    mask = read_proc_signal_mask(pid, proc_root)
    result["sigusr1_handler"] = handler_installed(mask, signal.SIGUSR1)
    result["sigusr2_handler"] = handler_installed(mask, signal.SIGUSR2)
    handler_ok = bool(result["sigusr2_handler"])
    if mask is None:
        result["degraded"].append(
            "cannot read /proc/<pid>/status: signal-handler state unknown")

    events, end = read_tail_talk_rows(journal, tail_bytes)
    result["events"] = events[-200:]
    play = analyze_playback(events, now=now_fn(), stale_after=stale_after)
    result["playback"] = play
    if end <= 0:
        reason = f"live journal is missing/unreadable: {journal}"
        result["reason"] = reason
        result["degraded"].append(reason)
        return result

    if play["speaking"]:
        if not allow_signal:
            result["reason"] = ("live helper is speaking; read-only mode sent no "
                                "signal (pass --live-signal to send the STOP)")
            result["degraded"].append(result["reason"])
            return result
        if not handler_ok:
            result["reason"] = ("refusing to signal: SIGUSR2 handler is not "
                                "installed (the signal would terminate the live "
                                "helper)")
            result["degraded"].append(result["reason"])
            return result
        # Re-validate immediately before any signal: never signal a pid that
        # changed or a process without the handler.
        fresh = resolve_live_helper(proc_root=proc_root, live_fn=live_fn,
                                    read_fn=read_fn)
        mask2 = (read_proc_signal_mask(int(fresh["pid"]), proc_root)
                 if fresh.get("ok") else None)
        if (not fresh.get("ok") or int(fresh.get("pid") or 0) != pid
                or not handler_installed(mask2, signal.SIGUSR2)):
            result["reason"] = ("live helper pid changed/disappeared or lost its "
                                "SIGUSR2 handler before the stop; not signalled")
            result["degraded"].append(result["reason"])
            return result
        t_stop = float(now_fn())
        try:
            kill_fn(pid, signal.SIGUSR2)
        except Exception as exc:  # noqa: BLE001
            result["reason"] = f"could not signal the live helper: {exc}"
            result["degraded"].append(result["reason"])
            return result
        result["signal_sent"] = True
        result["stop_at"] = t_stop
        new_events: List[Dict[str, Any]] = []
        offset = end
        deadline = t_stop + max(0.5, float(timeout))
        # Only the speaking turn's boundaries count (a later turn's events
        # cannot make this stop look successful).
        active_turn = play.get("turn_id")

        def _relevant(evs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if active_turn is None:
                return evs
            return [e for e in evs if e.get("turn_id") == active_turn]

        while now_fn() < deadline:
            batch, offset = read_talk_rows(journal, offset)
            new_events.extend(batch)
            types = [e.get("type") for e in _relevant(new_events)]
            if all(name in types for name in STOP_TRIPLE):
                break
            sleep_fn(0.05)
        result["events_after_stop"] = new_events[-100:]
        alive = bool(alive_fn(pid))
        result["helper_alive_after"] = alive
        relevant = _relevant(new_events)
        triple = ordered_triple(relevant, t_stop=t_stop)
        result["triple_present"] = triple["present"]
        result["triple_ordered"] = triple["ordered"]
        result["missing"] = triple["missing"]
        result["stop_latency_s"] = triple["latency_s"]
        ended_before = any(
            e.get("type") == "playback.stopped"
            and isinstance(e.get("ts"), (int, float))
            and not isinstance(e.get("ts"), bool)
            and float(e["ts"]) < t_stop for e in relevant)
        if not alive:
            result["verdict"] = "FAIL"
            result["reason"] = "live helper did not survive the stop signal"
        elif ended_before and not triple["ordered"]:
            result["reason"] = ("playback ended before the stop was exercised; "
                                "no stop could be observed")
            result["degraded"].append(result["reason"])
        elif triple["missing"]:
            result["verdict"] = "FAIL"
            result["reason"] = ("stop boundaries missing after the signal: "
                                + ", ".join(triple["missing"]))
        elif not triple["ordered"]:
            result["verdict"] = "FAIL"
            result["reason"] = "stop boundaries present but out of order"
        else:
            result["verdict"] = "PASS"
            result["reason"] = ("live helper stopped playback and journaled the "
                                "canonical stop triple")
        return result

    # --- not speaking -------------------------------------------------------
    if play["stale"]:
        note = (f"last playback.started is {play['started_age_s']}s old with no "
                "matching playback.stopped; treating it as stale")
    elif play["started_ts"] is None:
        note = "no playback.started in the journal tail"
    else:
        note = "no open playback in the journal tail"
    result["degraded"].append(note + " — live helper is not speaking")
    if not allow_signal:
        result["reason"] = ("live helper is not speaking; read-only mode sent no "
                            "signal (DEGRADED: nothing live to stop)")
        return result
    if state != "idle":
        result["reason"] = (f"refusing idle stop probe: helper state is "
                            f"{state!r} (it could cancel an in-flight turn)")
        result["degraded"].append(result["reason"])
        return result
    if not handler_ok:
        result["reason"] = ("refusing to signal: SIGUSR2 handler is not "
                            "installed (the signal would terminate the live "
                            "helper)")
        result["degraded"].append(result["reason"])
        return result
    fresh = resolve_live_helper(proc_root=proc_root, live_fn=live_fn,
                                read_fn=read_fn)
    mask2 = (read_proc_signal_mask(int(fresh["pid"]), proc_root)
             if fresh.get("ok") else None)
    if (not fresh.get("ok") or int(fresh.get("pid") or 0) != pid
            or not handler_installed(mask2, signal.SIGUSR2)):
        result["reason"] = ("live helper pid changed/disappeared or lost its "
                            "SIGUSR2 handler before the probe; not signalled")
        result["degraded"].append(result["reason"])
        return result
    t_stop = float(now_fn())
    try:
        kill_fn(pid, signal.SIGUSR2)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"could not signal the live helper: {exc}"
        result["degraded"].append(result["reason"])
        return result
    result["signal_sent"] = True
    result["stop_at"] = t_stop
    sleep_fn(0.15)
    alive = bool(alive_fn(pid))
    result["helper_alive_after"] = alive
    result["survives_sigusr2"] = alive
    if alive:
        result["reason"] = ("live helper is idle; SIGUSR2 was survived (handler "
                            "installed) but there was no playback to stop")
    else:
        result["verdict"] = "FAIL"
        result["reason"] = "live helper did not survive SIGUSR2"
    return result


# ---------------------------------------------------------------------------
# audio helpers (no mic, no network unless asked)
# ---------------------------------------------------------------------------
def make_tone_wav(duration_s: float = 6.0, *, rate: int = 22050,
                  freq: float = 440.0, volume: float = 0.05) -> bytes:
    """A quiet mono 16-bit tone of ``duration_s`` seconds (deterministic)."""
    import math
    frames = max(1, int(float(duration_s) * rate))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        chunk = bytearray()
        for i in range(frames):
            sample = int(32767 * volume * math.sin(2 * math.pi * freq * i / rate))
            chunk += int(sample).to_bytes(2, "little", signed=True)
            if len(chunk) >= 8192:
                wf.writeframes(bytes(chunk))
                chunk.clear()
        if chunk:
            wf.writeframes(bytes(chunk))
    return buf.getvalue()


def pad_wav_min_duration(data: bytes, duration_s: float) -> bytes:
    """Append silence so a synth'd wav is at least ``duration_s`` long."""
    try:
        with wave.open(io.BytesIO(data), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            width = wf.getsampwidth()
            raw = wf.readframes(wf.getnframes())
        needed = int(float(duration_s) * rate) - (
            len(raw) // max(1, channels * width))
        if needed <= 0:
            return data
        out = io.BytesIO()
        with wave.open(out, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(width)
            wf.setframerate(rate)
            wf.writeframes(raw)
            wf.writeframes(b"\x00" * needed * channels * width)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - keep the original audio
        return data


def _playback_tools() -> List[str]:
    return [name for name in ("paplay", "aplay") if shutil.which(name)]


def _deep_copy_cfg(cfg: Any) -> Any:
    from voicebridge.config import Config
    data = copy.deepcopy(getattr(cfg, "data", {}) or {})
    if not isinstance(data, dict):
        data = {}
    ve = data.setdefault("voice_edit", {})
    if not isinstance(ve, dict):
        ve = {}
        data["voice_edit"] = ve
    ve["barge_in"] = False          # never open a mic
    ve["speak_answers"] = True
    return Config(data=data, path=getattr(cfg, "path", None))


# ---------------------------------------------------------------------------
# live run
# ---------------------------------------------------------------------------
class _NullOverlay:
    def set_status(self, *_a: Any, **_k: Any) -> None:
        pass

    def show_preview(self, *_a: Any, **_k: Any) -> None:
        pass

    def show_error(self, *_a: Any, **_k: Any) -> None:
        pass


class _NullDesktop:
    def is_fullscreen(self, _wid: str = "") -> bool:
        return False

    def session(self) -> Dict[str, Any]:
        return {"x11": False}

    def set_clipboard(self, _text: str) -> None:
        pass


class _WavClient:
    """The helper's TTS client shape, returning a pre-rendered wav."""

    def __init__(self, wav: bytes) -> None:
        self._wav = wav

    def speak(self, _text: str) -> bytes:
        return self._wav


def _iso(ts: float) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(ts).isoformat(
            timespec="milliseconds")
    except Exception:  # noqa: BLE001
        return str(ts)


def run(*, out_dir: Optional[Path] = None, duration: float = 6.0,
        use_service_tts: bool = False, text: str = "Qubit stop harness.",
        stop_action: str = "signal", journal: Optional[Path] = None,
        startup_timeout: float = 6.0, play_settle: float = 0.2,
        join_timeout: float = 6.0, now_fn: Callable[[], float] = time.time,
        sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Run the live stop demonstration. Never raises; returns a summary."""
    from voicebridge import logging_setup
    from voicebridge.config import Config
    from voicebridge.desktop_helper import DesktopHelper
    from voicebridge.voice_edit.talk_events import TalkEventSequencer

    t0 = now_fn()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t0))
    base = Path(out_dir) if out_dir else (REPO_ROOT / DEFAULT_OUT)
    capture_dir = base / stamp
    capture_dir.mkdir(parents=True, exist_ok=True)
    journal = Path(journal) if journal else (capture_dir / "events.jsonl")
    summary: Dict[str, Any] = {
        "started": _iso(t0),
        "capture_dir": str(capture_dir),
        "journal": str(journal),
        "stop_action": stop_action,
        "degraded": [],
        "audio_source": "service-tts" if use_service_tts else "tone",
        "player": None,
        "player_pid": None,
        "stop_at": None,
        "player_exit_at": None,
        "stop_to_exit_s": None,
        "player_started": False,
        "player_exited": None,
        "playback_live_before_stop": None,
    }
    if not _playback_tools():
        summary["degraded"].append(
            "no paplay/aplay installed: playback cannot be demonstrated")
        return _finish(summary, [], t0, capture_dir, journal, stop_action)

    # Isolate the journal in the output dir unless the caller supplied one.
    journal.parent.mkdir(parents=True, exist_ok=True)
    try:
        start_off = journal.stat().st_size if journal.exists() else 0
    except Exception:  # noqa: BLE001
        start_off = 0
    try:
        logging_setup.setup(jsonl=True, jsonl_path=journal)
    except Exception as exc:  # noqa: BLE001
        summary["degraded"].append(f"journal setup failed: {exc}")
        start_off = 0

    # -- audio: quiet tone by default; live service TTS on request ----------
    try:
        wav = make_tone_wav(duration)
        if use_service_tts:
            try:
                from voicebridge.desktop_helper import (
                    HelperClient, resolve_service_base, resolve_token)
                cfg0 = Config.load()
                cli = HelperClient(resolve_service_base(cfg0),
                                   resolve_token(cfg0))
                got = cli.speak(text)
                if got:
                    wav = pad_wav_min_duration(got, duration)
                else:
                    summary["degraded"].append(
                        "service TTS returned no audio; used the tone")
            except Exception as exc:  # noqa: BLE001
                summary["degraded"].append(
                    f"service TTS unavailable ({str(exc)[:100]}); used the tone")
    except Exception as exc:  # noqa: BLE001
        summary["degraded"].append(f"could not build audio: {exc}")
        return _finish(summary, [], t0, capture_dir, journal, stop_action)

    try:
        cfg = _deep_copy_cfg(Config.load())
    except Exception:
        cfg = Config(data={"voice_edit": {"barge_in": False,
                                          "speak_answers": True}})
    helper = DesktopHelper(cfg, client=_WavClient(wav), overlay=_NullOverlay(),
                           desktop=_NullDesktop(),
                           talk=TalkEventSequencer())
    installed = False
    player_proc: Any = None
    speak_thread: Optional[threading.Thread] = None
    try:
        try:
            helper._install_orb_signal_handlers()
            installed = True
        except Exception as exc:  # noqa: BLE001
            summary["degraded"].append(f"signal handler install failed: {exc}")
        helper._talk.begin_turn()
        speak_thread = threading.Thread(target=helper._speak, args=(text,),
                                        name="live-stop-speak", daemon=True)
        speak_thread.start()

        deadline = now_fn() + max(0.1, float(startup_timeout))
        while now_fn() < deadline:
            player_proc = getattr(helper, "_playback_proc", None)
            if player_proc is not None and player_proc.poll() is None:
                summary["player_started"] = True
                try:
                    summary["player"] = str(player_proc.args[0])
                    summary["player_pid"] = int(player_proc.pid)
                except Exception:  # noqa: BLE001
                    pass
                break
            if not speak_thread.is_alive():
                break
            sleep_fn(0.02)
        if not summary["player_started"]:
            summary["degraded"].append(
                "player never stayed running (audio server/device missing?)")
        else:
            sleep_fn(max(0.0, float(play_settle)))
            # A player that died during the settle window never demonstrated
            # playback: no stop was delivered, so this run is DEGRADED (not a
            # stop failure). A real audio host keeps the process alive here.
            if player_proc.poll() is not None:
                summary["playback_live_before_stop"] = False
                summary["player_exited"] = True
                summary["player_exit_at"] = now_fn()
                summary["degraded"].append(
                    "player exited before the stop "
                    "(no audio server/device?) — stop not delivered")
                speak_thread.join(timeout=max(0.1, float(join_timeout)))
            else:
                summary["playback_live_before_stop"] = True
                summary["stop_at"] = now_fn()
                if stop_action == "signal":
                    os.kill(os.getpid(), signal.SIGUSR2)
                else:
                    helper.stop_speaking()
                speak_thread.join(timeout=max(0.1, float(join_timeout)))
            if speak_thread.is_alive():
                summary["degraded"].append(
                    "_speak did not return after the stop")
            # Wait out the player's own exit (SIGTERM handling).
            exit_at: Optional[float] = None
            if summary.get("player_exited") is True:
                exit_at = summary.get("player_exit_at")
            else:
                try:
                    player_proc.wait(timeout=3.0)
                    exit_at = now_fn()
                except Exception:  # noqa: BLE001 - still alive: kill for cleanup
                    try:
                        player_proc.terminate()
                        player_proc.wait(timeout=1.0)
                        exit_at = now_fn()
                    except Exception:  # noqa: BLE001
                        try:
                            player_proc.kill()
                        except Exception:  # noqa: BLE001
                            pass
            if exit_at is not None:
                summary["player_exit_at"] = exit_at
                summary["player_exited"] = True
                if summary.get("stop_at") is not None:
                    summary["stop_to_exit_s"] = round(
                        float(exit_at) - float(summary["stop_at"]), 4)
            else:
                summary["player_exited"] = False
    finally:
        if player_proc is not None and player_proc.poll() is None:
            try:
                player_proc.terminate()
                player_proc.wait(timeout=1.0)
            except Exception:  # noqa: BLE001
                try:
                    player_proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        if installed:
            try:
                prev1 = getattr(helper, "_orb_prev_sigusr1", None)
                prev2 = getattr(helper, "_orb_prev_sigusr2", None)
                if prev1 is not None:
                    signal.signal(signal.SIGUSR1, prev1)
                if prev2 is not None:
                    signal.signal(signal.SIGUSR2, prev2)
            except Exception:  # noqa: BLE001
                pass

    events, _ = read_talk_rows(journal, start_off)
    return _finish(summary, events, t0, capture_dir, journal, stop_action)


def _finish(summary: Dict[str, Any], events: List[Dict[str, Any]], t0: float,
            capture_dir: Path, journal: Path, stop_action: str) -> Dict[str, Any]:
    evidence = summarize(
        events,
        t_stop=summary.get("stop_at"),
        player_started=bool(summary.get("player_started")),
        player_exited=summary.get("player_exited"),
        playback_live_before_stop=summary.get("playback_live_before_stop"),
        stop_action=stop_action,
        degraded=summary.get("degraded"))
    summary["summary"] = evidence
    summary["verdict"] = evidence["verdict"]
    header = [
        f"live orb STOP harness — {summary['started']}",
        f"journal: {journal}",
        f"player: {summary.get('player') or '(none)'} "
        f"pid={summary.get('player_pid') or '-'}",
        f"stop action: {stop_action}",
        f"verdict: {evidence['verdict']} — {evidence['reason']}",
    ]
    if summary.get("degraded"):
        header.append("degraded: " + "; ".join(summary["degraded"]))
    header.append("--- talk timeline (voice_edit.talk.*) ---")
    try:
        (capture_dir / "timeline.txt").write_text(
            build_timeline(events, t0, header=header), encoding="utf-8")
        (capture_dir / "evidence.json").write_text(
            json.dumps({**summary, "events": events}, indent=2),
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - evidence write is best-effort
        summary.setdefault("degraded", []).append(f"evidence write failed: {exc}")
    summary["capture_dir"] = str(capture_dir)
    return summary


def run_live(*, out_dir: Optional[Path] = None, allow_signal: bool = False,
             journal: Optional[Path] = None,
             stale_after: float = LIVE_STALE_AFTER_S, timeout: float = 6.0,
             tail_bytes: int = LIVE_TAIL_BYTES,
             now_fn: Callable[[], float] = time.time,
             sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Verify the RUNNING helper; write live evidence. Never raises."""
    t0 = float(now_fn())
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t0))
    base = Path(out_dir) if out_dir else (REPO_ROOT / DEFAULT_OUT)
    capture_dir = base / stamp
    try:
        capture_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001 - evidence is best-effort
        pass
    try:
        result = live_check(allow_signal=allow_signal, journal=journal,
                            stale_after=stale_after, timeout=timeout,
                            tail_bytes=tail_bytes, now_fn=now_fn,
                            sleep_fn=sleep_fn)
    except Exception as exc:  # noqa: BLE001
        result = {"mode": "live", "verdict": "DEGRADED",
                  "reason": f"live check crashed: {exc}",
                  "degraded": [f"live check crashed: {exc}"],
                  "events": [], "events_after_stop": []}
    result["started"] = _iso(t0)
    result["capture_dir"] = str(capture_dir)

    events = list(result.get("events") or [])
    events.extend(result.get("events_after_stop") or [])
    seen = set()
    unique: List[Dict[str, Any]] = []
    for ev in events:
        key = (ev.get("ts"), ev.get("seq"), ev.get("type"), ev.get("turn_id"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)

    header = [
        f"live helper STOP check — {result['started']}",
        f"helper pid: {result.get('helper_pid') or '-'} "
        f"state={result.get('helper_state') or '-'}",
        f"journal: {result.get('journal')}",
        f"sigusr1 handler: {result.get('sigusr1_handler')} "
        f"sigusr2 handler: {result.get('sigusr2_handler')}",
        f"signal sent: {result.get('signal_sent')} "
        f"(allowed={result.get('signal_allowed')})",
        f"verdict: {result.get('verdict')} — {result.get('reason')}",
    ]
    if result.get("degraded"):
        header.append("degraded: " + "; ".join(result["degraded"]))
    header.append("--- talk timeline (voice_edit.talk.*) ---")
    ts_values = [float(e["ts"]) for e in unique
                 if isinstance(e.get("ts"), (int, float))
                 and not isinstance(e.get("ts"), bool)]
    timeline_t0 = min(ts_values) if ts_values else t0
    try:
        (capture_dir / "timeline.txt").write_text(
            build_timeline(unique, timeline_t0, header=header),
            encoding="utf-8")
        (capture_dir / "evidence.json").write_text(
            json.dumps({**result, "events": unique[-200:]}, indent=2),
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        result.setdefault("degraded", []).append(f"evidence write failed: {exc}")
    return result


# ---------------------------------------------------------------------------
# gated speak-test (real service TTS -> real host player -> real STOP)
# ---------------------------------------------------------------------------
def speak_test_allowed(environ: Optional[Dict[str, str]] = None) -> bool:
    """True only when the explicit opt-in env gate is opened (pure)."""
    env = os.environ if environ is None else environ
    try:
        return str(env.get(SPEAK_TEST_ENV, "") or "").strip().lower() in (
            "1", "true", "yes", "on")
    except Exception:  # noqa: BLE001
        return False


def summarize_speak_test(*, provider: str = "", wav_bytes: int = 0,
                         player_started: bool = False,
                         player_exited: Optional[bool] = None,
                         playback_live_before_stop: Optional[bool] = None,
                         helper_pid: Optional[int] = None,
                         helper_alive_after: Optional[bool] = None,
                         triple: Optional[Dict[str, Any]] = None,
                         service_stop: Optional[Dict[str, Any]] = None,
                         degraded: Optional[List[str]] = None) -> Dict[str, Any]:
    """Reduce speak-test observations to a PASS/FAIL/DEGRADED verdict (pure).

    A PASS requires a REAL provider clip, a player that was live immediately
    before the stop and ended after it, and a live helper that survived the
    stop. The canonical triple is required only when the helper owned playback
    (``triple.applicable``); the service-player path is explicitly marked
    ``triple_applicable=False`` rather than claiming a triple it never emits.
    """
    degraded = list(degraded or [])
    triple = triple or {}
    service_stop = service_stop or {}
    applicable = bool(triple.get("applicable", False))
    ordered = bool(triple.get("ordered", False))
    present = bool(triple.get("present", False))
    missing = list(triple.get("missing") or [])

    if not provider or int(wav_bytes or 0) <= 0:
        verdict = "DEGRADED"
        reason = "the live service returned no TTS audio (provider unavailable)"
    elif not player_started:
        verdict = "DEGRADED"
        reason = "the host player never started (no paplay/aplay or audio server)"
    elif playback_live_before_stop is False:
        verdict = "DEGRADED"
        reason = "the player exited before the stop (no audio device?)"
    elif player_exited is False:
        verdict = "FAIL"
        reason = "the player was still alive after the stop"
    elif helper_pid is not None and helper_alive_after is False:
        verdict = "FAIL"
        reason = "the live helper did not survive the stop signal"
    elif applicable and not present:
        verdict = "FAIL"
        reason = ("the live helper owned playback but the canonical stop "
                  "boundaries are missing: " + ", ".join(missing))
    elif applicable and not ordered:
        verdict = "FAIL"
        reason = "canonical stop boundaries are present but out of order"
    else:
        verdict = "PASS"
        if applicable:
            reason = ("live helper stopped its playback and journaled the "
                      "canonical stop triple")
        else:
            reason = ("real service TTS + real host player stopped; no helper "
                      "playback was live, so no helper triple applies")
    return {
        "verdict": verdict,
        "reason": reason,
        "provider": provider or "",
        "wav_bytes": int(wav_bytes or 0),
        "player_started": bool(player_started),
        "player_exited": player_exited,
        "playback_live_before_stop": playback_live_before_stop,
        "helper_pid": helper_pid,
        "helper_alive_after": helper_alive_after,
        "triple_applicable": applicable,
        "triple_present": present,
        "triple_ordered": ordered,
        "missing": missing,
        "stop_latency_s": triple.get("latency_s"),
        "service_stop": service_stop,
        "degraded": degraded,
    }


def _service_headers(token: str) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Api-Token"] = token
        headers["X-Qubit-Session-Token"] = token
    return headers


def _post_json(base: str, path: str, token: str, payload: Dict[str, Any],
               timeout: float = 60.0) -> Dict[str, Any]:
    """POST one JSON request to the live service; never raises."""
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}", data=json.dumps(payload).encode(),
        method="POST", headers=_service_headers(token))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
        out = json.loads(body) if body.strip() else {}
        return out if isinstance(out, dict) else {"value": out}
    except urllib.error.HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:  # noqa: BLE001
            parsed = {}
        return {"ok": False, "code": str(parsed.get("code") or f"HTTP_{exc.code}"),
                "detail": str(parsed.get("detail", "") or "")[:200]}
    except Exception as exc:  # noqa: BLE001 - unreachable service is DEGRADED
        return {"ok": False, "code": "SERVICE_UNREACHABLE",
                "detail": str(exc)[:200]}


def service_tts(base: str, token: str, text: str,
                timeout: float = 60.0) -> Dict[str, Any]:
    """Ask the RUNNING service for real TTS; return ``{ok, wav, provider}``."""
    import base64
    out = _post_json(base, SERVICE_TTS_PATH, token, {"text": str(text)},
                     timeout=timeout)
    url = str(out.get("data_url", "") or "")
    wav = b""
    if url.startswith("data:audio/") and "," in url:
        try:
            wav = base64.b64decode(url.split(",", 1)[1])
        except Exception:  # noqa: BLE001
            wav = b""
    return {
        "ok": bool(wav),
        "wav": wav,
        "provider": str(out.get("provider", "") or ""),
        "code": str(out.get("code", "") or ""),
        "detail": str(out.get("detail", "") or ""),
    }


def service_interrupt(base: str, token: str, mode: str = "stop",
                      timeout: float = 10.0) -> Dict[str, Any]:
    """Deliver the live service's dashboard STOP (never raises)."""
    return _post_json(base, SERVICE_INTERRUPT_PATH, token, {"mode": str(mode)},
                      timeout=timeout)


def _spawn_player(wav: bytes, capture_dir: Path) -> Any:
    """Start the host's real player on ``wav``; return the Popen (or None)."""
    import subprocess
    player = "paplay" if shutil.which("paplay") else (
        "aplay" if shutil.which("aplay") else "")
    if not player:
        return None
    path = capture_dir / "speak.wav"
    try:
        path.write_bytes(wav)
    except Exception:  # noqa: BLE001
        return None
    try:
        return subprocess.Popen(
            [player, str(path)], stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
    except Exception:  # noqa: BLE001
        return None


def _write_speak_evidence(capture_dir: Path, result: Dict[str, Any],
                          events: List[Dict[str, Any]], t0: float) -> None:
    header = [
        f"live speak-test STOP harness — {result.get('started')}",
        f"provider: {result.get('provider') or '(none)'} "
        f"wav_bytes={result.get('wav_bytes')}",
        f"player: {result.get('player') or '(none)'} "
        f"pid={result.get('player_pid') or '-'}",
        f"helper pid: {result.get('helper_pid') or '-'} "
        f"alive_after={result.get('helper_alive_after')}",
        f"stop: interrupt_request="
        f"{(result.get('service_stop') or {}).get('stop_seen')} "
        f"had_playback={(result.get('service_stop') or {}).get('had_playback')} "
        f"latency={result.get('stop_latency_s')}s",
        f"triple: applicable={result.get('triple_applicable')} "
        f"present={result.get('triple_present')} "
        f"ordered={result.get('triple_ordered')}",
        f"verdict: {result.get('verdict')} — {result.get('reason')}",
    ]
    if result.get("degraded"):
        header.append("degraded: " + "; ".join(result["degraded"]))
    header.append("--- talk timeline (voice_edit.talk.*) ---")
    try:
        (capture_dir / "timeline.txt").write_text(
            build_timeline(events, t0, header=header), encoding="utf-8")
        (capture_dir / "evidence.json").write_text(
            json.dumps({**result, "events": events[-200:]}, indent=2),
            encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - evidence is best-effort
        result.setdefault("degraded", []).append(f"evidence write failed: {exc}")


def run_speak_test(*, text: str = "Qubit live stop test.",
                   out_dir: Optional[Path] = None, duration: float = 6.0,
                   timeout: float = 6.0,
                   journal: Optional[Path] = None,
                   base_url: Optional[str] = None,
                   token: Optional[str] = None,
                   serve_fn: Optional[Callable[[], Tuple[str, str]]] = None,
                   speak_fn: Optional[Callable[[str], Dict[str, Any]]] = None,
                   interrupt_fn: Optional[Callable[..., Any]] = None,
                   player_fn: Optional[Callable[[bytes, Path], Any]] = None,
                   resolve_fn: Optional[Callable[..., Dict[str, Any]]] = None,
                   signal_fn: Callable[[int, int], Any] = os.kill,
                   alive_fn: Optional[Callable[[int], bool]] = None,
                   now_fn: Callable[[], float] = time.time,
                   sleep_fn: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Gated real-provider/real-player STOP demonstration. Never raises.

    Asks the RUNNING service for TTS, plays it through the host's real player,
    then delivers the orb STOP (service interrupt + SIGUSR2 to the validated
    live helper) and terminates the player. A private helper instance is never
    constructed; the only audio path is the live service's provider plus the
    host player. The live helper's canonical triple is asserted only when a
    real helper playback is already open in the journal.
    """
    t0 = float(now_fn())
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t0))
    base = Path(out_dir) if out_dir else (REPO_ROOT / DEFAULT_OUT)
    capture_dir = base / f"{stamp}-speak"
    try:
        capture_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    alive_fn = alive_fn or _pid_alive
    result: Dict[str, Any] = {
        "mode": "speak-test",
        "started": _iso(t0),
        "capture_dir": str(capture_dir),
        "verdict": "DEGRADED",
        "reason": "",
        "degraded": [],
        "text_chars": len(str(text or "")),
        "provider": "",
        "wav_bytes": 0,
        "player": None,
        "player_pid": None,
        "player_started": False,
        "player_exited": None,
        "playback_live_before_stop": None,
        "stop_at": None,
        "stop_to_exit_s": None,
        "helper_pid": None,
        "helper_alive_after": None,
        "signal_sent": False,
        "service_stop": None,
        "triple_applicable": False,
        "triple_present": None,
        "triple_ordered": None,
        "missing": [],
        "stop_latency_s": None,
        "events": [],
        "events_after_stop": [],
    }
    if not speak_test_allowed():
        result["verdict"] = "BLOCKED"
        result["reason"] = (f"speak-test is not enabled; set {SPEAK_TEST_ENV}=1 "
                            "to permit a real TTS/player stop")
        result["degraded"].append(result["reason"])
        _write_speak_evidence(capture_dir, result, [], t0)
        return result

    # -- live helper (required: it is the thing whose STOP we exercise) -----
    if resolve_fn is None:
        def resolve_fn(**_kw: Any) -> Dict[str, Any]:
            return resolve_live_helper(**_kw)
    helper = resolve_fn()
    result["helper_pid"] = helper.get("pid")
    if not helper.get("ok"):
        reason = str(helper.get("reason") or "no live helper")
        result["reason"] = reason
        result["degraded"].append(reason)
        _write_speak_evidence(capture_dir, result, [], t0)
        return result
    pid = int(helper["pid"])
    mask = read_proc_signal_mask(pid)
    handler_ok = handler_installed(mask, signal.SIGUSR2)
    if not handler_ok:
        reason = ("refusing speak-test: the SIGUSR2 handler is not installed "
                  "(the stop would terminate the live helper)")
        result["reason"] = reason
        result["degraded"].append(reason)
        _write_speak_evidence(capture_dir, result, [], t0)
        return result

    if journal is None:
        try:
            from voicebridge.config import canonical_state_dir
            journal = canonical_state_dir() / "events.jsonl"
        except Exception:  # noqa: BLE001
            journal = (Path.home() / ".local" / "state" / "qubit-voicebridge"
                       / "events.jsonl")
    result["journal"] = str(journal)

    # -- real service TTS ---------------------------------------------------
    if serve_fn is not None:
        base_url, token = serve_fn()
    elif base_url is None:
        try:
            from voicebridge.config import Config
            from voicebridge.desktop_helper import (
                resolve_service_base, resolve_token)
            cfg = Config.load()
            base_url = resolve_service_base(cfg)
            token = resolve_token(cfg)
        except Exception as exc:  # noqa: BLE001
            result["degraded"].append(f"service config unavailable: {exc}")
            base_url, token = "", ""
    result["service_base"] = base_url or ""
    if not base_url:
        result["reason"] = "the live service address could not be resolved"
        result["degraded"].append(result["reason"])
        _write_speak_evidence(capture_dir, result, [], t0)
        return result
    speak_fn = speak_fn or (lambda t: service_tts(base_url, token or "", t))
    try:
        speech = speak_fn(str(text))
    except Exception as exc:  # noqa: BLE001
        speech = {"ok": False, "code": "SPEAK_FAILED", "detail": str(exc)[:200]}
    result["provider"] = str(speech.get("provider", "") or "")
    wav = bytes(speech.get("wav") or b"")
    result["wav_bytes"] = len(wav)
    if not speech.get("ok") or not wav:
        reason = ("the live service could not synthesize audio: "
                  f"{speech.get('code') or 'no audio'} "
                  f"{str(speech.get('detail') or '')[:120]}".strip())
        result["reason"] = reason
        result["degraded"].append(reason)
        _write_speak_evidence(capture_dir, result, [], t0)
        return result
    wav = pad_wav_min_duration(wav, max(0.5, float(duration)))

    # -- pre-stop journal state (does the HELPER already own playback?) -----
    try:
        pre_events, offset = read_tail_talk_rows(Path(journal))
        pre = analyze_playback(pre_events, now=now_fn())
    except Exception:  # noqa: BLE001
        pre, offset = {"speaking": False, "turn_id": None}, 0
    helper_owned = bool(pre.get("speaking"))
    result["triple_applicable"] = helper_owned
    if offset <= 0:
        try:
            offset = Path(journal).stat().st_size
        except Exception:  # noqa: BLE001
            offset = 0

    # -- real host player ---------------------------------------------------
    player_fn = player_fn or _spawn_player
    try:
        proc = player_fn(wav, capture_dir)
    except Exception:  # noqa: BLE001
        proc = None
    if proc is None:
        result["reason"] = "no host player (paplay/aplay) is available"
        result["degraded"].append(result["reason"])
        _write_speak_evidence(capture_dir, result, [], t0)
        return result
    result["player"] = "paplay" if shutil.which("paplay") else (
        "aplay" if shutil.which("aplay") else "")
    try:
        result["player_pid"] = int(getattr(proc, "pid", 0) or 0) or None
    except Exception:  # noqa: BLE001
        result["player_pid"] = None
    deadline = now_fn() + max(0.1, float(timeout))
    while now_fn() < deadline:
        try:
            if proc.poll() is None:
                result["player_started"] = True
                break
        except Exception:  # noqa: BLE001
            break
        sleep_fn(0.02)
    if not result["player_started"]:
        result["reason"] = "the player exited before playback could start"
        result["degraded"].append(result["reason"])
        _write_speak_evidence(capture_dir, result, [], t0)
        return result
    sleep_fn(0.2)  # settle: prove it stays alive on a real audio device
    try:
        alive_before = proc.poll() is None
    except Exception:  # noqa: BLE001
        alive_before = False
    result["playback_live_before_stop"] = bool(alive_before)
    if not alive_before:
        result["player_exited"] = True
        result["reason"] = "the player exited before the stop (no audio device?)"
        result["degraded"].append(result["reason"])
        _write_speak_evidence(capture_dir, result, [], t0)
        return result

    # -- deliver the STOP: service interrupt + live helper SIGUSR2 + player --
    t_stop = float(now_fn())
    result["stop_at"] = t_stop
    interrupt_fn = interrupt_fn or (
        lambda mode="stop": service_interrupt(base_url, token or "", mode))
    try:
        interrupt_fn("stop")
    except Exception as exc:  # noqa: BLE001
        result["degraded"].append(f"service interrupt failed: {exc}")
    # Re-validate right before the signal: never signal a changed pid or one
    # without the handler.
    fresh = resolve_fn()
    mask2 = (read_proc_signal_mask(int(fresh["pid"]))
             if fresh.get("ok") else None)
    if (fresh.get("ok") and int(fresh.get("pid") or 0) == pid
            and handler_installed(mask2, signal.SIGUSR2)):
        try:
            signal_fn(pid, signal.SIGUSR2)
            result["signal_sent"] = True
        except Exception as exc:  # noqa: BLE001
            result["degraded"].append(f"could not signal the live helper: {exc}")
    else:
        result["degraded"].append(
            "live helper pid changed/disappeared or lost its SIGUSR2 handler "
            "before the stop; not signalled")
    # The helper does not own this player (it was idle), so end it exactly as
    # the helper's own stop would (SIGTERM, then SIGKILL on a linger).
    try:
        proc.terminate()
    except Exception:  # noqa: BLE001
        pass
    exit_at: Optional[float] = None
    try:
        proc.wait(timeout=max(0.2, float(timeout)))
        exit_at = now_fn()
    except Exception:  # noqa: BLE001 - still alive: bound it
        try:
            proc.kill()
            exit_at = now_fn()
        except Exception:  # noqa: BLE001
            exit_at = None
    result["helper_alive_after"] = bool(alive_fn(pid))
    result["player_exited"] = exit_at is not None
    if exit_at is not None:
        result["stop_to_exit_s"] = round(float(exit_at) - t_stop, 4)

    # -- journal evidence (triple only when the helper owned playback) ------
    new_events: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    service_offset = offset
    deadline = now_fn() + max(0.5, float(timeout))
    while now_fn() < deadline:
        try:
            batch, offset = read_talk_rows(Path(journal), offset)
            new_events.extend(batch)
            stop_batch, service_offset = read_service_rows(
                Path(journal), service_offset)
            rows.extend(stop_batch)
        except Exception:  # noqa: BLE001
            pass
        types = [e.get("type") for e in new_events]
        service_stop = analyze_service_stop(rows, t_stop=t_stop)
        if helper_owned and all(name in types for name in STOP_TRIPLE):
            break
        if not helper_owned and service_stop["stop_seen"]:
            break
        sleep_fn(0.05)
    result["events_after_stop"] = new_events[-100:]
    result["events"] = new_events[-200:]
    if helper_owned:
        relevant = new_events
        if pre.get("turn_id") is not None:
            relevant = [e for e in new_events
                        if e.get("turn_id") == pre.get("turn_id")]
        triple = ordered_triple(relevant, t_stop=t_stop)
    else:
        triple = {"present": False, "ordered": False, "missing": [],
                  "latency_s": None}
    service_stop = analyze_service_stop(rows, t_stop=t_stop)
    result["service_stop"] = service_stop
    result["triple_present"] = triple["present"]
    result["triple_ordered"] = triple["ordered"]
    result["missing"] = triple["missing"]
    result["stop_latency_s"] = triple["latency_s"]

    evidence = summarize_speak_test(
        provider=result["provider"], wav_bytes=result["wav_bytes"],
        player_started=result["player_started"],
        player_exited=result["player_exited"],
        playback_live_before_stop=result["playback_live_before_stop"],
        helper_pid=pid, helper_alive_after=result["helper_alive_after"],
        triple={"applicable": helper_owned, **triple},
        service_stop=service_stop, degraded=result["degraded"])
    result.update(evidence)
    _write_speak_evidence(capture_dir, result, new_events, t0)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Live-capable orb STOP harness (read-only by default).")
    ap.add_argument("--out", default=None,
                    help=f"output dir (default: {DEFAULT_OUT})")
    ap.add_argument("--duration", type=float, default=6.0,
                    help="audio length in seconds (default: 6)")
    ap.add_argument("--use-service-tts", action="store_true",
                    help="synthesize --text via the live service (HTTP, read-only)")
    ap.add_argument("--text", default="Qubit stop harness.",
                    help="text to synthesize with --use-service-tts")
    ap.add_argument("--stop", choices=("signal", "direct"), default="signal",
                    help="deliver SIGUSR2 (default) or call stop_speaking()")
    ap.add_argument("--journal", default=None,
                    help="append to this journal instead of the isolated one")
    ap.add_argument("--live-helper", action="store_true",
                    help="verify the RUNNING helper/orb (read-only)")
    ap.add_argument("--live-signal", action="store_true",
                    help="permit one SIGUSR2 to the RUNNING helper "
                         "(implies --live-helper)")
    ap.add_argument("--live-journal", default=None,
                    help="live journal path (default: <state>/events.jsonl)")
    ap.add_argument("--live-timeout", type=float, default=6.0,
                    help="seconds to wait for the stop triple (default: 6)")
    ap.add_argument("--live-stale-after", type=float,
                    default=LIVE_STALE_AFTER_S,
                    help="treat an open playback.started older than this as "
                         f"stale (default: {LIVE_STALE_AFTER_S:g}s)")
    ap.add_argument("--live-tail-bytes", type=int, default=LIVE_TAIL_BYTES,
                    help=f"journal tail to scan (default: {LIVE_TAIL_BYTES})")
    ap.add_argument("--speak-test", metavar="TEXT", default=None,
                    help="GATED: synthesize TEXT with the live service, play it "
                         "through the real host player, deliver the orb STOP and "
                         f"verify it (requires {SPEAK_TEST_ENV}=1)")
    ap.add_argument("--speak-duration", type=float, default=6.0,
                    help="minimum audio length in seconds so the stop lands "
                         "mid-playback (default: 6)")
    ap.add_argument("--speak-timeout", type=float, default=6.0,
                    help="seconds to wait for playback start/stop (default: 6)")
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args(argv)

    if args.speak_test is not None:
        speak = run_speak_test(
            text=args.speak_test,
            out_dir=Path(args.out) if args.out else None,
            duration=args.speak_duration,
            timeout=args.speak_timeout,
            journal=Path(args.live_journal) if args.live_journal else None,
        )
        if args.json:
            print(json.dumps(speak, indent=2))
        else:
            print(f"verdict: {speak.get('verdict')} — {speak.get('reason')}")
            print(f"provider: {speak.get('provider') or '-'} "
                  f"wav_bytes={speak.get('wav_bytes')} "
                  f"player={speak.get('player') or '-'} "
                  f"pid={speak.get('player_pid') or '-'}")
            print(f"helper: pid={speak.get('helper_pid') or '-'} "
                  f"alive_after={speak.get('helper_alive_after')} "
                  f"signal_sent={speak.get('signal_sent')}")
            print(f"playback: started={speak.get('player_started')} "
                  f"live_before_stop={speak.get('playback_live_before_stop')} "
                  f"exited={speak.get('player_exited')} "
                  f"stop_to_exit={speak.get('stop_to_exit_s')}s")
            print(f"triple: applicable={speak.get('triple_applicable')} "
                  f"present={speak.get('triple_present')} "
                  f"ordered={speak.get('triple_ordered')}")
            print(f"evidence: {speak.get('capture_dir')}/evidence.json")
            if speak.get("degraded"):
                for item in speak["degraded"]:
                    print(f"degraded: {item}")
        return {"PASS": 0, "FAIL": 1, "BLOCKED": 2}.get(
            str(speak.get("verdict")), 3)

    if args.live_helper or args.live_signal:
        live = run_live(
            out_dir=Path(args.out) if args.out else None,
            allow_signal=bool(args.live_signal),
            journal=Path(args.live_journal) if args.live_journal else None,
            stale_after=args.live_stale_after,
            timeout=args.live_timeout,
            tail_bytes=args.live_tail_bytes,
        )
        if args.json:
            print(json.dumps(live, indent=2))
        else:
            print(f"verdict: {live.get('verdict')} — {live.get('reason')}")
            print(f"helper: pid={live.get('helper_pid') or '-'} "
                  f"state={live.get('helper_state') or '-'} "
                  f"sigusr1={live.get('sigusr1_handler')} "
                  f"sigusr2={live.get('sigusr2_handler')}")
            print(f"signal sent: {live.get('signal_sent')} "
                  f"survived={live.get('survives_sigusr2')} "
                  f"alive_after={live.get('helper_alive_after')}")
            play = live.get("playback") or {}
            print(f"playback: speaking={play.get('speaking')} "
                  f"stale={play.get('stale')} "
                  f"started_age={play.get('started_age_s')}s "
                  f"last={((play.get('last_boundary') or {}).get('type'))}")
            print(f"triple: present={live.get('triple_present')} "
                  f"ordered={live.get('triple_ordered')} "
                  f"latency={live.get('stop_latency_s')}s")
            print(f"evidence: {live.get('capture_dir')}/evidence.json")
            if live.get("degraded"):
                for item in live["degraded"]:
                    print(f"degraded: {item}")
        return {"PASS": 0, "FAIL": 1}.get(str(live.get("verdict")), 3)

    result = run(
        out_dir=Path(args.out) if args.out else None,
        duration=args.duration,
        use_service_tts=args.use_service_tts,
        text=args.text,
        stop_action=args.stop,
        journal=Path(args.journal) if args.journal else None,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        ev = result.get("summary", {})
        print(f"verdict: {ev.get('verdict')} — {ev.get('reason')}")
        print(f"evidence: {result.get('capture_dir')}/evidence.json")
        if result.get("degraded"):
            for item in result["degraded"]:
                print(f"degraded: {item}")
        print(f"player: {result.get('player') or '(none)'} "
              f"stop->exit={result.get('stop_to_exit_s')}s "
              f"triple={ev.get('triple_present')}/{ev.get('triple_ordered')}")
    return {"PASS": 0, "FAIL": 1}.get(str(result.get("verdict")), 3)


if __name__ == "__main__":
    raise SystemExit(main())
