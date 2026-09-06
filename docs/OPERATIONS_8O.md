# Power Profile and One Policy Path (Packet 8O) — operations, security, rollback

Status: **IMPLEMENTED (hermetic core, operator-only manual tests remaining)** —
explicit authenticated power profile, one policy path for
voice/dashboard/CLI/MCP/tray/automation, the owner power grant, and global
power control. All capabilities remain **DISABLED / default deny**; "available"
never means "authorized". No subprocess, HTTP server, browser, account, service,
timer, model, audio, or user config is started or changed by this packet. Live
qualification (real Chromium/Comet/hardware/accounts) is a separate,
operator-authorized step.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §8O and the prompt-pack Packet 8O
prompt. Reuses Packet 7E's `PermissionsStore`/`ActionGate`, Packet 8F's
`ActionSpec`/`ActionRunner`, Packet 8N's `SessionGrantStore`/`GhostGate`, and
Packet 1A's `ConfirmationStore`/`CapabilityRegistry`.

## 0. What changed in the 8O repair (close-out)

The initial 8O hermetic layer was strengthened so the one policy path is real
for every channel:

- **One shared, correctly scoped `PowerControl`/`PowerPolicy` instance.** CLI,
  dashboard, voice, MCP, tray, automation, Ghost, and action executors all
  resolve the same process-wide instance for a config scope
  (`power_control(cfg)` / `power_policy(cfg)` return a cached singleton).
  Emergency stop, disable, circuit breakers, concurrency, and cancellation now
  affect every channel consistently.

### Cross-process, restart-persistent emergency control (8O repair)

A process-local Python singleton is **not** sufficient for the emergency-stop /
global-disable latches: the CLI, dashboard, bridge, voice, tray, Ghost/Comet,
and automation run as separate processes and must observe each other's control
actions. Packet 8O now persists the latched control state durably:

- **`PowerControlStore`** writes the emergency-stop / global-disable latches to
  `<state_dir>/power-control.json` (private `0600`) under a single cross-process
  flock with unique exclusive temp files, file + directory fsync, atomic rename,
  typed durability failures, and try/finally lock release on every path.
- **`power_control(cfg)`** binds the shared `PowerControl` to that durable store
  and reloads it on construction, so a CLI/dashboard emergency stop is observed
  by the running bridge / voice / tray / Ghost / Comet / automation and survives
  a restart of any one of them. `PowerPolicy.decide` and `CapabilityBroker`
  refresh the latch (short TTL) before every decision/execution.
- **Fail-closed load and refresh.** A corrupt, unknown-field, newer-schema, or
  forged-author durable control file NEVER loads as "cleared" — it latches
  emergency stop + global disable (deny). Writing over a corrupt underlying
  state is refused, so a reset can never silently clear an unreadable latch.
- **Explicit human authorization.** `emergency_stop` / `global_disable` /
  `global_enable` / `reset` require an authenticated human principal
  (`user` / `cli:user` / `dashboard`); the CLI requires `--confirm` and the
  dashboard POST requires `confirmed:true`. A model/page/tool/skill/automation
  author is refused with `CONFIRMATION_SOURCE_FORBIDDEN`.
- Circuit breakers and bounded-concurrency slots remain process-local runtime
  state (they track live in-process runs); the durable latches that must span
  processes and restarts are the emergency stop and the global disable.

### Tray and MCP/Comet scope (explicit)

- **Tray.** This repository ships **no executing tray controller** — `tray` is
  an optional, status-only diagnostic (`pystray` presence). `tray_status()`
  reports `executes_actions: False` truthfully; it never starts a second
  bridge/listener/browser and never touches cookies, tokens, or private page
  data. Any operator tray menu action must route through the shared
  authenticated power CLI/API (one policy path). The verification contract
  treats tray as a status-only diagnostic, not a policy path.
- **MCP/Comet.** No MCP server is intentionally shipped. `mcp_status()` reports
  a truthful typed `UNAVAILABLE`/`DISABLED` state (`server: "none"`); it never
  claims a fake "connected" MCP policy path. The Comet research path is
  broker-bound through `GhostRunner` (the one policy path) and disabled by
  default. The verification contract states this explicitly.
