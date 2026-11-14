"""P6-04 — canonical Office panel inventory.

One row per panel the Office shell can show. A row is not decoration: it names
the *real* data authority behind the panel (an importable Python module and a
symbol, plus the compat route when one exists), the read/write permission
scope, the states the panel must render, and exactly one real action or one
explicit typed refusal.

The rule this module exists to enforce: **no panel invents state and no
authority is a renderer.** ``validate_rows`` imports every authority and action
module, traverses every symbol, and resolves every route against the live
``voicebridge.qubit_compat.ROUTES`` table, so a typo or an aspirational route
fails the verifier instead of shipping as a fake panel.

Read-only: this module declares a registry. It opens no store, starts no
service, and performs no action.
"""
from __future__ import annotations

import importlib
from typing import Any, Mapping, Sequence

SCHEMA = "qubit-office-panel-inventory/1"

#: Every panel state a row must be able to render. ``typed_refusal`` is the
#: honest end state when the backend answers a named refusal instead of data.
REQUIRED_STATES = (
    "loading",
    "empty",
    "error",
    "retry",
    "unavailable",
    "typed_refusal",
)

#: The minimum panel set from the Phase 6 plan (P6-04).
REQUIRED_PANELS = (
    "agents",
    "sessions",
    "chat",
    "terminal",
    "deliveries",
    "timeline",
    "war_room",
    "memory",
    "approvals",
    "ops",
    "crm",
    "marketing",
    "etsy",
    "trading",
    "models",
    "mcp",
    "plugins",
    "skills",
    "budgets",
    "audit",
    "settings",
)

_WRITE_SCOPES = {"owner", "operator", "tenant", "session", "none"}


def _row(
    panel_id: str,
    title: str,
    group: str,
    *,
    module: str,
    symbol: str,
    route: str,
    route_method: str,
    read: str,
    write: str,
    action: Mapping[str, Any],
    deep_link: str,
    split: str,
    stack: str,
    source_command: str,
    description: str,
) -> dict[str, Any]:
    return {
        "panel_id": panel_id,
        "title": title,
        "group": group,
        "description": description,
        "authority": {
            "module": module,
            "symbol": symbol,
            "route": route,
            "route_method": route_method,
        },
        "permissions": {"read": read, "write": write},
        "states": list(REQUIRED_STATES),
        "action": dict(action),
        "deep_link": deep_link,
        "layout": {"split": split, "stack": stack},
        "source_command": source_command,
    }


def _real_route(method: str, route: str, label: str) -> dict[str, Any]:
    return {"kind": "real", "method": method, "route": route, "label": label}


def _real_module(module: str, symbol: str, label: str) -> dict[str, Any]:
    return {"kind": "real", "module": module, "symbol": symbol, "label": label}


def _refusal(code: str, detail: str, route: str, label: str) -> dict[str, Any]:
    return {
        "kind": "typed_refusal",
        "code": code,
        "detail": detail,
        "route": route,
        "label": label,
    }


