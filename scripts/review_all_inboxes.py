#!/usr/bin/env python3
"""Review all inboxes — aggregates gmail/messenger/instagram/tiktok in parallel. <2s when cached."""
from __future__ import annotations
import subprocess, sys, time, concurrent.futures
from pathlib import Path

VENV_PY = sys.executable  # portable: the bridge venv python invokes this script
SCRIPTS_DIR = Path(__file__).resolve().parent

SCRIPTS = [
    ("gmail", SCRIPTS_DIR / "review_gmail.py"),
    ("messenger", SCRIPTS_DIR / "review_messenger.py"),
    ("instagram", SCRIPTS_DIR / "review_instagram.py"),
    ("tiktok", SCRIPTS_DIR / "review_tiktok.py"),
]

def run_one(name, path, question):
    try:
        proc = subprocess.run([str(VENV_PY), str(path), question or ""], capture_output=True, text=True, timeout=15)
        out = (proc.stdout or "").strip() or (proc.stderr or "").strip() or "(no output)"
        return name, out
    except subprocess.TimeoutExpired:
        return name, f"{name.upper()} — timed out after 15s"
    except Exception as e:
        return name, f"{name.upper()} — error: {e}"

def main():
    q = sys.argv[1] if len(sys.argv) > 1 else ""
    t0 = time.time()
    # Run all 4 in parallel so total time ≈ max(one inbox), not sum
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run_one, name, path, q): name for name, path in SCRIPTS}
        for fut in concurrent.futures.as_completed(futs, timeout=18):
            name, out = fut.result()
            results[name] = out
    elapsed = time.time() - t0
    # Preserve order: gmail, messenger, instagram, tiktok
    parts = []
    for name, _ in SCRIPTS:
        out = results.get(name, f"{name.upper()} — no result")
        parts.append(out)
    # Summary header
    header = f"ALL INBOXES — {elapsed:.1f}s"
    # Try to extract totals from each report's Counts line
    print(header)
    print("=" * len(header))
    print("")
    for p in parts:
        print(p)
        print("")
        print("-" * 60)
        print("")
    # Overall counts line for summarizer
    print(f"Done in {elapsed:.1f}s. Sources: gmail, messenger, instagram, tiktok.")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as e:
        print(f"ALL INBOXES — error: {e}")
        sys.exit(0)
