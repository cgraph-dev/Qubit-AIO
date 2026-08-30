# Controlled Browser / Desktop / Editor Actions (Packet 8F) — operations, security, rollback

Status: **IMPLEMENTED** — typed, brokered, confirmation-gated actions.
Live adapters are **DISABLED**: contracts, previews, approvals, receipts and
audit work; executing a real browser/editor/desktop action is a separate
approved rollout step. No live service, Chromium, account, model, audio, or
user config is changed by this packet.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §8F and the prompt-pack Packet
8F prompt. Built on Packet 3A's `CapabilityBroker`, Packet 1A's
`ConfirmationStore`, Packet 7E's `PermissionsStore`/grants, Packet 8E's
integration records, and Packet 8B's approved loopback-CDP boundary.

## 1. What the action layer does

`voicebridge/actions.py` defines 13 typed action contracts (separate
capability IDs and metrics), each with an exact argument schema, a declared
side-effect tier, a bounded preview, and an honest rollback contract:

| capability | tier | scope | confirmation | rollback |
|---|---|---|---|---|
| `act.browser.read` | read_only | web | no | none |
| `act.browser.search` | reversible | web | no | reversible |
| `act.browser.fill` | reversible | web | no | reversible |
| `act.browser.draft` | reversible | web | no | reversible |
| `act.browser.write` | reversible | web | no | reversible |
| `act.mail.send` | mutating | mail | **always** | **impossible** |
| `act.social.post` | mutating | social | **always** | **impossible** |
| `act.purchase` | mutating/financial | web | **always** | **impossible** |
| `act.editor.write` | reversible | workspace | no | reversible |
| `act.editor.delete` | destructive | workspace | **always** | **impossible** |
| `act.desktop.launch` | reversible | desktop | no | reversible (launcher only) |
| `act.account.change` | mutating | account | **always** | **impossible** |
| `act.system.setting` | mutating | system | **always** | reversible |

Every action flows through the **existing** `CapabilityBroker` (decide +
consume confirmation + audit + bounded child execution + cancellation) AND the
**7E `PermissionsStore`** (mode + grants + scopes). `ActionBroker` is the same
broker type with deny-first confirmation for action capabilities, so a
confirmation-required action can never auto-issue its own confirmation.

Live adapters are **disabled**: without an explicitly injected synthetic
fixture, execution returns the typed `ADAPTER_DISABLED` result. Fixtures
(`tests/fixtures/actions/*.json`) only control the simulated adapter
*outcome*; the contract, gate, confirmation, action hash, receipts and audit
are always real.

## 2. Configuration

```yaml
actions:
  state_path: ""               # default <state_dir>/actions.json (0600/0700)
  enabled: false               # live adapter execution gate (rollout step)
  confirmation_ttl_s: 300.0    # fresh-confirmation window
  max_preview_chars: 400       # bounded previews
```

Environment overrides work through the standard `VOICE_BRIDGE__ACTIONS__*`
double-underscore path (e.g. `VOICE_BRIDGE__ACTIONS__CONFIRMATION_TTL_S`).

## 3. Preview → confirm → execute → receipt

1. `actions preview <id> --args '<json>'` — validates the exact schema,
   computes the **stable action hash** (action_id/version/adapter/target/
   account_scope/scope/args/idempotency_key), and returns a bounded preview
   that SHOWS the exact fields/recipients/content. Persists nothing.
2. The user confirms. `--confirm` (CLI) or `confirmed:true` (dashboard) or the
   spoken "yes" (voice) issues a **fresh, expiring, single-use confirmation**
   bound to the exact envelope digest. Anything changed (target/args/scope/
   account) → `stale_target`; anything expired → `expired`; replay → `replayed`.
3. The broker consumes the confirmation, executes the adapter under the
   contract timeout in a killable isolated child, and writes a redacted
   **receipt** (`<state_dir>/actions.json`): hash, preconditions, args digest,
   typed state, audit id, compensation. Private content is never persisted.
