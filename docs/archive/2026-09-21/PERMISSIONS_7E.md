# Permissions & Skills — Packet 7E

Status: **IMPLEMENTED** — Packet 7E (permission profiles, scoped grants, and
versioned skill manifests). Source contract: `docs/UNIFIED_AGENT_PLAN.md`
Phase 7 tasks 20–26 and the prompt-pack Packet 7E prompt.

Scope: this packet adds the typed contract + durable store + CLI + dashboard +
tests. It does **not** add any live website/app/editor integration and does not
change live services, accounts, Chromium, timers, or user config.

## 1. The two permission modes

- **restricted** (default): only explicitly granted capabilities and scopes
  work. A read-only/reversible capability inside a matching, non-expired grant
  is allowed; mutating/destructive/financial capabilities additionally require
  user confirmation.
- **unrestricted** (opt-in): pre-authorizes routine **non-destructive**
  (read-only/reversible) capabilities inside **user-selected scopes** for a
  time-bounded window (`ttl_s`) or until reset. It never bypasses:
  - hard-deny capability patterns (`HARD_DENY_AUTO` in
    `voicebridge/permissions.py` — send/publish/purchase/delete/credential/
    account/security/system), which always require confirmation;
  - schema validation, redaction, audit, isolation/resource limits, loopback
    boundaries, or confirmation for mutating/destructive/financial work.

An expired unrestricted mode **fails closed to restricted**.

Model output, page text, tool results, skill instructions, and generated plans
can never change the mode or widen a grant: `PermissionPolicy.decide()` accepts
only typed capability/scope/args and consults the durable store.

## 2. CLI surface

```
voicebridge permissions mode show|set <restricted|unrestricted> [--scope S ...] [--ttl SEC] [--confirm]
voicebridge permissions list
voicebridge permissions show <grant-id>
voicebridge permissions check <capability> [--scope S ...]
voicebridge permissions grant <capability> --scope S [--scope S ...] [--ttl SEC] [--confirm]
voicebridge permissions revoke <grant-id> [--confirm]
voicebridge permissions reset [--confirm]
voicebridge skills list|show <id>|check <manifest.json>
voicebridge skills install <manifest.json> [--confirm]
voicebridge skills enable|disable|remove|rollback <skill-id> [--backup PATH] [--confirm]
```

Rules: mutating commands are **dry-run by default** (preview output, no
persist) and require `--confirm` to persist. Every mutation writes a
timestamped `.bak`, persists atomically (`tmp` + `os.replace`), and appends a
bounded audit entry. Exit codes: `0` OK · `1` missing · `2` error/validation ·
`3` confirmation required.

## 3. Skill manifests

`SkillManifest` (`voicebridge/permissions.py`) validates before registration:
skill_id/version, source/revision, **license**, **content_hash (sha256)**,
capabilities, scopes, side-effect `tier`, `sensitivity`, `approval_policy`,
`timeout_s`, `resource_limits`, `audit_events`, `compatibility`, and
`rollback`/removal behavior. Invalid, unlicensed, or hash-mismatched manifests
are rejected. Installs are **staged disabled** until explicit user enablement;
a skill never becomes active by itself and never executes package hooks.

Example manifest:

```json
{
  "skill_id": "weather.editor", "version": "1.0.0", "name": "Weather Editor",
  "description": "synthetic skill", "source": "https://example.test/skills",
  "revision": "abc123", "license": "MIT",
  "content_hash": "<sha256-of-payload>",
  "capabilities": ["browser.snapshot"], "scopes": ["browser"],
  "tier": "read_only", "sensitivity": "internal", "approval_policy": "auto",
  "timeout_s": 30.0, "resource_limits": {"max_bytes": 4096},
  "audit_events": ["skill.run"], "compatibility": "linux",
  "rollback": "remove restores prior manifest state"
}
```

## 4. Dashboard parity

`GET /api/permissions` returns the typed state (mode, mode scopes/expiry,
grants, skills, audit, effective values, plus the 7D-compatible `items` view).
`POST /api/permissions` performs `set-mode`, `grant`, `revoke`, `reset`,
`skill-install`, `skill-enable`, `skill-disable`, `skill-remove`,
`skill-rollback`. Every mutation requires `confirmed: true` (else
`state: denied / CONFIRMATION_REQUIRED`) and the header token
(`X-Api-Token`). The dashboard panel in `frontend/index.html` shows a beginner
view (plain-language Restricted/Unrestricted, grants, risk warning, revoke/reset)
and an advanced view (effective check, audit). Frontend-only toggles are
rejected: every control calls the real backend and renders typed states.

## 5. Configuration

```yaml
permissions:
  state_path: ""          # default <state_dir>/permissions.json (private, atomic, audited)
  default_mode: restricted
```

## 6. Extension workflow (new site/app/editor)

1. Manifest first (validated, licensed, hashed) → staged disabled.
2. Smallest adapter or declared launcher; a launcher that only opens an app
   must not claim app control.
3. Register the capability + permission grant (scoped, expiring).
4. Route/tool schema; synthetic fixtures; negative/security/persistence/
   resource tests; audit; docs; staged canary.
5. Browser skills use the approved existing-Chromium/CDP contract; editor
   skills use explicit workspace scopes; terminal skills use declared argv or
   the isolated runner.

## 7. Rollback

- `permissions reset` returns to restricted and revokes every grant (backed
  up). `permissions revoke <id>` removes a single grant.
- `skills rollback <id>` restores a skill's previous status (or a snapshot
  from `--backup`); `skills remove` is backed up.
- Delete `voicebridge/permissions.py` + revert the CLI/webui/frontend/config/
  orchestrator edits to fully roll back the packet. No live service, account,
  Chromium, timer, or user config is touched by this packet.