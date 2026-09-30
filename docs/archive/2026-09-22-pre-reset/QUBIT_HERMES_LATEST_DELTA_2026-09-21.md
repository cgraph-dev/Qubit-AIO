# Qubit — Hermes Latest-Delta Capability Map (2026-09-21)

**Status:** documentation artifact (delta + adapter plan). Not a parity claim.
This file does not assert VERIFIED, 100%, 1:1, or completion.

**Machine-readable companion:**
[`QUBIT_HERMES_LATEST_DELTA_2026-09-21.json`](QUBIT_HERMES_LATEST_DELTA_2026-09-21.json)

## 1. Purpose

The active brief records latest-Hermes parity as **NOT VERIFIED — no latest-Hermes
comparison has run**. This artifact is that comparison: it enumerates the
capability delta from the contractual pin to the latest release tag and to the
live main head, classifies each meaningful capability against a Qubit
counterpart, and names the adapter Qubit must add.

No product source, manifest, or existing doc was modified to produce it.

## 2. Resolved revisions

| Ref | Revision | How resolved |
|---|---|---|
| Contractual pin | `2237be355906fbe6065ce1815711eee52b2d646e` (v2026.9.7) | pinned clone `/tmp/opencode/pinned/hermes-agent` |
| Latest release | `345cd2b057a452236de401d3534b8502a7465e8d` (v2026.9.14) | live `git ls-remote` (network available) |
| Latest main | `bb359ec5c1d5da6a66f00ee11f0ad7a86defaee2` | live `git ls-remote` |

The brief's recorded main head `5bb314fa0148a9879123770a609dab8f87f03fc9` is
**superseded** — it is 5 commits behind the live head. The local clone is
shallow, so commit counts were taken from the GitHub compare API.

## 3. Delta volume

| | pin -> release (v2026.9.14) | pin -> main |
|---|---|---|
| Files changed | 3,695 | 7,220 |
| Added | 949 | 2,673 |
| Modified | 2,006 | 3,735 |
| Deleted | 74 | 147 |
| Commits | 2,025 | 7,070 |

Area-level additions (files, pin -> release / pin -> main):

| Area | release | main |
|---|---|---|
| `hermes_cli/` new modules | 28 | 79 |
| `tools/` new files | 18 | 33 |
| `gateway/` new modules | 7 | 24 |
| `tui_gateway/` new modules | 26 | 31 |
| `agent/` new modules | 13 | 33 |
| `hermes_state_*` new modules | 3 | 7 |
| new optional-skill dirs | 9 | 12 |
| `plugin-catalog/` files | 11 | 224 |
| new model-provider dirs | 0 | 0 |
| new top-level plugin dirs | 0 | 0 |

Top-level CLI families: 66 (pin) -> 67 (release, +`vault`) -> 69 (main,
+`codex-runtime`, +`usage`). Slash commands: 101 -> 102 (only `/login` added).
Providers gained no new provider directories; only catalog data and the
`providers/base.py` contract changed.

## 4. Classification counts

118 capabilities classified across 10 areas. Status vocabulary for this artifact
is the delta vocabulary requested for the comparison: **IMPLEMENTED / PARTIAL /
MISSING** (distinct from the ledger's IMPLEMENTED / SAFE_EQUIVALENT /
OPERATOR_ONLY / OPEN).

| Status | Count |
|---|---|
| IMPLEMENTED | 1 |
| PARTIAL | 104 |
| MISSING | 13 |

| Area | Count |
|---|---|
| cli | 34 |
| agent | 19 |
| gateway | 18 |
| tools | 16 |
| tui-gateway | 15 |
| state | 6 |
| slash-commands | 4 |
| providers | 2 |
| skills | 2 |
| plugins | 2 |

**PARTIAL dominates** because Qubit's `qubit_runtime/` is a renamed fork at the
pin: the new upstream modules are absent, but most capabilities already have a
differently-shaped Qubit-owned counterpart in `voicebridge/`. `PARTIAL` here
means "a related canonical Qubit capability exists but is not the upstream
contract"; it is not a closure.

## 5. Top-10 highest-value gaps

Ranked by user-visible value to a local-first, voice-driven agent. Each names
the owning Qubit module for the adapter.

1. **Connector framework + tool gateway** (`tools/connectors/*`,
   `tools/tool_gateway/*`) — managed/local connectors, MCP OAuth, connector
   search. Qubit: PARTIAL — `voicebridge/mcp_servers.py`,
   `voicebridge/gateway_peers.py`. Owner: extend `mcp_servers` into a connector
   contract; managed connectors stay OPERATOR_ONLY.