- **Real executors wired through the policy before execution.** `CapabilityBroker`
  (host tools + actions + Ghost/Comet) checks the shared control before every
  run (emergency stop -> disable -> circuit breaker), tracks each real run for
  cancellation (`track`/`untrack`), records circuit failures, and enforces
  bounded concurrency. `ActionRunner`/`GhostRunner` accept a `power` policy and
  route preview/check/execute/retry/recover through `PowerGateAdapter` — the
  same typed decision the CLI/dashboard/voice return. The voice action preview
  path and the dashboard `/api/actions` check path were repaired to use the
  shared policy (they previously used a plain runner).
- **No execution bypass (`safety.tool_broker=false`).** The voice local-action
  and local-script paths NO LONGER have an unsecured `local_actions.execute` /
  `run_tool(cmd)` shell fallback. When the broker is unavailable or
  `safety.tool_broker=false`, they FAIL CLOSED with a typed `BROKER_DISABLED`
  denial — a direct adapter or raw shell route can never execute around the
  broker, grants, scopes, confirmation, audit, resource limits, or emergency
  stop. Regression tests prove no action/script executes with the broker
  disabled.
- **Automation is on the one policy path.** The hourly communications monitor
  (`run_hourly`, the automation channel) consults the shared DURABLE
  power-control latch before any scan: a latched emergency stop / global
  disable (or an unreadable/non-durable control state) fails closed with a
  typed `EMERGENCY_STOP` / `POWER_DISABLED` denial and no browser read ever
  happens.
- **Owner-grant issuer is strictly human.** `PowerGrantStore.issue/revoke/reset`
  require an authenticated human principal via `actions.require_human_principal`
  (`user`, `cli:user`, `dashboard`). Model, page, tool, skill, automation, and
  arbitrary text issuers are refused with `CONFIRMATION_SOURCE_FORBIDDEN` before
  any state change.
- **Grants bind exactly.** A grant must name exact capability ids and may bind
  exact argument hashes; origins are canonical (multi-label host, no path/query/
  fragment/credentials/IP/wildcard/ambiguous subdomain); roles, accounts,
  directories, and scopes are matched with exact boundaries. Matching is exact on
  capability, scope, target/origin, role, account, directory, and optional
  argument hash — the grant never widens silently and never auto-allows a
  high-impact action.
- **High-impact confirmations are verified, not assumed.** `PowerPolicy` verifies
  a presented confirmation id against the `ConfirmationStore`/broker store
  (existence, single-use, expiry, exact capability + envelope digest) before the
  gate allows; bogus/stale/replayed/changed-argument ids never pass. Nothing is
  consumed in the policy — the broker consumes once at execution.
- **Audit persistence fails closed.** `PowerPolicy` writes every decision to a
  durable `0600` JSONL (`<state_dir>/power-audit.jsonl`); a write failure raises
  a typed `AUDIT_PERSIST_FAILED` — never silently swallowed. Grant-store write
  failures already raise typed `PowerError`s.
- **Truthful inventory.** Ghost capabilities are reported `DISABLED` (adapter is
  disabled by default) instead of `AVAILABLE`; 8F action adapters are
  `DISABLED` unless `actions.enabled`; declared config rows report `AVAILABLE`
  (configured + present) — never `ENABLED` (running) without a live probe;
  `configured != running` and `authorized != available` are never conflated;
  `authorized` counts only rows covered by an active grant.
- **MCP/Comet typed.** There is no MCP server in this repository. `mcp_status()`
  reports a truthful `UNAVAILABLE`/`DISABLED` state and the Comet research path
  stays broker-bound (through `GhostRunner`) — no direct adapter calls and no
  fake "connected" success.
- **Secret hygiene.** Grants persist redacted notes (`0600`); audit records and
  all power decision fields pass through `redact_text`; receipts already redact
  private args; no cookie/token/page-text/message-body is written by this packet.

### Source identity is preserved through every layer

`model`, `automation`, `page`, `tool`, `skill`, and arbitrary text are NEVER
coerced to `user`. The gate adapter and policy route the ORIGINAL source into
the decision:

