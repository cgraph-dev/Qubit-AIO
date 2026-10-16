# REPORT — port/chunk-1 (agent/ + acp_adapter/)

Chunk of the 299 absent production `.py` files. Followed `PORT-METHOD.md`
(branch `port/chunk-5`).

## Fixed inputs

| Thing | Value |
|---|---|
| Worktree | `/tmp/port-1` (branch `port/chunk-1`, base `9c2f782e1`) |
| Upstream ref read | `origin/main` |
| Upstream HEAD at port time | `53bec2a311979ad6f77580ba491eca261ab988e0` (2026-10-16) |
| Vendored pin | `517b5e10febd619ce30bb22580e29b160266eb43` |
| Interpreter | `/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python` |
| Mapping rule | upstream root == `qubit_runtime/`; module renames per `scripts/qubit_rebrand.py` |

## Result

**5 ported, all imports-clean. 0 shims. 6 blocked on dependencies that are themselves
absent (all eight named deps are in the 299-list — other chunks own them).**

| Upstream path | Lines | Status | Imports clean | Missing deps |
|---|---|---|---|---|
| `agent/agent_init_params.py` | 33 | ported | yes | — |
| `agent/agent_init_ollama.py` | 67 | ported | yes | — |
| `agent/codex_service_tiers.py` | 137 | ported | yes | — |
| `acp_adapter/elicitation.py` | 71 | ported | yes | — |
| `acp_adapter/gateway_session_meta.py` | 123 | ported | yes | — |
| `acp_adapter/catalog.py` | 79 | dep-missing | no | `hermes_state_local` |
| `acp_adapter/gateway_commands.py` | 132 | dep-missing | no | `hermes_cli.gateway_client`, `hermes_cli.gateway_mutations` |
| `acp_adapter/gateway_server.py` | 942 | dep-missing | no | `acp_adapter.catalog`, `acp_adapter.gateway_commands`, `gateway.session_ingress_media`, `hermes_cli.gateway_chat`, `hermes_cli.gateway_client`, `hermes_cli.gateway_mutations` |
| `agent/chat_completion_helpers_tool_calls.py` | 73 | dep-missing | no | `agent.reasoning_carriers` |
| `agent/compaction_events.py` | 307 | dep-missing | no | `agent.relay_compaction` |
| `agent/compression_automation.py` | 18 | dep-missing | no | `agent.runtime_session_store` |

No `shim-skipped`: none of the 11 files is a facade/re-export stub. The smallest is
`agent/compression_automation.py` (18 lines) but it is real logic, not a re-export.

## Brand decisions (per §3 of the method; source of truth `scripts/qubit_rebrand.py`)

- `from hermes_state import SessionDB` → `from qubit_state import SessionDB`
  (`MODULE_TOKENS["hermes_state"] = "qubit_state"`; 899 existing production files already
  import `qubit_state`).
- User-facing command description `"Show Hermes version"` → `"Show Qubit version"`
  (matches sibling `acp_adapter/commands.py:69` and `qubit_cli/console_engine.py:796`).
- **`_meta.hermes.sessionProvenance` preserved verbatim.** It is the serialized ACP wire
  key emitted by `acp_adapter/provenance.py:86` (`{"hermes": {"sessionProvenance": ...}}`);
  §3d protects serialized JSON keys. My first write renamed it in a docstring and I
  reverted it (commit `47735b2ce`) — renaming only the docstring would have made the doc
  contradict the real key.
- `agent/codex_service_tiers.py`, `agent/agent_init_ollama.py`, `agent/agent_init_params.py`,
  `acp_adapter/elicitation.py` carried zero brand tokens; left verbatim.

No env var (`HERMES_*`/`QUBIT_*`) appears in any of the 11 files, so the dual-read hazard
did not arise. No file hardcodes the frozen pin.

## Blocked files — why, and what unblocks them

Every blocking dependency is a **top-level** import, so each file raises
`ModuleNotFoundError` until its dep lands; porting now would ship a file that does not
import. All eight deps are upstream-new (absent at the pin) and all are in
`/tmp/prod-files.txt`, i.e. owned by another chunk:

| Missing dep (upstream path) | In 299-list | Owned by | Blocks |
|---|---|---|---|
| `hermes_state_local.py` | yes | — | `acp_adapter/catalog.py` (needs `POLICY_PREFIX`) |
| `hermes_cli/gateway_client.py` | yes | — | `gateway_commands.py`, `gateway_server.py` |
| `hermes_cli/gateway_mutations.py` | yes | — | `gateway_commands.py`, `gateway_server.py` |
| `hermes_cli/gateway_chat.py` | yes | — | `gateway_server.py` |
| `gateway/session_ingress_media.py` | yes | — | `gateway_server.py` |
| `agent/reasoning_carriers.py` | yes | **ported on `port/chunk-3`** | `chat_completion_helpers_tool_calls.py` |
| `agent/relay_compaction.py` | yes | **ported on `port/chunk-3`** | `compaction_events.py` |
| `agent/runtime_session_store.py` | yes | **ported on `port/chunk-3`** | `compression_automation.py` |

`acp_adapter/gateway_server.py` additionally imports two files *from this chunk*
(`acp_adapter.catalog`, `acp_adapter.gateway_commands`), so it unblocks only after those
two unblock — a two-level dependency.

**Ordering note for the merge:** once `port/chunk-3` lands, re-run this chunk against the
merged tree and 3 more files become portable verbatim (the `agent/*` trio). The
`acp_adapter/*` trio needs the `gateway_*`/`hermes_state_local` chunks first.

## Verification performed

Each ported file:

```sh
cd /tmp/port-1 && PYTHONPATH=qubit_runtime \
  /home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python -c 'import <module>'
# agent.agent_init_params         OK
# agent.agent_init_ollama         OK
# agent.codex_service_tiers       OK
# acp_adapter.elicitation         OK
# acp_adapter.gateway_session_meta OK
```

Symbol-level checks (beyond the import) that the deferred imports inside the ported files
resolve: `agent_init_ollama` → `agent.model_metadata.{is_local_endpoint,query_ollama_num_ctx}`,
`agent.agent_runtime_helpers._ra`; `codex_service_tiers` →
`agent.model_metadata._codex_oauth_token_fingerprint|_fetch_codex_oauth_context_lengths_with_source|strip_codex_context_variant_suffix|_bare_codex_slug`,
`agent.codex_headers.{CODEX_AUX_BASE_URL,is_official_codex_base_url}`,
`qubit_cli.auth_constants.{AuthError,_decode_jwt_claims}`, `qubit_cli.auth_codex.resolve_codex_runtime_credentials`,
`qubit_cli.models.{normalize_provider,resolve_fast_mode_overrides}` — all present.
`gateway_session_meta` → `acp_adapter.{auth.detect_provider, model_catalog.build_model_state|encode_model_choice, provenance.session_provenance_meta}` +
third-party `acp.schema` — all present.

## Commits (branch `port/chunk-1`)

- `5ddeedbef` port(agent/acp_adapter): upstream agent_init_params, elicitation, gateway_session_meta
- `a0bbfcd82` port(agent): upstream agent_init_ollama, codex_service_tiers
- `47735b2ce` fix(port): keep `_meta.hermes` wire key verbatim in gateway_session_meta docstring

Plus this report, `PORTED.json`, `PROGRESS.md`.

## Obstacles worth recording

1. **A serialized wire key looks like brand and is not.** `_meta.hermes` survived a first
   pass as `_meta.qubit`; §3d's "serialized JSON keys are preserved" is what caught it.
   The signal: `acp_adapter/provenance.py` *emits* `{"hermes": ...}`, so the docstring must
   name `hermes` or it lies about the key.
2. **Two-level dependency inside a chunk.** `gateway_server.py` is blocked partly by two
   files in the *same* chunk. A chunk-level "portable" set must be computed transitively,
   not per-file — otherwise `gateway_server.py` looks one-unblockable-step-from-ready when
   it is three.
3. **`acp.schema` / `urllib.parse` false-positive on a naive dep scan.** A first pass that
   flags every non-existent module must whitelist third-party/stdlib roots or it reports
   `acp.schema` as "missing" and hides the real gaps.
