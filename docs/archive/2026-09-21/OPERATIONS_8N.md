# Ghost Desktop / Browser Capability Adapter (Packet 8N) — operations, security, rollback

Status: **IMPLEMENTED** — optional, supervised Ghost desktop/browser capability
adapter. All capabilities are **DISABLED by default** and the shell capability
is **hard-disabled** (`GHOST_SHELL=off`). No Ghost subprocess, HTTP server,
browser, account, service, timer, model, audio, or user config is started or
changed by this packet. Live execution requires an operator-approved, pinned
adapter AND an injected executor — a separate approved rollout step.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §8N and the prompt-pack Packet
8N prompt. Reuses Packet 8F's `ActionBroker`/`ActionGate`/`ActionRunner`,
Packet 1A's `ConfirmationStore`, Packet 7E's `PermissionsStore`, and the
approved loopback-only CDP boundary.

## 1. What the Ghost adapter does

`voicebridge/ghost.py` registers Ghost capability contracts **through the
existing 8F action machinery** — same envelope, stable action hash, 7E gate,
fresh-confirmation binding, receipts, cancellation and audit. Each capability
has a scope, target/origin, risk tier, timeout, resource limit, cancellation
path, audit receipt, and adapter health state.

| capability | tier | scope | session grant | confirmation |
|---|---|---|---|---|
| `ghost.desktop.discover` | read_only | desktop | yes | no |
| `ghost.desktop.launch` | reversible | desktop | yes | no |
| `ghost.window.anchor` | reversible | desktop | yes | no |
| `ghost.browser.tabs` | read_only | web | yes | no |
| `ghost.browser.screenshot` | read_only | web | yes | no |
| `ghost.input.scroll` | reversible | web/desktop | yes | no |
| `ghost.input.click` | mutating | web/desktop | no | **always** |
| `ghost.input.type` | mutating | web/desktop | no | **always** |
| `ghost.clipboard.read` | mutating | web | no | **always** |
| `ghost.shell` | mutating | shell | no | **always** (only when `GHOST_SHELL=on`) |
| `ghost.comet.connect` | read_only | research | yes | no |
| `ghost.comet.disconnect` | read_only | research | yes | no |
| `ghost.comet.mode` | read_only | research | yes | no |
| `ghost.comet.ask` | read_only | research | yes | no |
| `ghost.comet.wait` | read_only | research | yes | no |
| `ghost.comet.extract` | read_only | research | yes | no |
| `ghost.comet.evidence` | read_only | research | yes | no |

Read-only/reversible capabilities may use a **bounded, expiring,
target/origin/role-bound session grant** (human-issued only). High-impact
capabilities (click/type/clipboard, and shell when enabled) always require a
**fresh human confirmation bound to the exact action hash**; a session grant
is never enough and there is no approve-everything switch.

### Two explicit browser roles (strict origin binding)

- **Chromium** (account browser): Gmail, Messenger, Instagram, marketplace
  observation — loopback CDP port `9222`, profile `chromium-account`,
  approved origins `gmail.com`/`mail.google.com`/`messenger.com`/
  `instagram.com`/`facebook.com`.
- **Comet** (research browser): Perplexity, web research, forums, public
  browsing — loopback CDP port `9223`, profile `comet-research`, approved
  origins `perplexity.ai`.

Binding is **strict**: targets are parsed with `urllib.parse` and validated
against the exact approved host or a legitimate subdomain boundary
(`_host_in_approved`). `evilgmail.com`, `gmail.com.evil.test`,
`notmail.google.com`, user-info URLs, malformed URLs, unexpected ports,
unsupported schemes, query/fragment token-bearing targets, IP literals, and
bare/non-URL targets all fail closed. Cookies/local storage are never copied
between roles and there is **no wrong-browser fallback** — a Chromium grant
can never execute against Comet or vice versa (enforced in `GhostGate` for
every role-bound operation and in the persisted session-grant validator).

### Role binding is real

The browser `role` is a **first-class binding field**: it is merged into the
validated envelope `args` so it participates in the stable action hash and
stays bound through preview, prepare/confirmation, execute, retry, recover,
receipts, the CLI, the dashboard, voice, and fixture executors. A role-swap
changes the hash and is refused as a stale target.

### mcp-comet research-only adapter

`CometResearchAdapter` is the research-only boundary: connect/disconnect,
select `research` mode, submit a bounded query, bounded wait/poll, extract
sources, extract readable page content, and optionally capture bounded
evidence. It is **disabled by default**; `computer` mode and any write-capable
tool are **refused until Packet 8O**. Every comet operation executes **only
through the bound GhostRunner's broker executor** — a direct object call fails
closed with `COMET_DIRECT_CALL_FORBIDDEN`. Extraction requires real source
text (an empty/ignored extraction returns the typed `NO_SOURCE` failure, never
fake success); evidence capture requires real screenshot bytes (absent or
malformed data returns `NO_EVIDENCE`/`BAD_EVIDENCE`); `wait` polls with typed
PENDING/RUNNING/COMPLETED/TIMEOUT/CANCELLED/UNAVAILABLE/PROVIDER_ERROR/
DISCONNECTED states and is interrupted by emergency stop. Page text,
citations, screenshots and results are untrusted evidence that can never
authorize an action.

