#!/usr/bin/env python3
"""LIVE-capable STOP harness for the desktop helper's playback path.

What it proves (best-effort, on a machine with a working player): a wav is
played through the SAME ``DesktopHelper._speak`` path the orb uses, a STOP is
delivered (SIGUSR2, exactly like the orb's stop press), and the evidence is
recorded:

  * the helper's own player subprocess ends promptly after the stop;
  * the journal carries the canonical triple in order —
    ``voice_edit.talk.interrupt`` -> ``voice_edit.talk.playback.stopped`` ->
    ``voice_edit.talk.response.done``;
  * ``evidence.json`` + ``timeline.txt`` are written under ``--out``.

Safety by default:
  * NO synthetic input (no xdotool, no clicks, no keys);
  * NO microphone (barge-in is forced off; the real ``_speak`` path is used);
  * NO running-service mutation (``request_id`` is empty, so no cancel POST);
  * it writes only its output dir (and an isolated journal) unless an explicit
    ``--journal`` is supplied;
  * with no ``paplay``/``aplay`` or no working audio server it degrades with a
    clear ``DEGRADED`` verdict instead of failing hard.

Audio source: a generated 6 s low-volume tone by default (deterministic, no
service needed). ``--use-service-tts`` asks the live voice-edit service to
synthesize ``--text`` instead (read-only HTTP), padding it to the requested
duration so the stop always lands mid-playback.

Run::

    scripts/verify_orb_stop_live.py
    scripts/verify_orb_stop_live.py --duration 8 --out /tmp/orbstop --json
    scripts/verify_orb_stop_live.py --use-service-tts --text "Testing stop."

Exit codes: 0 PASS, 1 FAIL, 3 DEGRADED (environment cannot demonstrate it).
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
    ap.add_argument("--json", action="store_true", help="print JSON only")
    args = ap.parse_args(argv)

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
