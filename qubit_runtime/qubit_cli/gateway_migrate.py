"""``qubit gateway migrate``: converge a per-profile-gateway install onto the ONE host gateway,
with a table-driven preflight.

Ported from the reference ``hermes_cli/gateway_migrate.py``. Convergence is defined by TOPOLOGY,
not by a config flag — a host is converged when no secondary profile owns a gateway process or a
supervisor unit any more. That makes re-running the plan meaningful, and the plan is read-only:
this fork's ticket implements the real plan/preflight and refuses the DESTRUCTIVE apply as an
operator-owned surface (see :data:`APPLY_REFUSAL`).

Adaptations to the vendored tree (recorded here, not silenced):

* ``_home_env`` lives in :mod:`qubit_cli.gateway_migrate_guards` (avoids a facade <-> guards cycle)
  and switches ``QUBIT_HOME``.
* ``_installed_services`` asks the vendored service-manager helpers for macOS/Windows. The
  Windows scheduled-task helpers exist in the vendored tree; the launchd plist path is used exactly
  as the reference uses it.
* ``_credential_claims`` uses the vendored ``gateway.run_adapters.GatewayRunner`` when a platform
  fingerprint can be read, and skips claims it cannot fingerprint (no invented hashing).
* ``profile_channels.credential_env_keys`` is NOT vendored in 0.21.1, so the key-NAME lookup falls
  back to the platform id (still never a value).
* no **auto** migration hook: this fork's ticket is the explicit operator command only.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator, Optional

logger = logging.getLogger(__name__)

MANIFEST_NAME = "gateway_migration.json"
MIGRATE_COMMAND = "qubit gateway migrate"
_SERVED_WAIT_SECONDS = 90.0

# The apply is a SERVICE-management surface (it stops and uninstalls per-profile services). A typed
# refusal is never a working capability, so the real PLAN is implemented; the destructive step names
# its operator prerequisite instead of performing it.
APPLY_REFUSAL = (
    "gateway migrate: the plan above is read-only, but applying it stops and uninstalls every "
    "per-profile gateway service. That mutating step is operator-owned; run it from the operator "
    "console against the intended host (see qubit gateway migrate --dry-run for the exact plan)."
)
APPLY_REFUSAL_CODE = "GATEWAY_MIGRATE_OPERATOR_OWNED"


# --------------------------------------------------------------------------- data


@dataclass
class ProfileGateway:
    """One profile's standalone gateway footprint: live PID and/or installed service."""
    name: str
    home: Path
    pid: Optional[int] = None
    # EVERY installed unit: [("systemd", system), ("launchd", False)]. A profile can carry a user AND
    # a system unit at once; recording only the first found left the other live beside the
    # multiplexer.
    services: list[tuple[str, bool]] = field(default_factory=list)
    run_as_user: Optional[str] = None  # User= recorded by a system-scope systemd unit
    uid: Optional[int] = None  # owner of the gateway process/unit; None = unknown (never "different")
    runtime_home: Optional[Path] = None  # QUBIT_HOME the installed unit pins, when it differs from home

    @property
    def is_default(self) -> bool:
        return self.name == "default"

    @property
    def service(self) -> Optional[tuple[str, bool]]:
        """The unit the default gateway is (re)started through; None when nothing is installed."""
        return self.services[0] if self.services else None

    @property
    def has_gateway(self) -> bool:
        return self.pid is not None or bool(self.services)

    @property
    def has_system_unit(self) -> bool:
        return ("systemd", True) in self.services

    def service_label(self) -> str:
        return " + ".join(_service_label(s) for s in self.services) if self.services else "none"

    def to_dict(self) -> dict:
        return {
            "profile": self.name, "home": str(self.home), "pid": self.pid,
            "service": None if self.service is None else _service_dict(self.service),
            "services": [_service_dict(s) for s in self.services],
            "run_as_user": self.run_as_user,
            "uid": self.uid, "runtime_home": None if self.runtime_home is None else str(self.runtime_home),
        }


def _service_label(service: tuple[str, bool]) -> str:
    kind, system = service
    if kind == "systemd":
        return f"systemd ({'system' if system else 'user'})"
    return "Windows scheduled task" if kind == "windows" else kind


