"""Route supported ``qubit <family> <sub>`` pairs to the vendored fork.

Per the owner runtime amendment (2026-09-11) the Qubit CLI executes from the
Qubit source tree. Many families whose upstream implementation is the fork are
still open in the Qubit gap ledger for the CLI area; this module forwards the
pairs the Qubit parser does **not** already own straight into
:func:`voicebridge.qubit_runtime.run`, the same in-process entry point used by
``qubit agent`` and the dashboard ASGI bridge.

Design rules:

* Only ``(family, subcommand)`` pairs absent from :func:`build_parser` are
  forwarded, so a native Qubit command is never shadowed (a regression test
  enforces this against the live parser).
* ``argv`` after the subcommand is passed to the fork verbatim; upstream
  argparse help, errors, persistence and recovery behavior are preserved.
* Qubit-only global flags (``--config/--no-hotkey/--dry-run/--mute``) are
  stripped because the fork has no equivalent and would reject them; the fork
  resolves its own config (``QUBIT_HOME``/``~/.hermes``).
"""
from __future__ import annotations

import threading
from typing import Any, Dict, NamedTuple, Optional

# (qubit family, qubit subcommand) -> Qubit argv prefix. Every entry is a
# read-only or diagnostic surface: mutating/credentialed upstream commands are
# intentionally not routed here (they stay behind the Qubit policy/confirm
# surfaces owned by the backend agents, or the CONFIRM_TABLE below).
FORWARD_TABLE = {
    ("schedules", "doctor"): ("cron", "doctor"),
    ("schedules", "incidents"): ("cron", "incidents"),
    ("schedules", "status"): ("cron", "status"),
    ("schedules", "runs"): ("cron", "runs"),
    ("sessions", "stats"): ("sessions", "stats"),
    ("sessions", "pinned"): ("sessions", "pinned"),
    ("mcp", "list"): ("mcp", "list"),
    ("plugins", "capabilities"): ("plugins", "capabilities"),
    ("plugins", "compat"): ("plugins", "compat"),
    ("skills", "audit"): ("skills", "audit"),
    ("skills", "list-modified"): ("skills", "list-modified"),
    ("flags", "path"): ("config", "path"),
    ("flags", "env-path"): ("config", "env-path"),
    ("flags", "show"): ("config", "show"),
    ("flags", "check"): ("config", "check"),
    ("monitor", "status"): ("monitoring", "status"),
    ("frontier", "list"): ("fallback", "list"),
    ("setup", "doctor"): ("doctor",),
    # Wave 2: fork reads for the remaining cli rows whose Qubit family does
    # not share the Qubit family name (ledger `cli:curator`, `cli:learning`).
    # Only read-only/diagnostic subcommands are routed; the fork's mutating
    # subcommands stay behind the Qubit confirm/audit surfaces (owned by the
    # backend/voice agents) and are deliberately not forwarded raw.
    ("lessons", "status"): ("curator", "status"),
    ("lessons", "ledger"): ("curator", "ledger"),
    ("lessons", "usage"): ("curator", "usage"),
    ("lessons", "list-archived"): ("curator", "list-archived"),
    ("lessons", "list-unmanaged"): ("curator", "list-unmanaged"),
    ("mcp", "catalog"): ("mcp", "catalog"),
    # Wave 4: remaining fork reads for the still-open cli families.
    ("plugins", "info"): ("plugins", "info"),
    ("frontier", "ls"): ("fallback", "ls"),
    # Wave 1 Qubit CLI parity: read-only fork subcommands whose Qubit family
    # does not expose a native subcommand.
    ("tools", "list"): ("tools", "list"),
    # Wave 5 Qubit CLI closure: read-only/diagnostic fork subcommands whose
    # Qubit family does not already own the subcommand natively. Each pair is
    # exercised by tests/test_cli_closure_wave5.py (and the fork-parser check
    # proves the target exists); mutating/credentialed siblings stay in
    # CONFIRM_TABLE and never reach the fork without the shared guard.
    ("approvals", "suggest"): ("approvals", "suggest"),
    ("auth", "list"): ("auth", "list"),
    ("schedules", "notepad"): ("cron", "notepad"),
    # `gateway status/start/stop/restart` are Qubit-native (Owner Decision #10):
    # they drive the real Qubit gateway supervisor, so they are NOT forwarded to
    # the vendored messaging gateway.
    ("mcp", "config"): ("mcp", "config"),
    ("mcp", "ls"): ("mcp", "ls"),
    ("mcp", "picker"): ("mcp", "picker"),
    ("plugins", "search"): ("plugins", "search"),
    ("profile", "alias"): ("profile", "alias"),
    ("profile", "describe"): ("profile", "describe"),
    ("profile", "info"): ("profile", "info"),
    ("sessions", "browse"): ("sessions", "browse"),
    ("skills", "browse"): ("skills", "browse"),
    ("skills", "diff"): ("skills", "diff"),
    ("skills", "inspect"): ("skills", "inspect"),
    ("skills", "search"): ("skills", "search"),
    ("sync", "device"): ("sync", "device"),
    ("sync", "status"): ("sync", "status"),
}

