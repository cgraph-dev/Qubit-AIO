"""Shared leaf helpers for the ``qubit update`` modules (no Qubit imports; no cycle)."""

import logging
import sys
from contextlib import contextmanager
from pathlib import Path

# Log-record parity with the origin module.
logger = logging.getLogger("qubit_cli.update_cmd")


def _record_stop(reason: str, *, without_receipt: "str | None" = None) -> None:
    """Name the exit about to stop this update as one closed token (see update_receipt.record_stop_reason).

    ``without_receipt`` (``refused``/``failed``) is for an exit that fires before the receipt
    opens: a metrics row only. The receipt module is read from ``sys.modules``, never imported:
    an older updater can load this tree after the swap while its own, older ``update_receipt``
    (without these calls) stays loaded.
    """
    receipt = sys.modules.get("qubit_cli.update_receipt")
    if without_receipt is None:
        record = getattr(receipt, "record_stop_reason", None)
        if record is not None:
            record(reason)
    elif (record := getattr(receipt, "record_stop_without_receipt", None)) is not None:
        record(reason, without_receipt)


@contextmanager
def _best_effort(message: str):
    """Run a non-critical update step; swallow ``Exception`` and log it at debug.

    The updater must never die on bookkeeping (receipt, notices, cache seeds):
    ``message`` is the ``%s``-style debug line the inline ``try/except`` used.
    """
    try:
        yield
    except Exception as exc:
        logger.debug(message, exc)


def checkout_root(project_root) -> Path:
    """The git worktree that owns ``project_root``.

    Upstream's runtime tree IS the repository root, so ``PROJECT_ROOT`` and the checkout were the
    same path. Qubit vendors the runtime under ``qubit_runtime/``, so the runtime's
    ``PROJECT_ROOT`` can be one directory inside the checkout; git commands still work there (git
    walks up), but an explicit ``PROJECT_ROOT/.git`` probe does not. Check the path and its
    parent for a ``.git`` entry (a directory, or a file in a linked worktree).
    """
    base = Path(project_root).resolve()
    for candidate in (base, base.parent):
        if (candidate / ".git").exists():
            return candidate
    return base
