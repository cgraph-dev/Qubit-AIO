"""Behavioural probe: entry._claim_launch_home is CALLED by main() and gates startup.

Run from qubit_runtime/:  PYTHONPATH=. ../.venv/bin/python -W ignore scripts/_probe_laneA_dead_symbols.py

Each arm re-homes the PROCESS (``QUBIT_HOME`` + ``server._qubit_home``, which server caches
from the env at import) to a disposable home, so nothing here touches a real profile.
  A. free home        -> claim returns it, main() reaches _serve(), finally releases the lock
  B. same home inside -> the claim sees its own reservation (POSIX locks are per-process) ->
                         None -> main() refuses with SystemExit 1 and never calls _serve()
  C. the LIVE install home (owned by the running qubit-gateway) -> refused, SystemExit 1
"""
import os
import sys
import tempfile

work = tempfile.mkdtemp(prefix="laneA-claim-")

from tui_gateway import entry, server  # noqa: E402
from gateway.runtime_ownership import process_ownership  # noqa: E402

LIVE = "/home/trick/.local/state/qubit-voicebridge/runtime"

calls = []
real_claim = entry._claim_launch_home


def spy_claim():
    calls.append("claim")
    return real_claim()


entry._claim_launch_home = spy_claim
entry._serve = lambda: calls.append("serve")


def run_main(label):
    """Point the process at ``label``'s home, run main() with the spies, report the outcome."""
    calls.clear()
    os.environ["QUBIT_HOME"] = label
    server._qubit_home = server.get_process_qubit_home()   # what server caches at import
    home = server._launch_home()
    print(f"\n[{label}] server._launch_home() -> {home}")
    try:
        entry.main()
        code = None
    except SystemExit as exc:
        code = exc.code
    print(f"  main() exit: {code!r} | call order: {calls} | _serve ran: {'serve' in calls}")
    return home


disposable = run_main(work)                          # A
print("  reservation released by main()'s finally:", not process_ownership.owns(disposable))

# B: ANOTHER process owns the disposable home — the arm `main()` is refused for in production.
import subprocess  # noqa: E402
holder = subprocess.Popen([sys.executable, "scripts/_probe_laneA_lock_holder.py", str(disposable)],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                          env={**os.environ, "PYTHONPATH": "."})
assert holder.stdout.readline().strip() == "READY", "lock holder failed to start"
try:
    run_main(work)
finally:
    holder.stdin.close()
    holder.wait(timeout=30)

run_main(LIVE)                                       # C: the live install home
