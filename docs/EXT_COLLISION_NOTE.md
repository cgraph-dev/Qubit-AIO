# Collision note — browser-companion absorption

Scope: the agent-side half (`companion-plugin/`) of
`https://github.com/abundantbeing/hermes-browser-extension` at pin
`16d42430ff45d5833cb6176b401db867b591c2f2` (MIT, Copyright (c) 2026 Jon Komet),
absorbed as the native Qubit plugin `qubit_runtime/plugins/browser-companion/`.
The browser-side `extension/` tree is out of scope and stays upstream's.

This note is the decision authority for what was ported. Everything below was
read on both sides, not inferred: companion `plugin.yaml` + `tools.py` +
`control_tools.py` + `control_schemas.py` + `control_broker.py` + `hooks.py` +
`context_store.py` + `status_route.py`; Qubit `toolsets.py`,
`tools/browser_tool.py`, `tools/browser_extension_router.py`,
`gateway/browser_control_broker.py`, `voicebridge/browser.py`,
`voicebridge/browser_tabs.py`, `voicebridge/cdp.py`,
`voicebridge/cdp_supervisor.py`.

---

## (a) What the companion provides

`companion-plugin/plugin.yaml` declares 27 `provides_tools` and three hooks
(`pre_llm_call`, `pre_tool_call`, `post_tool_call`), `capabilities: []` (no
privileged host surface), and a `requires_hermes: ">=0.22"` version floor.

**Seven context tools** (`tools.py`; every one authorised by the `pre_tool_call`
hook, which hands the handler a one-shot lease and otherwise blocks):

| Tool | What it does |
|---|---|
| `browser_context_status` | Owner-scoped metadata for the browser context stored for this turn (context id, scope, payload hash, age). |
| `browser_get_context` | Consumes one stored BCP-v2 payload by opaque `context_id`, once, for this owner. |
| `browser_clear_context` | Clears this owner's stored context records. |
| `browser_event_log` | Last N (1–50) plugin lifecycle events, owner-scoped, metadata only. |
| `browser_control_status` | Last validated control envelope for this owner; historical metadata, never live authority. |
| `browser_context_journal` | Up to 500 metadata-only journal rows (who was delivered what, no payload). |
| `browser_text_utility` | Bounded, model-free text ops: `clean_formatting`, `make_bullets`, `text_stats`, `diff`. |

**Twenty `tab_*` tools** (`control_tools.py` + `control_schemas.py`), each one
wire action dispatched to a controller (the extension) that attached over the
plugin's own WebSocket broker: `tab_navigate`, `tab_snapshot`, `tab_click`,
`tab_type`, `tab_fill`, `tab_select`, `tab_press`, `tab_scroll`,
`tab_scroll_to`, `tab_hover`, `tab_drag`, `tab_back`, `tab_screenshot`,
`tab_batch`, `tab_list`, `tab_create`, `tab_activate`, `tab_close`,
`tab_group`, `tab_ungroup`. They are named `tab_*` **specifically because the
upstream core already owned `browser_*`** — `control_schemas.py` says so in its
module docstring: "two plugins cannot share a tool name".

Plus an HTTP surface (`status_route.py`, `context_routes.py`,
`control_routes.py`, `profile_routes.py`) wired through
`ctx.register_platform_handler("api_server", …)` and a bundled skill.

## (b) Qubit's existing browser surface

Runtime tools (confirmed live: `model_tools` import registers 103 tools, of
which `browser_*` accounts for the list below):

| Layer | Provides |
|---|---|
| `tools/browser_tool.py` (agent-browser CLI; local Chromium, Browser Use, Browserbase, Firecrawl, Camofox) | `browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_press`, `browser_get_images`, `browser_vision`, `browser_console` — same `@eN` ref contract, per-task sessions, SSRF and secret-in-URL guards (`evaluate_url_safety`). |
| `tools/browser_cdp_tool.py`, `tools/browser_use_cli.py` | `browser_cdp` (raw CDP), `browser_exec` (Python over CDP), `browser_dialog`. |
| `tools/browser_vault_tool.py` | `browser_vault{,_list,_fill,_save_login,_unlock,_enter_code}` — credentials never enter the conversation. |
| `tools/browser_extension_router.py` + `gateway/browser_control_broker.py` | **Qubit already has the extension-control lane.** `browser_control_enabled()` gates on `browser.extension_control.enabled`; the router re-routes `browser_navigate/click/type/scroll/snapshot/back/press/screenshot/tabs/tab_activate` to an attached extension controller, failing closed when the lane is bound. Capability allowlist: `browser_back, browser_click, browser_navigate, browser_press, browser_screenshot, browser_scroll, browser_snapshot, browser_tab_activate, browser_tabs, browser_type`. |
| `voicebridge/browser.py` | Typed CDP precondition state (`OK` / `BROWSER_RESTART_REQUIRED` / `UNAVAILABLE`), managed profile registry, `BROWSER_CLOSE_REFUSED` guard. No agent-facing tool. |
| `voicebridge/browser_tabs.py` (loopback CDP only, stdlib) | `tabs`, `read`, `find`, `screenshot`, `screencast`, `activate`, `open`, `navigate`, `fill`, `click`, `close`, `open_browser`, `doctor`, `inspect_element`, `annotate/clear_annotation`, `remote_attach`, `extension_attach`, `favicon`, `download`. Reachable from the CLI, voice commands, dashboard RPC and voice actions — **not a runtime tool the model calls**, and requires `confirmed=True` for anything that mutates the browser. |
| `voicebridge/cdp.py` | stdlib loopback-only CDP transport (`http_json`, `probe`); SSRF-by-config impossible. |
| `voicebridge/cdp_supervisor.py` | watchdog + health file for the debug-port Chromium; never signals a browser process. |