4. `actions cancel <receipt>` cancels an in-flight action (idempotent);
   `actions retry <receipt>` re-runs the same args (fresh confirmation for
   sensitive actions); `actions recover <receipt>` applies the declared
   compensation for reversible actions and returns the typed
   `ROLLBACK_IMPOSSIBLE` for sent messages, purchases, deletions, and account
   changes — rollback is never faked.

## 4. CLI / dashboard / voice parity

One backend (`ActionRunner`) is shared by:

- **CLI**: `voicebridge actions list|show|check|preview|prepare|execute|
  cancel|receipt|receipts|retry|recover`.
- **Dashboard** (`/settings`): `GET /api/actions` (list/show/check/receipts)
  and `POST /api/actions` (preview/execute/cancel/retry/recover/prepare) —
  authenticated with the same `X-Api-Token` header; every control renders the
  real typed backend state.
- **Voice**: exact action phrasings ("draft an email to <addr> about <subject>",
  "send an email to <addr> about <subject> saying <body>", "post to <handle>:
  <text>") route through the same preview + confirmation queue as
  CLI/dashboard. Fuzzy phrasing is never guessed into an action.

## 5. Security invariants (each tested in `tests/test_packet8f.py`)

- Restricted is the default; unrestricted never auto-approves hard-deny
  capability patterns and cannot bypass schema validation, confirmation,
  audit, or resource limits.
- Sending, publishing, purchasing, deleting, credential/account/security
  changes and system-wide actions ALWAYS require a fresh human confirmation
  immediately before execution; a model/page/skill/tool result can never
  confirm or widen a grant.
- Arbitrary JavaScript, free-form shell, coordinate-only scripts,
  page/model/skill instruction fields, secret-bearing URLs, hidden scopes and
  unbounded loops are rejected by construction at schema validation.
- The existing Packet 4D destructive-intent screen still refuses execution of
  content that reads as destructive (defense-in-depth; rephrase to proceed).
- Receipts and audit entries store bounded redacted metadata only — never
  private message bodies, values, or secret content.

## 6. Failure modes

Typed states: `denied` (NOT_GRANTED / CONFIRMATION_REQUIRED /
CONFIRMATION_SOURCE_FORBIDDEN / HARD_DENIED / DESTRUCTIVE_SCREEN /
ADAPTER_DISABLED), `expired`, `stale_target`, `replayed`, `AUTH_REQUIRED`,
`partial`, `failed`, `timeout`, `cancelled`, `interrupted` (crash recovery),
`recovered`, `ROLLBACK_IMPOSSIBLE`, `idempotent_replay`. A receipt left in
`running` across a restart is recovered to `interrupted` and never mistaken
for a completed action.

## 7. Tests and validation

- Focused: `.venv/bin/python -m pytest -q -W error tests/test_packet8f.py`
  (65 tests — default deny, schema/hash/expiry/stale-target, cancellation,
  idempotency, auth/partial/failed states, retry, recovery honesty, redaction,
  CLI/dashboard/voice parity, injection/JS/shell/coordinate/loop rejection,
  restart persistence, fixture surfaces).
- Affected regressions: 7E, 8E, 3A, webui, smoke, dashboard suites.
- Validation: `compileall`, `pip check`, `git diff --check`, `graft build`.

## 8. Rollback

- The action layer is inert by default (`actions.enabled: false`); disabling
  the flag or removing the config section restores prior behavior.
- Receipts live in a private `actions.json` with timestamped backups; remove
  the file and its `.bak` set to clear action state (backup first).
- `git revert <8F-commit>` restores the code; no live service, account,
  browser, model, or audio configuration is touched by this packet.
- Remote actions are never rolled back: a sent message, purchase, deletion or
  account change reports `ROLLBACK_IMPOSSIBLE` and no compensating operation
  is invented.

## 9. Known limitations

- Live adapters are not implemented in this packet (separate approved rollout
  step); every execution returns `ADAPTER_DISABLED` unless a synthetic
  fixture is explicitly selected.
- Natural-language action requests beyond the three exact voice patterns
  require future router/classifier work and are not guessed.
- The broker's destructive-intent screen may refuse message content that reads
  as destructive; users rephrase (documented defense-in-depth behavior).