## 2. Configuration

No live config is edited. The shell hard-disable uses an environment flag
(never stored in a Git-tracked file):

```text
VOICE_BRIDGE_GHOST_SHELL=off        # default; the shell capability is not
                                    # registered at all while off
VOICE_BRIDGE_GHOST_SHELL=on         # explicit operator enable only
```

Session grants persist to `<state_dir>/ghost-grants.json` (private `0600`
atomic JSON with a single cross-process flock held across read-merge-write,
unique exclusive temp files, file + directory fsync, and typed durability
failures). Every write re-reads and validates the LATEST on-disk grants under
the lock before merging, so independent processes never lose updates. Adapter
health is in-memory and typed; the dashboard/CLI show it read-only.

## 3. CLI

```text
voicebridge ghost list                     # capabilities + effective perms
voicebridge ghost status                   # adapter/browser-role/grant status
voicebridge ghost show <action_id>
voicebridge ghost check <action_id> [--scope S]
voicebridge ghost grant <capability> [--scope S] [--target T] [--role R] [--ttl TTL]
voicebridge ghost revoke <grant_id>
voicebridge ghost preview <action_id> [--args JSON] [--target T] [--scope S] [--role R]
voicebridge ghost execute <action_id> [--args JSON] [--target T] [--scope S]
                                      [--role R]
                                      [--confirm | --confirmation ID]
                                      [--fixture PATH]        # synthetic only
voicebridge ghost cancel <receipt_id>
voicebridge ghost receipts [--limit N]
voicebridge ghost enable | disable | emergency-stop | reset
```

`execute` is dry-run by default and every side-effecting path requires the 7E
gate plus (where required) a fresh confirmation. `--fixture` is the explicit
synthetic-simulation path; without it every capability returns the typed
`ADAPTER_DISABLED` result. Dashboard parity: the authenticated `/api/ghost`
endpoint (read-only) reports the same typed status and capability list.

## 4. Security invariants

- No bypass: every Ghost and Comet capability is a broker-bound
  `ToolBinding`; there is no direct HTTP/MCP/voice/CDP/subprocess execution
  path. Comet operations additionally refuse direct object calls
  (`COMET_DIRECT_CALL_FORBIDDEN`) and only the bound broker executor enters
  the adapter's authorized context.
- `GHOST_SHELL=off` by default removes the shell capability from the registry.
- Session grants are human-issued only, fail closed on load (forged issuer,
  unknown/high-impact capability, wildcard target, invalid role/scope,
  impossible timestamps/TTL/budget, malformed JSON, unknown fields, duplicate
  IDs), and never gain more authority than a fresh grant. Save failures are
  typed and a grant is never reported issued when persistence failed. Each
  write re-reads the latest on-disk state under a single flock and merges, so
  independent processes never lose updates; a failed persistence restores the
  in-memory map exactly (rollback).
- `unrestricted` mode never auto-authorizes Ghost capabilities: read/reversible
  Ghost ops require an explicit session grant or 7E grant, and high-impact ops
  still require a fresh confirmation. Unrestricted pre-auth cannot widen a
  grant or bypass Ghost default deny.
- High-impact capabilities always require a fresh confirmation bound to the
  exact action hash (target, origin, scope, arguments, role).
- Emergency stop is a global latch that **cancels real active runs** via the
  broker cancel callback (SIGKILL of the child process group for
  side-effect-capable tools; cooperative cancellation for read-only inline
  tools) and stays set until an explicit human reset.
- Loopback-only CDP (`127.0.0.1`/`::1`/`localhost`); public listeners are
  refused. No cookie import, no profile clone, no silent foreground focus,
  no second listener.
- Screenshots/clipboard are stored only as bounded, hashed evidence
  references — raw private bytes are never persisted.

## 5. Rollback

Revert the new files and the additive `actions.py`/`cli.py`/`webui.py`/
`broker.py` hunks. There is no data migration; removing the packet leaves 8F
behavior exactly as it was. `git diff --check`, `graft build`, and the
hermetic suite (`tests/test_packet8n.py`) are the verification gates.

## 6. Live rollout (NOT performed by this packet)

Enabling the real adapter is a separate, operator-authorized step: pin and
supervise a Ghost subprocess (or a reviewed loopback `ghost-http` server),
install a real executor, and run the Packet 8O qualification matrix (real
hardware/desktop sessions, both browser roles, controlled accounts, latency
and resource ceilings). Nothing in Packet 8N launches, connects, or enables a
live capability.