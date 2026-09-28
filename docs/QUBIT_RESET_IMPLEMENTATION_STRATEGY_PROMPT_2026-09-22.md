# RESET THE IMPLEMENTATION STRATEGY

**Status:** execution prompt (2026-09-22). Supersedes the draft that assumed the
runtime was rebuilt from stubs. Ground truth is the verified vendor delta in
[QUBIT_VENDOR_DELTA_2026-09-22.md](QUBIT_VENDOR_DELTA_2026-09-22.md).

## Ground truth (verified, do not re-litigate)

- `qubit_runtime/` **IS** the real vendored Hermes source, renamed. It is not a
  stub foundation and was not recreated from scratch.
- `qubit_runtime/` is `qubit-agent` **0.21.1**. The working local install
  `/home/trick/.hermes/hermes-agent` is `hermes-agent` **0.21.3** (git
  `db39ee3f28`). The fork is ~2 patch releases behind.
- The stub/adapter surface is the **overlay**, not the core:
  `voicebridge/qubit_compat.py`, `voicebridge/gateway/openclaw_handlers/`, and
  the Qubit route/voice/state adapters.
- None of the 72 modules present in the working install but absent from
  `qubit_runtime/qubit_cli/` (nor the 7 absent `qubit_state_*` modules) is
  imported by Qubit code. They are upstream **decomposition (PR #102117)** in
  0.21.2/0.21.3, not stripped functionality. So their absence does not break
  the boot.

## Objective

Make the actual Qubit-forked Hermes runtime **boot and serve end-to-end**, then
prove it behaviorally. Do **not** rebuild Hermes. Do **not** create parallel
registries, fake catalogs, or placeholder handlers. Do **not** replace working
Hermes modules with Qubit reimplementations.

## Rules

1. `qubit_runtime/` is the implementation baseline. Diff it against the working
   install **only** to locate real drift or bugs.
2. Never wholesale-copy upstream in. A naive copy re-introduces `hermes_cli/`,
   `hermes_state.py`, `HERMES_HOME`, `hermes_bootstrap.py`, etc. — and undoes the
   deep rebrand. Port only a module that is **provably missing AND required**,
   and then apply the hermes→qubit rename map.
3. Version sync (0.21.1 → 0.21.3) is a separate, policy-governed step under the
   upstream sync policy. It is **out of scope** for this boot fix; do not start
   it here.
4. `~/.hermes` is **read-only** migration/reference input. It must never be a
   hidden runtime fallback. Runtime authority is `QUBIT_HOME`.
5. Never claim functionality because a page/route/catalog renders. Prove real
   invocation with a concrete command and its observed output.
6. Do not commit, push, reset, or broad-clean. Before touching anything, back up
   the current tree **outside git** (the worktree is ~15k dirty entries).

## Step 0 — Reproduce and diagnose before changing code

Reproduce the failure once, then produce a filesystem backup. Diagnose by
comparing the **working Hermes process** with the **Qubit process** across:

- executable; source/package path; argv; working directory; environment;
  `QUBIT_HOME` vs `HERMES_HOME`; config; provider/model; gateway child process;
  PTY/WebSocket; tool/skill/plugin/MCP registries; session database; logs and
  exit codes.

Named suspects to check first (real objects, not invented names):

- **Not a port conflict (corrected 2026-09-22).** The `127.0.0.1:9119` listener
  is the managed `hermes-dashboard.service` (the reference). Qubit's webui is
  `8890`; its vendored 9119 server is inert. Do **not** treat 9119 as a Qubit
  conflict.
- **The real instability:** chronic TUI `npm install`/`build` failures
  (`qubit_runtime/ui-tui`) and repeated service restarts (18× on 2026-09-22),
  plus a SQLite WAL-reset warning. See `FINDINGS_2026-09-22.md`.
- Stale/duplicate systemd units under `systemd/` and in
  `~/.config/systemd/user/`.
- `QUBIT_HOME` pointing at, or falling back to, `~/.hermes`.

Note: there is no object named `qgateway` anywhere in the repo. Do not chase a
phantom string; name the real process, log, and unit.

Fix the **real cause**. Do not hide the error or mask it with a UI status.

## Boot baseline (must all pass before any new feature work)

1. Qubit service starts.
2. Qubit gateway starts and remains alive.
3. `/chat` opens the real terminal runtime.
4. `/chat` loads the real model/provider.
5. `/chat` shows real tools, skills, plugins, MCP, and sessions.
6. `/chat` accepts a prompt and returns a real response.
7. Hermes-compatible commands and options work.
8. Skills execute.
9. Plugins load and execute.
10. MCP servers/tools load and execute.
11. Sessions and history persist.
12. Gateway status/start/restart work.
13. Dashboard controls operate against the real backend.

Each item is proven by a **command + observed output saved as evidence** — never
by a 200, a rendered button, or a catalog entry.

## Runtime authority

Normal runtime must use `QUBIT_HOME`, Qubit-owned skills, plugins, MCP registry,
configuration, sessions/state, and one broker/policy/audit/confirmation/e-stop
path. `~/.hermes` is reference-only.

## Chat surfaces (behavior, not existence)

`/chat` must be the complete Hermes terminal behavior, rebranded as Qubit:
terminal; commands; options; tools; toolsets; skills; plugins; MCP;
providers/models; reasoning; sessions/history; streaming; cancellation;
reconnect; resize; confirmations; e-stop; errors and recovery. Preserve
*behavior through Qubit's canonical surfaces* — do not re-enable refused upstream
WS dialects or the upstream dashboard wholesale.

`/qubit-chat` must be the native Qubit local/voice surface on the same backend:
Brain4/local model, dynamic context, reasoning selector, API escalation, voice,
browser/local/auto STT, Right-Ctrl, and the same tools, skills, plugins, MCP,
sessions, broker, policy, and audit.

## Spec anchor (do not discard)

The frozen acceptance/parity/voice/rebrand contracts and
`docs/differential/INTERFACE.md` remain the definition of done. A working boot
baseline is **step 1 of satisfying them**, not a replacement for them.

## Only after the baseline works

Deep rebranding, OpenClaw adapters, future upstream sync, page parity, and final
verification.

## Report back

Report: the exact Hermes source paths compared, the exact Qubit runtime path
used, the root cause of the gateway exit, and real working smoke-test results
(commands + output). No commit, no push, no reset, no broad clean.