def _service_dict(service: tuple[str, bool]) -> dict:
    return {"kind": service[0], "system": service[1]}


@dataclass
class MigrationPlan:
    default_home: Path
    profiles: list[ProfileGateway]
    multiplex_flag_on: bool
    live_served: Optional[list[str]]  # served_profiles the live default gateway recorded, if any
    manifest: Optional[dict] = None
    interrupted: bool = False
    blockers: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    @property
    def secondaries(self) -> list[ProfileGateway]:
        return [p for p in self.profiles if not p.is_default]

    @property
    def default(self) -> ProfileGateway:
        return next(p for p in self.profiles if p.is_default)

    @property
    def already_multiplexed(self) -> bool:
        """Converged: nothing is left for this command to do (topology, not the flag)."""
        if self.interrupted or self.standalone_secondaries:
            return False
        return self.multiplex_flag_on or bool(self.live_served and len(self.live_served) > 1)

    @property
    def standalone_secondaries(self) -> list[ProfileGateway]:
        return [p for p in self.secondaries if p.has_gateway]

    @property
    def blocked(self) -> bool:
        return bool(self.blockers)

    def target_service_kind(self) -> Optional[tuple[str, bool]]:
        """Service manager the default gateway should end up on: its own, else the one the
        secondaries used (so a systemd-managed fleet stays systemd-managed)."""
        if self.default.service is not None:
            return self.default.service
        return next((p.service for p in self.secondaries if p.service is not None), None)

    def target_run_as_user(self) -> Optional[str]:
        """Preserve the system unit identity that the default unit replaces."""
        if self.default.run_as_user:
            return self.default.run_as_user
        return next((p.run_as_user for p in self.secondaries if p.run_as_user), None)

    def to_dict(self) -> dict:
        return {
            "default_home": str(self.default_home),
            "profiles": [p.to_dict() for p in self.profiles],
            "multiplex_flag_on": self.multiplex_flag_on,
            "live_served": self.live_served,
            "already_multiplexed": self.already_multiplexed,
            "interrupted": self.interrupted,
            "blockers": list(self.blockers),
            "notices": list(self.notices),
            "eligible": self.eligible_for_migration(),
            "command": MIGRATE_COMMAND,
        }

    def eligible_for_migration(self) -> bool:
        """>= 2 profiles, at least one secondary with its own gateway, multiplex off, no blockers."""
        return (
            len(self.profiles) >= 2 and bool(self.standalone_secondaries)
            and not self.already_multiplexed and not self.blocked
        )


# --------------------------------------------------------------------------- home / env plumbing


def _default_home() -> Path:
    from qubit_constants import get_default_qubit_root
    return get_default_qubit_root()


def _profile_homes() -> list[tuple[str, Path]]:
    from qubit_cli.profiles import profiles_to_serve
    return list(profiles_to_serve(multiplex=True))


def _live_gateway_pid(home: Path) -> Optional[int]:
    """Verified PID of a gateway SERVING ``home``, else None (never raises: a probe failure must not
    abort a migration plan)."""
    from qubit_cli.gateway_multiplex_mode import live_gateway_pid_for_home
    with contextlib.suppress(Exception):
        return live_gateway_pid_for_home(home)
    return None


def _host_gateway_owner():
    """The ONE live host gateway or None. The vendored 0.21.1 tree has no ``gateway.host_attach``, so
    this is the best-effort host process probe; it never raises."""
    with contextlib.suppress(Exception):
        from gateway.status import get_running_pid_cached
        from qubit_constants import get_default_qubit_root
        return get_running_pid_cached(get_default_qubit_root() / "gateway.pid", cleanup_stale=False)
    return None


def _own_gateway_pid(home: Path, owner) -> Optional[int]:
    """PID of a gateway ``home`` OWNS, else None. Ownership is what this command may stop; the host
    multiplexer is not owned by the profiles it serves."""
    pid = _live_gateway_pid(home)
    if pid is None or owner is None or pid != owner:
        return pid
    with contextlib.suppress(Exception):
        from gateway.status import _same_qubit_home
        from qubit_constants import get_default_qubit_root
        return pid if _same_qubit_home(Path(get_default_qubit_root()), Path(home)) else None
    return None


