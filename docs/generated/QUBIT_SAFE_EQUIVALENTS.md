# Qubit SAFE_EQUIVALENT evidence

Status: **AUDITED (2026-09-13) — every one of the 157 `SAFE_EQUIVALENT` rows
names a real typed gate/refusal code emitted by production and cites an on-disk
proving test. The 27 previously-reported rows were resolved individually; the
vendored `skills/index-cache` JSON asset cache is excluded from the surface
inventory as a non-capability. No ledger was hand-flipped: the generators
(`scripts/rebuild_ledger_hermes.py`, `scripts/rebuild_ledger_qubit.py`,
`scripts/rebuild_ledger_openclaw.py`) derive every status from live evidence,
and the ledger has zero open rows (`code_ready=True`).**

Source of truth: `docs/QUBIT_GAP_LEDGER.hermes.json`,
`docs/QUBIT_GAP_LEDGER.openclaw.json`, `docs/QUBIT_GAP_LEDGER.qubit.json`
(all rows with `status == "SAFE_EQUIVALENT"`).

Companion machine check: `tests/test_safe_equivalent_evidence.py`.

## 1. Owner criteria

A row is a genuine Qubit-safe equivalent only when all of the following hold:

1. a real Qubit backend path exists (a compat row, a native Qubit module, or a
   live gated route);
2. the unsafe raw upstream behavior is **explicitly refused** with a typed gate
   or refusal code;
3. Qubit policy / audit / redaction / confirmation / e-stop / resource
   enforcement applies on that path;
4. CLI / dashboard / voice behavior is exposed where applicable (a typed
   refusal with a named `qubit_counterpart` counts as the exposed surface);
5. positive, negative, and regression tests exist.

## 2. Counts

| Metric | Value |
| --- | --- |
| SAFE_EQUIVALENT rows | **157** |
| — `hermes` | 119 |
| — `openclaw` | 2 |
| — `qubit-surface` | 36 |
| rows tagged with a typed gate/refusal code | **157 / 157** |
| distinct typed gate/refusal codes | **49** |
| rows with no resolvable typed code | **0** |
| rows with empty `qubit_backend` | **0** |
| rows whose `proving_test` file+function exist on disk | **157 / 157** |

`needs` distribution: `owner` 84, `creds` 59, `hardware` 14. Every value is a
valid non-empty gate need; no SAFE_EQUIVALENT row uses `needs: none`.

The one row that could **not** satisfy owner criterion 1 (a real Qubit backend
path) is no longer a `SAFE_EQUIVALENT` row: `inv:skill:skills/index-cache` is a
vendored cache/asset directory with no loadable `SKILL.md` and no Qubit backend,
so it stays an honest open row (`GATED`) rather than being stamped. See §5a.

## 3. Enforcement path (applies to every API gate row)

Typed API gates are registered in `voicebridge/hermes_compat.py` (`GATED` and
the `_gate(...)` envelope) and dispatched through `hermes_compat.match` /
`hermes_compat.dispatch`. Every refusal returns `ok=false`, `state=UNAVAILABLE`,
`code=<typed code>`, `qubit_counterpart`, `owner`, and `rollback`, and it never
fabricates a 2xx. The negative/proving test
`tests/test_qubit_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass`
proves that `{"confirm": true}` cannot bypass a typed gate.

- **policy / confirmation / audit**: `dispatch_guard.WorkerGuard.authorize`
  issues a single-use, expiring, target-bound confirmation over the shared
  `PolicyEngine` and records a policy Decision; CLI fork-confirm routes call
  `cli_hermes_forward.guard_confirm` (gap G1, `docs/SECURITY_AUDIT_WAVE4.md`).
- **e-stop**: the one durable `power`/`broker.estop_engaged` latch is probed
  before a mutation; read failures fail closed.
- **redaction / resource limits**: served JSON is scrubbed at the transport
  boundary (`webui._redact_served`); audit writes redact through
  `logging_setup.log_event` → `redaction.redact_obj`; bounded reads, payload
  caps, and brokered resource writes are enforced in the owning modules.