# Subcommand sentinel meaning "every value of this axis".
ANY = "*"

# MCP read subcommands the Qubit CLI now owns natively on the ONE canonical
# registry (``voicebridge.mcp_servers``, ``<state_dir>/mcp_servers.json``).
# The ``FORWARD_TABLE`` rows below map them to the vendored fork, whose
# ``cmd_mcp_list`` reads the fork's ``config.yaml mcp_servers`` — a second store
# a dashboard-created server never reaches. ``route`` therefore declines these
# pairs so the native parser serves them from the canonical registry, the same
# authority ``qubit mcp status``/``invoke`` use. The table rows are retained
# because the generated ledger/source-inventory classify the surface from them.
NATIVE_MCP_READS = frozenset({
    ("mcp", "list"), ("mcp", "config"), ("mcp", "ls"), ("mcp", "picker"),
})

# Whole Qubit families backed by the vendored fork:
#   family -> (fork_family, readonly, confirm)
# ``readonly`` subcommands forward verbatim (the empty string is the bare
# family: help/default output). ``confirm`` subcommands forward only with
# Qubit's explicit ``--confirm`` (the flag is stripped before the fork sees it).
# A subcommand in neither set is a typed refusal, so no mutating upstream
# surface is reachable through an unrestricted bypass.
FAMILY_FORWARD = {
    "bundles": ("bundles", frozenset({"list", "show", ""}),
                frozenset({"create", "delete", "reload"})),
    "claw": ("claw", frozenset({""}),
             frozenset({"migrate", "cleanup", "clean"})),
    "console": ("console", frozenset(), frozenset({ANY})),
    "debug": ("debug", frozenset({""}), frozenset({"share", "delete"})),
    "desktop": ("desktop", frozenset(), frozenset({ANY})),
    "dump": ("dump", frozenset({ANY}), frozenset()),
    "gui": ("gui", frozenset(), frozenset({ANY})),
    "memory-graph": ("memory-graph", frozenset({"list", ""}),
                     frozenset({"edit", "delete"})),
    "moa": ("moa", frozenset({"list", "ls", ""}),
            frozenset({"configure", "config", "delete", "rm"})),
    "pause": ("pause", frozenset(), frozenset()),
    "portal": ("portal", frozenset({""}),
               frozenset({"login", "open", "tools", "info", "status"})),
    "prompt-size": ("prompt-size", frozenset({ANY}), frozenset()),
    "worktree": ("worktree", frozenset({"list", "ls", "audit", ""}),
                 frozenset({"prune"})),
}