def _gateway_identity(home: Path, pid: Optional[int], services: list[tuple[str, bool]]) -> tuple[Optional[int], Path]:
    from qubit_cli.gateway_migrate_guards import gateway_identity
    return gateway_identity(home, pid, services)


def _installed_services(home: Path) -> list[tuple[str, bool]]:
    """Every installed service for ``home``'s gateway (units / plist / scheduled task), user scope first."""
    from qubit_cli import gateway as gw
    from qubit_cli.gateway_migrate_guards import _home_env
    found: list[tuple[str, bool]] = []
    with _home_env(home):
        if gw.supports_systemd_services():
            found.extend(("systemd", system) for system in (False, True) if gw.get_systemd_unit_path(system=system).exists())
        if gw.is_macos() and gw.get_launchd_plist_path().exists():
            found.append(("launchd", False))
        if gw.is_windows() and _windows_task_installed():
            found.append(("windows", False))
    return found


def _windows_task_installed() -> bool:
    """Is a per-profile Windows gateway installed for the ACTIVE ``QUBIT_HOME``?"""
    from qubit_cli import gateway_windows as gww
    with contextlib.suppress(Exception):
        return bool(gww.is_task_registered() or gww.is_startup_entry_installed())
    return False


def _systemd_service_user(home: Path, services: list[tuple[str, bool]]) -> Optional[str]:
    """Read ``User=`` before migration removes a system-scope unit."""
    if ("systemd", True) not in services:
        return None
    from qubit_cli import gateway as gw
    from qubit_cli.gateway_migrate_guards import _home_env
    with _home_env(home):
        return gw._read_systemd_user_from_unit(gw.get_systemd_unit_path(system=True))


def _read_multiplex_flag(default_home: Path) -> bool:
    """The operator's EXPLICIT opt-in only. The unset default (on) is settled by the default gateway
    at boot and refused while a secondary runs its own gateway, so the plan reads it as "not yet
    multiplexed" and the plan proceeds."""
    from qubit_cli.gateway_multiplex_mode import explicit_multiplex_flag
    return explicit_multiplex_flag(default_home) is True


# --------------------------------------------------------------------------- preflight checks


def _profile_gateway_config(home: Path):
    """This profile's ``GatewayConfig`` read the way the multiplexer reads it."""
    from gateway.config import load_gateway_config
    from gateway.run import _profile_runtime_scope
    with _profile_runtime_scope(home):
        return load_gateway_config()


@contextlib.contextmanager
def _multiplex_read_mode() -> Iterator[None]:
    from agent.secret_scope import is_multiplex_active, set_multiplex_active
    previous = is_multiplex_active()
    set_multiplex_active(True)
    try:
        yield
    finally:
        set_multiplex_active(previous)


def _credential_probe(platform_config) -> SimpleNamespace:
    """Config-shaped stand-in for the adapter credential probe: token/api_key plus the id-style
    credentials adapters expose from ``extra``."""
    extra = getattr(platform_config, "extra", None) or {}
    return SimpleNamespace(
        token=getattr(platform_config, "token", None) or getattr(platform_config, "api_key", None),
        _app_id=extra.get("app_id"), _client_id=extra.get("client_id"), _bot_id=extra.get("bot_id"),
        _project_secret=extra.get("project_secret"), config=platform_config,
    )


def _credential_claims(config) -> dict[tuple, str]:
    """``(platform, fingerprint)`` for every enabled platform with a discoverable credential.

    Uses the vendored ``GatewayRunner`` static probe when present; skips claims it cannot fingerprint
    rather than inventing a hash."""
    claims: dict[tuple, str] = {}
    for platform, platform_config in config.platforms.items():
        if not platform_config.enabled:
            continue
        with contextlib.suppress(Exception):
            from gateway.run import GatewayRunner
            fp = GatewayRunner._adapter_credential_fingerprint(_credential_probe(platform_config))
            if fp is not None:
                claims[(platform.value, fp)] = platform.value
    return claims


def _credential_key_names(platform_value: str) -> str:
    """Env key NAMES (never values) that make ``platform_value`` connect as a bot; the platform id
    when the vendored tree carries no key registry."""
    with contextlib.suppress(Exception):
        from qubit_cli.profile_channels import credential_env_keys
        names = sorted(key for key, pid in credential_env_keys().items() if pid == platform_value)
        if names:
            return "/".join(names)
    return f"the {platform_value} token"