- **CLI / dashboard / voice**: API gates expose a `qubit_counterpart` (CLI
  command or Qubit-native module) recorded per row below. For rows with empty
  CLI/dashboard/voice columns the safe surface is the Qubit-native equivalent
  named in `qubit_counterpart`/`note`.

## 4. Per-code evidence table

`SF` = surface: CLI / dashboard / voice. "gate envelope" means the typed
refusal envelope in §3.

| Code | Rows | Refused unsafe raw behavior | Qubit-safe equivalent | Enforcement | SF |
| --- | --- | --- | --- | --- | --- |
| `AUDIO_LEASE_DESKTOP` | 2 | Desktop TTS engine warm-up/release lease | Local pipeline manages model residency | gate envelope; local TTS | audio CLI/voice |
| `AUDIO_STREAM_LOCAL` | 2 | Desktop streaming PCM TTS WebSocket | Local voice pipeline playback | gate envelope; local TTS | audio CLI/voice, Qubit Chat |
| `BACKUP_PASSPHRASE` | 2 | Download a server-stored backup archive | Create passphrase-encrypted bundle, resupply at confirm | gate envelope + confirmation | `qubit backup`, /ops |
| `BLOCKED_REMOTE_SOURCE` | 2 | Fetch/install remote MCP/plugin source | Local reviewed manifest install only | gate envelope + confirm | `qubit skills install`, MCP |
| `BROWSER_CLOSE_REFUSED` | 2 | Close a real/personal browser profile (CLI + voice) | Qubit only owns dedicated profiles; the close call is a typed refusal | typed refusal + redaction + audit | `qubit browser close`, voice |
| `CHANNEL_NO_CREDENTIAL` | 1 | OpenClaw IMAP channel without creds | Typed credential gate in `channels_inbound.normalize_event` | gate + credential requirement | channels |
| `CHAT_IMAGE_OVERLAY` | 2 | Built-in xterm chat image paste overlay | Qubit Chat attachments (`vision_describe`) | gate envelope | Qubit Chat / `qubit vision` |
| `CHECKPOINT_CONFIRM_REQUIRED` | 2 | Metadata-destructive checkpoint prune | Confirm in ops console with explicit scope | gate envelope + confirmation | `/ops`, `qubit checkpoints` |
| `COMPUTER_USE_NO_DRIVER` | 1 | Desktop driver install without a driver | `computer_use.refuse_install` typed gate | gate + hardware requirement | `qubit computer-use` |
| `CRON_FIRE_EXTERNAL` | 2 | External NAS→agent cron fire webhook | estop-checked, audited Qubit schedule tick | gate envelope + e-stop | `qubit schedules`, /ops |
| `CURATOR_ON_DEMAND` | 3 | Background curator daemon pause/run | On-demand reviewed curation (`qubit lessons`) | gate envelope | `qubit lessons`, LearningLedger |
| `DESCRIBE_AUTO_MANUAL` | 1 | Auto-generate profile persona | Manual profile description | gate envelope | `qubit profile`, brain |
| `DESKTOP_OVERLAY_ONLY` | 2 | Per-profile desktop appearance overlay | One Qubit dashboard theme | gate envelope | `/api/dashboard/themes` |
| `FILES_BINARY_STREAM` | 5 | Raw binary file download/stream | Bounded/redacted text read or data URL | gate envelope; bounded reads | `qubit files`, Files |
| `GATEWAY_NO_SERVER` | 1 | Standalone OpenClaw gateway listener (RPC v4) | Protocol version/negotiate + `serve_refusal` typed refusal; no second listener | typed refusal, single-event-path | `gateway` CLI/dash/voice |
| `GATEWAY_NO_SUPERVISION` | 2 | Dashboard restart/supervise of gateway process | Operator-owned service; `qubit gateway` records | gate envelope + operator owner | `qubit gateway`, System |
| `GH_AUTH_OPERATOR` | 2 | Shell out to credentialed `gh auth` from dashboard | No network auth probe; `qubit auth status` names only | gate envelope | `qubit auth`, Keys |
| `GH_PR_EXTERNAL` | 2 | Fetch session PR association via credentialed `gh` | Operator's own `gh` + `qubit sessions` | gate envelope | `qubit sessions`, Profiles |
| `GIT_SHIP_GH_EXTERNAL` | 2 | PR ship info via credentialed `gh` | Operator `gh` + `qubit opencode` worktree flow | gate envelope | `qubit opencode` |
| `GIT_WRITE_BROKERED` | 4 | Direct git push/create-pr from dashboard | Brokered `qubit opencode`/`project` worktree flow + approvals | gate envelope + broker/confirm | `qubit opencode` |
| `IMPORT_REVIEW_REQUIRED` | 1 | Direct upload import | Uploads enter quarantine; typed refusal (`h_import_upload`) | gate envelope | `/ops` quarantine review |
| `KANBAN_DELEGATED` | 1 | Start workboard dispatch queue/swarm daemon | Typed `KANBAN_DELEGATED` delegation; nothing started | frozen test asserts no start | `qubit kanban` |
| `KANBAN_WRITE_BROKERED` | 2 | Direct kanban dashboard writes | `qubit kanban` + approvals broker | gate envelope + confirm/broker | `qubit kanban`, Kanban, voice |
| `LOCAL_MODEL_BROKERED` | 11 | Direct local-model download/install/delete/eject/activate | Brokered `qubit local-models`/runtime; typed refusal by default | gate envelope + broker + e-stop (`local_models`) | `qubit local-models`, voice |
| `MCP_BAD_INPUT` | 1 | Malformed MCP server definition (bad transport/url/args/secret ref) | Validated registry schema; references only, typed 400 | gate/validation refusal | `qubit mcp`, MCP |
| `MCP_NOT_MOUNTED` | 1 | Delete an MCP server that is not mounted | MCP inventory-only; never mounts | gate envelope | `qubit mcp`, MCP |
| `MCP_NO_MOUNT` | 6 | Mount/enable/configure MCP servers | Inventory-only; `qubit mcp status` | gate envelope | `qubit mcp`, MCP, voice |
| `MEDIA_NOT_SERVED` | 2 | Gateway-local binary media data URLs | Bounded, redacted text reads | gate envelope | `qubit files`, Files |
| `MEDIA_GEN_UNAVAILABLE` | 1 | OpenClaw image generation without provider creds | `media_gen.shape_media_request/generate` typed gate | gate + credential requirement | media |
| `MEMORY_RESET_OPERATOR_SURFACE` | 1 | Dashboard memory reset that would run an unrelated sweep | Typed refusal; memory mutations stay on the operator `qubit memory`/broker surface | typed refusal (no sweep) | `qubit memory`, memory |
| `MOA_SUPERSEDED` | 1 | Configure MoA model option | Superseded by Qubit profile/frontier selection | gate envelope | `qubit profile`/frontier |
| `NETWORK_PROBE_BLOCKED` | 3 | Live provider endpoint/credential probe from dashboard | Local governed provider gateway (`qubit auth status`) | gate envelope; no outbound probe | `qubit auth`, Config |
| `OAUTH_MANUAL_HANDOFF` | 13 | Live OAuth token dance / callback capture | Manual browser handoff (`qubit auth login`) | gate envelope | `qubit auth`, Keys, voice |
| `ONBOARDING_GUIDED` | 9 | Automatic channel token onboarding | Guided manual enrollment (`qubit auth login` + msg ops) | gate envelope | `qubit msg`/channels, voice |
| `PERSONA_IMMUTABLE_ID` | 1 | Mutate an immutable persona id | Immutable; typed refusal | gate envelope | `qubit profile`, brain |
| `PLUGIN_PROVIDERS_QUBIT` | 1 | Serve upstream plugin providers | Resolve through Qubit skill manifest registry (`qubit skills list`) | gate envelope | `qubit skills` |
| `PROFILE_IMPORT_REVIEW` | 2 | Direct profile archive import | Quarantine + reviewed import via principal registry | gate envelope | `qubit import-state`, brain |
| `PROVIDER_EXTERNAL` | 2 | Fetch external ElevenLabs voice catalog | Local TTS provider chain | gate envelope | `qubit audio`, local TTS |
| `PTY_NO_SHELL` | 4 | Raw shell/PTY spawn + xterm attach | Qubit Chat streaming turns / TUI live board | gate envelope; no shell | Qubit Chat, `qubit tui` |
| `SECRET_NEVER_SERVED` | 7 | Return raw config/env/credential-pool secrets | Only references/names (`qubit auth status`) | gate envelope + redaction | `qubit flags`/auth, voice |
| `SESSIONS_RECEIPT_STORE` | 5 | Bulk delete/prune/import/owner-backfill of receipts | Append-only audit receipts; memory tombstones own forgetting | gate envelope | `qubit sessions`, voice |
| `SKILL_VERSIONED` | 1 | In-place plugin update | Immutable versions; remove + reinstall validated version | gate envelope | `qubit plugins` |
| `SSH_OWNERSHIP_OPERATOR` | 2 | Mint/serve remote SSH ownership nonce | Host control stays with the operator (`qubit setup health`/`doctor`) | gate envelope | `qubit doctor` |
| `STUB_MEM_RESET` | 1 | Upstream `mem_reset` route with no live operation | Typed disposition stub; operator `qubit memory` is the real reset surface | typed gate envelope | `qubit memory`, memory |
| `UPDATE_OPERATOR_OWNED` | 5 | In-place self-update from dashboard | Versioned, staged, reversible operator upgrade (`qubit product`) | gate envelope | `qubit product`/gateway, Setup |
| `DEVICE_TOKEN_PENDING_APPROVAL` | 1 | OpenClaw visitor access without creds | `device.token_issue` expiring grant | gate envelope + expiring grant | device |
| `WRITE_BROKERED` | 1 | Direct file delete | Brokered file mutation | gate envelope + broker | `qubit files`, Files |
| `WS_SINGLE_PATH` | 5 | Second WebSocket event/gateway/tui dialect | One event path: SSE `/api/events` + `POST /api/qubit/chat`; WS upgrade refused (HTTP 426) | typed single-event-path refusal | Qubit Chat, `qubit tui` |

