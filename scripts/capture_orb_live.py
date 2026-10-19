#!/usr/bin/env python3
"""LIVE, READ-ONLY capture harness for the running X11 Qubit voice orb.

Two independent evidence channels are captured over the *same* short window:

1. **Frame time-lapse** — locate the live orb window by title and grab ``N``
   frames into ``output/orb_capture/live/<ts>/frame-NNN.png``. Window lookup and
   grabbing reuse the approach in ``scripts/capture_orb_x11.sh`` (xdotool search
   + ImageMagick ``import``, falling back to ``xwd``+``convert``) and degrade
   gracefully (clear message, no traceback) when there is no ``DISPLAY``, no
   xdotool, or no matching window.

2. **Talk-event timeline** — tail the Qubit journal for ``voice_edit.talk.*``
   rows (the authoritative mic-open / transcript / response / playback /
   interrupt sequence emitted by ``TalkEventSequencer``) plus ``voice_edit.cancel``
   and ``stt.cancelled`` stop markers, and write ``timeline.txt`` + ``events.json``
   next to the frames. A stop press is therefore visible as
   ``interrupt`` / ``playback.stopped`` / ``response.done`` in order, time-stamped
   relative to capture start.

The journal path is resolved through the SAME canonical resolver the orb uses
(``voicebridge.orb.stream.journal_path``; ``<state dir>/events.jsonl``), so the
harness reads the exact stream the live orb consumes.

SAFETY — this harness is READ-ONLY by default:
  * it never clicks, keys, or activates any window;
  * ``xdotool search``, ``xwininfo`` and ``import -window`` are read-only X calls
    (no synthetic input), so it can never open the mic or start TTS.
A synthetic press helper exists ONLY for owners who explicitly need it, and is
dead by default: it requires BOTH ``--simulate-press`` AND the environment gate
``QUBIT_ORB_LIVE_ALLOW_SYNTHETIC_INPUT=1``. Never enable it in automation.

Usage::

    scripts/capture_orb_live.py                       # 8 frames @ 0.25 s + timeline
    scripts/capture_orb_live.py --frames 20 --interval 0.2
    scripts/capture_orb_live.py --out /tmp/orbcap --json
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_TITLE = "Qubit Voice Orb"
TALK_PREFIX = "voice_edit.talk."
CANCEL_EVENTS = frozenset({"voice_edit.cancel", "stt.cancelled"})
DEFAULT_OUT = "output/orb_capture/live"
DEFAULT_PREROLL_BYTES = 131072

#: Env gate for the (off-by-default) synthetic press helper.
SYNTHETIC_INPUT_ENV = "QUBIT_ORB_LIVE_ALLOW_SYNTHETIC_INPUT"


# --------------------------------------------------------------------------- #
# journal path + parsing (pure, no I/O side effects)
# --------------------------------------------------------------------------- #
def resolve_journal_path(explicit: Optional[str] = None) -> Optional[Path]:
    """The journal the live orb reads, or ``None`` when unresolvable.

    Order: explicit ``--journal``, ``$QUBIT_LIVE_JOURNAL``, then the canonical
    resolver the orb itself uses (``voicebridge.orb.stream.journal_path``).
    """
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("QUBIT_LIVE_JOURNAL")
    if env:
        return Path(env).expanduser()
    try:
        from voicebridge.orb.stream import journal_path as _jp
        jp = _jp()
        if jp is not None:
            return Path(jp)
    except Exception:
        pass
    try:
        from voicebridge.config import canonical_state_dir
        return Path(canonical_state_dir()) / "events.jsonl"
    except Exception:
        return None


def classify_event(row: Any) -> Optional[Dict[str, Any]]:
    """Normalize one journal row to a talk/cancel timeline entry, or ``None``.

    Kept pure and fail-soft. A row is captured when its ``event`` is a
    ``voice_edit.talk.*`` boundary (kind ``talk``; ``type`` is the boundary
    name) or a stop marker (kind ``cancel``). ``ts`` is the epoch seconds the
    row was journaled; ``turn_id``/``seq``/``pid`` are carried through when
    present so a stop press can be tied to its turn.
    """
    try:
        if not isinstance(row, dict):
            return None
        name = row.get("event")
        if not isinstance(name, str):
            return None
        if name.startswith(TALK_PREFIX):
            kind, etype = "talk", name[len(TALK_PREFIX):]
        elif name in CANCEL_EVENTS or name.endswith("voice_edit.cancel"):
            kind, etype = "cancel", name.rsplit(".", 1)[-1]
        else:
            return None
        ts = row.get("ts")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            ts = None
        return {
            "ts": ts,
            "event": name,
            "kind": kind,
            "type": etype,
            "turn_id": row.get("turn_id"),
            "seq": row.get("seq"),
            "pid": row.get("pid"),
        }
    except Exception:
        return None


def _read_lines_from(path: Path, offset: int) -> Tuple[List[str], int]:
    """Read whole lines from ``offset`` to EOF; return ``(lines, new_offset)``.

    A trailing partial line (no newline yet) is left unconsumed so a concurrent
    writer cannot corrupt the stream: the caller's next read resumes at
    ``new_offset`` and sees the completed line.
    """
    lines: List[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        start = offset
        while True:
            pos = fh.tell()
            line = fh.readline()
            if not line:
                break
            if not line.endswith("\n"):
                fh.seek(pos)  # leave the partial line for the next read
                break
            lines.append(line)
        return lines, fh.tell()


def read_window(path: Path, offset: int) -> Tuple[List[Dict[str, Any]], int]:
    """Parse classified events from ``offset`` to EOF; return ``(events, end)``."""
    try:
        lines, end = _read_lines_from(path, offset)
    except Exception:
        return [], offset
    out: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        ev = classify_event(row)
        if ev is not None:
            out.append(ev)
    return out, end


def read_preroll(path: Path, start_offset: int, max_bytes: int) -> List[Dict[str, Any]]:
    """Best-effort context: classified rows in the ``max_bytes`` before start.

    Used to show the turn a stop press interrupted even if its earliest
    boundaries landed just before the capture began. ``ts`` values are marked
    ``pre=True`` by the caller; here they are returned as ordinary events.
    """
    if start_offset <= 0 or max_bytes <= 0:
        return []
    try:
        read_from = max(0, start_offset - int(max_bytes))
        with path.open("rb") as fh:
            fh.seek(read_from)
            blob = fh.read(start_offset - read_from)
    except Exception:
        return []
    text = blob.decode("utf-8", "replace")
    if read_from > 0 and "\n" in text:
        text = text.split("\n", 1)[1]  # drop a possibly-partial first line
    out: List[Dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        ev = classify_event(row)
        if ev is not None:
            out.append(ev)
    return out


# --------------------------------------------------------------------------- #
# timeline rendering (pure)
# --------------------------------------------------------------------------- #
def build_timeline(
    events: List[Dict[str, Any]],
    t0: float,
    *,
    header: Optional[List[str]] = None,
) -> str:
    """Render classified events as a stable, time-relative text timeline.

    Events are ordered by their journal ``ts`` (rows with no timestamp keep
    stream order at the end). Each line shows ``+seconds`` from ``t0`` (capture
    start), the kind, the boundary type, and the turn/seq/pid it belongs to,
    so ``interrupt`` -> ``playback.stopped`` -> ``response.done`` reads in order.
    """
    def key(ev: Dict[str, Any]) -> Tuple[int, float]:
        ts = ev.get("ts")
        return (1, 0.0) if ts is None else (0, float(ts))

    ordered = sorted(events, key=key)
    lines: List[str] = list(header or [])
    for ev in ordered:
        ts = ev.get("ts")
        rel = "  ?    " if ts is None else f"{ts - t0:+8.3f}"
        turn = ev.get("turn_id") or "-"
        seq = ev.get("seq")
        seq_s = f"seq={seq}" if seq is not None else "seq=-"
        pid = ev.get("pid")
        pid_s = f" pid={pid}" if pid is not None else ""
        pre = " [pre]" if ev.get("pre") else ""
        lines.append(
            f"{rel}  {ev['kind']:<6} {ev['type']:<24} turn={turn:<9} "
            f"{seq_s}{pid_s}{pre}")
    return "\n".join(lines) + "\n"


def count_types(events: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count events by ``kind.type`` for the summary."""
    counts: Dict[str, int] = {}
    for ev in events:
        counts[f"{ev['kind']}.{ev['type']}"] = counts.get(
            f"{ev['kind']}.{ev['type']}", 0) + 1
    return counts


