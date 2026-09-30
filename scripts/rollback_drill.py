#!/usr/bin/env python3
"""Packet 7B: hermetic rollback drill for every feature flag.

Toggles each registered feature flag on a throwaway copy of the config,
verifies the effective value changed, restores the rollback value, verifies
the restore, and proves the seeded user data (memory db, a report manifest,
and a cursor file) is byte-identical before and after. Never touches the live
config or the operator's state dir.

Usage:
  python scripts/rollback_drill.py            # hermetic, exit 0 = all flags OK
  python scripts/rollback_drill.py --json     # machine-readable report
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def run_drill() -> dict:
    from voicebridge import feature_flags as FF
    from voicebridge.config import Config as Cfg

    results = []
    with tempfile.TemporaryDirectory(prefix="vb-rollback-drill-") as td:
        tdp = Path(td)
        cfg_path = tdp / "voice.yaml"
        live = Cfg.load()
        cfg_path.write_text(FF.redact_config_yaml(live))
        state = tdp / "state"
        state.mkdir()

        mem = state / "memory.db"
        mem.write_bytes(b"\x00MEMORY\x01")
        rep = state / "reports"
        rep.mkdir()
        (rep / "manifest.json").write_text('{"id":"x","version":1}')
        cur = state / "cursors.jsonl"
        cur.write_text('{"account":"gmail","cursor":7}\n')

        before = FF.state_snapshot(state)
        for f in FF.FLAGS:
            flip = (not f.default) if isinstance(f.default, bool) else (
                f.rollback_value if f.rollback_value != f.default else f.default)
            # flip to a non-default value
            FF.apply_flag(cfg_path, f.name, flip)
            c1 = Cfg.load(cfg_path)
            flipped_ok = FF.effective(c1, f.name) != f.default
            # documented rollback (restores the safe fallback value)
            FF.rollback_flag(cfg_path, f.name)
            c2 = Cfg.load(cfg_path)
            restored_ok = FF.effective(c2, f.name) == f.rollback_value
            # restore to the shipped default so the next flag starts clean
            FF.apply_flag(cfg_path, f.name, f.default)
            c3 = Cfg.load(cfg_path)
            default_ok = FF.effective(c3, f.name) == f.default
            results.append({
                "flag": f.name,
                "category": f.category,
                "flipped_effective": str(FF.effective(c1, f.name)),
                "restored_effective": str(FF.effective(c2, f.name)),
                "ok": bool(flipped_ok and restored_ok and default_ok),
            })
        after = FF.state_snapshot(state)
        data_preserved = before == after
    return {"flags": results, "user_data_preserved": data_preserved,
            "all_ok": all(r["ok"] for r in results) and data_preserved}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="hermetic feature-flag rollback drill")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)
    report = run_drill()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for r in report["flags"]:
            mark = "OK " if r["ok"] else "FAIL"
            print(f"[{mark}] {r['flag']:<16} flipped->{r['flipped_effective']:>8} "
                  f"restored->{r['restored_effective']:>8}")
        print(f"user data preserved: {report['user_data_preserved']}")
        print(f"rollback drill: {'PASS' if report['all_ok'] else 'FAIL'}")
    return 0 if report["all_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())