# Wave 6: first-class coverage for the fork families that were reachable only
# through the raw ``qubit agent <args>`` passthrough. These families are NOT
# declared by the Qubit parser, so they live in their own table: ``route_family``
# consults both, but the wave1 ``FAMILY_FORWARD`` contract (its keys must be
# live Qubit parser families — see ``tests/test_cli_parity_wave1.py``) stays
# intact and no native family is shadowed.
#
# Each family is classified subcommand-by-subcommand from the vendored parser
# help (``qubit_runtime/qubit_cli``): read-only/diagnostic subcommands forward
# verbatim; mutating/credentialed subcommands require Qubit's ``--confirm`` and
# the shared dispatch guard; an unlisted subcommand is a typed
# ``FORK_SUB_UNCLASSIFIED`` refusal, never an unrestricted bypass.
FAMILY_FORWARD_WAVE6 = {
    "curator": ("curator",
                frozenset({"status", "usage", "ledger", "list-archived",
                           "list-unmanaged", ""}),
                frozenset({"run", "pause", "resume", "pin", "unpin", "adopt",
                           "restore", "archive", "prune", "backup", "rollback",
                           "purge"})),
    # `journey`'s parser is also registered as the `learning` and
    # `memory-graph` aliases; the family name is forwarded verbatim so the
    # fork resolves the alias it was invoked with.
    "journey": ("journey", frozenset({"list", ""}),
                frozenset({"delete", "edit"})),
    "learning": ("learning", frozenset({"list", ""}),
                 frozenset({"delete", "edit"})),
    "pets": ("pets", frozenset({"list", "show", "doctor", ""}),
             frozenset({"install", "select", "off", "scale", "remove"})),
    # External secret-manager integration is credentialed end to end (the only
    # subcommands are Bitwarden/1Password flows), so every invocation is
    # confirm-gated — there is no read-only surface to forward raw.
    "secrets": ("secrets", frozenset(), frozenset({ANY})),
    "security": ("security", frozenset({"audit", ""}), frozenset()),
    "skin": ("skin", frozenset({"list", ""}), frozenset({"use", "set"})),
    "slack": ("slack", frozenset(), frozenset({"manifest"})),
    "fallback": ("fallback", frozenset({"list", "ls", ""}),
                 frozenset({"add", "remove", "rm", "clear"})),
    # `migrate` is dry-run by default; `xai` is still a config rewrite surface
    # and stays confirm-gated. The bare family only prints usage.
    "migrate": ("migrate", frozenset({""}), frozenset({"xai", "relay"})),
    "monitoring": ("monitoring", frozenset({"status", ""}), frozenset()),
    # `insights` is a read-only session-history analysis (leaf command).
    "insights": ("insights", frozenset({""}), frozenset()),
    # `import` restores a backup archive into the Qubit home (overwriting
    # files): a mutating leaf, so every invocation is confirm-gated.
    "import": ("import", frozenset(), frozenset({ANY})),
    # Wave 9: `vault` is the local encrypted autofill credential vault (ported
    # from the reference 0.21.4). `list`/`sources` are read-only metadata probes;
    # `add`/`rm` mutate the encrypted store, so they require Qubit's `--confirm`
    # and the shared dispatch guard. Secrets are never printed by any subcommand.
    "vault": ("vault", frozenset({"list", "sources", ""}),
              frozenset({"add", "rm"})),
    # Wave 6 owner-decision follow-up: `codex-runtime migrate` rewrites
    # ~/.codex/config.toml, so it is confirm-gated (bare `codex-runtime` only
    # prints help).
    #
    # `usage` is deliberately ABSENT: it is a Qubit-native top-level family
    # (``voicebridge/cli.py`` declares ``usage`` with ``analytics``/``telemetry``
    # and ``_INTEGRATION`` routes it to the local, redacted usage ledger +
    # telemetry gate). Routing it here shadowed that native family and turned
    # ``qubit usage telemetry`` into ``FORK_SUB_UNCLASSIFIED``. The ported
    # vendored account-usage report stays reachable as ``qubit agent usage``
    # (and is covered by tests/test_cli_ports_wave6.py).
    "codex-runtime": ("codex-runtime", frozenset({""}),
                      frozenset({"migrate"})),
}

# ---------------------------------------------------------------------------
# Wave 6: families that must NEVER be forwarded to the vendored fork.
#
# These are either Qubit-native authorities (gateway serve, session resume,
# the dashboard SPA, product upgrade, package uninstall, auth login/logout,
# msg send, the emergency-stop resume) or interactive credential/pairing
# flows. Forwarding them raw would run a second runtime/session/ESTOP
# authority or shadow a Qubit-owned surface, so each returns a typed refusal
# that names the Qubit-native equivalent. `qubit <family>` therefore never
# falls through to argparse's ``invalid choice`` and never silently bypasses
# Qubit policy.
#
# `chat` is intentionally absent: it is now a first-class classified forward
# (`chat_route` below) to the vendored interactive chat — the real working
# counterpart the old `QUBIT_NATIVE_CHAT` refusal only named.
# ---------------------------------------------------------------------------
NATIVE_REFUSALS = {
    "serve": ("QUBIT_NATIVE_SERVE",
              "qubit serve is not forwarded: the Qubit gateway is `qubit "
              "gateway serve` (or the `qubit webui` dashboard)"),
    "resume": ("QUBIT_NATIVE_RESUME",
               "qubit resume would engage a second emergency-stop authority; "
               "resume through `qubit power enable --confirm` (or `qubit "
               "autonomy resume`)"),
    "dashboard": ("QUBIT_NATIVE_DASHBOARD",
                  "qubit dashboard is not forwarded: Qubit owns the SPA at "
                  "`qubit webui`; dashboard registration is the native "
                  "`qubit webui register` confirm-gated flow"),
    # `update` is intentionally absent: Owner Decision #11 makes it a native
    # Qubit family (`qubit update --check|--apply|--rollback`), so it must not be
    # intercepted as a refusal or forwarded to the vendored fork here.
    "uninstall": ("QUBIT_NATIVE_UNINSTALL",
                  "qubit uninstall is not forwarded: use `qubit package "
                  "uninstall` (confirm-gated)"),
    "login": ("QUBIT_NATIVE_LOGIN",
              "qubit login is deprecated in the fork and not forwarded: use "
              "`qubit auth login`"),
    "logout": ("QUBIT_NATIVE_LOGOUT",
               "qubit logout clears credentials and is not forwarded: use "
               "`qubit auth logout`"),
    "send": ("QUBIT_NATIVE_SEND",
             "qubit send reuses gateway credentials and is not forwarded: use "
             "`qubit msg send`"),
    "whatsapp": ("QUBIT_NATIVE_WHATSAPP",
                 "qubit whatsapp is an interactive pairing flow and is not "
                 "forwarded: configure it through `qubit channels`"),
    "whatsapp-cloud": ("QUBIT_NATIVE_WHATSAPP_CLOUD",
                       "qubit whatsapp-cloud is an interactive setup flow and "
                       "is not forwarded: configure it through "
                       "`qubit channels`"),
}