# --------------------------------------------------------------------------- #
# X11 window discovery + frame grabbing (read-only)
# --------------------------------------------------------------------------- #
def _tool(name: str) -> Optional[str]:
    return shutil.which(name)


def find_window(title: str = DEFAULT_TITLE) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Locate the visible orb window; return ``(window_id, geometry)`` or None.

    Read-only: ``xdotool search`` + ``xwininfo``. Never sends input.
    """
    if not _tool("xdotool"):
        return None
    try:
        out = subprocess.run(
            ["xdotool", "search", "--onlyvisible", "--name", title],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    ids = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    if not ids:
        return None
    wid = ids[-1]
    geometry = window_geometry(wid) or {}
    return wid, geometry


def window_geometry(wid: str) -> Optional[Dict[str, Any]]:
    """Parse ``xwininfo`` for absolute position + size (read-only)."""
    if not _tool("xwininfo"):
        return None
    try:
        out = subprocess.run(
            ["xwininfo", "-id", wid],
            capture_output=True, text=True, timeout=5,
        )
    except Exception:
        return None
    geom: Dict[str, Any] = {}
    keymap = {
        "Absolute upper-left X": "x",
        "Absolute upper-left Y": "y",
        "Width": "width",
        "Height": "height",
        "Map State": "map_state",
    }
    for line in out.stdout.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        label = label.strip()
        if label in keymap:
            value = value.strip()
            geom[keymap[label]] = int(value) if value.isdigit() else value
    return geom or None


def grab_frame(wid: str, out_path: Path) -> bool:
    """Save one window frame to ``out_path`` (read-only X grab).

    Prefers ImageMagick ``import``; falls back to ``xwd``+``convert``. Returns
    ``False`` (no exception) when neither tool is available or the grab fails.
    """
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if _tool("import"):
            res = subprocess.run(
                ["import", "-window", str(wid), str(out_path)],
                capture_output=True, timeout=15,
            )
            return res.returncode == 0 and out_path.is_file()
        if _tool("xwd") and _tool("convert"):
            tmp = out_path.with_suffix(".xwd")
            res = subprocess.run(
                ["xwd", "-silent", "-id", str(wid), "-out", str(tmp)],
                capture_output=True, timeout=15,
            )
            if res.returncode != 0:
                return False
            res = subprocess.run(
                ["convert", str(tmp), str(out_path)],
                capture_output=True, timeout=15,
            )
            try:
                tmp.unlink()
            except Exception:
                pass
            return res.returncode == 0 and out_path.is_file()
    except Exception:
        return False
    return False


def _have_display() -> bool:
    return bool(os.environ.get("DISPLAY"))


# --------------------------------------------------------------------------- #
# off-by-default synthetic press helper (gated, never default)
# --------------------------------------------------------------------------- #
def simulate_press(wid: str, geometry: Dict[str, Any]) -> bool:
    """FAIL-CLOSED synthetic click on the orb center.

    This is the ONLY code path that sends input. It refuses unless BOTH
    ``QUBIT_ORB_LIVE_ALLOW_SYNTHETIC_INPUT=1`` is set AND the caller passed
    ``--simulate-press``. It can open the mic / start TTS; never use it in
    automation. Returns ``True`` only if the click was issued.
    """
    if os.environ.get(SYNTHETIC_INPUT_ENV) != "1":
        print(f"synthetic press refused: set {SYNTHETIC_INPUT_ENV}=1 to allow "
              "(it can trigger the real mic/TTS)")
        return False
    if not _tool("xdotool"):
        print("synthetic press refused: xdotool not installed")
        return False
    try:
        x = int(geometry.get("x", 0)) + int(geometry.get("width", 0)) // 2
        y = int(geometry.get("y", 0)) + int(geometry.get("height", 0)) // 2
    except Exception:
        return False
    try:
        subprocess.run(["xdotool", "mousemove", "--sync", str(x), str(y)],
                       capture_output=True, timeout=5)
        res = subprocess.run(["xdotool", "click", "1"],
                             capture_output=True, timeout=5)
        print(f"SYNTHETIC PRESS sent at ({x},{y}) — this may have triggered "
              "the real mic/TTS")
        return res.returncode == 0
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #
def _iso(ts: float) -> str:
    try:
        import datetime
        return datetime.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")
    except Exception:
        return str(ts)


def run(
    *,
    journal: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    title: str = DEFAULT_TITLE,
    frames: int = 8,
    interval: float = 0.25,
    preroll_bytes: int = DEFAULT_PREROLL_BYTES,
    find_window_fn: Callable[[str], Optional[Tuple[str, Dict[str, Any]]]] = find_window,
    grab_frame_fn: Callable[[str, Path], bool] = grab_frame,
    simulate: bool = False,
    sleep_fn: Callable[[float], None] = time.sleep,
    now_fn: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """Capture the live orb frame-by-frame + journal timeline. Never raises.

    Returns a summary dict (also print-able) describing where frames and the
    timeline landed, the window geometry, and per-type event counts.
    """
    t0 = now_fn()
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t0))
    base = Path(out_dir) if out_dir else (REPO_ROOT / DEFAULT_OUT)
    capture_dir = base / stamp
    summary: Dict[str, Any] = {
        "started": _iso(t0),
        "capture_dir": str(capture_dir),
        "frames_requested": int(frames),
        "frames_captured": 0,
        "frame_paths": [],
        "window_id": None,
        "geometry": {},
        "journal": str(journal) if journal else None,
        "journal_available": False,
        "event_counts": {},
        "event_total": 0,
        "degraded": [],
        "live_run": False,
    }

    # -- journal setup: mark the end so only THIS window's events are read ---
    start_off = 0
    if journal is not None:
        try:
            if journal.is_file():
                start_off = journal.stat().st_size
                summary["journal_available"] = True
            else:
                summary["degraded"].append(f"journal not found: {journal}")
        except Exception as exc:
            summary["degraded"].append(f"journal unreadable: {exc}")
    else:
        summary["degraded"].append("no journal path resolved")

    timeline_events: List[Dict[str, Any]] = []
    if summary["journal_available"]:
        for ev in read_preroll(journal, start_off, preroll_bytes):
            ev = dict(ev)
            ev["pre"] = True
            timeline_events.append(ev)

    # -- window setup --------------------------------------------------------
    found = None
    try:
        found = find_window_fn(title)
    except Exception:
        found = None
    if not _have_display():
        summary["degraded"].append("no DISPLAY: frame capture skipped")
    elif not _tool("xdotool"):
        summary["degraded"].append("xdotool not installed: frame capture skipped")
    elif found is None:
        summary["degraded"].append(
            f"no visible window titled '{title}': frame capture skipped")
    else:
        wid, geometry = found
        summary["window_id"] = wid
        summary["geometry"] = geometry or {}
        summary["live_run"] = True

    # -- capture loop (frames interleaved with journal reads) ----------------
    read_off = start_off
    n = max(0, int(frames))
    capture_dir.mkdir(parents=True, exist_ok=True)
    if simulate and summary["window_id"]:
        simulate_press(summary["window_id"], summary["geometry"])
    for i in range(n):
        if summary["live_run"]:
            path = capture_dir / f"frame-{i:03d}.png"
            if grab_frame_fn(summary["window_id"], path):
                summary["frames_captured"] += 1
                summary["frame_paths"].append(str(path))
        if summary["journal_available"]:
            events, read_off = read_window(journal, read_off)
            timeline_events.extend(events)
        if i < n - 1:
            sleep_fn(max(0.0, float(interval)))

    # Final journal drain (catch events appended after the last frame).
    if summary["journal_available"]:
        events, read_off = read_window(journal, read_off)
        timeline_events.extend(events)

    # -- write timeline + events.json ---------------------------------------
    talk_events = [e for e in timeline_events if not e.get("pre")]
    geometry = summary["geometry"]
    window_line = (
        f"window: {summary['window_id']} "
        f"{geometry.get('width', '?')}x{geometry.get('height', '?')} "
        f"at ({geometry.get('x', '?')},{geometry.get('y', '?')})"
        if summary["window_id"] else "window: (not found)")
    header = [
        f"live orb capture — {summary['started']}",
        f"journal: {summary['journal'] or '(none)'}",
        window_line,
        f"frames: {summary['frames_captured']}/{summary['frames_requested']} "
        f"@ {interval}s  dir: {capture_dir}",
    ]
    if summary["degraded"]:
        header.append("degraded: " + "; ".join(summary["degraded"]))
    header.append("--- journal timeline "
                  "(voice_edit.talk.* | voice_edit.cancel | stt.cancelled) ---")
    timeline_text = build_timeline(timeline_events, t0, header=header)
    try:
        (capture_dir / "timeline.txt").write_text(timeline_text, encoding="utf-8")
        (capture_dir / "events.json").write_text(
            json.dumps(timeline_events, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        summary["degraded"].append(f"could not write timeline: {exc}")

    summary["event_counts"] = count_types(talk_events)
    summary["event_total"] = len(talk_events)
    summary["timeline_path"] = str(capture_dir / "timeline.txt")
    summary["elapsed_s"] = round(now_fn() - t0, 3)
    return summary


def _print_summary(summary: Dict[str, Any]) -> None:
    print(f"output:  {summary['capture_dir']}")
    print(f"frames:  {summary['frames_captured']}/{summary['frames_requested']} "
          f"({len(summary['frame_paths'])} files)")
    if summary["window_id"]:
        g = summary["geometry"]
        print(f"window:  {summary['window_id']} "
              f"{g.get('width', '?')}x{g.get('height', '?')} "
              f"at ({g.get('x', '?')},{g.get('y', '?')})")
    else:
        print("window:  (not found)")
    print(f"journal: {summary['journal']} "
          f"available={summary['journal_available']}")
    print(f"events:  {summary['event_total']} talk/cancel rows")
    for key in sorted(summary["event_counts"]):
        print(f"  {key:<34} {summary['event_counts'][key]}")
    for note in summary["degraded"]:
        print(f"note:    {note}")
    print(f"live:    {summary['live_run']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only live orb frame + talk-timeline capture harness.")
    parser.add_argument("--frames", type=int, default=8,
                        help="number of frames to grab (default 8)")
    parser.add_argument("--interval", type=float, default=0.25,
                        help="seconds between frames (default 0.25)")
    parser.add_argument("--out", default=None,
                        help=f"output dir (default {DEFAULT_OUT})")
    parser.add_argument("--journal", default=None,
                        help="journal path (default: canonical orb journal)")
    parser.add_argument("--title", default=DEFAULT_TITLE,
                        help=f"orb window title (default '{DEFAULT_TITLE}')")
    parser.add_argument("--preroll-bytes", type=int, default=DEFAULT_PREROLL_BYTES,
                        help="journal bytes of pre-window context to include")
    parser.add_argument("--json", action="store_true",
                        help="print the summary as JSON")
    parser.add_argument("--simulate-press", action="store_true",
                        help="OPT-IN synthetic click on the orb (needs "
                             f"{SYNTHETIC_INPUT_ENV}=1; can trigger mic/TTS)")
    args = parser.parse_args(argv)

    journal = resolve_journal_path(args.journal)
    summary = run(
        journal=journal,
        out_dir=Path(args.out).expanduser() if args.out else None,
        title=args.title,
        frames=args.frames,
        interval=args.interval,
        preroll_bytes=args.preroll_bytes,
        simulate=args.simulate_press,
    )
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
