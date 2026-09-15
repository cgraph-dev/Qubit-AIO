# Qubit rebrand public-surface allowlist

Status: **owner gate — fail-closed scan**

The normal Qubit product must never expose `Hermes` or `OpenClaw` as a
product/assistant name, route prefix, URL, title, nav label, CLI command, help
text, installer/service/log/report label, or dashboard path
(`docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`, Identity promise).

`tests/test_qbpv2_rebrand_public_surface.py` scans the served/rendered public
surface and fails on every upstream-name occurrence that is not on this
explicit allowlist. New occurrences must be justified here or removed.

## Public surface in scope

- the route table served by `voicebridge/webui.py` (`SPA_ROUTE_PATHS`,
  `HERMES_ROUTE_ALIASES`, `HERMES_WS_GATE_CODES` keys);
- the served SPA shell (`GET /chat` and every public route), including the
  `href`/`src`/`action` URLs it points at;
- the built SPA bundle under `frontend/hermes-dist/assets/*.js` when present
  (curated product-phrase scan).

## Allowlisted occurrences

These are internal compatibility / wire-protocol identifiers, not product
names. They are removed from the scanned text before the upstream-name check.

| Token | Why it is allowed |
| --- | --- |
| `__HERMES_BASE_PATH__` | Internal compat identifier the vendored frontend still reads; the server injects it as `""` (contract-mandated). |
| `__HERMES_SESSION_TOKEN__` | Wire-protocol identifier for the loopback session token injected into the shell. |
| `X-Hermes-Session-Token` | Wire-protocol auth header the vendored SPA sends; renaming it would break the API client. |

## Out-of-scope (allowed by the frozen contract, not part of this scan)

The following legitimately retain upstream names and are not scanned here:

- legal notices and attribution: `LICENSE`, `THIRD_PARTY_NOTICES.md`,
  `voicebridge/upstream/ATTRIBUTION.md`, `docs/LICENSING.md`;
- provenance / source-comparison manifests: `docs/QUBIT_HERMES_VENDOR_MANIFEST.json`,
  `docs/HERMES_SOURCE_COMPARISON*.json`, `docs/HERMES_FRONTEND_MAP.json`,
  `docs/QUBIT_SOURCE_INVENTORY.json`, `docs/INVENTORY_*.json`;
- internal vendored source under `qubit_hermes/` and the pre-build vendored
  frontend inputs under `frontend/hermes-web/`, `frontend/hermes-shared/`,
  `frontend/hermes-qubit/`;
- migration-only compatibility code and its tests (e.g. the `_hermes_api`
  internal path rewrite, `hermes_compat` route names) — none of these are
  reachable as a public route;
- internal Python identifiers and module names (`webui.hermes_*`,
  `voicebridge/hermes_*.py`).

## Hard route rules asserted by the tests

- `/hermes` and `/hermes/*` return **404** (no route at all, not a redirect).
- `/` redirects to `/chat`; every other public route returns the SPA shell.
- `/agent/api/*` reaches the compat backend; native `/api/*` is unchanged.

## Additional explicit allowances (wave-2 verifier findings)

Product-facing rebrand fixes applied: locale files (`~/.qubit`, `qubit
plugins/update`), `EnvPage`/`ChannelsPage` env + gateway text, `SystemPage`
portal/backup strings, `SkillsPage` command text, console prompt fallback,
achievements share-card footer, kanban CSS comment, the config help text
(render-boundary rebrand is now case-insensitive), and the runtime state home
is `state/qubit` (was `state/hermes`).

The following remain and are explicitly allowed by the frozen contract
(attribution / provenance / internal identifiers), not product identity:

| Occurrence | Class |
| --- | --- |
| `DocsPage` help link `https://hermes-agent.nousresearch.com/docs/`, kanban plugin docs links | attribution/provenance (upstream documentation link) |
| achievements bundle `@NousResearch` attribution and MIT/upstream repo comment | legal attribution |
| optional-MCP `n8n` manifest `Installs from: https://github.com/…/hermes-n8n-mcp.git` | provenance (third-party source URL) |
| internal identifiers: `__HERMES_BASE_PATH__`, `__HERMES_SESSION_TOKEN__`, `__HERMES_AUTH_REQUIRED__`, `__HERMES_PLUGINS__`, `__HERMES_PLUGIN_SDK__`, `X-Hermes-Session-Token`, `hermes_flow` source id, `hermes-kanban-*`/`hermes-achievements` plugin/CSS ids, `hermes.tokenReloadAttempted`, `HERMES_QWEN_` env prefix, camel-case JS identifiers (`updateHermes`, `checkHermesUpdate`, `HERMES_DOCS_URL`) | wire protocol / developer API / data keys (renaming breaks clients) |
| `qubit_hermes/` module path and `qubit-hermes` console-script launcher | internal vendored module / migration-compat launcher (the canonical launcher is `qubit`) |