def _build_panels() -> tuple[dict[str, Any], ...]:
    return (
        _row(
            "agents",
            "Agents",
            "work",
            module="voicebridge.business.agents",
            symbol="AgentControlService",
            route="/api/office/business/agents",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route(
                "POST",
                "/api/office/business/agents/(?P<id>[^/]+)/pause",
                "Pause agent",
            ),
            deep_link="/qubit-office?panel=agents",
            split="docked left of the office canvas; shares the dock row with HUD",
            stack="phone: full-width sheet above the canvas, heads wrap",
            source_command="voicebridge.business.agents.AgentControlService",
            description="Seated roster with real control-plane grants and lifecycle.",
        ),
        _row(
            "sessions",
            "Sessions",
            "work",
            module="voicebridge.qubit_compat",
            symbol="h_sessions_list",
            route="/api/sessions",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route("POST", "/api/sessions/import", "Import session"),
            deep_link="/sessions",
            split="full-width table in the dashboard content column",
            stack="phone: single-column cards",
            source_command="h_sessions_list",
            description="Durable chat sessions from the single session authority.",
        ),
        _row(
            "chat",
            "Chat",
            "work",
            module="voicebridge.chat_transport",
            symbol="pty_feed",
            route="/api/qubit/chat/pty",
            route_method="POST",
            read="owner",
            write="owner",
            action=_real_route(
                "POST", "/api/qubit/chat/pty", "Send one chat turn"
            ),
            deep_link="/chat",
            split="terminal split: transcript left, context panel right",
            stack="phone: transcript only; context panel behind a toggle",
            source_command="h_chat_pty",
            description="Persisted agent turns rendered as PTY frames.",
        ),
        _row(
            "terminal",
            "Terminal",
            "work",
            module="voicebridge.qubit_compat",
            symbol="h_terminal_profiles",
            route="/api/terminal/profiles",
            route_method="GET",
            read="owner",
            write="operator",
            action=_real_module(
                "voicebridge.pty_ws", "handshake", "Open terminal WebSocket"
            ),
            deep_link="/qubit-chat",
            split="terminal fills the content column",
            stack="phone: terminal fills the viewport, no side rail",
            source_command="h_terminal_profiles",
            description="Terminal profiles plus the real PTY WebSocket handshake.",
        ),
        _row(
            "deliveries",
            "Deliveries",
            "work",
            module="voicebridge.business.eventlog",
            symbol="list_events",
            route="",
            route_method="",
            read="owner",
            write="none",
            action=_refusal(
                "OFFICE_CLIENT_UNSUPPORTED",
                "Qubit has no agent-delivery file registry; the Habblaud "
                "deliveries file route refuses instead of fabricating one.",
                "/api/office/habblaud/deliveries/file/(?P<id>[^/]+)",
                "Open delivery file",
            ),
            deep_link="/qubit-office?panel=deliveries",
            split="right-hand HUD list above the canvas",
            stack="phone: full-width list sheet",
            source_command="h_office_habblaud_refuse",
            description="Delivery/artifact history projected from the event log.",
        ),
        _row(
            "timeline",
            "Timeline",
            "work",
            module="voicebridge.business.operator_timeline",
            symbol="operator_timeline",
            route="/api/office/business/operator/timeline",
            route_method="GET",
            read="operator",
            write="none",
            action=_real_module(
                "voicebridge.business.operator_timeline",
                "operator_timeline",
                "Project causal timeline (replay=1 is labelled simulation)",
            ),
            deep_link="/qubit-office?panel=timeline",
            split="right-hand HUD timeline",
            stack="phone: full-width timeline sheet",
            source_command="h_business_operator_timeline",
            description="Causal event/job/checkpoint projection with replay.",
        ),
        _row(
            "war_room",
            "War Room",
            "business",
            module="voicebridge.business.warroom",
            symbol="list_sessions",
            route="/api/office/business/warroom/sessions",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route(
                "POST", "/api/office/business/warroom/sessions", "Create session"
            ),
            deep_link="/qubit-office?panel=war-room",
            split="main canvas panel, evidence rail on the right",
            stack="phone: session list, evidence behind a tab",
            source_command="h_business_warroom_create",
            description="Evidence/debate/decision sessions over the business store.",
        ),
        _row(
            "memory",
            "Memory",
            "business",
            module="voicebridge.active_memory",
            symbol="status",
            route="/api/memory",
            route_method="GET",
            read="tenant",
            write="tenant",
            action=_real_module(
                "voicebridge.business.memory", "MemoryService.remember",
                "Write a scoped memory record",
            ),
            deep_link="/memory",
            split="notes list left, scoped detail right",
            stack="phone: notes list then detail",
            source_command="h_memory",
            description="Scoped memory records and the Obsidian projection.",
        ),
        _row(
            "approvals",
            "Approvals",
            "business",
            module="voicebridge.qubit_compat",
            symbol="h_approvals_list",
            route="/api/ops/approvals",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route("POST", "/api/ops/confirm", "Confirm approval"),
            deep_link="/qubit-approvals",
            split="full-width journal with a detail drawer",
            stack="phone: list then detail",
            source_command="h_approvals_list",
            description="Pending confirmations on the shared approval journal.",
        ),
        _row(
            "ops",
            "Ops",
            "business",
            module="voicebridge.qubit_compat",
            symbol="h_ops_catalog",
            route="/api/ops",
            route_method="GET",
            read="operator",
            write="operator",
            action=_real_route("POST", "/api/ops/run", "Run catalog operation"),
            deep_link="/operations",
            split="operation grid with a confirmation panel",
            stack="phone: one operation per row",
            source_command="h_ops_catalog",
            description="Operations catalog with confirmation-gated execution.",
        ),
        _row(
            "crm",
            "CRM",
            "business",
            module="voicebridge.business.crm",
            symbol="pipeline",
            route="",
            route_method="",
            read="operator",
            write="operator",
            action=_real_module(
                "voicebridge.business.crm", "set_stage", "Move a pipeline stage"
            ),
            deep_link="/qubit-office?panel=crm",
            split="pipeline board with a contact detail rail",
            stack="phone: stage list then contact detail",
            source_command="voicebridge.business.crm.pipeline",
            description="Contacts, consent, suppression and pipeline stages.",
        ),
        _row(
            "marketing",
            "Marketing",
            "business",
            module="voicebridge.business.marketing",
            symbol="list_campaigns",
            route="",
            route_method="",
            read="operator",
            write="operator",
            action=_real_module(
                "voicebridge.business.marketing", "create_draft",
                "Create a campaign draft",
            ),
            deep_link="/qubit-office?panel=marketing",
            split="campaign list with a content/approval rail",
            stack="phone: campaign list then detail",
            source_command="voicebridge.business.marketing.list_campaigns",
            description="Campaign drafts, approvals and publication receipts.",
        ),
        _row(
            "etsy",
            "Etsy",
            "business",
            module="voicebridge.business.etsy_operations",
            symbol="sandbox_etsy_adapter",
            route="/api/office/business/etsy/status",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route(
                "POST", "/api/office/business/etsy/authorize",
                "Start Etsy OAuth connection",
            ),
            deep_link="/qubit-office?panel=etsy",
            split="connection card above the sandbox operations list",
            stack="phone: connection card then operations",
            source_command="h_business_etsy_status",
            description="Etsy connection plus the sandbox operations surface.",
        ),
        _row(
            "trading",
            "Trading",
            "business",
            module="voicebridge.business.trading",
            symbol="OrderManagementSystem",
            route="",
            route_method="",
            read="owner",
            write="owner",
            action=_real_module(
                "voicebridge.business.trading", "OrderManagementSystem.submit",
                "Submit a paper order",
            ),
            deep_link="/qubit-office?panel=trading",
            split="risk/ledger rail left, paper order blotter right",
            stack="phone: blotter then risk detail",
            source_command="voicebridge.business.trading.OrderManagementSystem",
            description="Paper-only OMS, risk engine and double-entry ledger.",
        ),
        _row(
            "models",
            "Models & Providers",
            "system",
            module="voicebridge.qubit_compat",
            symbol="h_model_options",
            route="/api/model/options",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route("POST", "/api/model/set", "Set the main model"),
            deep_link="/models",
            split="provider list with a model detail panel",
            stack="phone: provider list then detail",
            source_command="h_model_options",
            description="Provider/model catalog and the active route.",
        ),
        _row(
            "mcp",
            "MCP",
            "system",
            module="voicebridge.qubit_compat",
            symbol="h_mcp_servers",
            route="/api/mcp/servers",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route("POST", "/api/mcp/servers", "Add MCP server"),
            deep_link="/plugins?tab=mcp",
            split="server list with a tool/status detail rail",
            stack="phone: server list then detail",
            source_command="h_mcp_servers",
            description="Canonical MCP server registry and live status.",
        ),
        _row(
            "plugins",
            "Plugins",
            "system",
            module="voicebridge.qubit_compat",
            symbol="h_plugins",
            route="/api/dashboard/plugins",
            route_method="GET",
            read="owner",
            write="operator",
            action=_real_route(
                "POST", "/api/dashboard/plugins/reload", "Reload plugin store"
            ),
            deep_link="/plugins",
            split="catalog grid with a provenance rail",
            stack="phone: catalog cards one per row",
            source_command="h_plugins",
            description="Installed dashboard plugins and their manifests.",
        ),
        _row(
            "skills",
            "Skills",
            "system",
            module="voicebridge.qubit_compat",
            symbol="h_skills",
            route="/api/skills",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route("POST", "/api/skills", "Create a skill"),
            deep_link="/plugins?tab=skills",
            split="skill list with a content preview rail",
            stack="phone: skill list then preview",
            source_command="h_skills",
            description="Permissioned skill catalog from the single skill store.",
        ),
        _row(
            "budgets",
            "Budgets",
            "system",
            module="voicebridge.business.effective_state",
            symbol="effective_agent_state",
            route="/api/office/business/agents/(?P<id>[^/]+)/effective",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route(
                "POST",
                "/api/office/business/agents/(?P<id>[^/]+)/update",
                "Update agent budget limits",
            ),
            deep_link="/qubit-office?panel=budgets",
            split="budget table with an agent selector",
            stack="phone: per-agent budget cards",
            source_command="h_business_agent_effective",
            description="Budget limits and spend from the effective-state projection.",
        ),
        _row(
            "audit",
            "Audit",
            "system",
            module="voicebridge.business.eventlog",
            symbol="read_events",
            route="/api/office/business/agents/(?P<id>[^/]+)/audit",
            route_method="GET",
            read="owner",
            write="none",
            action=_real_module(
                "voicebridge.business.eventlog", "replay", "Replay the event log"
            ),
            deep_link="/qubit-office?panel=audit",
            split="event table with a JSON detail drawer",
            stack="phone: event list then detail",
            source_command="h_business_agent_audit",
            description="Append-only business event/audit trail.",
        ),
        _row(
            "settings",
            "Settings",
            "system",
            module="voicebridge.qubit_compat",
            symbol="h_config",
            route="/api/config",
            route_method="GET",
            read="owner",
            write="owner",
            action=_real_route("PUT", "/api/config", "Save configuration"),
            deep_link="/config",
            split="category rail left, form right",
            stack="phone: category list then form",
            source_command="h_config",
            description="Live settings record with a confirmation-gated save.",
        ),
    )


