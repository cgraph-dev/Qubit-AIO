"""Cron external worker: the PM dependency boot its own entry point never gets.

``sys.executable -m cron.scheduler --external-worker-file ...``
(``cron/scheduler.py::_launch_external_cron_worker``) is a full Qubit entry point that
reaches ``qubit_bootstrap`` only from its ``__main__``, and it imports Qubit packages the moment it
starts. ``cron/scheduler_worker_env.py`` restores the committed generation's
``site-packages`` on its ``PYTHONPATH`` so those imports resolve, but a pinned path is not a
boot: the worker holds no lease on the generation, so the PM collector may remove it
between the gateway's exit and the worker's next import (#122290 review), and its
``sys.path`` never ran the generation's ``.pth`` files.

``pm.environments.activate_dependencies`` is exactly that boot -- the same call
``qubit_bootstrap`` makes for every other entry point -- and it leases the generation it
selects for the life of the process. ``worker_bootstrap()`` runs it at the top of
``cron/__init__.py`` -- ``-m cron.scheduler`` executes the package before the module, and the
package's first import (``cron.jobs`` -> ``qubit_yaml`` -> ``ruamel``) is already a
dependency -- and does nothing unless ``_launch_external_cron_worker`` marked this child: the
gateway already booted through ``qubit_bootstrap``, and every other importer of
``cron.scheduler`` is an interpreter that owns its own dependencies.

A genuine activation failure propagates: continuing on the inherited, unleased path lets the
collector delete the generation under a live worker. The worker then exits before its ownership
acknowledgement, which ``_launch_external_cron_worker`` already reports as a failed dispatch with
the worker's stderr. PM's legitimate no-ops (no committed generation under a venv, wheel, Nix)
return normally.

That boot covers dependencies only. ``qubit_bootstrap`` also finishes a pending source update
and may relaunch the process into a fresh one (``qubit_cli/venv_sync.py::relaunch_command``, an
``-I`` interpreter that ignores the pinned ``PYTHONPATH``), and re-executes it after restoring a
tree a killed ``qubit update`` half-wrote. ``finish_worker_boot()`` runs it from
the worker's ``__main__`` before the payload is read, so a relaunch replays the whole worker: the
payload is still on disk and the marker still set, and the new process boots its dependencies
again. ``qubit_bootstrap`` re-runs the (idempotent) dependency boot; the package-init call above
must stay, because ``cron.jobs`` needs dependencies before ``__main__`` can run.
"""

from __future__ import annotations

import os
from pathlib import Path

# Set by ``_launch_external_cron_worker`` in the child's env and consumed by
# ``finish_worker_boot()`` -- after any relaunch, before the ack -- so it never reaches the
# worker's own children; they inherit the activated PYTHONPATH instead.
# Distinct from ``_QUBIT_CRON_EXTERNAL_WORKER`` (the owning execution id) in scheduler.py.
WORKER_MARKER = "_QUBIT_CRON_WORKER_BOOT"
# A marked process was booted at package import; finish_worker_boot re-arms the marker for the
# bootstrap import so a relaunch replays the whole worker.
_BOOT_CLAIMED = False


def worker_bootstrap() -> None:
    """Run PM's dependency boot in the marked external worker (again after a relaunch)."""
    global _BOOT_CLAIMED
    if not os.environ.get(WORKER_MARKER):
        return
    _BOOT_CLAIMED = True
    from pm.environments import activate_dependencies

    activate_dependencies(Path(__file__).resolve().parent.parent)
    # Consumed here so the worker's own children do not inherit it; finish_worker_boot re-arms it
    # for the bootstrap import when it may relaunch.
    os.environ.pop(WORKER_MARKER, None)


def finish_worker_boot() -> None:
    """Run ``qubit_bootstrap`` before the worker reads its payload; may relaunch the worker.

    Not from ``worker_bootstrap()``: while ``-m cron.scheduler`` executes ``cron/__init__.py``,
    ``sys.argv[0]`` is still ``-m`` and ``__main__`` has no spec, so the relaunch command could
    not name the module to re-run. In ``__main__`` both are set.
    """
    global _BOOT_CLAIMED
    if _BOOT_CLAIMED:
        # The package import consumed the marker; a relaunch from the bootstrap import must carry
        # it so the new process boots its dependencies too.
        os.environ[WORKER_MARKER] = "1"
    try:
        import qubit_bootstrap  # noqa: F401
    finally:
        _BOOT_CLAIMED = False
        os.environ.pop(WORKER_MARKER, None)