## (c) Overlap

- **Genuine duplicates of a Qubit capability** — the 20 `tab_*` tools, all of
  them, and the seven context tools as a *class* (browser context is a
  capability Qubit already reaches over loopback CDP through
  `voicebridge/browser_tabs.read/find`).
- **Same name, different contract (hard conflict)** — the companion's wire
  action names `browser_navigate`, `browser_click`, `browser_type`,
  `browser_scroll`, `browser_back`, `browser_press`, `browser_screenshot`,
  `browser_snapshot`, `browser_tabs`, `browser_tab_activate` are Qubit tool
  ids that already exist with a different argument contract
  (`browser_screenshot` vs Qubit's `browser_vision`; `browser_tabs` vs
  Qubit's absent-by-that-name tab list). Upstream shields this with a
  `tab_*` rename; that shield is unnecessary here because Qubit already owns
  the lane, but it is exactly why the tools must not be registered verbatim.
- **Genuinely new** — none of the 27 is a new capability. `browser_text_utility`
  is the one tool with no Qubit counterpart *as a tool* (see the decision),
  and even that is a wrapper over `difflib`/`re`.
- **Not portable at all** — `status_route.py`, `context_routes.py`,
  `control_routes.py`, `profile_routes.py` and the `register_status_route`
  factory need `ctx.register_platform_handler`. Qubit's
  `PluginContext` (checked method-by-method) has **no** such seam, and
  `plugins/AGENTS.md` forbids widening core for a plugin-specific need. That
  whole HTTP half is therefore undeliverable without a core change this
  absorption is not allowed to make.

## (d) Decision per tool

| Companion tool | Decision | Reason |
|---|---|---|
| `browser_context_status` | **PORT** | Owner-scoped "is context attached" answer Qubit has no tool for; kept verbatim (schema unchanged), fails soft to `{"available": false}`. |
| `browser_get_context` | **PORT** | The consume-once opaque-lease read is the useful half of the companion; kept with its `context_id` contract. |
| `browser_clear_context` | **PORT** | Trivial, coherent with the two above, and needed to make the lease story coherent. |
| `browser_context_journal` | **DROP** | Metadata-only diagnostics for a delivery channel that does not exist in this port; nothing to journal. |
| `browser_event_log` | **DROP** | Same reason; `browser_context_status` already reports the state that matters. |
| `browser_control_status` | **DEFER** | Live control truth belongs to `gateway/browser_control_broker.py`, which Qubit already routes through; a second historical-only view would be a lie by omission. Revisit if the plugin ever owns a lane. |
| `browser_text_utility` | **DROP** | Not a browser capability — a `difflib`/`re` convenience duplicating what `browser_exec` (Python, stdlib available) and `execute_code` already do; registering it would be a new core-shaped tool with no browser in it. |
| `tab_back`, `tab_click`, `tab_drag`, `tab_fill`, `tab_hover`, `tab_navigate`, `tab_press`, `tab_screenshot`, `tab_scroll`, `tab_scroll_to`, `tab_select`, `tab_snapshot`, `tab_type`, `tab_batch` | **DROP** | Qubit's `browser_*` tools already do every one of these and already route to an attached extension controller via `browser_extension_router`. Registering a parallel `tab_*` set would give the model two browsers-with-the-same-name and a tool list that grows 14 entries for zero capability. |
| `tab_list`, `tab_activate`, `tab_close`, `tab_create` | **DROP** | Same; Qubit's lane covers `browser_tabs` / `browser_tab_activate`, and `voicebridge/browser_tabs.py` covers list/activate/close/open with confirmation + audit. |
| `tab_group`, `tab_ungroup` | **DEFER** | The only tab actions with no Qubit equivalent at any layer (the broker capability allowlist has no grouping capability). Useful, but they need the extension side to negotiate a capability Qubit's broker does not have — that is a core/broker change, not a plugin port. |
| hook `pre_llm_call` | **DROP** | Its job is to cache a `hermes.browser.turn.v2` envelope out of the user message. No extension route exists in this port to deliver one, and Qubit's own `pre_llm_call` payload would need a live extension half to be meaningful. |
| hooks `pre_tool_call` / `post_tool_call` | **PORT (narrowed)** | `pre_tool_call` is load-bearing: it is what makes the three ported tools owner-scoped (no lease ⇒ `available: false`). `post_tool_call`'s receipt/journal recording goes with the DROPped journal; not ported. |
| HTTP routes (`status`, `context`, `control`, `profile`) | **DROP** | No `ctx.register_platform_handler` in Qubit's plugin surface; see (c). |
| `control_broker.py` + `control_routes.py` | **DROP** | Reimplements `gateway/browser_control_broker.py`, which Qubit already ships and already routes through. |
| `journal.py`, `context_store.py` receipts | **DROP** | Only the journal/event-log half of `context_store` goes; the record/lease core is ported. |
| bundled skill `hermes-browser-extension` | **PORT → RENAME** | Rewritten as `skills/browser-companion/SKILL.md` describing only the tools that exist, and saying plainly that browser context is unavailable unless the extension half is attached. |

Rejected scope, stated plainly: **this port adds no browser control and no new
browser capability to Qubit.** Everything that would have was either already
owned by `browser_*` + the extension router, or needed an extension half that
is upstream's deliverable, or needed a core seam `plugins/AGENTS.md` forbids.
What it adds is the owner-scoped BCP-v2 context contract (`status` / `get` /
`clear`) as a plugin — which is the only part of the companion that is not
duplicative.