PANELS: tuple[dict[str, Any], ...] = _build_panels()


def panel_rows() -> list[dict[str, Any]]:
    """A mutable copy of every panel row (callers cannot mutate the registry)."""
    return [dict(row) for row in PANELS]


def panel_ids() -> list[str]:
    return [row["panel_id"] for row in PANELS]


def get_panel(panel_id: str) -> dict[str, Any] | None:
    for row in PANELS:
        if row["panel_id"] == panel_id:
            return dict(row)
    return None


def inventory() -> dict[str, Any]:
    """The declarative inventory document (no timestamp; deterministic)."""
    return {
        "ok": True,
        "schema": SCHEMA,
        "required_states": list(REQUIRED_STATES),
        "required_panels": list(REQUIRED_PANELS),
        "panels": panel_rows(),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _default_route_index() -> set[tuple[str, str]]:
    from voicebridge import qubit_compat as HC

    return {
        (str(route.get("method", "")), str(route.get("pattern", "")))
        for route in HC.ROUTES
    }


def _default_module_loader(name: str) -> Any:
    return importlib.import_module(name)


def _resolve_symbol(module: Any, dotted: str) -> bool:
    target: Any = module
    for part in str(dotted).split("."):
        if not part:
            return False
        try:
            target = getattr(target, part)
        except AttributeError:
            return False
    return True


def _is_renderer(module: str) -> bool:
    lowered = module.lower()
    return (
        lowered.startswith("frontend")
        or ".game." in lowered
        or lowered.startswith("game")
        or lowered.startswith("qubit_office_renderer")
    )


def validate_rows(
    rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    route_index: set[tuple[str, str]] | None = None,
    module_loader: Any = None,
) -> list[dict[str, str]]:
    """Return a list of problems. An empty list means the inventory is valid.

    ``route_index`` and ``module_loader`` are injectable so the negative tests
    can prove each check fires without importing the whole compat surface.
    """
    problems: list[dict[str, str]] = []
    seen: dict[str, int] = {}
    source = PANELS if rows is None else rows
    routes = _default_route_index() if route_index is None else route_index
    load = _default_module_loader if module_loader is None else module_loader
    loaded: dict[str, Any] = {}

    def problem(code: str, panel_id: str, detail: str) -> None:
        problems.append(
            {"code": code, "panel_id": str(panel_id), "detail": detail}
        )

    for row in source:
        pid = str(row.get("panel_id", "") or "")
        if not pid:
            problem("PANEL_ID_MISSING", "", "a row has no panel_id")
            continue
        seen[pid] = seen.get(pid, 0) + 1
        if not str(row.get("title", "") or "").strip():
            problem("PANEL_TITLE_MISSING", pid, "title is empty")
        if not str(row.get("group", "") or "").strip():
            problem("PANEL_GROUP_MISSING", pid, "group is empty")
        if not str(row.get("deep_link", "") or "").startswith("/"):
            problem("DEEP_LINK_INVALID", pid, "deep_link must start with '/'")
        layout = row.get("layout") or {}
        if not str(layout.get("split", "") or "").strip():
            problem("LAYOUT_SPLIT_MISSING", pid, "layout.split is empty")
        if not str(layout.get("stack", "") or "").strip():
            problem("LAYOUT_STACK_MISSING", pid, "layout.stack is empty")
        if not str(row.get("source_command", "") or "").strip():
            problem("SOURCE_COMMAND_MISSING", pid, "source_command is empty")

        # States -----------------------------------------------------------
        states = list(row.get("states") or [])
        if len(states) != len(set(states)):
            problem("STATE_DUPLICATE", pid, f"duplicate states: {states}")
        unknown = [s for s in states if s not in REQUIRED_STATES]
        if unknown:
            problem("STATE_UNKNOWN", pid, f"unknown states: {unknown}")
        missing = [s for s in REQUIRED_STATES if s not in states]
        if missing:
            problem("STATE_MISSING", pid, f"missing states: {missing}")

        # Permissions ------------------------------------------------------
        permissions = row.get("permissions") or {}
        read = str(permissions.get("read", "") or "").strip()
        write = str(permissions.get("write", "") or "").strip()
        if not read:
            problem("PERMISSION_READ_MISSING", pid, "read permission is empty")
        if not write:
            problem("PERMISSION_WRITE_MISSING", pid, "write permission is empty")
        elif write not in _WRITE_SCOPES:
            problem(
                "PERMISSION_WRITE_UNKNOWN", pid,
                f"write scope {write!r} is not one of {sorted(_WRITE_SCOPES)}",
            )

        # Authority --------------------------------------------------------
        authority = row.get("authority") or {}
        module_name = str(authority.get("module", "") or "").strip()
        symbol = str(authority.get("symbol", "") or "").strip()
        if not module_name:
            problem("AUTHORITY_MODULE_MISSING", pid, "authority.module is empty")
        elif _is_renderer(module_name):
            problem(
                "AUTHORITY_IS_RENDERER", pid,
                f"{module_name!r} is a renderer, not a data authority",
            )
        elif not (
            module_name.startswith("voicebridge.")
            or module_name.startswith("qubit_runtime.")
        ):
            problem(
                "AUTHORITY_MODULE_SUSPECT", pid,
                f"{module_name!r} is outside the backend packages",
            )
        else:
            if module_name not in loaded:
                try:
                    loaded[module_name] = load(module_name)
                except Exception as exc:  # noqa: BLE001 - report, never crash
                    loaded[module_name] = exc
                    problem(
                        "AUTHORITY_MODULE_UNRESOLVED", pid,
                        f"{module_name}: {exc}",
                    )
            module = loaded.get(module_name)
            if not isinstance(module, Exception) and module is not None:
                if not symbol:
                    problem(
                        "AUTHORITY_SYMBOL_MISSING", pid,
                        "authority.symbol is empty",
                    )
                elif not _resolve_symbol(module, symbol):
                    problem(
                        "AUTHORITY_SYMBOL_UNRESOLVED", pid,
                        f"{module_name}.{symbol} does not exist",
                    )
        route = str(authority.get("route", "") or "")
        if route:
            method = str(authority.get("route_method", "") or "")
            if not method:
                problem(
                    "AUTHORITY_ROUTE_METHOD_MISSING", pid,
                    "route is set but route_method is empty",
                )
            elif (method, route) not in routes:
                problem(
                    "AUTHORITY_ROUTE_UNKNOWN", pid,
                    f"{method} {route} is not a registered compat route",
                )

        # Action -----------------------------------------------------------
        action = row.get("action") or {}
        kind = str(action.get("kind", "") or "")
        label = str(action.get("label", "") or "").strip()
        if kind not in ("real", "typed_refusal"):
            problem(
                "ACTION_KIND_INVALID", pid,
                f"action.kind {kind!r} is neither 'real' nor 'typed_refusal'",
            )
            continue
        if not label:
            problem("ACTION_LABEL_MISSING", pid, "action.label is empty")
        if kind == "real":
            method = str(action.get("method", "") or "")
            action_route = str(action.get("route", "") or "")
            action_module = str(action.get("module", "") or "")
            action_symbol = str(action.get("symbol", "") or "")
            if action_route:
                if not method:
                    problem(
                        "ACTION_METHOD_MISSING", pid,
                        "real route action has no method",
                    )
                elif (method, action_route) not in routes:
                    problem(
                        "ACTION_ROUTE_UNKNOWN", pid,
                        f"{method} {action_route} is not a registered route",
                    )
            if action_module:
                if _is_renderer(action_module):
                    problem(
                        "ACTION_IS_RENDERER", pid,
                        f"{action_module!r} is a renderer",
                    )
                elif action_module not in loaded:
                    try:
                        loaded[action_module] = load(action_module)
                    except Exception as exc:  # noqa: BLE001
                        loaded[action_module] = exc
                        problem(
                            "ACTION_MODULE_UNRESOLVED", pid,
                            f"{action_module}: {exc}",
                        )
                module = loaded.get(action_module)
                if not isinstance(module, Exception) and module is not None:
                    if not action_symbol or not _resolve_symbol(
                        module, action_symbol
                    ):
                        problem(
                            "ACTION_SYMBOL_UNRESOLVED", pid,
                            f"{action_module}.{action_symbol} does not exist",
                        )
            if not action_route and not action_module:
                problem(
                    "ACTION_HAS_NO_TARGET", pid,
                    "real action names neither a route nor a module symbol",
                )
        else:
            code = str(action.get("code", "") or "")
            detail = str(action.get("detail", "") or "").strip()
            refusal_route = str(action.get("route", "") or "")
            if not code or not code.isupper() or " " in code:
                problem(
                    "REFUSAL_CODE_INVALID", pid,
                    f"typed refusal code {code!r} is not an UPPER_SNAKE token",
                )
            if not detail:
                problem(
                    "REFUSAL_DETAIL_MISSING", pid,
                    "typed refusal has no detail",
                )
            if refusal_route and ("GET", refusal_route) not in routes:
                problem(
                    "REFUSAL_ROUTE_UNKNOWN", pid,
                    f"GET {refusal_route} is not a registered route",
                )

    for panel_id, count in seen.items():
        if count > 1:
            problem("PANEL_ID_DUPLICATE", panel_id, f"seen {count} times")
    for required in REQUIRED_PANELS:
        if required not in seen:
            problem("PANEL_REQUIRED_MISSING", required, "required panel absent")
    return problems


__all__ = [
    "PANELS",
    "REQUIRED_PANELS",
    "REQUIRED_STATES",
    "SCHEMA",
    "get_panel",
    "inventory",
    "panel_ids",
    "panel_rows",
    "validate_rows",
]