# ---------------------------------------------------------------------------
# Wave 6: OpenClaw top-level CLI families. OpenClaw is an external reference
# implementation, not a runtime Qubit launches: these families cannot be
# reached through the vendored fork at all, so they are never routed through
# `qubit agent`. Each maps to either a Qubit-native counterpart
# (``OPENCLAW_NATIVE_EQUIVALENT``) or a typed ``OPERATOR_ONLY``/``MISSING``
# envelope naming what the caller should use instead.
# ---------------------------------------------------------------------------
OPENCLAW_NATIVE_EQUIVALENT = "OPENCLAW_NATIVE_EQUIVALENT"
OPENCLAW_OPERATOR_ONLY = "OPENCLAW_OPERATOR_ONLY"
OPENCLAW_MISSING = "OPENCLAW_MISSING"
OPENCLAW_FAMILIES = {
    "agents": (OPENCLAW_NATIVE_EQUIVALENT,
               "The `agents` family is not a Qubit surface; use the one Qubit "
               "runtime via `qubit agent <args>`"),
    "audit": (OPENCLAW_NATIVE_EQUIVALENT,
              "The `audit` family has no Qubit forward; read the canonical "
              "audit trail with `qubit receipts` / `qubit actions list`"),
    "devices": (OPENCLAW_NATIVE_EQUIVALENT,
                "The `devices` family has no Qubit forward; use `qubit device "
                "status` / `qubit device enroll`"),
    "nodes": (OPENCLAW_NATIVE_EQUIVALENT,
              "The `nodes` family has no Qubit forward; use `qubit node list` / "
              "`qubit node status`"),
    "onboard": (OPENCLAW_NATIVE_EQUIVALENT,
                "The `onboard` family has no Qubit forward; use `qubit setup` "
                "(or `qubit brain onboard`)"),
    "sandbox": (OPENCLAW_OPERATOR_ONLY,
                "The `sandbox` family needs a cloud worker service "
                "Qubit does not run; Qubit runs one local brokered sandbox "
                "(`qubit runtime status`)"),
    "system": (OPENCLAW_NATIVE_EQUIVALENT,
               "The `system` family has no Qubit forward; use `qubit status` / "
               "`qubit doctor`"),
    "terminal": (OPENCLAW_NATIVE_EQUIVALENT,
                 "The `terminal` family has no Qubit forward; use `qubit "
                 "console` (brokered shell)"),
    "worktrees": (OPENCLAW_NATIVE_EQUIVALENT,
                  "The `worktrees` family has no Qubit forward; use `qubit "
                  "worktree list` / `qubit worktree add|remove|switch "
                  "--confirm`"),
}

