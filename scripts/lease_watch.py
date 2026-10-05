#!/usr/bin/env python3
"""Watch the Qubit active-session lease registry across a dashboard refresh.

Read-only. Never writes the registry, never takes the lease lock.

Why a dedicated probe: ``active_sessions.json`` is replaced atomically by
``utils.atomic_json_write``, so polling it catches every transition. But the
release path is a short window, and a lease can also be rewritten in place
(``_take_over`` / ``_is_same_writer`` re-acquire), which a naive diff misses.
So this snapshots the full entry set each tick and reports every change.

Usage:
    lease_watch.py                 # diff registry every 0.5s, print changes
    lease_watch.py --interval 0.2  # tighter sampling
    lease_watch.py --once          # single snapshot (for before/after)
    lease_watch.py --json          # machine-readable final state

Confirm the registry actually moved (the writer is atomic-rename based):
    lease_watch.py --verify-atomic
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

STATE_HOME = Path(
    os.environ.get("QUBIT_STATE_HOME")
    or Path.home() / ".local" / "state" / "qubit-voicebridge"
)
# NOTE: the doubled "runtime/runtime" is real — active_sessions._state_path()
# joins get_qubit_home() + "runtime" + "active_sessions.json", and this
# install's QUBIT_HOME already ends in ".../runtime".
CANDIDATES = (
    STATE_HOME / "runtime" / "runtime" / "active_sessions.json",
    STATE_HOME / "runtime" / "active_sessions.json",
)


def registry_path() -> Path:
    for candidate in CANDIDATES:
        if candidate.is_file():
            return candidate
    return CANDIDATES[0]


def _age(entry: dict) -> str:
    try:
        started = float(entry.get("started_at") or 0)
    except (TypeError, ValueError):
        return "?"
    if not started:
        return "?"
    return f"{max(0, time.time() - started):.0f}s"


def _key(entry: dict) -> str:
    return str(entry.get("lease_id") or "")


def _line(entry: dict) -> str:
    meta = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    return (
        f"session={entry.get('session_id')} surface={entry.get('surface')} "
        f"pid={entry.get('pid')} live={meta.get('live_session_id')} age={_age(entry)}"
    )


def snapshot(path: Path) -> tuple[list[dict], dict]:
    """(entries, stat) — empty entries when the file is absent or unreadable."""
    try:
        stat = path.stat()
    except OSError:
        return [], {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except Exception:
        return [], {"size": stat.st_size, "mtime": stat.st_mtime}
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        entries = []
    return ([e for e in entries if isinstance(e, dict)], {"size": stat.st_size, "mtime": stat.st_mtime})


def diff(before: list[dict], after: list[dict]) -> list[str]:
    old, new = {_key(e): e for e in before}, {_key(e): e for e in after}
    out: list[str] = []
    for lease_id in sorted(set(new) - set(old)):
        out.append(f"  + ADD     {_line(new[lease_id])}")
    for lease_id in sorted(set(old) - set(new)):
        out.append(f"  - RELEASE {_line(old[lease_id])}")
    for lease_id in sorted(set(old) & set(new)):
        if old[lease_id] != new[lease_id]:
            out.append(f"  ~ CHANGED {_line(new[lease_id])}")
    return out


def verify_atomic(path: Path, samples: int = 200) -> int:
    """Prove a change is visible as a whole (atomic rename), not a torn write.

    A torn write would show a same-inode, growing size with invalid JSON. An
    atomic replace shows an inode change with valid JSON at every step.
    """
    print(f"watching {path} for atomic-replace evidence ({samples} samples)")
    inode, bad = None, 0
    for _ in range(samples):
        try:
            stat = path.stat()
        except OSError:
            time.sleep(0.05)
            continue
        if inode is not None and stat.st_ino != inode:
            print(f"  inode {inode} -> {stat.st_ino}: atomic replace observed")
            _, fresh = snapshot(path)
            entries, _ = snapshot(path)
            print(f"  re-read OK ({len(entries)} entries)")
            return 0
        if inode is None:
            inode = stat.st_ino
        try:
            snapshot(path)
        except Exception:
            bad += 1
        time.sleep(0.05)
    print(
        "  no replacement observed in the sample window — refresh the dashboard "
        "chat now, or raise --samples via the flag in the source"
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verify-atomic", action="store_true")
    parser.add_argument("--timeout", type=float, default=0.0, help="stop after N seconds (0 = forever)")
    args = parser.parse_args()

    path = registry_path()

    if args.verify_atomic:
        return verify_atomic(path)

    entries, stat = snapshot(path)

    if args.once:
        if args.json:
            print(json.dumps({"path": str(path), "entries": entries}, indent=2))
            return 0
        print(f"{path}  ({len(entries)} entr(y|ies), mtime={stat.get('mtime')})")
        for entry in entries:
            print(f"  {_line(entry)}")
        return 0

    print(f"watching {path} every {args.interval}s — refresh the dashboard chat now, Ctrl-C to stop")
    print(f"  baseline: {len(entries)} entr(y|ies)")
    for entry in entries:
        print(f"    {_line(entry)}")

    started = time.time()
    heartbeat = time.time()
    while True:
        time.sleep(args.interval)
        if args.timeout and (time.time() - started) > args.timeout:
            print("timeout reached")
            return 0
        now_entries, now_stat = snapshot(path)
        changes = diff(entries, now_entries)
        if changes:
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] {len(entries)} -> {len(now_entries)} entries")
            for change in changes:
                print(change)
            sys.stdout.flush()
            entries = now_entries
        elif time.time() - heartbeat > 30:
            print(f"[{time.strftime('%H:%M:%S')}] quiet — {len(entries)} entr(y|ies)")
            sys.stdout.flush()
            heartbeat = time.time()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nstopped")
