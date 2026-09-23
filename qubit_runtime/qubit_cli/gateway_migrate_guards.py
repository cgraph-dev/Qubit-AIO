"""Boundaries the automatic multiplex migration must not cross, and the opt-out.

Ported from the reference ``hermes_cli/gateway_migrate_guards.py``. The multiplexer replaces a
kernel-enforced boundary (separate UNIX users, separate service domains, separate ``QUBIT_HOME``
trees) with in-process isolation. An operator may choose that with ``qubit gateway migrate``;
an unattended path must not choose it for them. ``build_migration_plan`` records the same
findings as NOTICES so a dry run shows them.

Adaptations to the vendored tree:

* ``_home_env`` moves here from ``gateway_migrate`` so this module has no module-level import of the
  migration facade (the facade imports the guards), and it switches ``QUBIT_HOME`` (the vendored
  constant) rather than ``HERMES_HOME``.
* the pinned-home reader is the vendored ``_qubit_home_from_systemd_unit_file``; the 0.21.1 tree
  has no per-path ``_qubit_home_pinned_by_unit`` (add it with the systemd unit writer, not here).
"""
from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

if TYPE_CHECKING:
    from qubit_cli.gateway_migrate import MigrationPlan, ProfileGateway


@contextlib.contextmanager
def _home_env(home: Path) -> Iterator[None]:
    """Run service-manager helpers as if ``home`` were the active ``QUBIT_HOME``. Both the
    contextvar override (``get_qubit_home``) and ``os.environ`` (gateway identity files, unit
    generation) are switched, then restored."""
    import qubit_constants
    from qubit_constants import reset_qubit_home_override, set_qubit_home_override
    previous = os.environ.get("QUBIT_HOME")
    token = set_qubit_home_override(str(home))
    os.environ["QUBIT_HOME"] = str(home)
    qubit_constants._default_qubit_root_memo = None
    try:
        yield
    finally:
        reset_qubit_home_override(token)
        if previous is None:
            os.environ.pop("QUBIT_HOME", None)
        else:
            os.environ["QUBIT_HOME"] = previous
        qubit_constants._default_qubit_root_memo = None


# --------------------------------------------------------------------------- identity resolution


def _pid_uid(pid: int) -> Optional[int]:
    """Owner uid of a live process: ``/proc`` where it exists, ``ps`` on macOS; None when unknown."""
    with contextlib.suppress(OSError):
        return os.stat(f"/proc/{pid}").st_uid
    from qubit_cli.gateway import is_macos
    if not is_macos():
        return None
    with contextlib.suppress(OSError, ValueError, subprocess.SubprocessError):
        result = subprocess.run(["ps", "-o", "uid=", "-p", str(pid)], capture_output=True, text=True,
                                encoding="utf-8", check=False, timeout=2)
        if result.returncode == 0 and result.stdout.strip():
            return int(result.stdout.strip())
    return None


def _system_unit_uid(unit_path: Path) -> Optional[int]:
    """uid a system unit runs as: its ``User=`` (root when absent); None when the name is unknown."""
    from qubit_cli.gateway import _read_systemd_user_from_unit
    user = _read_systemd_user_from_unit(unit_path)
    if user is None:
        return 0
    import pwd
    with contextlib.suppress(KeyError):
        return pwd.getpwnam(user).pw_uid
    return None


def gateway_identity(home: Path, pid: Optional[int], services: list[tuple[str, bool]]) -> tuple[Optional[int], Path]:
    """``(uid, runtime_home)`` of the gateway that serves ``home``.

    uid: the live process owner; else, for a system unit, its ``User=`` — and ONLY that: a system
    unit's principal is whatever systemd will run, so an unresolvable ``User=`` stays None rather
    than borrowing the profile directory's owner. Without a system unit (user-scope systemd /
    launchd / detached), the profile directory owner is the account the gateway runs as. None means
    unknown. runtime_home: the ``QUBIT_HOME`` an installed unit pins, which is where the gateway
    really runs; ``home`` otherwise.
    """
    from qubit_cli.gateway import _qubit_home_from_systemd_unit_file, get_systemd_unit_path

    uid: Optional[int] = _pid_uid(pid) if pid is not None else None
    runtime_home = home
    has_system_unit = False
    for kind, system in services:
        if kind != "systemd":
            continue
        with _home_env(home):
            unit_path = get_systemd_unit_path(system=system)
        pinned = _qubit_home_from_systemd_unit_file(system=system) if unit_path.exists() else None
        if pinned and runtime_home == home:
            runtime_home = Path(pinned).expanduser()
        if system:
            has_system_unit = True
            if uid is None:
                uid = _system_unit_uid(unit_path)
    if uid is None and not has_system_unit:
        with contextlib.suppress(OSError):
            uid = home.stat().st_uid
    return uid, runtime_home


