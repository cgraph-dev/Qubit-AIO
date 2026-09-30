# Qubit PARITY — the reference dashboard

**Owner directive (2026-09-22).** Qubit is built on the upstream foundation. The
working local reference instance is the behavioral reference, and **everything that
works there must work in Qubit** — every route, page, button, control, skill,
MCP server, browser action, terminal, IDE action, and command. On top of that,
**every one of those capabilities must be commandable by voice through the Qubit
voice bridge.** "Everything via Qubit voice."

This is not achieved. It is the definition of done.

## The reference surface

| | Value |
|---|---|
| URL | `http://127.0.0.1:9119/?profile=default` |
| Status | live, HTTP 200, serves the reference dashboard |
| Working install | `/home/trick/.hermes/hermes-agent` (`hermes-agent` 0.21.3, git `db39ee3f`) |
| Web layer | `hermes_cli/web_server.py` (1,931 lines) + `hermes_cli/web_routers/` (23 routers) |
| Route literals | ~245 unique paths across the web layer |
| Skills / MCP | 14 skill dirs, 65 optional MCPs |

## The rule

1. **CLI-for-CLI.** Every reference CLI command and subcommand must work in
   Qubit (via `qubit` or `qubit agent`). Known gaps: `qubit chat` returns a
   typed refusal (`QUBIT_NATIVE_CHAT`); `codex-runtime`, `credentials`, and
   `vault` are missing from `qubit agent`.
2. **Route-for-route.** Every HTTP route the reference dashboard serves must have a
   working Qubit counterpart on the same canonical authority path (broker /
   policy / audit / confirmation / e-stop).
3. **Control-for-control.** Every button, form, control, page, and navigation
   entry must work against the real Qubit backend.
4. **Tool-for-tool.** Every skill, plugin, MCP server/tool, browser action,
   terminal command, IDE action, and model/provider op must work in Qubit.
5. **Voice-for-everything.** Every capability above must be reachable by voice
   through the Qubit voice bridge. A capability with no voice path is **not done**.
6. **Behavioral proof.** A capability counts only with a command + observed
   output. A rendered page, a catalog row, or a typed refusal is **not** parity.

## Current state (measured 2026-09-22 — not parity)

| Surface | Reference | Qubit | Gap |
|---|---|---|---|
| CLI commands | 173 (`hermes`) | 163 (`qubit agent`) | `qubit chat` refused; `codex-runtime`, `credentials`, `vault` missing |
| Web routes | ~245 literals, 23 routers | `qubit_compat.ROUTES` 415 (**94 GATED**) | 94 GATED routes are refusals, not working |
| Dashboard controls | full live UI | `dashboard_ops` catalog 562 + 35 extra; **3199 OPEN** | no E2E run |
| Voice | n/a | `voice_commands.REGISTRY` 510 + 18 guards | coverage of every capability unproven |
| Skills | 14 dirs | present (fork) | not proven end-to-end |
| MCP | 65 optional | present (fork) | not proven end-to-end |

The 94 `GATED` routes are **deliberate policy refusals with working
counterparts** (no raw PTY/console, no WS dialect, no secret reveal, no remote
fetch, operator-owned update), not missing features. Under the owner amendment a
refusal is still not a working capability, so each must be **dispositioned**:
either the owner confirms the refusal *and* its counterpart works (with a voice
path), or it is a genuine gap that must be implemented. See
`FINDINGS_2026-09-22.md` §G.

## Method (repeatable)

1. **Enumerate the reference.** Build a route + control inventory from the live
   reference UI and its source (`hermes_cli/web_server.py`, `web_routers/*`).
2. **Inventory Qubit.** Enumerate `qubit_compat.ROUTES`, `webui.py` routes,
   `dashboard_ops.catalog`, and the CLI/voice registries.
3. **Diff and classify.** Each reference entry → `PORTED` (works) / `GATED`
   (refusal — treat as OPEN) / `MISSING`. Only `PORTED` with behavioral evidence
   counts.
4. **Voice map.** Each capability → a voice intent. No voice path = OPEN.
5. **Close.** Port/adapt from the reference (never recreate); prove with a command +
   output; re-run.

## Non-negotiables

- Do not rebuild the upstream with stubs, fake registries, or placeholder handlers.
  Port or adapt the real source.
- Do not edit `docs/differential/INTERFACE.md` or any frozen record.
- Do not claim parity, `VERIFIED`, or completion without the behavioral evidence.