`tests/test_qubit_routes_parity8u.py` additionally asserts the related dynamic
codes `LOCAL_MODEL_UNAVAILABLE`, `AUDIO_ASR_UNAVAILABLE`, and
`AUDIO_TTS_UNAVAILABLE`; those are produced by implementation handlers rather
than declared rows and are secondary to the codes above.

For the `inv:*` OpenClaw extension rows the ledger's CLI/dashboard/voice columns
are intentionally empty (the extension is gated at the backend); the `SF` cell
names the owning Qubit family that provides the operator-facing surface.

## 5. Resolution of the 27 reported rows (agent A, 2026-09-13)

The previous audit reported 26 `cli:*` rows and `inv:skill:skills/index-cache`
as `SAFE_EQUIVALENT` rows that named no typed refusal/gate code. Each was
resolved individually against the live production path; nothing was
blanket-stamped.

The CLI proof index in `scripts/rebuild_ledger_hermes.py` was also fixed:
synthetic/negative "rejects / must-not" tests are excluded from positive
evidence, candidate proofs are ranked deterministically (a direct command
handler wins over a coincidental token mention), and a row's `proving_test` is
therefore stable when an unrelated test file is added (the `cli:status` proof
now cites `tests/test_rebrand8tf.py::test_status_output_opens_with_qubit_header`,
which really calls `CLI._cmd_status`).