# Mutating/credentialed (qubit family, sub) -> fork argv prefix. These are NOT
# in FORWARD_TABLE: they require the caller's explicit ``--confirm`` before the
# fork runs, so the unsafe upstream surface is never an unrestricted bypass.
CONFIRM_TABLE = {
    ("flags", "edit"): ("config", "edit"),
    ("schedules", "edit"): ("cron", "edit"),
    ("schedules", "run"): ("cron", "run"),
    ("schedules", "tick"): ("cron", "tick"),
    ("mcp", "test"): ("mcp", "test"),
    ("plugins", "enable"): ("plugins", "enable"),
    ("plugins", "remove"): ("plugins", "remove"),
    ("sessions", "clean-markers"): ("sessions", "clean-markers"),
    ("sessions", "export"): ("sessions", "export"),
    ("sessions", "optimize-storage"): ("sessions", "optimize-storage"),
    ("sessions", "pin"): ("sessions", "pin"),
    ("tools", "disable"): ("tools", "disable"),
    ("tools", "enable"): ("tools", "enable"),
    ("tools", "post-setup"): ("tools", "post-setup"),
    # Wave 5 Qubit CLI closure: mutating/credentialed fork subcommands. These
    # are NOT read-only: without Qubit's explicit ``--confirm`` the fork is
    # never invoked; with it the shared dispatch guard (single-use
    # confirmation + emergency-stop probe + PolicyEngine audit) authorizes.
    ("approvals", "test"): ("approvals", "test"),
    ("auth", "add"): ("auth", "add"),
    ("auth", "priority"): ("auth", "priority"),
    ("auth", "refresh"): ("auth", "refresh"),
    ("auth", "remove"): ("auth", "remove"),
    ("auth", "reset"): ("auth", "reset"),
    ("auth", "spotify"): ("auth", "spotify"),
    ("schedules", "remove"): ("cron", "remove"),
    ("webui", "register"): ("dashboard", "register"),
    ("frontier", "add"): ("fallback", "add"),
    ("frontier", "clear"): ("fallback", "clear"),
    ("frontier", "remove"): ("fallback", "remove"),
    ("gateway", "enroll"): ("gateway", "enroll"),
    ("gateway", "migrate-legacy"): ("gateway", "migrate-legacy"),
    ("gateway", "setup"): ("gateway", "setup"),
    ("gateway", "uninstall"): ("gateway", "uninstall"),
    ("mcp", "install"): ("mcp", "install"),
    ("mcp", "login"): ("mcp", "login"),
    ("mcp", "reauth"): ("mcp", "reauth"),
    ("mcp", "rm"): ("mcp", "rm"),
    ("mcp", "serve"): ("mcp", "serve"),
    ("memory", "off"): ("memory", "off"),
    ("memory", "reset"): ("memory", "reset"),
    ("memory", "setup"): ("memory", "setup"),
    ("product", "xai"): ("migrate", "xai"),
    ("pairing", "clear-pending"): ("pairing", "clear-pending"),
    ("plugins", "disable"): ("plugins", "disable"),
    ("plugins", "pack"): ("plugins", "pack"),
    ("profile", "create"): ("profile", "create"),
    ("profile", "delete"): ("profile", "delete"),
    ("profile", "export"): ("profile", "export"),
    ("profile", "import"): ("profile", "import"),
    ("profile", "rename"): ("profile", "rename"),
    ("profile", "update"): ("profile", "update"),
    ("profile", "use"): ("profile", "use"),
    ("profile", "migrate-identity"): ("profile", "migrate-identity"),
    ("profile", "purge-identity"): ("profile", "purge-identity"),
    ("brain", "bitwarden"): ("secrets", "bitwarden"),
    ("brain", "onepassword"): ("secrets", "onepassword"),
    ("sessions", "import"): ("sessions", "import"),
    ("sessions", "recover"): ("sessions", "recover"),
    ("sessions", "repair"): ("sessions", "repair"),
    ("sessions", "repair-routing"): ("sessions", "repair-routing"),
    ("sessions", "retitle-skills"): ("sessions", "retitle-skills"),
    ("sessions", "unpin"): ("sessions", "unpin"),
    ("skills", "config"): ("skills", "config"),
    ("skills", "opt-in"): ("skills", "opt-in"),
    ("skills", "opt-out"): ("skills", "opt-out"),
    ("skills", "repair-official"): ("skills", "repair-official"),
    ("skills", "reset"): ("skills", "reset"),
    ("skills", "snapshot"): ("skills", "snapshot"),
    ("skills", "tap"): ("skills", "tap"),
    ("skills", "trust"): ("skills", "trust"),
    ("skills", "uninstall"): ("skills", "uninstall"),
    ("skills", "untrust"): ("skills", "untrust"),
    ("theme", "use"): ("skin", "use"),
    ("msg", "manifest"): ("slack", "manifest"),
    ("sync", "disable"): ("sync", "disable"),
    ("sync", "enable"): ("sync", "enable"),
    ("sync", "now"): ("sync", "now"),
    ("sync", "propose"): ("sync", "propose"),
    ("sync", "pull"): ("sync", "pull"),
    ("sync", "push"): ("sync", "push"),
    ("webhook", "subscribe"): ("webhook", "subscribe"),
}


class FamilyRoute(NamedTuple):
    """One resolved route for a ``qubit <family> [...]`` call."""

    kind: str            # "forward" | "needs-confirm" | "refused"
    argv: list           # fork argv when kind == "forward"; else the gated argv
    code: str = ""
    detail: str = ""


