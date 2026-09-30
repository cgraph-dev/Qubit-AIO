# Qubit Canonical-Authority Matrix — 2026-09-21

**Status:** READ-ONLY AUDIT ARTIFACT. Not a completion claim. This document
records where each Qubit capability-execution surface does or does not pass
through the one canonical broker / policy / confirmation / audit / e-stop
authority. It implements no fix. The body below is the audit snapshot at
`f984911`; a dated **post-audit addendum** at the end records the same-day
repairs and the two rows that still remain. The audit body is not rewritten.

**Repo / revision audited:** `/home/trick/Projects/Repos/qubit-voicebridge`
at `HEAD = f984911c81e119fe6fe1220510b8ac324c1224a4` (branch `main`, worktree
dirty mid deep-rebrand). Every claim below is cited as `file:line` in that
worktree.

**Trigger:** an earlier fix routed the orchestrator `_agent_tool` `web.*` path
through the shared `CapabilityBroker` (`voicebridge/orchestrator.py:2842`,
`voicebridge/broker.py:917`). This audit enumerates the surfaces that still
execute outside that authority.

---

## 0. What counts as "canonical" here

A capability execution is canonical only when, before any side effect, it
passes through **`CapabilityBroker.execute`** (`voicebridge/broker.py:519-701`)
and therefore reaches all of:

| Guarantee | Where enforced |
| --- | --- |
| Shared power / emergency-stop / global-disable / circuit breaker | `voicebridge/broker.py:397-423`, checked at `:551-558` |
| Destructive-intent screen | `voicebridge/broker.py:559-570` |
| Owner-scoped autonomy profile | `voicebridge/broker.py:579-602` |
| PolicyEngine decision (+ grants/scopes) | `voicebridge/broker.py:603-622`; `voicebridge/capabilities.py:271-330` |
| Durable single-use confirmation journal | `voicebridge/broker.py:150-193` (`confirmation_store_for` → `approval journal`); issuance `:612-614` |
| Broker audit (`broker.exec.*`) | `voicebridge/broker.py:855-863`, calls at `:547-701` |
| Cancellation + bounded concurrency | `voicebridge/broker.py:470-516`, `:640-655` |

`voicebridge/web_tool.py` additionally enforces its own SSRF/bounds/redaction
policy and a direct e-stop probe (`voicebridge/web_tool.py:139-146`, checked at
`:320`, `:585`, `:694`). A direct `web_tool` caller therefore keeps e-stop and
SSRF but **loses** the broker's policy decision, durable confirmation, broker
audit, scoped autonomy, shared concurrency/circuit breaker. That is the
guarantee set this matrix tests against.

Dispositions used: **OK** (already canonical), **SAFE_EQUIVALENT** (real local
path, documented reason, same guarantees), **GAP** (should route through the
broker).

---

## 1. Agent/model tool dispatch

| Surface | Evidence | Guarantee reached | Class |
| --- | --- | --- | --- |
| `_agent_tool` `local.*` and `web.*` | `voicebridge/orchestrator.py:2842-2874`; registered at `:2839-2840`; `register_web_tools` `voicebridge/broker.py:917-950` | Broker decide/audit/e-stop/autonomy; `source="model"` | **OK** |
| Regression proof | `tests/test_agent_tool_source8u.py:95-150` asserts `web.fetch` resolves through the one broker with `source="model"` and that a broker e-stop refuses before any fetch | — | **OK** |

## 2. Local actions and local scripts (orchestrator)

| Surface | Evidence | Guarantee reached | Class |
| --- | --- | --- | --- |
| Right-Ctrl / voice local action | `voicebridge/orchestrator.py:2739-2790` (`broker.execute("local."+name, ...)`) | Broker (no direct `local_actions.execute` fallback, `:2742-2752`) | **OK** |
| Local script templates | `voicebridge/orchestrator.py:1122-1141` (`broker.execute(script_capability_name(...))`) | Broker; fails closed if broker disabled `:1124-1136` | **OK** |
| `ActionRunner` family (CLI/dashboard/voice/ghost/opencode) | `voicebridge/actions.py:850-872`, `:1596-1620`; `voicebridge/ghost.py:1388`; `voicebridge/opencode/runner.py:56` | Broker via `self.broker.execute` (`actions.py:1613`) | **OK** |
| `voicebridge/local/actions.py:729 execute()` | `voicebridge/local/actions.py:729-739` — `act.fn(m.params)` direct | None (no policy/broker/audit) | **GAP (latent)** |