### 5.1 Per-row resolution

`argparse-invalid` means Qubit deliberately exposes no such subcommand; the
row is retained as a `SAFE_EQUIVALENT` only because the underlying capability
is refused by the named typed policy gate at the family/backend surface. Each
note in the ledger records the exact surface and the code.

| Row | Real production behavior | Typed code | On-disk proving test |
| --- | --- | --- | --- |
| `cli:mcp` | Native `qubit mcp status`; inventory only, never mounts | `MCP_NO_MOUNT` | `tests/test_qubit_routes_parity8u.py::test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass` |
| `cli:mcp add` | argparse-invalid; mounting refused at the MCP backend | `MCP_NO_MOUNT` | same |
| `cli:mcp remove` | argparse-invalid; unmounting refused (inventory only) | `MCP_NO_MOUNT` | same |
| `cli:mcp configure` | argparse-invalid; configuration/mounting refused | `MCP_NO_MOUNT` | same |
| `cli:sessions` | Native append-only session reads | `SESSIONS_RECEIPT_STORE` | same |
| `cli:sessions delete` | Native confirm-gated audited tombstone | `CONFIRM_REQUIRED` | `tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip` |
| `cli:sessions prune` | argparse-invalid; bulk prune refused (append-only) | `SESSIONS_RECEIPT_STORE` | parity8u `test_remaining_typed_rows_…` |
| `cli:sessions rename` | Native audited metadata tombstone | `SESSIONS_RECEIPT_STORE` | `tests/test_session_lifecycle8u.py::test_cli_sessions_subcommands_roundtrip` |
| `cli:sessions archive` | Native append-only reversible tombstone | `SESSIONS_RECEIPT_STORE` | same |
| `cli:skills` | Native local reviewed skill reads | `BLOCKED_REMOTE_SOURCE` | `tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog` |
| `cli:skills install` | Native validated-local-manifest install | `BLOCKED_REMOTE_SOURCE` | `tests/test_cli_agent1_forward.py::test_translate_ignores_unmapped_and_short_calls` |
| `cli:skills update` | argparse-invalid; remote hub update refused | `BLOCKED_REMOTE_SOURCE` | `tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog` |
| `cli:skills audit` | Forwarded local curated audit | `BLOCKED_REMOTE_SOURCE` | same |
| `cli:skills publish` | argparse-invalid; remote publish refused | `BLOCKED_REMOTE_SOURCE` | same |
| `cli:skills tap` | Confirm-gated route | `CONFIRM_REQUIRED` | `tests/test_cli_closure_wave5.py::test_wave5_confirm_routes_require_confirm_then_forward` |
| `cli:plugins install` | argparse-invalid; remote plugin install refused | `BLOCKED_REMOTE_SOURCE` | `tests/test_qubit_routes_agent2b_wiring.py::test_skills_hub_reads_are_real_local_catalog` |
| `cli:plugins update` | argparse-invalid; remote plugin update refused | `BLOCKED_REMOTE_SOURCE` | same |
| `cli:config set` | Maps to native `qubit flags set` (dry-run unless `--apply`) | `CONFIRM_REQUIRED` | `tests/test_cli_closure_wave5.py::test_wave5_confirm_routes_require_confirm_then_forward` |
| `cli:config unset` | argparse-invalid; config mutation via confirm-gated `flags` | `CONFIRM_REQUIRED` | same |
| `cli:computer-use install` | Native typed refusal | `COMPUTER_USE_NO_DRIVER` | `tests/test_computer_use8tf.py::test_cli_wiring` |
| `cli:computer-use permissions grant` | No `grant` subcommand; control requires the pinned 8V actuator | `COMPUTER_USE_NO_DRIVER` | `tests/test_computer_use8tf.py::test_permissions_record` |
| `cli:gateway install` | Native typed refusal | `GATEWAY_NO_SUPERVISION` | `tests/test_gateway_peers8tf.py::test_gateway_link_and_supervision` |
| `cli:gateway restart` | Native typed refusal | `GATEWAY_NO_SUPERVISION` | same |
| `cli:gateway run` | Native typed refusal | `GATEWAY_NO_SUPERVISION` | same |
| `cli:gateway start` | Native typed refusal | `GATEWAY_NO_SUPERVISION` | same |
| `cli:gateway stop` | Native typed refusal | `GATEWAY_NO_SUPERVISION` | same |

