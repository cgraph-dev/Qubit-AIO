# Red-team: interrupted-turn auto-resume (both consumers)

Date: 2026-10-04. Read-only analysis. Product code not modified by this task.
Verified against tree (branch `voice-final`) by a follow-up pass; line numbers below are
as-found, checked by grep/read (trust the code, numbers drift — see Corrections).

Two independent consumers of the interrupted-turn marker exist:

| Consumer | Surface | Marker writer | Marker reader / gate |
|---|---|---|---|
| TUI gate | dashboard PTY, desktop | `prompt_turn._record_turn_marker` (`prompt_turn.py:171`; sole call `:1039`) | `session_auto_continue._maybe_schedule_auto_continue` (`session_auto_continue.py:57`) |
| SSE page | `/qubit-chat` | `voicebridge/chat_transport.py` | `chat_transport._pending_interrupted_turn` (`:747`) |

## Findings

| # | Finding | Severity | Live-reachable | Evidence |
|---|---|---|---|---|
| F1 | SSE consumer writes **no attempt count**: `_write_turn_marker` calls `module.record_turn_start(home, session_id, prompt)` at `chat_transport.py:541` with no `attempts`, so the marker is always `attempts=0` (the signature default, `turn_marker.py:75`). The crash-loop breaker (`attempts >= max_attempts`, `session_auto_continue.py:72`) therefore never trips on the SSE path. | Med | Yes | `chat_transport.py:541`; TUI counterpart passes `attempts=marker_attempt` (`prompt_turn.py:184`). |
| F2 | SSE marker written **only on the local-model path**. `_write_turn_marker` has exactly **one** call site, `chat_transport.py:4226`, inside the local streaming `_worker`. Every other turn shape returns *before* that line: affirmative-settle (`:4017`/`:4040`), slash (`:4065`), direct facts (`:4090`), classifier branch → `_stream_branch` (`:4109`), remote route → `_stream_remote` (`:4114`/`:4140`). The blocking `turn()` (`:3308`) writes **no** marker at all. So a crash mid remote/branch/web/slash/fact turn leaves nothing to resume. | High | Yes | grep `record_turn_start` in `chat_transport.py` → one site (local only); grep `_write_turn_marker` → one call (`:4226`) + def (`:534`). |
| F3 | Cross-process lost update in the marker store: `turn_marker._update` (`:63`) takes a **per-process** `threading.Lock` (`_lock`, `:26`); `atomic_json_write` gives atomic file replace but not cross-process read-modify-write. Two processes recording different keys concurrently can drop one. | Med | Yes in principle (multiplex / two backends on one home); **not observed** on the operator's single-backend setup (see T2) | **Re-run this pass** — `scratch/probe_race5.py` (monkeypatches `_store` with a 50ms sleep). Raw output: `stored keys: ['A']` / `A present? True | B present? False` / `LOST UPDATE PROVEN`. (The `A`/`B` survivor is timing-dependent; the loss is the invariant.) |
| F4 | Prune off-by-one: `_prune` caps to `_MAX_ENTRIES=32` (`turn_marker.py:22,51-53`), then `record_turn_start` adds the new key (`:86`) → steady state **33 entries**. | Low | Yes | `turn_marker.py` prune cap vs add. |
| F5 | TUI gate returns `None` **without clearing** when `marker['auto_continue']` is falsy (`session_auto_continue.py:68-69`), leaving the marker to linger until the 24h prune. (Contrast the stale/disabled/crash-loop branch at `:72-74`, which *does* clear.) | Low | Yes | `session_auto_continue.py:68`. |
| F6 | `read_turn_marker` (`turn_marker.py:95`) is age-blind — freshness is the caller's job. The `/chat` consumer applies its own window (`_auto_continue_allowed`, `chat_transport.py:777`), so stale markers do not fire there. | Low | No (both consumers gate) | `turn_marker.py:95`; `chat_transport.py:777`. |
| F7 | A stale marker for a **non-existent** session cannot fire via the SPA: `h_chat_session_show` (`qubit_compat.py:5567`) 404s for a missing session, and the SPA only calls it after `listChatSessions` confirms existence (`ChatPage.tsx:499-533`). | — | No (safe) | `qubit_compat.py:5567`; `ChatPage.tsx:499`. |

