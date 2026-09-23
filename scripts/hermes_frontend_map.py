#!/usr/bin/env python3
"""Generate docs/archive/2026-09-21/HERMES_FRONTEND_MAP.json: machine-readable Hermes web/src
inventory mapped to Qubit equivalents.

Hermes pin: https://github.com/NousResearch/hermes-agent @
2237be355906fbe6065ce1815711eee52b2d646e (tag v2026.9.7), MIT.
Oracle: /tmp/pinned-upstream/hermes-agent (read-only; verified rev-parse).

Adaptation rule (owner amendment 2026-09-11): the pinned upstream
React/Vite/Node frontend is vendored into frontend/qubit-web and
frontend/qubit-shared and built with Vite; React/TS is a supported Qubit
dashboard runtime. Every upstream file maps to a byte-identical vendored
file or a documented Qubit patch/rebrand recorded in
docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.json; routes/endpoints additionally map to
shared Qubit backend operations, tested policy refusals, or documented
omissions. MIT notices retained in THIRD_PARTY_NOTICES.md (see
docs/archive/2026-09-21/UPSTREAM_REUSE.md R34).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ORACLE = Path("/tmp/pinned-upstream/hermes-agent/web/src")
REPO = Path(__file__).resolve().parents[1]

REVISION = "2237be355906fbe6065ce1815711eee52b2d646e"
REPO_URL = "https://github.com/NousResearch/hermes-agent"

#: Qubit-side path/identifier renames applied to the preserved hand-maintained
#: audit sections so a regenerated map never records a pre-rebrand path. The
#: upstream ``files[*].path`` values (``web/src/...``) and each dialog's
#: ``upstream`` field are deliberately not touched: they name the pinned
#: upstream file, which did not move.
_REBRAND_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("qubit_hermes/", "qubit_runtime/"),
    ("frontend/qubit-web/src/components/HermesConsoleModal.tsx",
     "frontend/qubit-web/src/components/QubitConsoleModal.tsx"),
    ("voicebridge.hermes_plugins", "voicebridge.qubit_plugins"),
    ("voicebridge.hermes_compat", "voicebridge.qubit_compat"),
    ("tests/test_hermes_plugins.py", "tests/test_qubit_plugins.py"),
    ("tests/test_hermes_frontend_inventory8u.py",
     "tests/test_qubit_frontend_inventory8u.py"),
    # The local base-path constant is QUBIT_BASE_PATH; the injected browser
    # global keeps the upstream wire name and is masked around this rename.
    ("HERMES_BASE_PATH", "QUBIT_BASE_PATH"),
)
#: Injected browser global keeps the upstream wire name; mask it so the bare
#: ``HERMES_BASE_PATH`` rename above cannot rewrite ``window.__HERMES_BASE_PATH__``.
_WIRE_BASE_PATH = "window.__HERMES_BASE_PATH__"
_WIRE_BASE_PATH_MASK = "\x00__HERMES_WIRE_BASE_PATH__\x00"
#: Category labels the deep rebrand rewrote in the vendored Achievements engine.
_REBRAND_CATEGORY = {"Hermes Native": "Qubit Native"}


def _canonicalize(value):
    """Rewrite pre-rebrand Qubit paths inside a preserved audit section."""
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_canonicalize(v) for v in value]
    if isinstance(value, str):
        value = value.replace(_WIRE_BASE_PATH, _WIRE_BASE_PATH_MASK)
        for old, new in _REBRAND_REPLACEMENTS:
            value = value.replace(old, new)
        return value.replace(_WIRE_BASE_PATH_MASK, _WIRE_BASE_PATH)
    return value

# route -> (qubit route, disposition, note)
ROUTES = {
    "/chat": ("/chat", "adapted",
              "Qubit-safe native chat: POST /api/qubit/chat "
              "(voicebridge.agent.turn, the same module as CLI text and voice "
              "PTT) with typed loading/empty/error/retry states, browser speech "
              "input, per-session transcript persistence, and the sessions "
              "switcher. /api/pty is refused (PTY_NO_SHELL); the page no longer "
              "opens or retries it."),
    "/sessions": ("/sessions", "adapted",
                  "sessions op (receipt sessions) + actions receipts; "
                  "Hermes rename/export/import/prune/search/stats map to "
                  "memory + task stores per PARITY_HERMES sessions row."),
    "/files": ("/projects", "adapted",
               "files_inspect/list/read (project-root confined); Hermes "
               "mkdir/upload-stream refused (write policy, no bypass)."),
    "/analytics": ("/activity+/reports+/memory", "partial",
                   "insights/activity/reports/logs journal-derived views; "
                   "token-billing charts omitted (local runner has no token "
                   "economy; owner: local-runner)."),
    "/models": ("/models", "adapted",
                "frontier/config_profiles/reasoning/prompt_budget + "
                "capability/mcp/proxy groups."),
    "/logs": ("/system+/activity", "adapted", "logs op + live event feed."),
    "/cron": ("/tasks", "adapted",
              "schedules_* ops + opencode/task_run; blueprints map to "
              "task-templates + schedules_create."),
    "/skills": ("/skills", "adapted",
                "skills_*/verified/curator/plugins_install/hooks; hub "
                "fetch refused (reviewed-only, tested)."),
    "/plugins": ("/skills", "adapted", "reviewed-install flow."),
    "/mcp": ("/models", "adapted",
             "mcp inventory; server mount/execution refused (direct-MCP "
             "bypass policy)."),
    "/channels": ("/messages", "adapted", "msg_* inbox/draft/send/approve."),
    "/webhooks": ("/messages", "adapted",
                  "webhook list/show/add/remove/test + verify extra."),
    "/pairing": ("/messages+/token-setup", "adapted",
                 "pairing list/create/approve/revoke + device ceremony."),
    "/profiles": ("/memory", "adapted", "brain_* profiles/ Vaults."),
    "/profiles/new": ("/memory", "adapted",
                      "brain_onboard_plan + onboard_apply form."),
    "/config": ("/settings", "adapted",
                "profiles/models/audio/permissions/power/export/rollback; "
                "raw-config dump refused (secret policy)."),
    "/env": ("/security", "adapted",
             "key-reference status only; value reveal refused (secret "
             "policy)."),
    "/system": ("/system", "adapted",
                "runtime/doctor/product/package/verify/backup/sync/etc.; "
                "gateway process control refused (single event path)."),
    "/docs": ("/docs", "adapted", "Qubit docs index + safe render."),
}

# endpoint prefix -> (qubit op group or REFUSED/OMITTED code, note)
ENDPOINTS = {
    "/api/actions/": ("actions", "adapted"),
    "/api/analytics": ("insights/activity/reports", "partial:no-token-charts"),
    "/api/auth/me": ("auth_status", "adapted"),
    "/api/auth/providers": ("auth_login", "adapted:key-refs"),
    "/api/auth/ws-ticket": ("pairing", "adapted:ticket-model"),
    "/api/chat/image-upload": ("vision_describe", "adapted:bounded"),
    "/api/config": ("settings/product", "adapted"),
    "/api/config/raw": ("—", "REFUSED:CONFIG_RAW (secret policy)"),
    "/api/config/schema": ("settings", "adapted:forms"),
    "/api/config/defaults": ("setup_check/product", "adapted"),
    "/api/console": ("—", "OMITTED:PTY (browser PTY spawn refused; transcript+TUI equivalent)"),
    "/api/credentials/pool": ("—", "REFUSED:CREDENTIAL_POOL (credential export forbidden)"),
    "/api/cron/blueprints": ("schedules+task-templates", "adapted"),
    "/api/cron/delivery-targets": ("monitor", "adapted:quiet-hours"),
    "/api/cron/jobs": ("schedules", "adapted"),
    "/api/curator": ("curator/verified", "adapted"),
    "/api/dashboard/agent": ("worker/agent", "adapted"),
    "/api/dashboard/font": ("themes", "adapted"),
    "/api/dashboard/theme": ("themes", "adapted"),
    "/api/dashboard/plugin": ("plugins_install/skills", "adapted:reviewed"),
    "/api/dashboard/plugins": ("skills/verified", "adapted:list"),
    "/api/dashboard/plugins/hub": ("—", "REFUSED:REMOTE_PLUGIN (reviewed-only, tested)"),
    "/api/dashboard/agent-plugins/install": ("—", "REFUSED:REMOTE_PLUGIN (reviewed-only, tested)"),
    "/api/dashboard/plugin-providers": ("plugins_install", "adapted:reviewed"),
    "/api/env": ("secrets/auth_status", "adapted:key-refs"),
    "/api/env/reveal": ("—", "REFUSED:ENV_REVEAL (secret policy)"),
    "/api/events": ("events/stream", "adapted:SSE journal"),
    "/api/ws": ("events/stream", "adapted:single authority"),
    "/api/pub": ("events/stream", "adapted:single authority"),
    "/api/files": ("files", "adapted:read-only"),
    "/api/files/mkdir": ("—", "REFUSED:FILE_WRITE (write policy)"),
    "/api/files/upload-stream": ("—", "REFUSED:FILE_WRITE (write policy)"),
    "/api/gateway": ("gateway", "adapted:records"),
    "/api/gateway/start": ("—", "REFUSED:GATEWAY_SUPERVISION (single event path, tested)"),
    "/api/gateway/stop": ("—", "REFUSED:GATEWAY_SUPERVISION (single event path, tested)"),
    "/api/gateway/restart": ("—", "REFUSED:GATEWAY_SUPERVISION (single event path, tested)"),
    "/api/hermes/update": ("product/update_status", "adapted"),
    "/api/logs": ("logs", "adapted"),
    "/api/mcp": ("mcp", "adapted:inventory"),
    "/api/mcp/catalog/install": ("—", "REFUSED:REMOTE_MCP (never mounts/fetches)"),
    "/api/mcp/oauth/flows": ("—", "REFUSED:REMOTE_MCP (never mounts/fetches)"),
    "/api/mcp/servers": ("mcp", "adapted:inventory"),
    "/api/memory": ("memory/brain", "adapted"),
    "/api/memory/reset": ("memory_forget/brain_profile_sweep", "adapted:scoped"),
    "/api/messaging/platforms": ("msg", "adapted"),
    "/api/messaging/telegram/onboarding": ("pairing/device", "adapted:refs"),
    "/api/messaging/whatsapp/onboarding": ("pairing/device", "adapted:refs"),
    "/api/model/": ("config_profiles/frontier", "adapted"),
    "/api/ops/backup": ("backup/export_state", "adapted:form-passphrase"),
    "/api/ops/checkpoints": ("checkpoints", "adapted"),
    "/api/ops/config-migrate": ("product_migrate", "adapted"),
    "/api/ops/debug-share": ("—", "REFUSED:DEBUG_SHARE (exfiltration policy)"),
    "/api/ops/doctor": ("doctor", "adapted"),
    "/api/ops/dump": ("—", "REFUSED:STATE_DUMP (secret policy)"),
    "/api/ops/hooks": ("hooks", "adapted"),
    "/api/ops/import": ("import_state", "adapted:form-passphrase"),
    "/api/ops/import-upload": ("—", "REFUSED:FILE_WRITE (server-side bundles only)"),
    "/api/ops/prompt-size": ("prompt_budget", "adapted"),
    "/api/ops/security-audit": ("security/verify_release", "adapted"),
    "/api/pairing": ("pairing", "adapted"),
    "/api/plugins/kanban/events": (
        "qubit-boards (ops) + /api/events SSE",
        "partial: plugin WS upgrade refused by the single event path; board "
        "reads stay live via /api/plugins/kanban/board and all mutations run "
        "through /api/ops/* + /api/ops/confirm"),
    "/api/portal": ("—", "OMITTED:EXTERNAL_PORTAL (external account console; guidance only)"),
    "/api/profiles": ("brain/config_profiles", "adapted"),
    "/api/providers/oauth": ("auth_login", "adapted:key-refs,live-dance-provider-side"),
    "/api/pty": (
        "POST /api/qubit/chat",
        "REFUSED:PTY_NO_SHELL (Qubit-safe equivalent = native chat turn; "
        "frontend no longer connects the raw WS)"),
    "/api/sessions": ("sessions/memory", "adapted:receipts+stores"),
    "/api/skills": ("skills", "adapted"),
    "/api/skills/hub": ("—", "REFUSED:REMOTE_PLUGIN (reviewed-only, tested)"),
    "/api/skills/toggle": ("skills_enable/disable", "adapted"),
    "/api/status": ("status", "adapted"),
    "/api/system/stats": ("runtime/status", "adapted"),
    "/api/tools/toolsets": ("permissions/capability", "adapted:grants"),
    "/api/webhooks": ("webhook", "adapted"),
}


def inventory_files():
    items = []
    for path in sorted(ORACLE.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ORACLE).as_posix()
        data = path.read_bytes()
        suffix = path.suffix
        if rel.startswith("pages/"):
            kind = "test" if rel.endswith(".test.tsx") else "page"
        elif rel.startswith("components/"):
            kind = "test" if ".test." in rel else "component"
        elif rel.startswith(("contexts/", "themes/")):
            kind = "context"
        elif rel.startswith("i18n/"):
            kind = "i18n"
        elif rel.startswith(("hooks/", "lib/")):
            kind = "test" if ".test." in rel else "lib"
        elif rel in ("App.tsx", "main.tsx", "index.css"):
            kind = "shell"
        else:
            kind = "other"
        if kind == "i18n" and path.name != "en.ts" and "context" not in rel and "index" not in rel and "types" not in rel and "define-locale" not in rel:
            disposition = "OMITTED:I18N (23 non-English locales; English-first UI, owner decision)"
        elif kind == "test":
            disposition = "covered-by-qubit-tests (behavior re-proven in Qubit suites, not ported)"
        else:
            disposition = ("vendored Hermes React/Vite source (byte-identical "
                           "or documented Qubit patch; see "
                           "docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.json; "
                           "routes/endpoints below)")
        items.append({
            "path": f"web/src/{rel}",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "kind": kind,
            "disposition": disposition,
        })
    return items


def main():
    if not ORACLE.is_dir():
        raise SystemExit(f"oracle missing: {ORACLE}")
    rev = __import__("subprocess").run(
        ["git", "-C", str(ORACLE.parents[1]), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip()
    assert rev == REVISION, f"oracle pin drift: {rev}"
    doc = {
        "source": {"repo": REPO_URL, "revision": REVISION,
                   "tag": "v2026.9.7", "license": "MIT (Nous Research)",
                   "oracle": str(ORACLE), "verified_rev": rev},
        "adaptation_rule": (
            "Per the owner amendment 2026-09-11 and "
            "docs/archive/2026-09-21/QUBIT_DASHBOARD_REBUILD_PLAN.md, the pinned Hermes "
            "React/Vite/Node frontend is vendored into frontend/qubit-web "
            "(from hermes-agent/web) and frontend/qubit-shared (from "
            "hermes-agent/qubit-shared) and is built with Vite; React/TS is a "
            "supported Qubit dashboard runtime and the former static-only "
            "runtime constraint no longer applies. Every upstream file maps to "
            "a byte-identical vendored file or a documented Qubit "
            "patch/rebrand carrying upstream+Qubit hashes, a reason, and an "
            "affected regression test in docs/archive/2026-09-21/HERMES_SOURCE_COMPARISON.json; "
            "routes/endpoints additionally map to shared Qubit backend "
            "operations, tested policy refusals, or documented omissions."),
        "routes": {k: {"qubit": v[0], "disposition": v[1], "note": v[2]}
                   for k, v in ROUTES.items()},
        "endpoints": {k: {"qubit": v[0], "disposition": v[1]}
                      for k, v in ENDPOINTS.items()},
        "files": inventory_files(),
    }
    out = REPO / "docs/archive/2026-09-21" / "HERMES_FRONTEND_MAP.json"
    # The map also carries hand-maintained audit sections (vendoring hashes,
    # inventory detail, per-plugin audits, open items, live-verification
    # records). Regeneration owns the mechanical keys above and must preserve
    # those audited sections verbatim rather than dropping them.
    preserved_keys = ("vendoring", "inventory", "achievements_audit",
                      "missing_paths", "frontend_repairs", "kanban_audit",
                      "live_verification")
    if out.is_file():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except ValueError:
            existing = {}
        for key in preserved_keys:
            if key in existing:
                doc[key] = _canonicalize(existing[key])
        audit = doc.get("achievements_audit")
        if isinstance(audit, dict) and isinstance(audit.get("categories"),
                                                   list):
            audit["categories"] = [
                _REBRAND_CATEGORY.get(c, c) for c in audit["categories"]]
    out.write_text(json.dumps(doc, indent=1) + "\n")
    kinds = {}
    for f in doc["files"]:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    print(f"files: {len(doc['files'])} {kinds}")
    print(f"routes: {len(doc['routes'])} endpoints: {len(doc['endpoints'])}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
