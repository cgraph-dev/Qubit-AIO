"""E2E probe: the shim releases a REAL lease in a REAL registry.

No fakes for the lease layer — uses ``try_acquire_active_session`` to write a
lease through the production acquire path, then the shim to release it, then
proves a *different writer* can claim the same session id afterwards (which is
exactly what the refreshed dashboard tab does).

Run:
    PYTHONPATH=qubit_runtime:. .venv/bin/python scripts/probe_pty_lease_shim.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, "qubit_runtime")
sys.path.insert(0, ".")

from qubit_cli import active_sessions as A  # noqa: E402
from voicebridge import pty_lease_shim as shim  # noqa: E402

home = pathlib.Path(tempfile.mkdtemp()) / "home"
(home / "runtime").mkdir(parents=True)
state = A._state_path(home)

# 1. a REAL lease, written through the real acquire path, owned by this pid
lease, err = A.try_acquire_active_session(
    session_id="dashboard-real123",
    surface="tui",
    config={},
    registry_home=home,
    metadata={"live_session_id": "real123"},
)
assert err is None, err
before = json.loads(state.read_text())["entries"]
print(f"1. acquired:            {[(e['session_id'], e['pid']) for e in before]}")
print(f"   lease.pid == our pid: {before[0]['pid'] == os.getpid()}")

# 2. the shim releases it, keyed on the child pid (mirrors PtyBridge.pid)
class _Session:
    class bridge:
        pid = os.getpid()


shim._registry_home = lambda: home  # bind the temp home
released = shim.release_leases_for_sessions([_Session()], reason="e2e probe")
print(f"2. released count:      {released}")

# 3. the real registry no longer holds it
after = json.loads(state.read_text())["entries"]
print(f"3. remaining entries:   {[(e['session_id'], e['pid']) for e in after]}")

# 4. a DIFFERENT writer can now claim that exact session id (the refreshed tab)
lease2, err2 = A.try_acquire_active_session(
    session_id="dashboard-real123",
    surface="desktop",
    config={},
    registry_home=home,
    metadata={"live_session_id": "newtab"},
)
verdict = "OK" if err2 is None else f"REFUSED -> {str(err2).splitlines()[0]}"
print(f"4. re-claim by new writer: {verdict}")

# 5. control: without the shim, the second writer IS refused (proves the probe bites)
lease3, err3 = A.try_acquire_active_session(
    session_id="dashboard-real123",
    surface="tool",
    config={},
    registry_home=home,
    metadata={"live_session_id": "third"},
)
control = "OK (unexpected)" if err3 is None else "REFUSED (expected)"
print(f"5. control, still-held id: {control}")

for held in (lease, lease2, lease3):
    if held is not None:
        held.release()
