"""Durable manual-serve handoffs, independent of gateway restart receipts."""

import json
import logging
import math
import os
import sys
import tempfile
from pathlib import Path

from qubit_constants import get_qubit_home

logger = logging.getLogger(__name__)


def defer_manual_serve(runtime: dict, *, require_alive: bool = False) -> bool:
    """Transfer an identified manual runtime to its own durable restart reminder."""
    from qubit_cli.process_identity import _pid_alive_matches

    if runtime.get("kind") not in ("serve", "dashboard") or runtime.get("supervisor") != "manual-serve" or runtime.get("restart_via") != "respawn-argv":
        return False
    pid = runtime.get("pid")
    detail = runtime.get("detail")
    if not isinstance(detail, dict):
        return False
    created = detail.get("create_time")
    if type(pid) is not int or pid <= 0:
        return False
    identified = type(created) in (int, float) and math.isfinite(created) and created > 0
    if not identified:
        # Without a recorded creation time no durable reminder can be filed (#116507);
        # only a provably dead pid discharges the row, anything less stays pending.
        return not require_alive and _pid_alive_matches(pid, None) is False
    try:
        alive = _pid_alive_matches(pid, created)
        if require_alive and alive is not True:
            return False
        if alive is False:
            return True
        directory = get_qubit_home() / "serve_restart_pending"
        directory.mkdir(parents=True, exist_ok=True)
        row = {"kind": runtime["kind"], "profile": runtime.get("profile", "unknown"), "pid": pid, "create_time": created}
        target = directory / f"{pid}-{float(created).hex()}.json"
        # One immutable file per incarnation avoids read/merge/write races between CLI startups.
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(row, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return True
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("Could not preserve manual serve obligation: %s", exc)
        return False


def retain_receipt_manual_serves(receipt: dict) -> list[dict]:
    """Return transfers still owed so receipt rotation cannot discard failed writes.

    ``carried_manual_serves`` is the durable running record's copy of the previous receipt's rows
    (update_receipt.begin): read it too, or the warning vanishes while an update runs and after a
    run killed before its ``plan`` stage.
    """
    plan = receipt.get("plan") or {}
    rows = list(plan.get("runtimes") or []) + list(receipt.get("pending_manual_serves") or [])
    rows += list(receipt.get("carried_manual_serves") or [])
    pending = []
    for row in rows:
        if not isinstance(row, dict) or row.get("kind") not in ("serve", "dashboard") or row.get("supervisor") != "manual-serve":
            continue
        if not defer_manual_serve(row) and row not in pending:
            pending.append(row)
    return pending


def warn_pending_manual_serves(*, startup: bool = False, pending_manual: list[dict] | None = None) -> None:
    """Warn about manual debt independently of gateway evidence; optionally reuse a snapshot's failed transfers."""
    from qubit_cli.process_identity import _pid_alive_matches
    from qubit_cli.update_receipt import read_latest_receipt

    stream = sys.stderr if startup else sys.stdout
    if pending_manual is None:
        pending_manual = retain_receipt_manual_serves(read_latest_receipt() or {})
    for row in pending_manual:
        print(f"  ⚠ {row['kind']} [{row.get('profile', 'unknown')}] pid {row.get('pid', 'unknown')}: manual restart reminder could not be saved; restart remains pending in the update receipt.", file=stream)
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        if type(detail.get("create_time")) in (int, float):
            print("    Ask its owner to relaunch `qubit serve` / `qubit dashboard`; check reminder storage permissions and free space.", file=stream)
        else:
            # No usable creation time means identity, not storage, blocked the durable reminder.
            print("    This host could not read the process creation time, so no durable reminder could be filed; ask its owner to relaunch `qubit serve` / `qubit dashboard`, and the warning clears once the pid is confirmed gone.", file=stream)
    directory = get_qubit_home() / "serve_restart_pending"
    for path in sorted(directory.glob("*.json")):
        try:
            row = json.loads(path.read_text(encoding="utf-8-sig"))
            if _pid_alive_matches(row["pid"], row["create_time"]) is False:
                path.unlink(missing_ok=True)
                continue
            print(f"  ⚠ {row['kind']} [{row['profile']}] pid {row['pid']}: manual restart still pending; this process may still serve pre-update code.", file=stream)
            print("    Ask its owner to relaunch `qubit serve` / `qubit dashboard` (reconnect Desktop for an SSH backend).", file=stream)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.debug("Could not reconcile manual serve obligation %s: %s", path, exc)
            print(f"  ⚠ Manual serve restart reminder could not be verified: {path.name}", file=stream)


# --- ported from upstream 088f657ecf ---

def _defer_identified(runtime: dict, *, extra: dict | None = None, require_alive: bool = False) -> bool:
    """Shared tail of the manual and unit deferrals: True when the incarnation is gone or its reminder
    (plus *extra* fields) was filed; False while it is still owed and nothing durable was written."""
    from qubit_cli.process_identity import _pid_alive_matches

    pid, created = runtime.get("pid"), runtime["detail"].get("create_time")
    if type(pid) is not int or pid <= 0:
        return False
    identified = type(created) in (int, float) and math.isfinite(created) and created > 0
    if not identified:
        # Without a recorded creation time no durable reminder can be filed (#116507);
        # only a provably dead pid discharges the row, anything less stays pending.
        return not require_alive and _pid_alive_matches(pid, None) is False
    try:
        alive = _pid_alive_matches(pid, created)
        if require_alive and alive is not True:
            return False
        if alive is False:
            return True
        _write_reminder({"kind": runtime["kind"], "profile": runtime.get("profile", "unknown"), "pid": pid,
                         "create_time": created, **(extra or {})})
        return True
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("Could not preserve serve restart obligation: %s", exc)
        return False


def _write_reminder(row: dict) -> None:
    """Persist one reminder row, one immutable file per incarnation (no read/merge/write races
    between CLI startups). Raises OSError/ValueError/TypeError to the caller."""
    directory = get_qubit_home() / "serve_restart_pending"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{row['pid']}-{float(row['create_time']).hex()}.json"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(row, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def is_unit_serve_row(row: object) -> bool:
    """A serve/dashboard row whose systemd unit owns it (MainPID), with the unit recorded (#141021)."""
    return (isinstance(row, dict) and row.get("kind") in ("serve", "dashboard") and row.get("supervisor") == "systemd"
            and isinstance(row.get("detail"), dict) and bool(row["detail"].get("systemd_unit")))


def is_reminder_row(row: object) -> bool:
    """Rows the receipt-backed reminder flow carries: manual serves and unit-owned serves."""
    return is_unit_serve_row(row) or (
        isinstance(row, dict) and row.get("kind") in ("serve", "dashboard") and row.get("supervisor") == "manual-serve")


def defer_unit_serve(runtime: dict) -> bool:
    """The unit-owned twin of :func:`defer_manual_serve`: file a durable reminder carrying the unit's
    restart command for a pre-update incarnation that is still alive. A unit-owned row moved off the
    manual-serve reminder when it was classified ``systemd`` (#141021), and the fleet obligation names
    only gateways, so without this a unit the update could not restart (no root, an aborted run,
    ``--no-gateway-restart``) is never mentioned again. True when the row is discharged (process gone)
    or the reminder was filed; False when it is still owed and nothing durable could be written."""
    from qubit_cli.main_dashboard import _systemd_restart_commands

    if not is_unit_serve_row(runtime):
        return False
    detail = runtime["detail"]
    return _defer_identified(runtime, extra={
        "restart": _systemd_restart_commands(str(detail["systemd_unit"]), detail.get("systemd_scope"))})