# Qubit launcher global flags that do not exist in the upstream parser.
# ``--dry-run`` is deliberately NOT stripped: the fork's own mutating commands
# (claw migrate, worktree prune) use it, and dropping it could turn a preview
# into a real apply.
_QUBIT_GLOBALS = frozenset({"--config", "--no-hotkey", "--dry-run", "--mute"})
_FAMILY_GLOBALS = frozenset({"--config", "--no-hotkey", "--mute", "--confirm"})

# ---------------------------------------------------------------------------
# Shared dispatch authority for confirmed fork mutations (Wave 4, gap G1).
#
# ``--confirm`` used to be a boolean presence check that forwarded straight to
# the fork with no PolicyEngine Decision, no e-stop probe, and no audit. A
# confirmed mutation now flows through the SAME dispatch guard (single-use,
# expiring, target-bound confirmation + shared PolicyEngine) the worker paths
# use, after the ONE durable emergency-stop latch is probed. There is no
# unrestricted bypass: an unavailable guard or read failure refuses.
# ---------------------------------------------------------------------------
FORK_CAPABILITY = "fork.forward"
FORK_SCOPE = "fork"
# One engine per state tree: the confirmation store is durable
# (actions.json) when a real config exists, so a fork mutation confirmed by
# ``qubit <family> ... --confirm`` in one CLI process is bound to the same
# store the next process reads. Keyed by the backing store path so distinct
# hermetic configs (tests) never share a cross-process confirmation store.
_FORK_ENGINES: Dict[str, Any] = {}
_FORK_ENGINE_LOCK = threading.Lock()


def _engine_key(store: Any) -> str:
    pstore = getattr(store, "_pstore", None)
    path = getattr(pstore, "path", None)
    return str(path) if path else "in-memory"


def _get_fork_engine(cfg: Any = None):
    """The shared engine for fork mutations (one registry/store per state)."""
    from .broker import confirmation_store_for
    from .capabilities import CapabilityRegistry, CapabilitySpec, PolicyEngine, Tier

    store = confirmation_store_for(cfg, default_ttl_s=120.0)
    key = _engine_key(store)
    with _FORK_ENGINE_LOCK:
        engine = _FORK_ENGINES.get(key)
        if engine is None:
            reg = CapabilityRegistry()
            reg.register(CapabilitySpec(
                name=FORK_CAPABILITY, version="1.0.0", tier=Tier.MUTATING,
                summary="forward a mutating fork subcommand",
                scopes=(FORK_SCOPE,), timeout_s=900.0))
            engine = PolicyEngine(registry=reg, confirmations=store)
            _FORK_ENGINES[key] = engine
        return engine


def _fork_cfg():
    try:
        from .config import Config

        return Config.load()
    except Exception:
        return None


def guard_confirm(argv: list, family: str, sub: str,
                  cfg=None) -> Optional[FamilyRoute]:
    """Authorize one ``--confirm`` fork mutation through the shared contract.

    Returns ``None`` when the route may forward, or a typed ``refused`` route.
    Fail-closed at every step: the single durable emergency-stop latch is
    probed first, then the shared :class:`WorkerGuard` issues and consumes a
    short-lived single-use confirmation (issuer ``user``) and records the
    PolicyEngine Decision. A read error, an unavailable guard, or any policy
    denial refuses — never forwards.
    """
    from .logging_setup import log_event

    argv = list(argv or [])
    label = f"qubit {family} {sub}".strip()
    resolved_cfg = cfg if cfg is not None else _fork_cfg()
    if resolved_cfg is None:
        return FamilyRoute(
            "refused", [], code="FORK_GUARD_UNAVAILABLE",
            detail=f"{label}: shared policy state is unavailable; refused")
    from .broker import estop_engaged

    try:
        latched = bool(estop_engaged(resolved_cfg))
    except Exception:
        latched = True                       # probe failure is fail-closed
    if latched:
        log_event("cli.fork.estop", family=family, sub=sub)
        return FamilyRoute(
            "refused", [], code="EMERGENCY_STOP",
            detail=f"{label}: emergency stop is latched")
    from .dispatch_guard import DispatchBlocked, WorkerGuard
    from .redaction import redact_obj

    try:
        guard = WorkerGuard(resolved_cfg, engine=_get_fork_engine(resolved_cfg))
        guard.authorize(FORK_CAPABILITY, {"argv": redact_obj(argv)},
                        source="user", correlation_id=f"fork:{family}:{sub}")
    except DispatchBlocked as exc:
        return FamilyRoute(
            "refused", [], code=exc.code,
            detail=f"{label}: {exc.detail or exc.code}")
    except Exception:
        return FamilyRoute(
            "refused", [], code="FORK_GUARD_UNAVAILABLE",
            detail=f"{label}: shared dispatch guard unavailable; refused")
    log_event("cli.fork.confirmed", family=family, sub=sub)
    return None


