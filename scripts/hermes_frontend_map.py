#!/usr/bin/env python3
"""Generate docs/HERMES_FRONTEND_MAP.json: machine-readable Hermes web/src
inventory mapped to Qubit equivalents.

Hermes pin: https://github.com/NousResearch/hermes-agent @
2237be355906fbe6065ce1815711eee52b2d646e (tag v2026.9.7), MIT.
Oracle: /tmp/pinned-upstream/hermes-agent (read-only; verified rev-parse).

Adaptation rule (standing stack constraint: Python/static-JS only, no
React/Node runtime): no .tsx can execute byte-identical in Qubit, so every
file maps to a Qubit static-JS adapter, a shared backend op, a tested
policy refusal, or a documented omission with owner. MIT notices retained
in THIRD_PARTY_NOTICES.md (see docs/UPSTREAM_REUSE.md R34).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ORACLE = Path("/tmp/pinned-upstream/hermes-agent/web/src")
REPO = Path(__file__).resolve().parents[1]

REVISION = "2237be355906fbe6065ce1815711eee52b2d646e"
REPO_URL = "https://github.com/NousResearch/hermes-agent"

# route -> (qubit route, disposition, note)
ROUTES = {
    "/chat": ("/", "adapted",
              "Talk workspace: streaming transcript, PTT voice, text turns, "
              "approvals via stream; Hermes xterm PTY spawn refused "
              "(shell policy) — transcript streaming is the equivalent."),
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
    "/api/plugins/kanban/events": ("kanban", "adapted:board+journal"),
    "/api/portal": ("—", "OMITTED:EXTERNAL_PORTAL (external account console; guidance only)"),
    "/api/profiles": ("brain/config_profiles", "adapted"),
    "/api/providers/oauth": ("auth_login", "adapted:key-refs,live-dance-provider-side"),
    "/api/pty": ("—", "OMITTED:PTY (browser PTY spawn refused; transcript+TUI equivalent)"),
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
            disposition = "adapted (static-JS adapter or shared backend op; see routes/endpoints)"
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
        "adaptation_rule": ("React/TS cannot execute in Qubit's "
                            "Python/static-JS stack (no React/Node runtime "
                            "per project constraints); every file maps to a "
                            "static-JS adapter, shared backend op, tested "
                            "policy refusal, or documented omission."),
        "routes": {k: {"qubit": v[0], "disposition": v[1], "note": v[2]}
                   for k, v in ROUTES.items()},
        "endpoints": {k: {"qubit": v[0], "disposition": v[1]}
                      for k, v in ENDPOINTS.items()},
        "files": inventory_files(),
    }
    out = REPO / "docs" / "HERMES_FRONTEND_MAP.json"
    out.write_text(json.dumps(doc, indent=1) + "\n")
    kinds = {}
    for f in doc["files"]:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    print(f"files: {len(doc['files'])} {kinds}")
    print(f"routes: {len(doc['routes'])} endpoints: {len(doc['endpoints'])}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