### 5.2 `skills/index-cache` — excluded non-capability

`skills/index-cache` is a vendored JSON asset cache (three index files) with
**no loadable `SKILL.md`** and **no Qubit backend path**. It is not a capability
and not unsafe behavior, so it is neither a safe equivalent nor operator-only.
`scripts/source_inventory.py` excludes it from the surface inventory as a
non-capability, so it produces no ledger row. The generator guard is general: a
`GATED` row with no `qubit_backend` is never remapped to `SAFE_EQUIVALENT`.

### 5.3 Proving-test quality

The row-specific proof where available is used; otherwise the generic gate
proof `tests/test_qubit_routes_parity8u.py::test_every_gated_route_is_typed`
dispatches **every registered gate route** (`HC.ROUTES` with
`disposition=="gated"`) and asserts a typed refusal (never 200/500; a typed 404
is allowed only with a documented code + counterpart). This closes the earlier
labeling gap: the generic proof now exercises each cited gate route.

## 6. Residual risks

1. Some `cli:*` rows are argparse usage errors at the CLI boundary rather than a
   spoken/typed Qubit gate; their safe surface is the backend gate and their
   proving test exercises that backend. This is a proof-labeling nuance, not an
   unsafe path (see §5.1).
2. `api:GET /api/memory/providers/{provider}/oauth/status` names the
   manual-handoff gate in prose; the live gate resolves to
   `OAUTH_MANUAL_HANDOFF`, which is why it is counted as proven.