def _strip_qubit_globals(argv: list) -> list:
    """Drop Qubit-only global flags (and ``--config``'s value) from ``argv``."""
    out: list = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        name = tok.split("=", 1)[0]
        if name in _QUBIT_GLOBALS:
            if name == "--config" and "=" not in tok and i + 1 < len(argv):
                i += 2
                continue
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def translate(argv: list) -> Optional[list]:
    """Return the fork argv for a ``qubit <family> <sub>`` call, or ``None``.

    ``argv`` is the raw Qubit argument vector without the program name.
    """
    raw = list(argv or [])
    if len(raw) < 2:
        return None
    mapped = FORWARD_TABLE.get((raw[0], raw[1]))
    if mapped is None:
        return None
    return [*mapped, *_strip_qubit_globals(raw[2:])]


def maybe_forward(argv: list) -> Optional[int]:
    """Forward to the vendored fork when ``argv`` matches the table.

    Returns the fork's exit code, or ``None`` when the call is not a forwarded
    family/subcommand (the caller then continues with the Qubit parser). Fork
    ``argparse`` errors propagate as ``SystemExit`` — upstream behavior.
    """
    mapped = translate(argv)
    if mapped is None:
        return None
    from . import qubit_runtime

    return qubit_runtime.run(mapped)


def _strip_family_globals(argv: list) -> list:
    """Drop Qubit-only family flags (``--confirm``/``--config``/…) from argv."""
    out: list = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        name = tok.split("=", 1)[0]
        if name in _FAMILY_GLOBALS:
            if name == "--config" and "=" not in tok and i + 1 < len(argv):
                i += 2
                continue
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


def route_family(argv: list, cfg=None) -> Optional[FamilyRoute]:
    """Resolve a whole-family ``qubit <family> [...]`` call, or ``None``.

    Read-only subcommands/help forward to the fork verbatim; mutating
    subcommands become ``needs-confirm`` without Qubit's ``--confirm`` and
    forward (with the flag stripped) when it is present AND the shared
    dispatch guard authorizes it; everything else is a typed refusal.
    """
    raw = list(argv or [])
    if not raw:
        return None
    family = raw[0]
    entry = FAMILY_FORWARD.get(family) or FAMILY_FORWARD_WAVE6.get(family)
    if entry is None:
        return None
    fork_family, readonly, confirm = entry
    tail = raw[1:]
    # Upstream help is a read-only surface and is always preserved.
    if any(tok in ("-h", "--help") for tok in tail):
        return FamilyRoute("forward",
                           [fork_family, *_strip_family_globals(tail)])
    if family == "pause":
        # The fork's ESTOP is a second emergency-stop authority; Qubit owns one.
        # With --confirm this defers to Qubit's native `qubit pause` handler
        # (the shared PowerControl latch); a bare `qubit pause` stays a typed
        # refusal so the fork sentinel is never engaged.
        if "--confirm" in raw:
            return None
        return FamilyRoute(
            "refused", [], code="ESTOP_QUBIT_OWNED",
            detail=("qubit pause maps to the Qubit emergency stop, not a second "
                    "fork sentinel; use `qubit pause --confirm` (or "
                    "`qubit power emergency-stop --confirm`)"))
    sub = tail[0] if tail and not tail[0].startswith("-") else ""
    if family == "worktree" and sub in ("add", "remove", "switch") \
            and "--confirm" in raw:
        # Qubit-native, confirmation-gated git worktree lifecycle; the pinned
        # fork only owns list/prune. Unconfirmed calls still hit the typed
        # refusal below, so no unconfirmed write ever runs.
        return None
    label = f"qubit {family} {sub}".strip()
    if ANY in readonly or sub in readonly:
        return FamilyRoute("forward",
                           [fork_family, *_strip_family_globals(tail)])
    if ANY in confirm or sub in confirm:
        args = _strip_family_globals(tail)
        if "--confirm" not in raw:
            return FamilyRoute(
                "needs-confirm", [fork_family, *args], code="CONFIRM_REQUIRED",
                detail=f"{label} requires --confirm (explicit human authorization)")
        denied = guard_confirm(raw, family, sub, cfg)
        if denied is not None:
            return denied
        return FamilyRoute("forward", [fork_family, *args])
    return FamilyRoute(
        "refused", [], code="FORK_SUB_UNCLASSIFIED",
        detail=(f"{label} is not a classified read-only surface; a mutating "
                "fork subcommand is never exposed as an unrestricted bypass"))