- an owner grant is auto-allowed only for an authenticated human (`user`) or the
  read-only `check` probe — non-human sources can never consume a grant;
- high-impact actions require `source == "user"` AND a fresh single-use
  confirmation verified against the canonical envelope (capability, complete
  arguments, target/origin, account, role, scope, action hash, expiry, human
  issuer);
- the shared 7E/8F/8N gate sees the original source, never a coerced `user`.

### Canonical origins and the explicit subdomain policy

- `http` allows only port 80; `https` allows only port 443.
- Paths, queries, fragments, credentials, IP literals, wildcards, malformed
  hosts, empty labels, and bare single-label hosts are rejected.
- Subdomains ARE allowed by default, but only at a label boundary
  (`target == allowed` or `target.endswith("." + allowed)`); lookalike hosts
  (`notgmail.com`, `perplexity.ai.evil.com`) never match. Exact-host matching is
  available via `allow_subdomains=False`. Every caller passes this explicitly;
  subdomains are never silently treated as exact matches.

### Lock, persistence, and audit durability

- The grant store releases its cross-process flock with `try/finally` on every
  path (success, read failure, mutate failure, write/rename/fsync failure, and
  exception) — a failed issue/revoke/reset can never hang the next operation.
- The durable power audit is a `0600` JSONL appended under a cross-process
  flock with file + directory fsync and complete-write accounting; a persistence
  failure raises a typed `AUDIT_PERSIST_FAILED` and no record is duplicated.

## 1. What the power profile does

`voicebridge/power.py` provides three coordinated pieces:

1. **PowerProfile (`power_profile`)** — a typed inventory of every installed
   capability across all **11 power domains** (`memory, browser, desktop,
   editor, file, network, shell, account, notifications, audio, model`). Each
   row carries `adapter`, `adapter_version`, `scope`, `target_or_origin`,
   `risk_tier`, `state`, `expiry`, `resource_limits`, `audit_status`, and
   `revocable`/`disableable` controls. **Available never means authorized**:
   the inventory reports `available`/`enabled`/`authorized` separately and the
   only way a row becomes `AUTHORIZED` is through an actual grant decision.
2. **Owner power grant (`PowerGrantStore`)** — one explicit, expiring,
   revocable, target-bound grant selected by the operator (browsers, apps,
   accounts, origins, directories, scopes, **exact capabilities**, optional
   **exact argument hashes**, duration). Inside the grant, routine
   read/search/navigation and exact-target non-sensitive form filling are
   auto-allowed. **Drafting stays local and non-external.** Posting, sending,
   publishing, purchasing, deleting, and modifying project code ALWAYS require
   a fresh human confirmation bound to the exact action hash — even inside the
   grant. The grant never widens silently: matching is exact on capability,
   scope, origin/role/account/directory, argument hash, tier, and expiry.
3. **PowerControl + PowerPolicy** — global emergency stop (latched, cancels
   tracked real runs), global disable, per-adapter circuit breakers (fail
   closed when open), bounded concurrency, kill/reap tracking, restart recovery,
   durable fail-closed audit, and the **one decision path** every channel must
   call before executing:

```text
global emergency stop  ->  global disable  ->  per-adapter circuit breaker
->  bounded concurrency  ->  capability known  ->  owner power grant routine
   auto-allow (read/search/navigate/fill)  ->  shared 7E/8F/8N gate
   (session grants + explicit grants + fresh confirmation for high-impact)
```

## 2. Configuration

No live config is edited and no new service is created. The grant persists to
`<state_dir>/power-grant.json` (private `0600`, atomic JSON, single
cross-process flock, unique exclusive temp file, file + directory fsync, typed
durability failures, fail-closed load). Grant default duration is 7 days
(`power.grant_ttl_s`), hard ceiling 90 days. Global power-control state is
typed and **durable**: the emergency-stop / global-disable latches persist to
`<state_dir>/power-control.json` (private `0600`, atomic, cross-process flock,
fail-closed load) so they survive restart and are observed by every channel;
circuit breakers and concurrency slots are process-local runtime state. The
dashboard/CLI show both read-only.