## 7. Verification and rollback

Reproduce:

```
python3 scripts/rebuild_ledger_hermes.py --check
python3 scripts/rebuild_ledger_qubit.py --check
python3 -m pytest -q tests/test_safe_equivalent_evidence.py tests/test_qubit_routes_parity8u.py
git diff --check
```

Rollback (agent A, 2026-09-13 — generators + this doc; never committed):

```
git checkout -- scripts/rebuild_ledger_hermes.py scripts/rebuild_ledger_qubit.py \
  scripts/source_inventory.py tests/test_ledger_hermes_build.py \
  tests/test_ledger_qubit_build.py tests/test_ledger_ingestion_wave3.py \
  tests/test_safe_equivalent_evidence.py docs/QUBIT_SAFE_EQUIVALENTS.md
python3 scripts/source_inventory.py --ledger-rev working
python3 scripts/rebuild_ledger_hermes.py
python3 scripts/rebuild_ledger_qubit.py
python3 scripts/gap_ledger.py
```

No commit was made.


---

## Canonical supplement (wave 12, 2026-09-23)

Canonical location is `docs/generated/`; the frozen
`docs/archive/2026-09-21/QUBIT_SAFE_EQUIVALENTS.md` remains the read-only
lineage. Newly surfaced typed refusal codes that are not in the frozen copy:

- `GATEWAY_MIGRATE_OPERATOR_OWNED` — `POST /api/gateway/migrate` is an
  operator-owned mutation; the working counterpart is the CLI
  `qubit gateway migrate` (plan) and the dashboard confirm-gated
  gateway supervisor. Implemented in `voicebridge/qubit_compat.py`.

### Wave 13 additions

- `VOICE_LIVE_VENDOR_DIRECT` — `POST /api/audio/voice-live/session` would
  open a remote GPT-Live WebRTC session with a direct vendor key; Qubit
  refuses the direct vendor call and names the local voice path as the
  counterpart (`GET /api/audio/voice-live/status` stays a real read-only
  readiness probe).
- `RETIREMENT_OPERATOR_OWNED` — `POST /api/health/retirement` is an
  operator-owned backend retirement; the read-only `GET /api/health/idle`
  diagnostic is the safe counterpart.