`local/actions.py:execute` has **no callers** (Graft `graft_trace_calls execute
--in voicebridge/local/actions.py` → "no indexed callers"; raw grep confirms
only comments mention it at `orchestrator.py:2743,2777`). It is retained dead
code with an unguarded direct-invocation path.

## 3. Web fetches

| Surface | Evidence | Guarantee reached | Class |
| --- | --- | --- | --- |
| Agent tool `web.*` | `orchestrator.py:2842-2874` | Broker | **OK** |
| CLI `qubit websites fetch|search` | `voicebridge/cli.py:7474-7484` (`WT.fetch` / `WT.search`); parser `cli.py:1993-1998` | `web_tool` SSRF/bounds/redaction/e-stop only | **GAP** |
| Gateway `controlUi.linkPreview` | `voicebridge/gateway/rpc.py:809-835` (`WT.fetch(cfg, url)` at `:824`); registered `rpc.py:1597` | `web_tool` only | **GAP** |
| Voice `_b_websites` fetch/search | `voicebridge/voice_commands.py:2252-2260` (`WT.fetch`/`WT.search`); entries `:6756-6768` | `web_tool` only; no voice journal/broker | **GAP** |
| Vendored `/chat` TUI `web_search`/`web_extract` | `qubit_runtime/tools/web_tools.py` (separate process; see §8) | Upstream in-process approval, separate authority | **SAFE_EQUIVALENT** |

## 4. File writes

| Surface | Evidence | Guarantee reached | Class |
| --- | --- | --- | --- |
| Dashboard `POST /api/fs/write-text` | route `voicebridge/qubit_compat.py:5738`; handler `:8715-8761`; client boolean `if not b.get("confirm")` `:8736`; `FP.apply_patch(..., confirmed=True)` `:8745-8746` | Module gates only (`file_patch.py:542-556`) | **GAP** |
| Dashboard `POST /api/files/upload` (+ `upload-stream`) | routes `qubit_compat.py:5739-5740`; handler `:8764-8804` delegates to `h_fs_write_text` at `:8803` | Same as above | **GAP** |
| Dashboard `POST /api/files/mkdir` | route `qubit_compat.py:5741`; handler `:8807-8847`; client boolean `:8822`; direct `resolve_target`+`mkdir` `:8827-8828` | No `_require_gates`: **e-stop not checked**, no confirmation journal, no broker | **GAP** |
| CLI `qubit files patch|rollback` | `voicebridge/cli.py:8536-8548`; parser `:2425-2441` | Boolean `--confirm`; no broker/durable confirmation | **GAP** (operator-facing) |
| Voice `file_patch_rollback` | `voice_commands.py:1868-1872`; entry `confirm=True` `voice_commands.py:7000`; gate `execute` `:9505-9544` | Voice journal confirmation already consumed before backend | **SAFE_EQUIVALENT** |
| `local.apply_patch` capacity | `file_patch.capability_name()` returns `"local.apply_patch"` `voicebridge/file_patch.py:720-722`, but **no registration exists** in `broker.py`, `local/actions.py`, or anywhere (repo-wide search: only `file_patch.py:722`) | — | **GAP (root cause of the four rows above)** |

`file_patch`'s own docstring claims "brokered" (`voicebridge/file_patch.py:1`)
and the CLI help text says "confirmed, brokered, reversible"
(`voicebridge/cli.py:2427,2437`); the handler docstring claims "file_patch
broker" (`qubit_compat.py:8717`). None of those call sites registers or calls
the broker: `apply_patch` is invoked directly with a caller-supplied boolean.

## 5. Gateway handlers

| Surface | Evidence | Guarantee reached | Class |
| --- | --- | --- | --- |
| `controlUi.linkPreview` | `gateway/rpc.py:818-827` | `web_tool` only | **GAP** |
| `talk.node*` dispatch | `gateway/openclaw_handlers/talk_node.py:238-257` | `broker.execute` | **OK** |
| `surfaceControl.*` | `gateway/openclaw_handlers/surface_control.py:125-132` | `broker.execute` | **OK** |
| `system`/`scheduler_media` action receipts, cancel/retry | `openclaw_handlers/system.py:370-393`; `scheduler_media.py:380-386,415-421`; `actions.py:1738-1813` | Read store / `ActionStore`; gateway `retry` passes `source="gateway"` so confirmation-required actions fail closed in the gate `actions.py:1585-1592` | **SAFE_EQUIVALENT** |
| `sessions*`, `security`, `worktrees` e-stop probes | `openclaw_handlers/sessions.py:97-99`, `security.py:68-70`, `sessions_advanced.py:81-83`, `sessions_compaction.py:66-68`, `worktrees.py:59-61` | `BR.estop_engaged` only (read/refusal surfaces) | **SAFE_EQUIVALENT** |