## 3. CLI

```text
voicebridge power profile                        # inventory all 11 domains
voicebridge power status                         # control + grant + health
voicebridge power circuits                       # per-adapter breakers
voicebridge power show <capability>              # one capability contract
voicebridge power check <capability> [--scope S] [--target T] [--role R] [--account A]
voicebridge power grant [--browser chromium|comet] [--app APP] [--account A]
                        [--origin URL] [--directory DIR] [--scope S]
                        [--capability CAP] [--ttl SECONDS] [--confirm] [--note TEXT]
voicebridge power revoke <grant_id> [--confirm]
voicebridge power reset-grant [--confirm]
voicebridge power emergency-stop --confirm | reset --confirm | disable --confirm | enable --confirm
```

`grant`/`revoke` are dry-run by default and require `--confirm`. `--capability`
is required and must name exact capability ids (e.g. `ghost.comet.ask`);
the grant is issued by the authenticated human CLI channel (`cli:user`).
`check` is the read-only single policy path: it returns `ALLOWED` (owner-grant
routine), `CONFIRMATION_REQUIRED` (high-impact), or a typed denial
(`EMERGENCY_STOP`, `POWER_DISABLED`, `CIRCUIT_OPEN`, `CONCURRENCY_LIMIT`,
`NOT_GRANTED`, `UNKNOWN_CAPABILITY`, `CONFIRMATION_SOURCE_FORBIDDEN`,
`TARGET_MISMATCH`, `EXPIRED_CONFIRMATION`, `REPLAYED_CONFIRMATION`). Dashboard
parity: the authenticated `/api/power` GET endpoint returns the same
profile/status and `?capability=…` performs the same `check`; POST performs the
same mutating actions with explicit confirmation. Voice requests flow through
the same `PowerPolicy`/runner gate (`action_runner(..., power=…)` in the
orchestrator).

## 4. Security invariants

- Available != authorized: the profile exposes states and controls; nothing in
  this packet enables or executes a capability.
- One policy path: `PowerPolicy.decide` is the only decision entry for voice,
  dashboard, CLI, MCP, tray, automation, Ghost, and action executors. The
  shared `CapabilityBroker` checks emergency stop / disable / circuit / bounded
  concurrency before every real run and tracks it for cancellation. Model/page/
  skill/tool text is never an input to the decision (only typed capability/
  scope/target/role/args are).
- Owner-grant issuance is human-only: `issue`/`revoke`/`reset` reject model,
  page, tool, skill, automation, and arbitrary-text issuers with
  `CONFIRMATION_SOURCE_FORBIDDEN` before any state change.
- Grants bind exactly: required exact capability ids, optional exact argument
  hashes, canonical origins (multi-label host, no path/query/fragment/
  credentials/IP/wildcard/ambiguous subdomain), and exact scope/role/account/
  directory boundaries. Matching is exact — the grant never widens silently and
  never auto-allows a high-impact action.
- High-impact actions (post/send/publish/purchase/delete/project-code) always
  require a fresh human confirmation bound to the exact action hash; the power
  grant can never auto-allow them. The presented confirmation is VERIFIED
  against the confirmation store (existence, single-use, expiry, exact
  capability + envelope digest) before the gate allows — bogus/stale/replayed/
  changed-argument ids are rejected with typed codes.
- The grant fails closed on load: forged owner, unknown fields, newer schema,
  wildcard target/scope, invalid origin/role/directory/capability, impossible
  TTL, and expired records never load more authority than a fresh grant. A
  corrupt/unreadable grant file degrades the store and returns None.
- Emergency stop is a global latch that cancels tracked real runs through the
  caller's own cancel callbacks (registered by the broker for every execution)
  and persists until an explicit human reset. The latch is DURABLE: it is
  written to `power-control.json` under a cross-process flock and reloaded on
  restart, so a CLI/dashboard emergency stop is observed by the running bridge,
  voice, tray, Ghost/Comet, and automation and survives a restart of any one of
  them. Corrupt / unknown-field / newer-schema / forged-author control state
  fails closed (never silently clears the latch). Reset / disable / enable all
  require explicit human confirmation (`--confirm` / `confirmed:true`).