def duplicate_credential_lines(configs: list[tuple[str, object]]) -> list[str]:
    """One finding per platform credential two profiles both hold, with the remedy. Profile names +
    key names only, never a value or hash."""
    owners: dict[tuple, str] = {}
    lines: list[str] = []
    for name, cfg in configs:  # default first: it wins the claim, like at multiplexer startup
        for claim, platform_value in _credential_claims(cfg).items():
            owner = owners.setdefault(claim, name)
            if owner == name:
                continue
            key = _credential_key_names(platform_value)
            lines.append(
                f"Profiles '{owner}' and '{name}' both hold the same {platform_value} credential ({key}): "
                f"one platform token can serve only one gateway, so the bot answers from whichever profile "
                f"claims it first and the other's adapter is parked. Give '{name}' its own bot token, or remove "
                f"{key} from the profile that should not own it, then run {MIGRATE_COMMAND}."
            )
    return lines


def duplicate_credential_findings() -> list[str]:
    """The preflight's duplicate-credential check read straight from the local profile homes, for
    diagnostics that have no migration plan."""
    configs: list[tuple[str, object]] = []
    with _multiplex_read_mode():
        for name, home in _profile_homes():
            with contextlib.suppress(Exception):
                configs.append((name, _profile_gateway_config(home)))
    return duplicate_credential_lines(configs)


def _check_duplicate_credentials(plan: MigrationPlan, configs: dict[str, object]) -> None:
    """BLOCKER: the same bot credential configured on two profiles."""
    plan.blockers.extend(duplicate_credential_lines(
        [(p.name, configs[p.name]) for p in plan.profiles if p.name in configs]))


def platform_serves_profile_prefix(platform_value: str) -> bool:
    """True when the adapter for ``platform_value`` declares ``serves_profile_prefix`` (it answers
    ``/p/<profile>/...`` on the default listener). Read from the adapter CLASS, never a hand-kept list."""
    from gateway.platforms.base import BasePlatformAdapter

    def _declares(cls) -> bool:
        return isinstance(cls, type) and issubclass(cls, BasePlatformAdapter) and bool(
            getattr(cls, "serves_profile_prefix", False))

    with contextlib.suppress(Exception):
        from gateway.config import Platform
        from gateway.run import _BUILTIN_ADAPTERS, _builtin_adapter_import
        spec = _BUILTIN_ADAPTERS.get(Platform(platform_value))
        if spec is not None:
            adapter_cls, _ok = _builtin_adapter_import(spec[0], spec[1], spec[2])
            return _declares(adapter_cls)
    with contextlib.suppress(Exception):
        from qubit_cli.plugins import discover_plugins
        discover_plugins()  # idempotent
        from gateway.platform_registry import platform_registry
        entry = platform_registry.get(platform_value)
        if entry is not None:
            factory = entry.adapter_factory
            if _declares(factory):
                return True
            import importlib
            module = importlib.import_module(factory.__module__)
            return any(_declares(getattr(module, name)) for name in dir(module))
    return False


def _listener_url(default_cfg, platform_value: str, profile: str) -> str:
    from gateway.config import Platform
    extra = {}
    with contextlib.suppress(Exception):
        extra = (default_cfg.platforms.get(Platform(platform_value)) or SimpleNamespace(extra={})).extra or {}
    defaults = {"api_server": ("127.0.0.1", 8642), "webhook": ("0.0.0.0", 8644)}
    host, port = defaults.get(platform_value, ("<host>", "<port>"))
    host = extra.get("host") or host
    port = extra.get("port") or port
    tail = {"api_server": "/v1/...", "webhook": "/webhooks/<route>"}.get(platform_value, "/...")
    return f"http://{host}:{port}/p/{profile}{tail}"