# --------------------------------------------------------------------------- guards


def _service_label(profile: ProfileGateway) -> str:
    return profile.service_label() if profile.services else "no service manager (detached)"


def _guard_service_domain(plan: MigrationPlan, profile: ProfileGateway) -> Optional[str]:
    """Different manager or scope than the one the fleet converges on (system vs user systemd, launchd
    vs systemd). The reference is the default's own unit when it has one, else the manager
    ``target_service_kind()`` elects from the secondaries. Two units on one profile is an ambiguous
    topology the unattended path does not resolve either."""
    if len(profile.services) > 1:
        return (f"Profile '{profile.name}' has more than one installed service ({profile.service_label()}): "
                f"an ambiguous service topology is not folded automatically.")
    target = plan.target_service_kind()
    reference = plan.default.services or ([target] if target is not None else [])
    if set(profile.services) == set(reference):
        return None
    from qubit_cli.gateway_migrate import _service_label as _kind_label
    against = (f"the default gateway runs under {_service_label(plan.default)}" if plan.default.services
               else f"the fleet converges on {_kind_label(target)}")
    return (f"Profile '{profile.name}' runs under {_service_label(profile)} while {against}: "
            f"a different service domain is not folded automatically.")


def _guard_unix_user(plan: MigrationPlan, profile: ProfileGateway) -> Optional[str]:
    default_uid = plan.default.uid
    if default_uid is None and plan.default.has_system_unit:
        return ("The default gateway runs a system unit whose User= cannot be resolved on this host: "
                "an unknown service principal is not folded into automatically.")
    if profile.uid is None and profile.has_system_unit:
        return (f"Profile '{profile.name}' runs a system unit whose User= cannot be resolved on this host: "
                f"an unknown service principal is not folded automatically.")
    if default_uid is None or profile.uid is None or profile.uid == default_uid:
        return None
    return (f"Profile '{profile.name}' runs as uid {profile.uid} while the default gateway runs as uid "
            f"{default_uid}: a UNIX privilege boundary is not folded automatically.")


def _guard_home_tree(plan: MigrationPlan, profile: ProfileGateway) -> Optional[str]:
    profiles_root = (plan.default_home / "profiles").resolve()
    runtime_home = (profile.runtime_home or profile.home).resolve()
    if runtime_home.is_relative_to(profiles_root):
        return None
    return (f"Profile '{profile.name}' runs with QUBIT_HOME={runtime_home}, outside {profiles_root}: "
            f"the multiplexer would serve {profile.home} instead of the live home.")


_AUTO_MIGRATION_GUARDS = (
    _guard_service_domain,
    _guard_unix_user,
    _guard_home_tree,
)


def auto_migration_blockers(plan: MigrationPlan) -> list[str]:
    """Every boundary a standalone secondary sits behind; empty when the fleet is one user, one service
    domain, one profiles/ tree — the only shape an unattended migration may fold on its own."""
    findings = [
        finding
        for profile in plan.standalone_secondaries
        for guard in _AUTO_MIGRATION_GUARDS
        if (finding := guard(plan, profile)) is not None
    ]
    return list(dict.fromkeys(findings))  # a default-side finding repeats per secondary


# --------------------------------------------------------------------------- opt-out


def auto_migration_opted_out(default_home: Path) -> bool:
    """``gateway.auto_multiplex_migration: false`` in the DEFAULT profile's effective config: the same
    ``load_config`` the rest of the CLI reads, so a managed ``false`` wins over a user's ``true`` and a
    YAML string ``"false"`` is false, not truthy. Only the nested key counts, there is no top-level
    alias."""
    from qubit_cli.config import load_config_readonly
    from utils import is_truthy_value
    with _home_env(default_home):
        gateway_section = load_config_readonly().get("gateway")
    if not isinstance(gateway_section, dict):
        return False
    return not is_truthy_value(gateway_section.get("auto_multiplex_migration"), default=True)