## Threat-model items that did NOT reproduce (verified safe)

- **Attempts persistence.** Probe `probe_marker.py`: `attempts=2` round-trips correctly; default 0; overflow truncates at 64000; empty prompt is skipped.
- **Nested-note wrapping.** `_auto_continue_note` embeds the ORIGINAL prompt; `_record_turn_marker` stores `session.pop("_auto_continue_prompt") or text` — the original (`prompt_turn.py:180`). Probe F: `['TASK X', 'TASK X']` across resumes, no accumulation. Probe D: attempt increments to 1, original stored exactly once.
- **Cross-home isolation.** Probe G: a marker under home A is not read for a session whose `profile_home` is B. Probe H: a marker under the session's own `profile_home` fires even when `server._qubit_home` differs (`_session_home` line 36 — `profile_home` wins).
- **Double execution across consumers.** Today only one consumer is live per surface, and the guards hold: `resumedRef` Set (`ChatPage.tsx:469`) once-per-session-per-page-load, the `_auto_continue_scheduled` latch (`session_auto_continue.py:77`), and `_stopped()` in the SSE worker.
- **Stale marker resurrection.** Both consumers clear stale markers on read (`session_auto_continue.py:72-74`; `chat_transport.py:777`).

## Theoretical only (do not treat as live bugs)

- **T1** — Probe E showed that with `running=True` a concurrent second resume could re-schedule, because the kickoff's bail branches (`session_auto_continue.py:89/93/105`) reset `_auto_continue_scheduled`. Only reachable if the kickoff bails immediately; not observed live.
- **T2** — F3's race needs two processes writing the same marker file inside the same ~50ms window (multiplex, or two backends on one home). Reproduced under a forced 50ms `_store` delay (see F3); **not** observed on the operator's single-backend setup. F3's `Live-reachable` column reflects "reachable in principle", not an observed live hit.

## Live evidence

- `~/.local/state/qubit-voicebridge/runtime/desktop/interrupted_turns.json` held
  `{"20261003_093120_d09dc4": {"attempts": 0, "prompt": "continue delpy 5 agents", ...}}` at 11:06
  (77s old, fresh); the session exists in `state.db` (`source=tui`, `ended_at IS NULL`);
  the file was consumed and cleared by the running backend by 11:15 → the feature is live-reachable.
- Existing suite `qubit_runtime/tests/tui_gateway/test_auto_continue.py`: **20 passed** (re-run this pass, 22.85s).

## Probe commands

```
# marker round-trip / attempts persistence
.venv/bin/python scratch/probe_marker.py
# cross-process lost update  (re-run this pass; PROVES F3)
.venv/bin/python scratch/probe_race5.py
# TUI gate probes A-H
cd qubit_runtime && ../.venv/bin/python -m pytest scratch/test_redteam_autocontinue.py -q
# existing gate suite  (re-run this pass: 20 passed)
cd qubit_runtime && ../.venv/bin/python -m pytest tests/tui_gateway/test_auto_continue.py -q
```

Use `.venv/bin/python` (system `python3` lacks `ruamel`).

## Corrections made this pass (vs the parent's first draft)

- **F2 line number was wrong**: draft said the SSE marker was at `:4125`; the sole call is
  **`:4226`** (`:4125` is the tail of the remote-escalation `return`). All F1/F4/F5/F6 line
  numbers checked and correct.
- **F3 upgraded from "quoted" to "re-run"**: probe files still present; the run stored only one
  of `{A, B}`. Draft's quoted `A False | B True` is the mirror outcome — same loss.
- **F3 severity/live-reachability reconciled with T2** (in-principle reachable, not observed live).
- **TUI marker site corrected**: `_record_turn_marker` is defined at `prompt_turn.py:171` and
  called once (`:1039`); `:1096` is the marker-retire check, not a write. (Draft mentioned
  "~877/~1094".)

## Open (not closed by this task)

- F2 (remote/branch turns leave no marker) is the highest-severity item and is a real
  fails-to-resume gap. It is not fixed here.
- F1 (SSE never persists an attempt count) means the SSE crash-loop breaker is inert.