def confirm_pair(argv: list, cfg=None) -> Optional[FamilyRoute]:
    """Resolve a ``CONFIRM_TABLE`` pair, or ``None``.

    Without Qubit's ``--confirm`` the fork is never invoked; the caller gets a
    typed ``needs-confirm`` envelope carrying the exact argv that would run.
    With it, the shared dispatch guard (single-use confirmation + e-stop +
    policy audit) must authorize before the pair forwards.
    """
    raw = list(argv or [])
    if len(raw) < 2:
        return None
    mapped = CONFIRM_TABLE.get((raw[0], raw[1]))
    if mapped is None:
        return None
    args = _strip_family_globals(raw[2:])
    label = f"qubit {raw[0]} {raw[1]}"
    if "--confirm" not in raw:
        return FamilyRoute(
            "needs-confirm", [*mapped, *args], code="CONFIRM_REQUIRED",
            detail=f"{label} requires --confirm (explicit human authorization)")
    denied = guard_confirm(raw, raw[0], raw[1], cfg)
    if denied is not None:
        return denied
    return FamilyRoute("forward", [*mapped, *args])


def chat_route(argv: list) -> Optional[FamilyRoute]:
    """Resolve ``qubit chat [...]`` to the vendored interactive chat, or ``None``.

    ``qubit chat`` is the Qubit CLI equivalent of the vendored
    ``qubit agent chat``: it delegates the whole invocation to the same
    in-process runtime, so upstream argparse help, session persistence,
    provider resolution, one-shot mode, and shutdown behavior are preserved
    verbatim. It is a first-class classified forward — never the raw
    ``qubit agent`` passthrough.

    The interactive/headless gate lives in :func:`voicebridge.cli.main`: a
    non-TTY invocation with no one-shot selector returns a typed
    ``CHAT_REQUIRES_TTY`` result instead of launching a TUI that cannot read
    input. Qubit-only launcher globals are stripped here, exactly as for the
    other classified forwards.
    """
    raw = list(argv or [])
    if not raw or raw[0] != "chat":
        return None
    return FamilyRoute("forward", ["chat", *_strip_family_globals(raw[1:])])


def family_refusal(argv: list) -> Optional[FamilyRoute]:
    """Typed refusal for families that must never be forwarded to the fork.

    Covers the Qubit-native / interactive families (``NATIVE_REFUSALS``) and
    the OpenClaw-only families (``OPENCLAW_FAMILIES``). The envelope names the
    Qubit-native counterpart or the operator prerequisite, so ``qubit
    <family>`` is always a typed result instead of argparse ``invalid choice``
    — and is never routed through the raw ``qubit agent`` passthrough.
    """
    raw = list(argv or [])
    if not raw:
        return None
    family = raw[0]
    if family in NATIVE_REFUSALS:
        code, detail = NATIVE_REFUSALS[family]
        return FamilyRoute("refused", [], code=code, detail=detail)
    if family in OPENCLAW_FAMILIES:
        code, detail = OPENCLAW_FAMILIES[family]
        return FamilyRoute("refused", [], code=code, detail=detail)
    return None


def route(argv: list, cfg=None) -> Optional[FamilyRoute]:
    """Resolve any fork-backed read-only/confirm-gated Qubit CLI call.

    Returns ``None`` when the Qubit parser owns the call.
    """
    raw = list(argv or [])
    if len(raw) >= 2 and (raw[0], raw[1]) in NATIVE_MCP_READS:
        # Native canonical MCP read (``voicebridge.mcp_servers``); never the
        # fork's second ``config.yaml mcp_servers`` store.
        return None
    resolved = route_family(argv, cfg=cfg)
    if resolved is not None:
        return resolved
    resolved = confirm_pair(argv, cfg=cfg)
    if resolved is not None:
        return resolved
    resolved = family_refusal(argv)
    if resolved is not None:
        return resolved
    resolved = chat_route(argv)
    if resolved is not None:
        return resolved
    mapped = translate(argv)
    if mapped is not None:
        return FamilyRoute("forward", list(mapped))
    return None


__all__ = ["FORWARD_TABLE", "FAMILY_FORWARD", "FAMILY_FORWARD_WAVE6",
           "CONFIRM_TABLE", "NATIVE_REFUSALS", "OPENCLAW_FAMILIES",
           "OPENCLAW_NATIVE_EQUIVALENT", "OPENCLAW_OPERATOR_ONLY",
           "OPENCLAW_MISSING", "NATIVE_MCP_READS", "FamilyRoute", "translate",
           "maybe_forward", "route", "route_family", "confirm_pair",
           "chat_route", "family_refusal"]