def _check_secondary_port_binders(plan: MigrationPlan, configs: dict[str, object]) -> None:
    """BLOCKER when a secondary enables a port-binding platform with no ``/p/<profile>/`` ingress;
    NOTICE (URL changes) when the ingress exists."""
    from gateway.config import platform_binds_port
    default_cfg = configs.get("default")
    for profile in plan.secondaries:
        cfg = configs.get(profile.name)
        if cfg is None:
            continue
        for platform, platform_config in cfg.platforms.items():
            if not platform_config.enabled or not platform_binds_port(platform.value, platform_config.extra):
                continue
            if platform_serves_profile_prefix(platform.value):
                plan.notices.append(
                    f"Profile '{profile.name}': {platform.value} moves onto the default listener at "
                    f"{_listener_url(default_cfg, platform.value, profile.name)} (its key/secret is "
                    f"unchanged; update clients that call the old per-profile port)."
                )
            else:
                plan.blockers.append(
                    f"Profile '{profile.name}' enables {platform.value}, which binds its own port and has no "
                    f"/p/{profile.name}/ ingress on the default listener yet; the multiplexer would skip "
                    f"the whole profile. Disable it there (platforms.{platform.value}.enabled: false) or "
                    f"add a /p/<profile>/ ingress for it first."
                )


_PREFLIGHT_CHECKS: tuple[Callable[[MigrationPlan, dict[str, object]], None], ...] = (
    _check_duplicate_credentials,
    _check_secondary_port_binders,
)


def _load_profile_configs(plan: MigrationPlan) -> dict[str, object]:
    configs: dict[str, object] = {}
    with _multiplex_read_mode():
        for profile in plan.profiles:
            try:
                configs[profile.name] = _profile_gateway_config(profile.home)
            except Exception as exc:  # unreadable config is itself a blocker, not a crash
                plan.blockers.append(f"Profile '{profile.name}': could not load its gateway config ({exc}).")
    return configs


def build_migration_plan() -> MigrationPlan:
    """Enumerate profiles + their OWN gateway footprint, then run every preflight check."""
    from qubit_cli.gateway_multiplex_served import recorded_served_profiles
    default_home = _default_home()
    # Probed ONCE: every profile's ownership verdict is relative to the same host process.
    owner = _host_gateway_owner()
    profiles = []
    for name, home in _profile_homes():
        pid, services = _own_gateway_pid(home, owner), _installed_services(home)
        uid, runtime_home = _gateway_identity(home, pid, services)
        profiles.append(ProfileGateway(name=name, home=home, pid=pid, services=services,
                                       run_as_user=_systemd_service_user(home, services), uid=uid,
                                       runtime_home=None if runtime_home == home else runtime_home))
    plan = MigrationPlan(
        default_home=default_home, profiles=profiles,
        multiplex_flag_on=_read_multiplex_flag(default_home),
        live_served=recorded_served_profiles(default_home),
        manifest=_read_manifest(default_home),
    )
    plan.interrupted = plan.multiplex_flag_on and _manifest_not_yet_served(plan.manifest, plan.live_served)
    if len(plan.profiles) < 2:
        plan.notices.append("Only one profile exists: nothing to multiplex.")
        return plan
    configs = _load_profile_configs(plan)
    for check in _PREFLIGHT_CHECKS:
        check(plan, configs)
    from qubit_cli.gateway_migrate_guards import auto_migration_blockers
    # Notices, not blockers: the explicit command is the operator's decision.
    plan.notices.extend(f"Not migrated automatically by `qubit update`: {b}" for b in auto_migration_blockers(plan))
    plan.notices.append(
        "Profiles created after the migration are served by the running multiplexer as soon as "
        "they exist (it rescans profiles/ on create/delete and every 30s)."
    )
    return plan


# --------------------------------------------------------------------------- printing


def _print(lines: list[str]) -> None:
    for line in lines:
        print(line)


def _signalled_gateways(plan: MigrationPlan) -> list[str]:
    """Every RUNNING gateway process an apply would signal, in apply order. Derived from the steps an
    apply actually takes, not from "secondaries that happen to expose a PID"."""
    def _who(p: ProfileGateway, suffix: str = "") -> str:
        where = f"pid {p.pid}" if p.pid else f"supervised by {p.service_label()}"
        return f"{p.name} ({where}{suffix})"

    signalled = [_who(p) for p in plan.standalone_secondaries if p.pid or p.services]
    default = plan.default
    if default.services or default.pid:
        signalled.append(_who(default, ", restarted onto the new flag"))
    return signalled