## 6. Voice dispatch

| Surface | Evidence | Guarantee reached | Class |
| --- | --- | --- | --- |
| Canonical `voice_commands.execute` | `voicebridge/voice_commands.py:9461-9560` | Source allowlist `:9478`; e-stop for confirm-tier `:9505-9510`; durable journal confirmation `:9511-9544`; audit `:9545-9560` | **OK** (its own authority; see note) |
| Action-backed voice backends | `voice_commands.py:2838-3040,2969-2982` (`A.action_runner(...).execute`) | Broker via `ActionRunner` | **OK** |
| `_b_websites` fetch/search | `voice_commands.py:2252-2260`; entries `web_fetch`/`web_search` are `confirm=False` `:6756-6768` | `web_tool` only; the voice `execute` e-stop block is skipped for non-confirm entries `:9505` | **GAP** |
| `voicebridge/voice_commands.py` handlers that mutate state directly | e.g. `_b_websites` plus read/status backends | — | See individual rows |

**Note:** the voice `execute` path is a real second mediation layer (journal
confirmation + e-stop for confirm-tier + audit) rather than the capability
broker. It satisfies the voice-actionability contract's single-authority rule
only where its backend then reaches the broker or the same store authority.
Its `_b_websites` backend does not.

## 7. CLI commands

| Surface | Evidence | Guarantee reached | Class |
| --- | --- | --- | --- |
| `qubit websites fetch|search` | `cli.py:7474-7484` | `web_tool` only | **GAP** |
| `qubit files patch|rollback --confirm` | `cli.py:8536-8548` | Boolean gate; no broker | **GAP** (operator-facing) |
| Broker registry probes (no execution) | `cli.py:5527-5530`, `websites.py:1614-1615`, `webui.py:2205-2210`, `voice_commands.py:1805-1811` | Read-only registry/permission inference | **OK** |
| Operator diagnostics/services (`systemctl`, `pactl`, `git`, packaging) | e.g. `lifecycle.py:557`, `audio/mic.py:94`, `projects.py:254`, `release.py:1420` | Operator-invoked infrastructure; not model/voice capabilities | **SAFE_EQUIVALENT** |

## 8. PTY / TUI (`/chat`) and the vendored tool loop

| Surface | Evidence | Guarantee reached | Class |
| --- | --- | --- | --- |
| PTY session auth / e-stop boundary | `voicebridge/pty_ws.py:557-579` (refuses at `:576-579`), `:643-654` (per-pump e-stop closes the session) | Auth ticket + origin/peer + e-stop at session boundary | **SAFE_EQUIVALENT** |
| Vendored TUI tool loop (`web_search`/`web_extract`/file/terminal) | `qubit_runtime/tools/web_tools.py:266-405,496-510`; approval gate `qubit_runtime/tools/approval.py:1-40`, `approval_gateway_wait.py:1-30` | Genuine separate process running the vendored runtime's own approval/policy, not the Qubit broker | **SAFE_EQUIVALENT** (documented separate-process surface; see caveat) |
| TUI session writes | §9 below | Same `state.db` | **SAFE_EQUIVALENT** |

