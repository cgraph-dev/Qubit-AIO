"""Prove resumable_session() actually RESUMES, not just returns None on a fresh install.

A function that always returns None would pass the fresh-install test. This drives the
positive case in an isolated QUBIT_HOME: create a setup profile with a card file and a
mid-guide state, and assert the session id comes back; then complete the guide and assert
it stops being resumable.
"""
import os
import tempfile
from pathlib import Path

home = Path(tempfile.mkdtemp(prefix="resume-probe-"))
os.environ["QUBIT_HOME"] = str(home)
os.environ["HERMES_HOME"] = str(home)

from qubit_cli import setup_profile as sp  # noqa: E402

print(f"  isolated home: {home}")

failures = []

# 1. no setup profile yet -> nothing to resume
if sp.resumable_session() is not None:
    failures.append("fresh install should not be resumable")
else:
    print("  [1] fresh install            -> None            OK")

# 2. create the setup profile
prof = sp.ensure_setup_profile()
print(f"  [2] created setup profile: {prof.name}")

# 3. a setup conversation that started the guide -> resumable
sid = "setup-session-abc123"
cards_dir = home / "setup-cards"
cards_dir.mkdir(parents=True, exist_ok=True)
(cards_dir / f"{sid}.json").write_text('{"step": "connectors", "asked": ["name", "theme"]}')

got = sp.resumable_session()
if got == sid:
    print(f"  [3] mid-guide conversation   -> {got}   OK")
else:
    failures.append(f"expected {sid}, got {got}")

# 4. completed guide -> no longer resumable
sp.mark_intro_seen()
got2 = sp.resumable_session()
if got2 is None:
    print("  [4] after completion         -> None            OK")
else:
    failures.append(f"completed guide still resumable: {got2}")

print()
if failures:
    print("  FAILURES:")
    for f in failures:
        print(f"    - {f}")
    raise SystemExit(1)
print("  ALL FOUR CASES PASS")