def format_plan(plan: MigrationPlan, *, dry_run: bool) -> list[str]:
    head = "Migration plan (dry run — nothing changed)" if dry_run else "Migration plan"
    lines = [head, f"  default home: {plan.default_home}", "", "  profile      gateway pid   service"]
    for p in plan.profiles:
        lines.append(f"  {p.name:<12} {str(p.pid or '-'):<13} {p.service_label()}")
    lines.append("")
    if plan.already_multiplexed:
        lines.append("  ✓ The default gateway is already multiplexing"
                     + (f" (serving {', '.join(plan.live_served)})" if plan.live_served else " (flag on)") + ".")
        return lines
    if plan.interrupted:
        lines.append(f"  ↻ An earlier migration was interrupted before the default gateway came up "
                     f"(flag on, no live multiplexer; manifest {plan.default_home / MANIFEST_NAME}); this run resumes it.")
    elif plan.multiplex_flag_on and plan.standalone_secondaries:
        lines.append("  ↻ Half-migrated host: the flag is on, but the profile(s) below still own a "
                     "gateway. This run converges them.")
    steps = []
    signalled = _signalled_gateways(plan)
    for p in plan.standalone_secondaries:
        what = " + ".join(x for x in (f"stop pid {p.pid}" if p.pid else "", f"uninstall {p.service_label()}" if p.services else "") if x)
        steps.append(f"  - {p.name}: {what}")
    if len(plan.profiles) < 2:  # the notice already says "only one profile exists"
        return lines + _plan_tail(plan)
    if not steps:
        lines.append("  No secondary profile runs its own gateway; the only step is turning the flag on:")
    else:
        lines += ["  Steps:", *steps]
    lines.append(f"  - default: set gateway.multiplex_profiles: true in {plan.default_home / 'config.yaml'}")
    target = _resume_target(plan)[0] if plan.manifest is not None else plan.target_service_kind()
    lines.append(f"  - default: {'restart' if plan.default.has_gateway else 'start'} the gateway"
                 + (f" via {target[0]}" if target else " (detached)") + f", verify it serves {len(plan.profiles)} profiles")
    lines.append(f"  - record the previous state in {plan.default_home / MANIFEST_NAME} "
                 f"(used to undo a FAILED apply, and to resume this command after a crash)")
    if signalled:
        lines += ["",
                  "  ⚠ This SIGTERMs running gateway process(es): " + ", ".join(signalled) + ".",
                  "    They drain in-flight turns and exit; their profiles are served by the host "
                  "gateway afterwards."]
    return lines + _plan_tail(plan)


def _plan_tail(plan: MigrationPlan) -> list[str]:
    lines: list[str] = []
    if plan.blockers:
        if plan.manifest is None:
            lines += ["", "  ✗ Blockers (fix these first, nothing will be changed):"]
        else:
            lines += ["",
                      f"  ⚠ Blockers found, but a migration is already in progress on this host "
                      f"(manifest {_manifest_path(plan.default_home)}).",
                      "    Fix these afterwards:"]
        lines += [f"    • {b}" for b in plan.blockers]
    if plan.notices:
        lines += ["", "  Notices:"]
        lines += [f"    • {n}" for n in plan.notices]
    return lines


# --------------------------------------------------------------------------- manifest / resume


def _manifest_secondaries(manifest: dict) -> Optional[list[dict]]:
    """The manifest's secondary records, or None when the (hand-edited) manifest is malformed."""
    recs = manifest.get("secondaries", [])
    if not isinstance(recs, list) or not all(isinstance(r, dict) and r.get("profile") and r.get("home") for r in recs):
        return None
    return recs


def _service_from_dict(service) -> Optional[tuple[str, bool]]:
    if isinstance(service, dict) and service.get("kind"):
        return str(service["kind"]), bool(service.get("system"))
    return None


def _recorded_services(rec: dict) -> list[tuple[str, bool]]:
    """Every service a manifest record names: ``services`` or, in an older manifest, the single
    ``service``."""
    recorded = rec.get("services")
    if isinstance(recorded, list):
        return [s for s in (_service_from_dict(r) for r in recorded) if s is not None]
    service = _service_from_dict(rec.get("service"))
    return [service] if service is not None else []