**Caveat (owner review):** the vendored TUI's tool calls are mediated by the
vendored approval system, not `CapabilityBroker`; Qubit grants, scoped
autonomy, and the `approval journal` do not cover them. This is consistent with
`docs/QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md` §4 ("`/chat` … renders the
real vendored terminal … a terminal surface"), and e-stop is enforced at the
PTY boundary, but the disposition should be owner-confirmed rather than
assumed terminal.

---

## 9. `/chat` TUI same-SessionDB verification (task item 4)

The documented claim — the `/chat` TUI writes directly to the vendored
`qubit_state.SessionDB`, which is the same `state.db` the native adapter fronts
— **still holds**:

1. The PTY spawn sets the child `QUBIT_HOME` to the Qubit runtime home:
   `voicebridge/pty_ws.py:283` (`use_qubit_home(cfg)`) and `:293`
   (`env["QUBIT_HOME"] = str(qubit_home)`).
2. `use_qubit_home` returns `runtime_home_for_cfg(cfg)`
   (`voicebridge/qubit_runtime.py:342-379`, home at `:358`).
3. The native adapter resolves the same path: `qubit_state_db_path` uses
   `runtime_home_for_cfg(cfg)` (`voicebridge/qubit_session_store.py:127-139`)
   and opens `SessionDB(db_path=path)` (`:115-124`).
4. The store-boundary contract declares the fork's `state.db` writable and
   shared: `voicebridge/auth_status.py:64-76` (`writable: True`,
   `home_authority: voicebridge.qubit_runtime.default_home`).

The fail-closed drift guard in `voicebridge/qubit_compat.py:517-533` still
names `voicebridge.qubit_session_store.QubitSessionStore` as the chat-transcript
authority, so a divergence would raise rather than silently split. **No GAP**
in the session-store authority for `/chat`.

---

## 10. GAP count

**Reachable GAPs: 7.** One additional latent GAP and one root-cause row are
recorded separately (not double-counted in the 7). **Six of these seven and the
root cause were repaired later the same day — see the post-audit addendum at the
end of this document; G7 and the latent L1 remain.**

| # | GAP | file:line | Missing guarantee | Minimal fix (not implemented) |
| --- | --- | --- | --- | --- |
| G1 | CLI `qubit websites fetch/search` | `voicebridge/cli.py:7474-7484` | Broker policy, durable confirmation, broker audit, shared concurrency/circuit breaker, scoped autonomy | Register web tools on a host broker (`register_web_tools`) and call `broker.execute("web.fetch"/"web.search", args, source="user")` in the CLI handler |
| G2 | Gateway `controlUi.linkPreview` | `voicebridge/gateway/rpc.py:818-827` | Broker policy/audit/concurrency; per-call confirmation not needed (READ_ONLY) but the decision path must be shared | Build `build_host_broker(cfg=cfg)`, `register_web_tools`, `broker.execute("web.fetch", ...)`, map `ExecResult` to `GatewayError` |
| G3 | Voice `_b_websites` fetch/search | `voicebridge/voice_commands.py:2256-2260` | Broker policy/audit; voice contract §3.3 ("same policy, confirmation, e-stop, audit mediation") | Have the `web_fetch`/`web_search` backends dispatch `broker.execute(...)` instead of `WT.fetch`/`WT.search` |
| G4 | Dashboard `POST /api/fs/write-text` | `voicebridge/qubit_compat.py:8736-8746` | Durable single-use confirmation (client boolean only), broker decision, source attribution | Register `local.apply_patch` (`file_patch.capability_name()`, `file_patch.py:720-722`) on the host broker and execute with a real `confirmation_id`; require the journal confirmation instead of `b.get("confirm")` |
| G5 | Dashboard `POST /api/files/upload` / `upload-stream` | `voicebridge/qubit_compat.py:8803` | Same as G4 (delegates to the same boolean gate) | Same as G4 (fix at the shared `h_fs_write_text` seam) |
| G6 | Dashboard `POST /api/files/mkdir` | `voicebridge/qubit_compat.py:8822-8828` | **E-stop not checked at all** (`_require_gates` bypassed), no confirmation, no broker | Route through `file_patch._require_gates(cfg, confirmed)` plus broker/durable confirmation, or register a `local.mkdir` capability |
| G7 | CLI `qubit files patch|rollback --confirm` | `voicebridge/cli.py:8536-8548` | Broker decision + durable confirmation (boolean `--confirm` is not the journal) | Route through the same registered `local.apply_patch` capability with a broker-issued confirmation |
| L1 | `voicebridge/local/actions.py:729 execute()` | `voicebridge/local/actions.py:729-739` | All broker guarantees; direct `act.fn` call | Delete the dead function or make it raise `DispatchBlocked` (no callers today) |
| R1 | `local.apply_patch` never registered (root cause) | `voicebridge/file_patch.py:720-722` | n/a (capacity absent) | Register the capability in `build_host_broker` so G4–G7 have a broker target |

---

## 11. Top 5 gaps (priority order)

1. **G4/G5 — dashboard file writes accept a client-supplied boolean.**
   `voicebridge/qubit_compat.py:8736-8746` / `:8803`. `if not b.get("confirm")`
   then `FP.apply_patch(..., confirmed=True)`. The contract requires a durable,
   single-use, journal-backed confirmation consumable by every surface; this is
   a replayable request field. **Fix:** register `local.apply_patch`
   (`file_patch.py:720-722`) and execute with a broker-issued `confirmation_id`.
2. **G6 — `POST /api/files/mkdir` skips e-stop entirely.**
   `voicebridge/qubit_compat.py:8822-8828` never calls `_require_gates`
   (`file_patch.py:542-556`), so a latched emergency stop does not refuse it.
   **Fix:** call `_require_gates(cfg, confirmed)` and route the create through a
   broker capability.
3. **G2 — gateway `controlUi.linkPreview` fetches outside the broker.**
   `voicebridge/gateway/rpc.py:818-827`. SSRF/e-stop survive in `web_tool`, but
   the gateway request never reaches policy, broker audit, or the shared power
   control. **Fix:** `build_host_broker(cfg=cfg)` + `register_web_tools` +
   `broker.execute("web.fetch", ...)`.
4. **G3 — voice web fetch/search bypasses voice policy mediation.**
   `voicebridge/voice_commands.py:2256-2260`; the entries are `confirm=False`
   (`:6756-6768`) so the `execute` e-stop block at `:9505` is skipped and the
   backend calls `WEB_TOOL` directly. **Fix:** dispatch `web.*` through the
   broker from the backend.
5. **G1 — CLI `qubit websites fetch/search` is not canonical.**
   `voicebridge/cli.py:7474-7484`. **Fix:** same broker dispatch as G2.

---

## 12. Unknowns / NOT VERIFIED

- Whether `/api/fs/*`, `/api/files/*`, and `controlUi.linkPreview` are reached
  only by authenticated dashboard sessions is not assessed here; this audit
  covers the missing broker/confirmation guarantees only.
- The vendored TUI tool-loop disposition (§8) is a documented-separate-process
  judgment that requires owner review before it can be called terminal.
- No live end-to-end run was executed; all claims are source-level and cite the
  current worktree. Per the release-gate contract, no capability here is
  VERIFIED.

---

## Addendum — post-audit repairs (2026-09-21, later same day)

The audit body above was written at ~19:12; the repairs below landed at
~19:42–19:56 in the same worktree. This addendum is still source-level: no live
end-to-end run was executed and no repaired row is `VERIFIED`. Evidence is the
current worktree plus focused regression tests (not the final matrix).

**Repaired (6 reachable GAPs + root cause):**

| # | Row | Repair evidence |
| --- | --- | --- |
| G1 | CLI `qubit websites fetch/search` | `voicebridge/cli.py:7476-7487` builds the host broker, `register_web_tools`, and dispatches `web.fetch`/`web.search` with `source="user"` |
| G2 | Gateway `controlUi.linkPreview` | `voicebridge/gateway/rpc.py:819-845` dispatches `web.fetch` through the shared broker (`source="user"`); refusal maps to the shared power control |
| G3 | Voice `_b_websites` | `voicebridge/voice_commands.py:2253-2278` dispatches `web.*` through the shared broker instead of `WT.fetch`/`WT.search` |
| G4 | Dashboard `POST /api/fs/write-text` | `voicebridge/qubit_compat.py:8761` `_file_write_broker` registers `local.apply_patch` (`voicebridge/broker.py:983`); handler `:8873-8919` requires a durable journal `confirmation_id` (a client boolean is not authority) |
| G5 | Dashboard `POST /api/files/upload` / `upload-stream` | `voicebridge/qubit_compat.py:8922` delegates to the same brokered `h_fs_write_text` seam |
| G6 | Dashboard `POST /api/files/mkdir` | `voicebridge/qubit_compat.py:8965-9001` routes through `local.files_mkdir` with the shared e-stop/confirmation journal (previously skipped the gate entirely) |
| R1 | `local.apply_patch` never registered | `voicebridge/broker.py:983` `register_file_patch` registers `local.apply_patch` + `local.files_mkdir` as MUTATING capabilities; caller `voicebridge/qubit_compat.py:8768-8772` |

Regression tests (source-level): `tests/test_web_broker_routing9u.py` (G1–G3),
`tests/test_file_write_broker_authority.py` (G4–G6, R1, including a latched
e-stop refusal on `mkdir`/`write-text` and a durable single-use confirmation on
the shared journal).

**Related repair (approvals confirm):** `voicebridge/qubit_compat.py:2549-2571`
`h_ops_confirm` now settles owned skill/toolset mutation ids through the one
shared journal before falling through to `ops_confirm_request`, so
`/api/ops/confirm` is a single standard settle. Source-level only.

**Still open:**

- **G7 — CLI `qubit files patch|rollback` (operator-facing).**
  `voicebridge/cli.py:8582-8594` (`_cmd_files`, `cmd == "patch"`/`"rollback"`)
  still calls `FP.apply_patch` / `FP.rollback` directly with
  `confirmed=bool(args.confirm)`; it does not resolve `local.apply_patch`
  through the broker or a broker-issued confirmation.
- **L1 — `voicebridge/local/actions.py:729 execute()` (latent).** Still a direct
  `act.fn(m.params)` call with no broker/policy/audit; still no indexed callers
  (retained dead code).

No disposition in this addendum is terminal; closing G7/L1 and confirming the
repairs require the final matrix and an independent read-only verifier.