- Tray: no executing tray controller is shipped; `tray_status()` is a
  status-only diagnostic (`executes_actions: False`), never starts a second
  bridge/listener/browser, never touches cookies/tokens/private page data, and
  any tray action must use the shared authenticated power CLI/API.
- MCP/Comet: no MCP server is shipped; `mcp_status()` reports a truthful typed
  `UNAVAILABLE`/`DISABLED` state (`server: "none"`) — no fake "connected"
  policy path. The Comet research path is broker-bound through `GhostRunner`
  (one policy path) and disabled by default.
- Circuit breakers trip after repeated adapter failures and deny while open.
- Bounded concurrency fails closed (no unbounded queuing).
- Audit is durable and fail-closed: every power decision is appended to a `0600`
  JSONL; a write failure raises `AUDIT_PERSIST_FAILED` (never swallowed). All
  journal/audit fields pass through redaction; notes and targets are redacted;
  the grant file is `0600` and contains no secrets.

## 5. Rollback

Revert the new file `voicebridge/power.py`, `tests/test_packet8o.py`, and the
additive `cli.py`/`webui.py`/`actions.py`/`broker.py`/`ghost.py`/
`capabilities.py`/`orchestrator.py`/`product.py`/`communications/monitor.py`/
`frontend/settings.html` hunks.
There is no data migration; removing the packet leaves 8F/8N/7E behavior exactly
as it was. A durable `power-control.json` file left in the state dir is simply
ignored after rollback (the 8O control layer is the only reader). `git diff
--check`, `graft build`, and the hermetic suite
(`tests/test_packet8o.py` plus the 7E/8E/8F/8M/8N regressions) are the
verification gates.

## 6. Live qualification (NOT performed by this packet — operator steps)

The following Packet 8O qualification matrix is operator-only manual testing;
none of it can be reproduced hermetically and it must be performed on a
disposable/controlled environment with explicit operator authorization:

1. **Real browser roles.** Start Chromium with loopback CDP on `127.0.0.1:9222`
   (Gmail/Messenger/Instagram/marketplace profile) and the Comet research
   browser on `127.0.0.1:9223`. Log in manually and complete CAPTCHA/2FA.
   Run read-only scans and confirm: separate ports/profiles, target and account
   binding, no cross-profile cookies, no wrong-browser fallback, session
   expiry/reconnect/restart, and that a search/page result cannot authorize a
   tool. Verify read/fill/draft are session-grantable and post/send/purchase
   always require fresh approval.
2. **Real hardware/desktop sessions.** Exercise the microphone/output,
   X11/Wayland session resolution, GPU/model residency, memory provider, and
   latency + CPU/RAM/disk/network/context/screenshot ceilings.
3. **Live grant + confirmation round-trip.** Issue an owner grant from the
   authenticated dashboard, revoke it, confirm a high-impact action, and verify
   the typed `TARGET_MISMATCH`/`EXPIRED`/`REPLAYED` states against a real
   target change.
4. **Signed packaging / upgrade / rollback / uninstall** on a clean machine.
5. **Operator-controlled account canary** for Gmail/Instagram/Messenger new-item
   detection without reading message bodies.
6. **Cross-process emergency control (operator).** With the bridge running,
   issue `voicebridge power emergency-stop --confirm` from a separate CLI
   process and confirm the running bridge/dashboard/voice deny new runs and
   cancel active ones within the refresh window; restart the bridge and confirm
   the latch is still observed; `voicebridge power reset --confirm` clears it.
7. **Tray (operator).** Confirm the optional tray icon shows typed readiness
   only and that any menu action delegates to the authenticated power CLI/API
   (no second bridge/listener/browser, no cookie/token access).
8. **MCP/Comet (operator).** Confirm `mcp_status()` reports `UNAVAILABLE`/
   `DISABLED` (`server: "none"`) and that the only research route is the
   broker-bound Comet adapter via `GhostRunner` — no second policy path.

This packet implements the hermetic inventory, grant, control, policy, CLI,
dashboard, tests, and rollback only — nothing here launches, connects, or
enables a live capability.