#!/usr/bin/env python3
"""Deterministic page/control coverage artifact generator (source-level only).

Reads the current worktree; emits docs/generated/QUBIT_PAGE_COVERAGE_2026-09-21.json.

This generator is read-only against source: it imports the canonical Qubit route
table, dashboard-op catalog, voice registries, and CLI parser, and regex-scans
the current `frontend/qubit-web` pages and plugin bundles. It performs no
browser/E2E verification and stamps every source-present control OPEN.

Deterministic: no timestamps; the revision is `git rev-parse HEAD`; all
discovery uses `sorted(...)`; output is `json.dump(..., indent=2)` plus one
trailing newline. Identical worktrees at the same revision produce a
byte-identical file, so the artifact can be reproduced and `--check`ed.

The artifact embeds the revision it was generated at. ``--check`` therefore
compares the *revision-independent* content: it normalizes the recorded
generation revision (the top-level fields, every row's
``last_verification_revision``, and the ``method`` prose) before diffing, so it
passes on a moved HEAD while still catching real content drift.

Usage:
    python scripts/page_coverage.py                 # write the artifact
    python scripts/page_coverage.py --out <path>    # write elsewhere
    python scripts/page_coverage.py --check         # render in memory, diff
                                                    # vs the on-disk artifact
                                                    # (revision-independent),
                                                    # exit 2 on drift

Exit codes: 0 ok; 2 usage/drift/generator error.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _head_revision():
    """The current commit, or ``UNKNOWN`` outside a git worktree."""
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "UNKNOWN"


REV = _head_revision()
GENERATED_DIR = os.path.join(ROOT, "docs", "generated")
OUT = os.path.join(GENERATED_DIR, "QUBIT_PAGE_COVERAGE_2026-09-21.json")


def _canonical_artifact(name):
    """Path to a canonical input artifact, always under ``docs/generated/``.

    The canonical manifests live in ``docs/generated/`` and are the only
    inputs this generator reads. If a canonical copy is absent (legacy
    checkout), the frozen ``docs/archive/2026-09-21/`` copy is read as a
    **read-only** provenance fallback; the archive is never written, because
    regeneration always targets the canonical ``docs/generated/`` path.
    """
    generated = os.path.join(GENERATED_DIR, name)
    if os.path.exists(generated):
        return generated
    legacy = os.path.join(ROOT, "docs", "archive", "2026-09-21", name)
    if os.path.exists(legacy):
        return legacy
    raise FileNotFoundError(
        "canonical artifact %s is missing; regenerate it with its owning "
        "script (the frozen archive is read-only)" % generated)

sys.path.insert(0, ROOT)


def _bundled_plugin_page(preferred, *legacy):
    """Resolve a bundled dashboard-plugin entry file, tolerating a rebrand
    rename of the plugin directory (e.g. ``hermes-achievements`` ->
    ``qubit-achievements``). The on-disk frontend plugin dir is the source of
    truth for the served bundle, so return whichever candidate exists."""
    base = "frontend/qubit-web/public/plugins"
    for name in (preferred, *legacy):
        rel = "%s/%s/dist/index.js" % (base, name)
        if os.path.exists(os.path.join(ROOT, rel)):
            return rel
    return "%s/%s/dist/index.js" % (base, preferred)


# ---------------------------------------------------------------------------
# Area definitions: plan section -> page area
# ---------------------------------------------------------------------------
AREAS = [
    dict(id="chat", plan=1, name="Chat surfaces",
         routes=["/chat", "/qubit-chat"],
         sidebar=["Chat", "Qubit Chat"],
         pages=["frontend/qubit-web/src/pages/TerminalChatPage.tsx",
                "frontend/qubit-web/src/pages/ChatPage.tsx"],
         plugins=[],
         upstream=["web/src/pages/ChatPage.tsx"],
         cli=["chat_sessions", "chat-sessions"],
         voice_prefixes=["chat_session"],
         known_gaps=["/chat is a PTY terminal over /agent/api/pty; /qubit-chat is the agent-turn surface. "
                     "Both must share the canonical session store and voice path."]),
    dict(id="sessions", plan=2, name="Sessions",
         routes=["/sessions"], sidebar=["Sessions"],
         pages=["frontend/qubit-web/src/pages/SessionsPage.tsx"],
         plugins=[], upstream=["web/src/pages/SessionsPage.tsx"],
         cli=["sessions"], voice_prefixes=["session", "sessions"],
         known_gaps=[]),
    dict(id="files", plan=3, name="Files",
         routes=["/files"], sidebar=["Files"],
         pages=["frontend/qubit-web/src/pages/FilesPage.tsx"],
         plugins=[], upstream=["web/src/pages/FilesPage.tsx"],
         cli=["project"], voice_prefixes=["projects"],
         known_gaps=["Write/upload/mkdir are brokered; binary download/stream is a typed refusal "
                     "(FILES_BINARY_STREAM). Path confinement via fs policy."]),
    dict(id="models", plan=4, name="Models and Providers",
         routes=["/models", "/qubit-models"], sidebar=["Models", "Models & Providers"],
         pages=["frontend/qubit-web/src/pages/ModelsPage.tsx"],
         plugins=["qubit-models"], upstream=["web/src/pages/ModelsPage.tsx (built-in /models)"],
         cli=["model", "profile"], voice_prefixes=["model_profile", "model"],
         known_gaps=["Two surfaces (/models built-in and /qubit-models plugin) must not expose "
                     "conflicting registries; single profiles.json authority."]),
    dict(id="logs_reports", plan=5, name="Logs and Reports",
         routes=["/logs", "/reports", "/analytics"], sidebar=["Logs", "Reports", "Analytics"],
         pages=["frontend/qubit-web/src/pages/LogsPage.tsx",
                "frontend/qubit-web/src/pages/AnalyticsPage.tsx"],
         plugins=["qubit-reports"], upstream=["web/src/pages/LogsPage.tsx",
                                              "web/src/pages/AnalyticsPage.tsx"],
         cli=["logs", "insights"], voice_prefixes=["logs", "insights"],
         known_gaps=["Token/cost analytics omitted (local runner has no token economy)."]),
    dict(id="cron", plan=6, name="Cron",
         routes=["/cron"], sidebar=["Cron"],
         pages=["frontend/qubit-web/src/pages/CronPage.tsx"],
         plugins=[], upstream=["web/src/pages/CronPage.tsx"],
         cli=["schedules"], voice_prefixes=["schedule"],
         known_gaps=["Managed-cron fire is external (CRON_FIRE_EXTERNAL)."]),
    dict(id="skills", plan=7, name="Skills",
         routes=["/skills"], sidebar=["Skills"],
         pages=["frontend/qubit-web/src/pages/SkillsPage.tsx"],
         plugins=[], upstream=["web/src/pages/SkillsPage.tsx"],
         cli=["skills"], voice_prefixes=["skills"],
         known_gaps=["Remote hub fetch refused; local reviewed manifests only."]),
    dict(id="plugins", plan=8, name="Plugins",
         routes=["/plugins"], sidebar=["Plugins"],
         pages=["frontend/qubit-web/src/pages/PluginsPage.tsx"],
         plugins=[], upstream=["web/src/pages/PluginsPage.tsx"],
         cli=["plugins"], voice_prefixes=["plugins"],
         known_gaps=["Remote agent-plugin install refused; local manifests only."]),
    dict(id="mcp", plan=9, name="MCP",
         routes=["/mcp"], sidebar=["MCP"],
         pages=["frontend/qubit-web/src/pages/McpPage.tsx"],
         plugins=[], upstream=["web/src/pages/McpPage.tsx"],
         cli=["mcp"], voice_prefixes=["mcp"],
         known_gaps=["OAuth flows are manual-handoff typed refusals (OAUTH_MANUAL_HANDOFF)."]),
    dict(id="channels", plan=10, name="Channels",
         routes=["/channels"], sidebar=["Channels"],
         pages=["frontend/qubit-web/src/pages/ChannelsPage.tsx"],
         plugins=[], upstream=["web/src/pages/ChannelsPage.tsx"],
         cli=["msg", "channels"], voice_prefixes=["msg"],
         known_gaps=["Live credentials are operator-only; onboarding is guided manual handoff."]),
    dict(id="webhooks", plan=11, name="Webhooks",
         routes=["/webhooks"], sidebar=["Webhooks"],
         pages=["frontend/qubit-web/src/pages/WebhooksPage.tsx"],
         plugins=[], upstream=["web/src/pages/WebhooksPage.tsx"],
         cli=["webhook"], voice_prefixes=["webhooks"],
         known_gaps=[]),
    dict(id="pairing", plan=12, name="Pairing",
         routes=["/pairing"], sidebar=["Pairing"],
         pages=["frontend/qubit-web/src/pages/PairingPage.tsx"],
         plugins=[], upstream=["web/src/pages/PairingPage.tsx"],
         cli=["pairing"], voice_prefixes=["pairing"],
         known_gaps=[]),
    dict(id="profiles", plan=13, name="Profiles",
         routes=["/profiles", "/profiles/new"], sidebar=["Profiles"],
         pages=["frontend/qubit-web/src/pages/ProfilesPage.tsx",
                "frontend/qubit-web/src/pages/ProfileBuilderPage.tsx"],
         plugins=[], upstream=["web/src/pages/ProfilesPage.tsx",
                               "web/src/pages/ProfileBuilderPage.tsx"],
         cli=["profile", "project"], voice_prefixes=["projects"],
         known_gaps=["Absolute local paths / secret values must never be exposed."]),
    dict(id="config", plan=14, name="Config",
         routes=["/config"], sidebar=["Config"],
         pages=["frontend/qubit-web/src/pages/ConfigPage.tsx"],
         plugins=[], upstream=["web/src/pages/ConfigPage.tsx"],
         cli=["config", "flags"], voice_prefixes=["flags"],
         known_gaps=["Secret writes return typed refusals (CONFIG_RAW / SECRET_NEVER_SERVED)."]),
    dict(id="keys", plan=15, name="Keys",
         routes=["/env"], sidebar=["Keys"],
         pages=["frontend/qubit-web/src/pages/EnvPage.tsx"],
         plugins=[], upstream=["web/src/pages/EnvPage.tsx"],
         cli=["auth"], voice_prefixes=["auth"],
         known_gaps=["env:/file: references only; secret values never rendered or logged."]),
    dict(id="system", plan=16, name="System",
         routes=["/system"], sidebar=["System"],
         pages=["frontend/qubit-web/src/pages/SystemPage.tsx"],
         plugins=[], upstream=["web/src/pages/SystemPage.tsx"],
         cli=["status", "doctor", "product", "package", "runtime"], voice_prefixes=["status", "doctor"],
         known_gaps=["In-place dashboard self-update refused (UPDATE_OPERATOR_OWNED); "
                     "gateway supervision operator-owned."]),
    dict(id="agents", plan=17, name="Agents",
         routes=["/agents"], sidebar=["Agents"],
         pages=["frontend/qubit-web/src/pages/AgentsPage.tsx"],
         plugins=[], upstream=[],
         cli=[], voice_prefixes=[],
         known_gaps=["Read-only page; desktop-only capabilities are operator prerequisites, "
                     "not fake web controls."]),
    dict(id="docs", plan=18, name="Documentation",
         routes=["/docs"], sidebar=["Documentation"],
         pages=["frontend/qubit-web/src/pages/DocsPage.tsx"],
         plugins=[], upstream=["web/src/pages/DocsPage.tsx"],
         cli=[], voice_prefixes=[],
         known_gaps=["OpenAPI browser only; not a full docs product."]),
    dict(id="qubit_chat_plugin", plan=19, name="Qubit Chat plugin",
         routes=[], sidebar=[], pages=[], plugins=[],
         upstream=[], cli=[], voice_prefixes=[],
         known_gaps=["No qubit-chat plugin exists (removed 2026-09-17); /qubit-chat is a built-in "
                     "route. Must not be resurrected as a shadow backend."]),
    dict(id="models_plugin", plan=20, name="Models & Providers plugin",
         routes=["/qubit-models"], sidebar=["Models & Providers"],
         pages=[], plugins=["qubit-models"], upstream=[],
         cli=["model", "profile"], voice_prefixes=["model_profile"],
         known_gaps=["Plugin and built-in /models must not expose conflicting registries."]),
    dict(id="boards", plan=21, name="Qubit Boards",
         routes=["/qubit-boards"], sidebar=["Boards"],
         pages=[], plugins=["qubit-boards"], upstream=["frontend/qubit-web/public/plugins/kanban (OpenClaw kanban plugin)"],
         cli=["kanban"], voice_prefixes=["kanban"],
         known_gaps=["Dual UI with /kanban; single writable store required (kanban.db)."]),
    dict(id="kanban", plan=22, name="Kanban",
         routes=["/kanban"], sidebar=["Kanban"],
         pages=["frontend/qubit-web/public/plugins/kanban/dist/index.js"],
         plugins=[], upstream=["web/src/plugins"],
         cli=["kanban"], voice_prefixes=["kanban"],
         known_gaps=["Vendored plugin reads served; writes gated (KANBAN_WRITE_BROKERED). "
                     "Dual UI with /qubit-boards; no duplicate writable board store."]),
    dict(id="gateway", plan=23, name="Gateway",
         routes=["/qubit-gateway"], sidebar=["Gateway"],
         pages=[], plugins=["qubit-gateway"], upstream=["OpenClaw gateway JSON-RPC surface (QUBIT_OPENCLAW_UPGRADE_MANIFEST.json)"],
         cli=["gateway", "peer"], voice_prefixes=["gateway"],
         known_gaps=["433/434 OpenClaw contractual-pin gateway methods have no registered handler "
                     "or terminal disposition (QUBIT_OPENCLAW_UPGRADE_MANIFEST.json)."]),
    dict(id="memory", plan=24, name="Memory",
         routes=["/memory"], sidebar=["Memory"],
         pages=[], plugins=["qubit-memory"], upstream=["Hermes memory/curator pages"],
         cli=["memory"], voice_prefixes=["memory"],
         known_gaps=["External provider handling operator-only; OAuth manual handoff."]),
    dict(id="runtime", plan=25, name="Runtime Inventory",
         routes=["/qubit-runtime"], sidebar=["Runtime"],
         pages=[], plugins=["qubit-runtime"], upstream=["Hermes runtime inventory surface"],
         cli=["runtime"], voice_prefixes=["runtime"],
         known_gaps=[]),
    dict(id="operations", plan=26, name="Operations",
         routes=["/operations", "/qubit-approvals"], sidebar=["Operations"],
         pages=[], plugins=["qubit-ops", "qubit-approvals"], upstream=["Hermes operations/approvals surface"],
         cli=["approvals"], voice_prefixes=["approvals"],
         known_gaps=["281 dashboard controls reported OPEN in the 2026-09-17 ledger; "
                     "/qubit-approvals is a hidden header-banner plugin on the shared journal."]),
    dict(id="achievements", plan=27, name="Achievements",
         routes=["/achievements"], sidebar=["Achievements"],
         pages=[_bundled_plugin_page("qubit-achievements", "hermes-achievements")],
         plugins=[], upstream=["qubit_runtime/plugins/qubit-achievements"],
         cli=[], voice_prefixes=[],
         known_gaps=["Vendored plugin bundle; dashboard page present, share/export via canvas PNG."]),
    dict(id="devices", plan=28, name="Devices",
         routes=["/devices"], sidebar=["Devices"],
         pages=[], plugins=["qubit-devices"], upstream=["OpenClaw browser/desktop operation surface"],
         cli=["device", "computer-use", "browser"], voice_prefixes=["device"],
         known_gaps=["Hardware actions require explicit authorization; hardware-only dispositions."]),
]

# route table prefix -> area id (ordered, first match wins)
PREFIX_MAP = [
    ("/api/qubit/chat", "chat"),
    ("/api/qubit/desktop", "devices"),
    ("/api/qubit/browser", "devices"),
    ("/api/qubit/gateway", "gateway"),
    ("/api/qubit/runtime", "runtime"),
    ("/api/qubit/agents", "agents"),
    ("/api/qubit/credentials", "keys"),
    ("/api/qubit/local-models", "models"),
    ("/api/qubit/egress", "system"),
    ("/api/qubit/policy", "system"),
    ("/api/qubit/hooks", "operations"),
    ("/api/qubit/usage", "operations"),
    ("/api/qubit/receipts", "operations"),
    ("/api/qubit/logbook", "operations"),
    ("/api/qubit/update", "system"),
    ("/api/qubit", "operations"),
    ("/api/plugins/qubit-achievements", "achievements"),
    ("/api/plugins/kanban", "kanban"),
    ("/api/sessions", "sessions"),
    ("/api/files", "files"),
    ("/api/fs", "files"),
    ("/api/git", "files"),
    ("/api/model", "models"),
    ("/api/local-models", "models"),
    ("/api/providers", "models"),
    ("/api/logs", "logs_reports"),
    ("/api/analytics", "logs_reports"),
    ("/api/cron", "cron"),
    ("/api/skills", "skills"),
    ("/api/dashboard/agent-plugins", "plugins"),
    ("/api/dashboard/plugins", "plugins"),
    ("/api/dashboard/plugin-providers", "plugins"),
    ("/api/mcp", "mcp"),
    ("/api/messaging", "channels"),
    ("/api/webhooks", "webhooks"),
    ("/api/pairing", "pairing"),
    ("/api/profiles", "profiles"),
    ("/api/config", "config"),
    ("/api/env", "keys"),
    ("/api/credentials", "keys"),
    ("/api/system", "system"),
    ("/api/runtime", "runtime"),
    ("/api/health", "system"),
    ("/api/status", "system"),
    ("/api/power", "system"),
    ("/api/egress", "system"),
    ("/api/dashboard/themes", "system"),
    ("/api/dashboard/theme", "system"),
    ("/api/dashboard/font", "system"),
    ("/api/gateway", "gateway"),
    ("/api/memory", "memory"),
    ("/api/curator", "memory"),
    ("/api/learning", "memory"),
    ("/api/ops", "operations"),
    ("/api/actions", "operations"),
    ("/api/approvals", "operations"),
    ("/api/transfers", "operations"),
    ("/api/recall", "operations"),
    ("/api/documents", "operations"),
    ("/api/ask", "operations"),
    ("/api/shares", "operations"),
    ("/api/sessions/shares", "operations"),
    ("/share", "operations"),
    ("/api/audio", "chat"),
    ("/api/auth", "system"),
    ("/api/chat", "chat"),
    ("/login", "system"),
    ("/auth", "system"),
    ("/openapi.json", "docs"),
]

VOICE_BACKEND_AREA = {
    "sessions": "sessions", "chat_sessions": "sessions",
    "memory": "memory", "skills": "skills", "kanban": "boards",
    "pairing": "pairing", "webhook": "webhooks", "msg": "channels",
    "themes": "system", "projects": "files", "mcp": "mcp",
    "auth_status": "keys", "auth_login": "keys", "auth_verify": "keys",
    "auth_logout": "keys", "status": "system", "doctor": "system",
    "gateway": "gateway", "runtime": "runtime", "device": "devices",
    "approvals": "operations", "hooks": "operations", "checkpoints": "operations",
    "logs": "logs_reports", "insights": "logs_reports",
    "skills": "skills", "plugins": "plugins", "capability": "system",
    "worker": "operations", "sync": "operations", "vision_status": "chat",
    "vision_describe": "chat", "import_agent": "system", "proxy": "system",
    "backup": "operations", "autonomy": "operations", "security": "system",
    "setup_check": "system", "verify_release": "system", "diagnostics": "system",
    "pause": "operations", "resume": "operations", "themes_set": "system",
}

CLI_FAMILY_AREA = {
    "sessions": "sessions", "chat-sessions": "sessions", "chat_sessions": "sessions",
    "memory": "memory", "skills": "skills", "kanban": "boards",
    "pairing": "pairing", "webhook": "webhooks", "msg": "channels",
    "theme": "system", "project": "files", "mcp": "mcp",
    "auth": "keys", "status": "system", "doctor": "system",
    "gateway": "gateway", "runtime": "runtime", "device": "devices",
    "computer-use": "devices", "browser": "devices",
    "approvals": "operations", "hooks": "operations", "checkpoints": "operations",
    "logs": "logs_reports", "insights": "logs_reports",
    "plugins": "plugins", "channels": "channels", "profile": "profiles",
    "model": "models", "config": "config", "flags": "config",
    "product": "system", "package": "system", "acp": "operations",
    "proxy": "system", "sync": "operations", "branding": "system",
    "permissions": "operations", "lsp": "operations",
}

# ---------------------------------------------------------------------------
# extraction helpers
# ---------------------------------------------------------------------------
def read(path):
    with open(os.path.join(ROOT, path), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def lines(path):
    return read(path).splitlines()


def area_by_id(aid):
    for a in AREAS:
        if a["id"] == aid:
            return a
    return None


def assign_route_area(pattern):
    for pref, aid in PREFIX_MAP:
        if pattern.startswith(pref):
            return aid
    return "unmapped_global"


def extract_routes():
    from voicebridge import qubit_compat as Q
    out = []
    for r in Q.ROUTES:
        gate = r.get("gate") or {}
        out.append(dict(method=r["method"], pattern=r["pattern"],
                        handler=r["handler"], disposition=r["disposition"],
                        gate_code=gate.get("code"), gate_counterpart=gate.get("counterpart")))
    return out


def extract_openclaw():
    p = _canonical_artifact("QUBIT_OPENCLAW_UPGRADE_MANIFEST.json")
    with open(p, encoding="utf-8") as fh:
        d = json.load(fh)
    methods = d["pinned_surface_disposition"]["methods"]
    rows = []
    for name, info in methods.items():
        rows.append(dict(method=name, status=info.get("status", "OPEN"),
                         basis=info.get("basis"), evidence=info.get("evidence")))
    return rows, d["pinned_surface_disposition"]["counts"], d["revisions"]


def extract_voice():
    from voicebridge import voice_commands as V
    rows = []
    for regname, reg in (("REGISTRY", V.REGISTRY),
                         ("PLUGIN_FLEET_REGISTRY", V.PLUGIN_FLEET_REGISTRY),
                         ("MODEL_PROFILE_REGISTRY", V.MODEL_PROFILE_REGISTRY)):
        for eid, e in reg.items():
            backend = getattr(e, "backend", "") or ""
            rows.append(dict(id=eid, registry=regname, title=getattr(e, "title", ""),
                             backend=backend,
                             aliases=list(getattr(e, "aliases", ()) or ())[:6],
                             parity=list(getattr(e, "parity", ()) or ())))
    return rows


def extract_ops():
    from voicebridge import dashboard_ops as OPS
    rows = []
    for r in OPS.catalog():
        rows.append(dict(id=r.get("id"), title=r.get("title"),
                         tier=r.get("tier"), mutating=bool(r.get("mutating"))))
    return rows


def extract_cli():
    import argparse
    from voicebridge.cli import build_parser
    parser = build_parser()

    def walk(node, prefix=""):
        for a in getattr(node, "_actions", []):
            if isinstance(a, argparse._SubParsersAction):
                for name, sub in a.choices.items():
                    full = (prefix + " " + name).strip()
                    yield full
                    yield from walk(sub, full)

    return sorted(set(walk(parser)))


CONTROL_UPPER = [
    "Button", "Input", "Select", "Checkbox", "Switch", "Textarea", "Segmented",
    "BottomSheet", "Dialog", "ConfirmDialog", "DeleteConfirmDialog",
    "ModelPickerDialog", "SkillEditorDialog", "ToolsetConfigDrawer",
    "ScheduleBuilder", "AutomationBlueprints", "OAuthLoginModal",
    "OAuthProvidersCard", "PlatformsCard", "ModelInfoCard", "ModelReloadConfirm",
    "QubitConsoleModal", "ChatSessionList", "ChatSessionArtifacts", "ChatSidebar",
    "ProfileSwitcher", "LanguageSwitcher", "ThemeSwitcher", "AuthWidget",
    "AutoField", "Tabs", "TabsList", "TabsTrigger", "TabsContent", "Table",
    "Pagination", "DropdownMenu", "RadioGroup", "Slider",
]
CONTROL_LOWER = ["button", "input", "select", "textarea", "table"]
LABEL_RE = re.compile(
    r'(?:aria-label|placeholder|title|label|alt)="([^"]{2,80})"')


def extract_controls(path):
    """Return list of (lineno, tag, label, snippet)."""
    src = lines(path)
    tags = "|".join(CONTROL_UPPER)
    low = "|".join(CONTROL_LOWER)
    any_re = re.compile(r"<(" + tags + r"|" + low + r")\b")
    out = []
    seen = set()
    for i, line in enumerate(src):
        window = "\n".join(src[i:i + 9])
        for m in any_re.finditer(line):
            tag = m.group(1)
            if (i + 1, tag) in seen:
                continue
            seen.add((i + 1, tag))
            lm = LABEL_RE.search(line) or LABEL_RE.search(window)
            label = lm.group(1) if lm else ""
            if not label:
                tm = re.search(r">\s*([A-Za-z][A-Za-z0-9 ,.'&/:-]{2,50})\s*<", window)
                label = tm.group(1).strip() if tm else ""
            out.append((i + 1, tag, label, line.strip()[:140]))
    return out


def extract_plugin_controls(path):
    src = lines(path)
    out = []
    pat = re.compile(r'(?:React\.)?(?:createElement|e|h)\(\s*"(button|input|select|textarea|a|table)"')
    for i, line in enumerate(src):
        for m in pat.finditer(line):
            tag = m.group(1)
            lm = LABEL_RE.search(line)
            label = lm.group(1) if lm else ""
            out.append((i + 1, tag, label, line.strip()[:140]))
    return out


def extract_plugin_ops(path):
    src = read(path)
    ops = sorted(set(re.findall(r'run(?:Read|Op)\("([a-z0-9_]+)"', src)))
    eps = sorted(set(re.findall(r'(?:fetchJSON|authedFetch)\(\s*"(/api/[^"]+)"', src)))
    return ops, eps


# ---------------------------------------------------------------------------
# build rows
# ---------------------------------------------------------------------------
def build_artifact():
    rows = []
    areas_out = OrderedDict()
    for a in AREAS:
        areas_out[a["id"]] = dict(
            id=a["id"], plan_section=a["plan"], name=a["name"],
            routes=a["routes"], sidebar_links=a["sidebar"],
            breadcrumbs=[],  # no breadcrumb component exists in the SPA
            plugins=a["plugins"], page_files=a["pages"],
            upstream_counterparts=a["upstream"],
            cli_families=a["cli"], voice_prefixes=a["voice_prefixes"],
            known_gaps=a["known_gaps"],
            controls=[], api_operations=[], cli_commands=[], voice_intents=[],
            canonical_operations=[], open_count=0, row_count=0,
        )
        rows.append(dict(row_id="area:" + a["id"], kind="page_area", area_id=a["id"],
                         area_name=a["name"], plan_section=a["plan"],
                         name=a["name"], routes=a["routes"], sidebar_links=a["sidebar"],
                         breadcrumb=None, control_type=None,
                         upstream_counterpart="; ".join(a["upstream"]) or None,
                         qubit_implementation="; ".join(a["pages"]) or None,
                         disposition="OPEN",
                         reason="Page area has at least one source-present control without a verified "
                                "end-to-end behavioral chain; no browser/E2E run.",
                         evidence_path="docs/generated/QUBIT_PAGE_COVERAGE_2026-09-21.json",
                         last_verification_revision=REV, verification_level="source_only"))

    # ---- client routes (34) ----
    client_routes = [
        ("/", "RootRedirect -> /sessions", None),
        ("/sessions", "SessionsPage", "sessions"),
        ("/files", "FilesPage", "files"),
        ("/analytics", "AnalyticsPage", "logs_reports"),
        ("/models", "ModelsPage", "models"),
        ("/logs", "LogsPage", "logs_reports"),
        ("/cron", "CronPage", "cron"),
        ("/skills", "SkillsPage", "skills"),
        ("/plugins", "PluginsPage", "plugins"),
        ("/mcp", "McpPage", "mcp"),
        ("/pairing", "PairingPage", "pairing"),
        ("/channels", "ChannelsPage", "channels"),
        ("/webhooks", "WebhooksPage", "webhooks"),
        ("/system", "SystemPage", "system"),
        ("/agents", "AgentsPage", "agents"),
        ("/profiles", "ProfilesPage", "profiles"),
        ("/profiles/new", "ProfileBuilderPage", "profiles"),
        ("/config", "ConfigPage", "config"),
        ("/env", "EnvPage", "keys"),
        ("/docs", "DocsPage", "docs"),
        ("/chat", "TerminalChatPage", "chat"),
        ("/qubit-chat", "ChatPage (persistent host)", "chat"),
        ("/qubit-approvals", "qubit-approvals plugin (hidden tab, header-banner)", "operations"),
        ("/qubit-boards", "qubit-boards plugin", "boards"),
        ("/qubit-brand", "qubit-brand plugin (hidden tab)", None),
        ("/devices", "qubit-devices plugin", "devices"),
        ("/qubit-gateway", "qubit-gateway plugin", "gateway"),
        ("/memory", "qubit-memory plugin", "memory"),
        ("/qubit-models", "qubit-models plugin", "models_plugin"),
        ("/operations", "qubit-ops plugin", "operations"),
        ("/reports", "qubit-reports plugin", "logs_reports"),
        ("/qubit-runtime", "qubit-runtime plugin", "runtime"),
        ("/achievements", "qubit-achievements bundled plugin", "achievements"),
        ("/kanban", "kanban bundled plugin", "kanban"),
    ]
    assert len(client_routes) == 34, len(client_routes)
    for path, comp, aid in client_routes:
        an = area_by_id(aid)["name"] if aid else None
        rows.append(dict(row_id="route:" + path, kind="client_route", area_id=aid,
                         area_name=an, name=path, routes=[path], sidebar_links=[],
                         breadcrumb=None, control_type=None,
                         upstream_counterpart=(("web/src/pages/" + comp.split()[0])
                                               if comp.endswith("Page") or "Page" in comp else None),
                         qubit_implementation="frontend/qubit-web/src/App.tsx:176-196,476-502",
                         disposition="OPEN",
                         reason="Route registered; no browser/E2E verification run.",
                         evidence_path="frontend/qubit-web/src/App.tsx",
                         last_verification_revision=REV, verification_level="source_only"))

    # ---- sidebar links ----
    sidebar = [
        ("Chat", "/chat", "chat"), ("Qubit Chat", "/qubit-chat", "chat"),
        ("Sessions", "/sessions", "sessions"), ("Files", "/files", "files"),
        ("Analytics", "/analytics", "logs_reports"), ("Models", "/models", "models"),
        ("Logs", "/logs", "logs_reports"), ("Cron", "/cron", "cron"),
        ("Skills", "/skills", "skills"), ("Plugins", "/plugins", "plugins"),
        ("MCP", "/mcp", "mcp"), ("Channels", "/channels", "channels"),
        ("Webhooks", "/webhooks", "webhooks"), ("Pairing", "/pairing", "pairing"),
        ("Profiles", "/profiles", "profiles"), ("Config", "/config", "config"),
        ("Keys", "/env", "keys"), ("System", "/system", "system"),
        ("Agents", "/agents", "agents"), ("Documentation", "/docs", "docs"),
        ("Reports", "/reports", "logs_reports"), ("Boards", "/qubit-boards", "boards"),
        ("Gateway", "/qubit-gateway", "gateway"), ("Memory", "/memory", "memory"),
        ("Models & Providers", "/qubit-models", "models_plugin"),
        ("Operations", "/operations", "operations"), ("Runtime", "/qubit-runtime", "runtime"),
        ("Devices", "/devices", "devices"), ("Achievements", "/achievements", "achievements"),
        ("Kanban", "/kanban", "kanban"),
    ]
    for label, path, aid in sidebar:
        rows.append(dict(row_id="sidebar:" + path, kind="sidebar_link",
                         area_id=aid, area_name=area_by_id(aid)["name"] if aid else None,
                         name=label, routes=[path], sidebar_links=[label],
                         breadcrumb=None, control_type=None, upstream_counterpart=None,
                         qubit_implementation="frontend/qubit-web/src/App.tsx:206-245,277-326",
                         disposition="OPEN",
                         reason="Nav entry rendered; link target not E2E-verified.",
                         evidence_path="frontend/qubit-web/src/App.tsx",
                         last_verification_revision=REV, verification_level="source_only"))

    # breadcrumb row: none exist
    rows.append(dict(row_id="breadcrumb:none", kind="breadcrumb", area_id=None,
                     area_name=None, name="(no breadcrumb component in SPA)",
                     routes=[], sidebar_links=[], breadcrumb=None, control_type=None,
                     upstream_counterpart="web/src/App.tsx (upstream also has no breadcrumb bar)",
                     qubit_implementation="frontend/qubit-web/src/App.tsx",
                     disposition="OPEN",
                     reason="Plan requires breadcrumbs; the SPA renders page titles via "
                            "PageHeaderProvider but no breadcrumb trail. Gap recorded.",
                     evidence_path="frontend/qubit-web/src/App.tsx",
                     last_verification_revision=REV, verification_level="source_only"))

    # explicit known-gap rows required by the task
    gap_rows = [
        ("gap:portal", "operations", "GET /api/portal reads through the fork proxy, not Qubit authority",
         "frontend/qubit-web/src/lib/api.ts:1885",
         "Portal status is fetched via the vendored fork proxy path; it is not routed through "
         "qubit_compat.ROUTES and has no canonical Qubit operation."),
        ("gap:docs_openapi_only", "docs", "Documentation is an OpenAPI browser only",
         "frontend/qubit-web/src/pages/DocsPage.tsx",
         "DocsPage renders OpenAPI/schema browsing; it is not a full documentation product."),
        ("gap:agents_readonly", "agents", "Agents page is read-only",
         "frontend/qubit-web/src/pages/AgentsPage.tsx",
         "Only GET /api/qubit/agents is exposed; no create/edit/activate controls."),
        ("gap:boards_kanban_dual", "boards", "Boards and Kanban are dual UIs",
         "frontend/qubit-app/plugins/qubit-boards/qubit-boards.js; "
         "frontend/qubit-web/public/plugins/kanban/dist/index.js",
         "Two dashboard surfaces read the same kanban store; vendored kanban writes are gated "
         "(KANBAN_WRITE_BROKERED). Single-store authority must be proven."),
    ]
    for rid, aid, name, impl, reason in gap_rows:
        rows.append(dict(row_id=rid, kind="known_gap", area_id=aid,
                         area_name=area_by_id(aid)["name"] if aid else None,
                         name=name, routes=[], sidebar_links=[], breadcrumb=None,
                         control_type=None, upstream_counterpart=None,
                         qubit_implementation=impl, disposition="OPEN", reason=reason,
                         evidence_path=impl.split(";")[0],
                         last_verification_revision=REV, verification_level="source_only"))

    # ---- controls ----
    control_count = 0
    for a in AREAS:
        for pf in a["pages"]:
            if pf.endswith(".js"):
                ctrls = extract_plugin_controls(pf)
            else:
                ctrls = extract_controls(pf)
            for lineno, tag, label, snippet in ctrls:
                control_count += 1
                rows.append(dict(row_id="control:%s:%s:%d" % (a["id"], os.path.basename(pf), lineno),
                                 kind="control", area_id=a["id"], area_name=a["name"],
                                 name=(label or tag), routes=[], sidebar_links=[],
                                 breadcrumb=None, control_type=tag,
                                 upstream_counterpart="; ".join(a["upstream"]) or None,
                                 qubit_implementation="%s:%d" % (pf, lineno),
                                 disposition="OPEN",
                                 reason="Source-present %s; no verified end-to-end chain "
                                        "(input->auth->canonical op->policy/broker/e-stop->runtime->"
                                        "state->result->error/retry/cancel/audit). Browser/E2E not run."
                                        % tag,
                                 evidence_path=pf, snippet=snippet,
                                 last_verification_revision=REV, verification_level="source_only"))
        # plugin ops as api-operation rows
        for pl in a["plugins"]:
            pj = "frontend/qubit-app/plugins/%s/%s.js" % (pl, pl)
            if os.path.exists(os.path.join(ROOT, pj)):
                for lineno, tag, label, snippet in extract_plugin_controls(pj):
                    control_count += 1
                    rows.append(dict(row_id="control:%s:%s:%d" % (a["id"], pl, lineno),
                                     kind="control", area_id=a["id"], area_name=a["name"],
                                     name=(label or tag), routes=[], sidebar_links=[],
                                     breadcrumb=None, control_type=tag,
                                     upstream_counterpart="; ".join(a["upstream"]) or None,
                                     qubit_implementation="%s:%d" % (pj, lineno),
                                     disposition="OPEN",
                                     reason="Source-present %s in overlay plugin bundle; no verified "
                                            "end-to-end chain. Browser/E2E not run." % tag,
                                     evidence_path=pj, snippet=snippet,
                                     last_verification_revision=REV,
                                     verification_level="source_only"))
                ops, eps = extract_plugin_ops(pj)
                for op in ops:
                    rows.append(dict(row_id="pluginop:%s:%s" % (pl, op), kind="canonical_operation",
                                     area_id=a["id"], area_name=a["name"], name=op,
                                     routes=[], sidebar_links=[], breadcrumb=None,
                                     control_type=None, upstream_counterpart=None,
                                     qubit_implementation=pj, disposition="OPEN",
                                     reason="Plugin dispatches canonical op through /api/ops; not "
                                            "E2E-verified.",
                                     evidence_path=pj, last_verification_revision=REV,
                                     verification_level="source_only"))

    # ---- backend API operations ----
    route_rows = extract_routes()
    for r in route_rows:
        aid = assign_route_area(r["pattern"])
        an = area_by_id(aid)["name"] if area_by_id(aid) else "Unmapped / global"
        disp = "OPEN"
        reason = "Route registered in qubit_compat.ROUTES; no end-to-end behavioral proof."
        if r["disposition"] == "gated":
            cp = r["gate_counterpart"] or ""
            # SAFE_EQUIVALENT only when counterpart names an in-tree canonical surface
            if cp and any(tok in cp for tok in
                          ("/api/", "qubit ", "CLI", "GET ", "POST ", "dashboard ", "overlay ")):
                disp = "SAFE_EQUIVALENT"
                reason = ("Typed refusal gate (%s) with a source-present canonical counterpart: %s. "
                          "Counterpart not E2E-verified; source-level only." % (r["gate_code"], cp))
            else:
                disp = "OPEN"
                reason = ("Typed refusal gate (%s) with no verified working counterpart. "
                          "Counterpart text: %s" % (r["gate_code"], cp or "none"))
        rows.append(dict(row_id="api:%s:%s" % (r["method"], r["pattern"]),
                         kind="api_operation", area_id=aid, area_name=an,
                         name="%s %s" % (r["method"], r["pattern"]),
                         routes=[], sidebar_links=[], breadcrumb=None, control_type=None,
                         upstream_counterpart="Hermes endpoint map: docs/archive/2026-09-21/"
                                              "HERMES_FRONTEND_MAP.json#endpoints",
                         qubit_implementation="voicebridge/qubit_compat.py ROUTES (handler=%s)" % r["handler"],
                         disposition=disp, reason=reason,
                         evidence_path="voicebridge/qubit_compat.py",
                         handler=r["handler"], route_disposition=r["disposition"],
                         gate_code=r["gate_code"], gate_counterpart=r["gate_counterpart"],
                         last_verification_revision=REV, verification_level="source_only"))

    # ---- upstream contractual-pin gateway methods ----
    oc_rows, oc_counts, oc_rev = extract_openclaw()
    for m in oc_rows:
        st = m["status"]
        # Never propagate an IMPLEMENTED claim from the manifest: nothing here is
        # E2E-verified. Manifest status is preserved in a field instead.
        disp = "OPEN" if st != "SAFE_EQUIVALENT" else "SAFE_EQUIVALENT"
        rows.append(dict(row_id="openclaw:" + m["method"], kind="openclaw_gateway_method",
                         area_id="gateway", area_name="Gateway",
                         name=m["method"], routes=[], sidebar_links=[], breadcrumb=None,
                         control_type=None,
                         upstream_counterpart="OpenClaw gateway method (contractual pin %s)"
                                              % oc_rev["contractual_pin"]["commit"],
                         qubit_implementation=None, disposition=disp,
                         manifest_status=st,
                         reason=(m["basis"] or "No registered Qubit RPC handler; disposition pending.")
                                + " Manifest status=%s; downgraded to OPEN for source-only artifact "
                                  "(no E2E run)." % st,
                         evidence_path="docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json",
                         last_verification_revision=REV, verification_level="source_only"))

    # ---- CLI commands ----
    cli_rows = extract_cli()
    for cmd in cli_rows:
        fam = cmd.split()[0]
        aid = CLI_FAMILY_AREA.get(fam)
        an = area_by_id(aid)["name"] if aid else "Unmapped / global"
        rows.append(dict(row_id="cli:" + cmd.replace(" ", "_"), kind="cli_command",
                         area_id=aid, area_name=an, name="qubit " + cmd,
                         routes=[], sidebar_links=[], breadcrumb=None, control_type=None,
                         upstream_counterpart=None,
                         qubit_implementation="voicebridge/cli.py build_parser",
                         disposition="OPEN",
                         reason="CLI subcommand registered; not executed end-to-end in this artifact.",
                         evidence_path="voicebridge/cli.py",
                         last_verification_revision=REV, verification_level="source_only"))

    # ---- voice intents ----
    voice_rows = extract_voice()
    for v in voice_rows:
        aid = VOICE_BACKEND_AREA.get(v["backend"])
        if not aid:
            for pref, a in ((p, a["id"]) for a in AREAS for p in a["voice_prefixes"]):
                if v["id"].startswith(pref):
                    aid = a
                    break
        an = area_by_id(aid)["name"] if aid else "Unmapped / global"
        rows.append(dict(row_id="voice:" + v["id"], kind="voice_intent",
                         area_id=aid, area_name=an, name=v["title"] or v["id"],
                         routes=[], sidebar_links=[], breadcrumb=None, control_type=None,
                         upstream_counterpart=None,
                         qubit_implementation="voicebridge/voice_commands.py (%s)" % v["registry"],
                         disposition="OPEN",
                         reason="Voice intent registered (backend=%s); canonical path not "
                                "E2E-verified. Right-Ctrl path requires live run." % v["backend"],
                         evidence_path="voicebridge/voice_commands.py",
                         voice_id=v["id"], backend=v["backend"], aliases=v["aliases"],
                         last_verification_revision=REV, verification_level="source_only"))

    # ---- canonical dashboard operations ----
    for op in extract_ops():
        oid = op["id"] or ""
        aid = None
        for pref, a in ((p, a["id"]) for a in AREAS for p in a["voice_prefixes"]):
            if oid.startswith(pref):
                aid = a
                break
        an = area_by_id(aid)["name"] if aid else "Unmapped / global"
        rows.append(dict(row_id="op:" + oid, kind="canonical_operation",
                         area_id=aid, area_name=an, name=op["title"] or oid,
                         routes=[], sidebar_links=[], breadcrumb=None, control_type=None,
                         upstream_counterpart=None,
                         qubit_implementation="voicebridge/dashboard_ops.py catalog",
                         disposition="OPEN",
                         reason="Canonical registry op (tier=%s); not E2E-verified." % op["tier"],
                         evidence_path="voicebridge/dashboard_ops.py",
                         op_id=oid, tier=op["tier"], mutating=op["mutating"],
                         last_verification_revision=REV, verification_level="source_only"))

    # ---- per-area aggregation ----
    for row in rows:
        aid = row.get("area_id")
        if aid in areas_out:
            a = areas_out[aid]
            a["row_count"] += 1
            if row["disposition"] == "OPEN":
                a["open_count"] += 1
            if row["kind"] == "control":
                a["controls"].append(row["row_id"])
            elif row["kind"] == "api_operation":
                a["api_operations"].append(row["row_id"])
            elif row["kind"] == "cli_command":
                a["cli_commands"].append(row["row_id"])
            elif row["kind"] == "voice_intent":
                a["voice_intents"].append(row["row_id"])
            elif row["kind"] == "canonical_operation":
                a["canonical_operations"].append(row["row_id"])
            if row["kind"] == "page_area":
                a["disposition"] = row["disposition"]

    counts = Counter(r["disposition"] for r in rows)
    kind_counts = Counter(r["kind"] for r in rows)
    per_area_open = {aid: a["open_count"] for aid, a in areas_out.items()}
    # rows not attributed to one of the 28 plan areas (global backend routes,
    # CLI/voice entries with no area mapping, hidden plugin routes)
    unmapped_open = sum(1 for r in rows
                        if r["disposition"] == "OPEN" and r.get("area_id") not in areas_out)
    per_area_open["unmapped_global"] = unmapped_open

    artifact = OrderedDict()
    artifact["artifact"] = "QUBIT_PAGE_COVERAGE_2026-09-21.json"
    artifact["schema"] = "qubit-page-coverage/1"
    artifact["generated_by"] = "source-level extraction (no LLM, deterministic)"
    artifact["generated_from_revision"] = REV
    artifact["last_verification_revision"] = REV
    artifact["browser_or_e2e_verification_run"] = False
    artifact["control_label_note"] = (
        "Control `name` is a best-effort label inferred from the JSX element's own "
        "aria-label/placeholder/title or the nearby JSX window. The authoritative control "
        "identity is `qubit_implementation` (file:line) plus `control_type`; labels may be "
        "approximate for elements without an inline accessible name."
    )
    artifact["disposition_rules"] = (
        "No control is IMPLEMENTED. A control/route/op is OPEN until an end-to-end chain is "
        "observed: input -> auth -> canonical op -> policy/broker/e-stop -> real runtime/provider "
        "-> persistent state/event -> correct result -> error/retry/cancel/recovery/audit. "
        "SAFE_EQUIVALENT is used only for typed-refusal gates whose counterpart names an in-tree "
        "canonical surface; the counterpart itself is not E2E-verified here."
    )
    artifact["method"] = (
        "Static extraction from the worktree at revision %s: App.tsx routes/sidebar, pages/*.tsx "
        "JSX controls, plugin manifests + bundles, qubit_compat.ROUTES, dashboard_ops.catalog, "
        "voice_commands registries, cli.build_parser, and the canonical generated "
        "OpenClaw upgrade manifest. The frozen upstream frontend map under "
        "docs/archive/2026-09-21/ is read-only lineage and is never opened or written. "
        "No server started; no Playwright/browser walk; no E2E."
        % REV
    )
    artifact["sources"] = [
        "frontend/qubit-web/src/App.tsx",
        "frontend/qubit-web/src/pages/*.tsx",
        "frontend/qubit-web/src/lib/api.ts",
        "frontend/qubit-app/plugins/*/manifest.json",
        "frontend/qubit-web/public/plugins/fleet.json",
        "voicebridge/qubit_compat.py",
        "voicebridge/dashboard_ops.py",
        "voicebridge/voice_commands.py",
        "voicebridge/cli.py",
        "docs/archive/2026-09-21/HERMES_FRONTEND_MAP.json (frozen read-only provenance; not opened)",
        "docs/generated/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json",
    ]
    artifact["known_gaps"] = [
        "/api/portal reads through the fork proxy, not Qubit authority (frontend/qubit-web/src/lib/api.ts:1885).",
        "Documentation (/docs) is an OpenAPI browser only (frontend/qubit-web/src/pages/DocsPage.tsx).",
        "Agents (/agents) is read-only (frontend/qubit-web/src/pages/AgentsPage.tsx).",
        "Boards (/qubit-boards) and Kanban (/kanban) are dual UIs over the kanban store; writes gated.",
        "%d/%d OpenClaw contractual-pin gateway methods are OPEN "
        "(QUBIT_OPENCLAW_UPGRADE_MANIFEST.json)."
        % (oc_counts.get("OPEN", 0), sum(oc_counts.values())),
        "No breadcrumb component exists in the SPA.",
    ]
    artifact["summary"] = OrderedDict()
    artifact["summary"]["page_areas_total"] = len(AREAS)
    artifact["summary"]["client_routes_total"] = len(client_routes)
    artifact["summary"]["total_rows"] = len(rows)
    # Canonical disposition order (fixed) so a dict reordering in the data
    # source can never reorder the artifact output.
    artifact["summary"]["counts_by_disposition"] = {
        k: counts.get(k, 0)
        for k in ("IMPLEMENTED", "SAFE_EQUIVALENT", "OPERATOR_ONLY", "OPEN")
    }
    artifact["summary"]["counts_by_kind"] = dict(sorted(kind_counts.items()))
    # `unmapped_global` sorts last; every real area name sorts before it.
    artifact["summary"]["per_area_open_counts"] = dict(
        sorted(per_area_open.items(),
               key=lambda kv: (kv[0] == "unmapped_global", kv[0])))
    artifact["summary"]["openclaw_gateway_pin_counts"] = {
        k: oc_counts.get(k, 0)
        for k in ("IMPLEMENTED", "SAFE_EQUIVALENT",
                  "OPERATOR_ONLY_EXTERNAL", "OPERATOR_ONLY_ARCHITECTURE",
                  "OPEN")
    }
    artifact["summary"]["openclaw_revisions"] = oc_rev
    artifact["summary"]["explicit_statement"] = (
        "NO browser, Playwright, or end-to-end verification was run for this artifact. "
        "All dispositions are source-level. No row is claimed IMPLEMENTED. "
        "This artifact does not assert completion, 100%, 1:1, or VERIFIED."
    )
    artifact["page_areas"] = list(areas_out.values())
    artifact["rows"] = rows
    return artifact


def _render(artifact):
    """Serialize exactly as written to disk (indent=2 + one trailing newline)."""
    return json.dumps(artifact, indent=2, ensure_ascii=False) + "\n"


#: Fields that record the generation revision, plus the ``method`` prose that
#: embeds it. Normalized before a ``--check`` diff so a moved HEAD never fails.
_REV_FIELDS = ("generated_from_revision", "last_verification_revision")
_METHOD_REV_RE = re.compile(r"at revision [0-9a-f]{7,40}")
REV_TOKEN = "<REVISION>"


def _revision_independent(doc):
    """Return *doc* with its embedded generation revision normalized away.

    ``--check`` must be portable across a moved HEAD: the artifact records the
    revision it was generated at, so a byte diff would fail after every commit
    even when the extracted content is unchanged. Normalizing only the
    revision-bearing fields (and the ``method`` prose) keeps real content drift
    detectable while ignoring revision-only drift. Non-revision 40-hex values
    (upstream pins, method-inventory commits) are deliberately left intact.
    """
    def scrub(node):
        if isinstance(node, dict):
            return {
                key: (REV_TOKEN if key in _REV_FIELDS else scrub(value))
                for key, value in node.items()
            }
        if isinstance(node, list):
            return [scrub(item) for item in node]
        if isinstance(node, str):
            return _METHOD_REV_RE.sub("at revision " + REV_TOKEN, node)
        return node

    return scrub(doc)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=OUT,
                    help="output path (default: %(default)s)")
    ap.add_argument("--check", action="store_true",
                    help="render in memory and fail (exit 2) if the on-disk "
                         "artifact differs (revision-independent compare); "
                         "writes nothing")
    args = ap.parse_args(argv)

    artifact = build_artifact()
    rendered = _render(artifact)

    if args.check:
        target = args.out
        if not os.path.isfile(target):
            print("check FAILED: %s is absent" % target, file=sys.stderr)
            return 2
        with open(target, encoding="utf-8") as fh:
            on_disk_text = fh.read()
        try:
            on_disk = json.loads(on_disk_text)
            fresh = json.loads(rendered)
        except ValueError as exc:
            print("check FAILED: %s is not valid JSON: %s" % (target, exc),
                  file=sys.stderr)
            return 2
        if _revision_independent(on_disk) != _revision_independent(fresh):
            print("check FAILED: %s differs from regenerated output"
                  % target, file=sys.stderr)
            return 2
        print("check OK: %s is current (revision-independent) at revision %s"
              % (target, artifact["generated_from_revision"]))
        return 0

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(rendered)

    summary = artifact["summary"]
    print("wrote", args.out)
    print("revision", artifact["generated_from_revision"])
    print("total rows", summary["total_rows"])
    print("counts", summary["counts_by_disposition"])
    print("kinds", summary["counts_by_kind"])
    print("openclaw", summary["openclaw_gateway_pin_counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