def _recorded_run_as_user(rec: dict) -> Optional[str]:
    user = rec.get("run_as_user")
    return user if isinstance(user, str) and user else None


def _target_from_manifest(manifest: dict) -> tuple[Optional[tuple[str, bool]], Optional[str]]:
    """Service manager + ``User=`` the resumed default install should use, read from the recorded
    footprint."""
    recs = [r for r in (manifest.get("default"), *(_manifest_secondaries(manifest) or [])) if isinstance(r, dict)]
    target = next((s for r in recs for s in _recorded_services(r)), None)
    user = next((u for r in recs if (u := _recorded_run_as_user(r)) is not None), None)
    return target, user


def _manifest_path(default_home: Path) -> Path:
    return default_home / MANIFEST_NAME


def _read_manifest(default_home: Path) -> Optional[dict]:
    path = _manifest_path(default_home)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _manifest_not_yet_served(manifest: Optional[dict], live_served: Optional[list[str]]) -> bool:
    """The postcondition an apply waits for, re-derived from live state."""
    if manifest is None:
        return False
    recs = [r for r in (manifest.get("default"), *(_manifest_secondaries(manifest) or [])) if isinstance(r, dict)]
    migrated = {str(r.get("profile") or "default") for r in recs} | {"default"}
    return not migrated <= set(live_served or [])


def _resume_target(plan: MigrationPlan) -> tuple[Optional[tuple[str, bool]], Optional[str]]:
    """Service manager + ``User=`` an apply would use: the manifest's record wins on a resume."""
    target, run_as_user = plan.target_service_kind(), plan.target_run_as_user()
    if plan.manifest is None:
        return target, run_as_user
    m_target, m_user = _target_from_manifest(plan.manifest)
    return m_target or target, m_user or run_as_user


def _resume_findings(plan: MigrationPlan, target, run_as_user) -> list[str]:
    """Safety checks re-run on a RESUME and reported as findings instead of refusals."""
    findings = [*plan.blockers]
    apply_blocker = _preflight_apply(plan, target, run_as_user)
    if apply_blocker is not None:
        findings.append(apply_blocker)
    if not findings:
        return []
    return ["  ⚠ Preflight findings — resuming anyway (this host is mid-migration):",
            *[f"    • {f}" for f in findings]]


def _preflight_apply(plan: MigrationPlan, target: Optional[tuple[str, bool]],
                     run_as_user: Optional[str]) -> Optional[str]:
    """A failure of the destructive phase that is knowable from the plan alone. Mirrors the checks
    ``systemd_install``/``_service_call`` make on a system unit and the config write's read-guard."""
    from qubit_cli import gateway as gw
    from qubit_cli.config import require_readable_config_before_write
    try:
        require_readable_config_before_write(plan.default_home / "config.yaml")
    except Exception as exc:
        return f"default: config.yaml cannot be updated ({exc})"
    touches_system_unit = target == ("systemd", True) or any(p.has_system_unit for p in plan.standalone_secondaries)
    if touches_system_unit:
        try:
            gw._require_root_for_system_service("migration")
        except Exception as exc:
            return str(exc)
    if plan.default.service is None and target == ("systemd", True):
        if run_as_user is None:
            try:
                gw._system_service_identity()  # the implicit-root refusal, before anything is removed
            except ValueError as exc:
                return f"default: {exc}"
        else:
            import pwd
            try:
                pwd.getpwnam(run_as_user)
            except KeyError:
                return f"default: the recorded service user '{run_as_user}' does not exist on this host"
    return None


# --------------------------------------------------------------------------- command


def cmd_migrate(args) -> None:
    """``qubit gateway migrate``: print the read-only plan + preflight, then refuse the destructive
    apply as an operator-owned surface."""
    dry_run = bool(getattr(args, "dry_run", False))
    plan = build_migration_plan()
    _print(format_plan(plan, dry_run=dry_run))
    if dry_run:
        return
    # The mutating step stops/uninstalls services. Typed refusal, never a silent no-op.
    print()
    _print([f"✗ {APPLY_REFUSAL}",
            f"  (code: {APPLY_REFUSAL_CODE})"])
    raise SystemExit(3)
