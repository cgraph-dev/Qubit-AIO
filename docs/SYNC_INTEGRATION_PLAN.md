# Upstream integration — where we are, what's missing, how to stop the slop

Baseline: pin `69948c0057` → `a036b13793` (2026-09-25 → 2026-10-06).
**5,441 commits, 5,142 files** (`git rev-list --count 69948c0057..a036b13793`).

## Session-lease bug — "This chat is open in another Qubit window" (fixed 2026-10-06)

Recurring, user-visible, and NOT an upstream bug — it is in our PTY launcher.

**Symptom:** close the browser, reopen an old session → refused with
`This chat is open in another Qubit window/terminal. Details: session <id> opened by tui 38m ago.`
Lease holder is an orphan `qubit --tui` whose browser is long gone, still alive.

**Mechanism (three facts that compose into the failure):**

1. `voicebridge/pty_ws.py::detach_all()` / the disconnect path **deliberately does not kill
   the PTY** — by design, a browser *refresh* must not destroy an in-flight turn. The
   terminal survives as a detached session, still owning the vendored session lease.
2. `IDLE_TIMEOUT_S = 30 * 60.0` — that orphan stays alive, and keeps its lease, for **30
   minutes** after the browser closes.
3. The lease is **pid-fenced** (`qubit_cli/active_sessions.py::_prune_dead`): it is only
   reclaimed when the holder's *process* dies. A live-but-clientless TUI is never pruned,
   and it self-vouches on every sweep, so nothing reaps it.

So a reopen attempts to resume a session whose lease is held by a live orphan → refusal.

**Why re-attach did not save us:** `pty_ws.py` had
`if key and not resume:` — the re-attach branch was skipped whenever a resume target was
supplied, and `_default_spawn` *always* sets `resume`. Browsers additionally clear
`sessionStorage` on close, so the reopened tab presents a **new** `?attach=` token that
matches no detached key. Both paths therefore fell through to a fresh spawn.

**Fix:**
- `pty_ws.py`: dropped the `not resume` guard so a detached terminal is always re-adopted;
  added `_Session.session_id` (the canonical resume target) and `_reattach_session()` so a
  rotated attach token re-attaches by resume target instead of spawning a second terminal.
- Regression coverage: `tests/test_pty_detach_reattach.py` (3 new tests) + a standalone
  probe at `runtime/cache/scratch/probe_reattach.py` that drives the real `serve()` path.

**Still open (deliberate, not done):** the frontend could keep the attach token across a
browser close (localStorage + a live-document guard) so the *server* re-attach is not the
only safety net. `sessionStorage` cannot do this by design; see
`frontend/qubit-web/src/lib/pty-attach-token.ts`.

## The number that matters is not 5,142

5,142 is the file delta. Most of it is not integration work:

| Bucket | Files | Verdict |
|---|---|---|
| `apps/` (Electron desktop) | 1,640 | not our surface — the shipped dashboard is the web SPA on 8890 |
| `tests/` | 1,320 | 1,029 of the missing symbols live here; a prior blind sync created a mis-routed duplicate tree and caused mass failures |
| `website/` `locales/` `.github/` `nix/` `pm/` | ~370 | build/docs/CI — low risk, low value |

**The real surface is the capability audit**, not the file count:
`docs/SYNC_CAPABILITY_GAPS.md` → **1,833 missing symbols across 454 files**, of which
**199 runtime files / 656 symbols** (excluding tests + scripts).

## Corrected measurements (the old numbers were wrong)

Two silent-failure bugs made every prior measurement understate the gap:

1. **`BASE_SHA` was `bd0affe5`** (2026-10-02) — 1,313 commits short of current upstream.
   Reported 4,128 commits / 4,438 files. Real: 5,441 / 5,142.
2. **`__root__` was passed as a git pathspec** (`-- __root__`), which matches nothing.
   It reported **0 files** and a `--run __root__` would have merged nothing while
   reporting success — silently skipping the 49-file primitive layer
   (`utils.py`, `qubit_state_errors.py`, `qubit_constants.py`).

Both are fixed. The capability audit therefore went from **346 → 1,833** missing symbols.

## Why the primitive layer goes first

Consumers import symbols DEFINED in more primitive modules. Three real failures came
from the primitive layer landing late or not at all:

- `utils.mkstemp_beside`
- `qubit_state_errors.SessionActiveWriteGuardError`
- `qubit_platform.host.products.is_nvidia_n1x_pci_id`

Plus two found on 2026-10-06 (same class — symbol dropped, call site survived):

- `tui_gateway/server.py` did not import `turn_marker.marker_writer_state`
  → `NameError` killed every resume of a crash-interrupted session.
- `qubit_cli/plugins.py` was missing `publish_gateway_message_host` /
  `clear_published_gateway_message_host` / `_attach_published_gateway_host`
  → `ImportError` killed the gateway daemon at startup; systemd hit its restart limit.

**This class is the actual "AI slop"**: a rename or partial port drops a symbol, the
import site survives, and the failure surfaces far away as a runtime crash.

## The mechanical guard (do this for every slice)

```bash
python3 scripts/sync_import_gate.py --save /tmp/gate_before.json      # baseline
python3 scripts/sync_upstream.py --apply --subtree <sub> --json-out /tmp/o.json
python3 scripts/sync_import_gate.py --compare /tmp/gate_before.json   # exit 1 = regression
```

The gate compares failure **identity**, not counts — a count-only gate once missed a
regression where a benign failure was replaced by a new one and the count stayed at 2.

## Entry-point resolution sweep (the class-level fix)

Beyond the curated module list in the gate, the whole-tree check that would have caught
both 2026-10-06 bugs is:

> for every `from X import Y` / `X.Y` in the vendored tree, assert `Y` exists on `X`.

Nothing currently does this exhaustively. Recommended before declaring the sync done.

## Ordering

`__root__` → primitives → `agent`/`tools`/`gateway` → `tui_gateway`/`cron`/`providers`
→ surfaces. Never reorganize this by file count.

## Deletions — never auto-apply

154 upstream deletions include `apps/desktop/electron/plugin-compat-notice.ts` and
`.../plugin-settings-form.tsx` (plugin-compat UI — the surface for our own adapter work),
plus brand assets. Check against `docs/PARITY.md` per file. Mirroring upstream deletions
blindly deletes features we still ship.

## Independence status

`voicebridge/agent_compat/` makes foreign-plugin compatibility a name-mapping problem
rather than a chase-every-commit problem. It is test-enforced:

- `test_adapters_carry_no_runtime_dependency_on_the_foreign_agent`
- `test_unmodified_foreign_plugin_executes` (real unmodified upstream plugin)
- `test_framework_is_not_used_in_tree`

None of the three external dependencies is a runtime dependency:

| Dependency | Runtime need | Safe to remove |
|---|---|---|
| `~/.cache/qubit-pins/*` | none (build-time only; zero runtime refs) | yes, but loses the sync baseline + adapter test fixture |
| `hermes-dashboard.service` (:9119) | none | yes |
| `openclaw-gateway.service` (:18789) | none | yes |

`agent_compat.install()` is built and tested but **not yet called anywhere** — the last
mile of not depending on foreign plugins.