2. **Unified vault store + external backends** (`agent/vault_store.py`,
   `agent/vault_backends/*`, `hermes_cli/vault.py`) — Qubit: PARTIAL —
   `voicebridge/env_store.py`, `voicebridge/credentials.py`,
   `qubit_runtime/agent/secret_sources/*`. Owner: Qubit vault adapter over
   `vault:/ref:` references; vendor re-sync of backends.
3. **MoA alternation** (`agent/moa_alternation.py`) — Qubit: MISSING (only a
   forward-table entry). Owner: provider-side alternation under the one
   runtime, or OPERATOR_ONLY.
4. **Multi-host topology** (`gateway/host_topology.py`, `host_attach.py`,
   `host_rendezvous.py`) — Qubit: PARTIAL — `voicebridge/node_host.py`,
   `gateway_peers.py`, `pairing.py`. Owner: node/peer registrations; no second
   host authority.
5. **Turn-runner reliability gates** (`gateway/run_delivery_queue_watch.py`,
   `run_idle_gates.py`, `run_inbound_unauthorized.py`, `run_turn_runner_*`) —
   Qubit: PARTIAL — `voicebridge/runner_contract.py`, `lifecycle.py`,
   `question_prompt.py`. Owner: fold gates into the Qubit turn runner.
6. **Optional-skills catalog** (12 new skill dirs incl. `dynamic-workflow`,
   `ai-presenter-video`, `scrollcraft`, `pr-lens`) — Qubit: MISSING. Owner:
   vendor re-sync via the vendor manifest; no runtime change.
7. **Plugin catalog data** (`plugin-catalog/*.yaml`, 224 files) — Qubit:
   PARTIAL — `voicebridge/plugin_runtime.py:plugin_catalog`. Owner: import as
   reviewed local manifests; remote fetch stays refused.
8. **Auxiliary reasoning floor / structured output / fallback recovery**
   (`agent/auxiliary_*`) — Qubit: PARTIAL — `voicebridge/brain.py`,
   `voicebridge/providers/resolver.py`. Owner: extend the auxiliary path.
9. **Session lifecycle: rewind / timeline / user-copy**
   (`hermes_state_rewind.py`, `hermes_state_timeline.py`,
   `hermes_state_user_copy.py`) — Qubit: PARTIAL —
   `voicebridge/checkpoints.py`, `voicebridge/task_ledger.py`,
   `voicebridge/portable.py`. Owner: extend the one `state.db`.
10. **Live/duplex voice tool** (`tools/voice_live.py`) — Qubit: PARTIAL —
    `voicebridge/orchestrator.py` (the realtime/duplex lane is intentionally
    removed). Owner: re-entry only through the single speech registry; otherwise
    keep the baseline voice path.

Also notable: `bot_loop_guard` (MISSING), `methods_free_tier` (MISSING — no
anonymous/hosted tier; OPERATOR_ONLY), `thinking-toggle parity` (MISSING),
`codex-runtime` runtime toggle (PARTIAL), `status_report`/`status_bar_git`
(MISSING), `desktop_console` (MISSING), and `vault_login_classifier` (MISSING).

## 6. Adapter-plan rules (unchanged architecture)

Every proposed adapter preserves the frozen architecture:

- one runtime authority (`qubit_runtime/`), resolved by
  `voicebridge/qubit_runtime.py`;
- one session/store authority (`QubitSessionStore` over `state.db`);
- one provider/model registry (`<state_dir>/profiles.json`);
- one broker/policy/audit/e-stop path;
- no second listener, router, action store, or secret store.

Two adapter shapes are used: **vendored re-sync** (bring the upstream module into
the renamed fork via the deterministic vendor manifest — skills, provider data,
optional-mcps, vault backends) and **Qubit adapter** (map the upstream contract
onto an existing `voicebridge/` module — connectors, vault refs, host topology,
turn-runner gates). Adapters that require an external service (managed
connectors, free tier, hosted rooms, desktop console) are OPERATOR_ONLY.

## 7. Unverified references

- The brief's `latest_main` value `5bb314fa…` is stale; live head is
  `bb359ec5…` (5 commits ahead).
- The brief's "72 top-level CLI families" differs from the measured
  66/67/69 (different counting method; likely includes `chat`/`serve`/`agent`
  and plugin-registered families).
- The local pinned clone is shallow, so ancestry/commit counts came from the
  GitHub compare API rather than the local object store.
- Optional-skill and provider-plugin deltas were enumerated by path/name, not
  semantically diffed line-by-line.
- No claim here is `VERIFIED`; this is a documented delta, not a behavioral
  parity result. See
  [`QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md`](QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md).
