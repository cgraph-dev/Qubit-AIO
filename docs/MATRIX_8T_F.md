# Packet 8T-F close-out matrix (human companion)

Machine-readable: `docs/MATRIX_8T_F.json` (authoritative; every id below is
parity-tested). Gate rule enforced in tests: every row has status
IMPLEMENTED with proof (vendored code, wired behavior, or proven
Qubit-owned counterpart); unsafe upstream variants stay refused with
typed errors and absence tests. No row is BLOCKED, REJECTED, or DEFERRED:
postponement without evidence is not accepted. (The older
`FEATURE_MATRIX_8U.json` rates upstream reusability for future 8U tickets
— a different axis, not foundation integration.)

## What actually executes pinned Hermes code

## What actually executes pinned Hermes code

1. `voicebridge/upstream/hermes_ansi_strip.py` — verbatim
   `tools/ansi_strip.py`; runs on every `HermesBackend.run_task` return and
   every `RealHermesRuntime.turn` reply (differential-tested vs the clone).
2. `voicebridge/upstream/hermes_sqlite_util.py` — verbatim
   `hermes_cli/sqlite_util.py`; `write_txn` was tried in the memory-store
   migration and reverted with root cause (executescript implicit-commit);
   differential-tested and reserved for non-executescript writes.
3. Isolated Hermes v0.21.1 install (hash-verified copy of the pinned clone)
   supervised by `RealHermesRuntime` for agent turns against local Ollama;
   version/argv/session/timeout/cancel/redaction proven live
   (`tests/test_real_hermes.py`). Operator installs untouched.

## IMPLEMENTED rows (35 — no code-side BLOCKED/REJECTED rows remain)

H-ansi-strip, H-real-exec, H-approvals (Qubit confirmations),
H-estop (latch+cancel + pause/resume/bind_estop cross-wire, proven live),
O-secretref (wired), O-approvals (expiry/settle-once/no-renewal),
O-audit (+ new receipt `runtime` provenance), O-sessions (cancellation
paths), O-cron (monitor validation), O-skills (7E manifests),
O-onboard (8T setup), O-memory (5B/8P stores), O-models (8Q gateway),
Q-voice + Q-broker (Qubit-owned, suites cited),
H-sqlite-txn (Qubit-owned explicit-transaction discipline in kanban;
atomic-claim concurrency proven),
H-stream (Qubit-owned local_llm streaming with cancellation),
H-stt/H-tts (Qubit ASR/TTS contracts), H-wake (single-mic-path spotter,
never auto-downloads), H-browser + O-browser (loopback CDP, read-only
unless confirmed), H-files (scoped project-roots inspection),
H-terminal (no shell-strings by construction; TUI live board),
H-acp (contracts + pinned LSP; serve/bypass flags refused),
H-sessions (single memory authority), H-gateway + O-frames (records +
WS frame guards), O-board (widget canvas: layout, sandbox, roles,
snapshots), O-docker (pinned confined runtimes, no daemon/pulls),
H-cloudmem (local-only memory + device handoff; cloud refused),
H-desktop (supervised Ghost control; raw actuators absent),
H-cloudbrowser (loopback-only; cloud refused),
H-installer (Qubit packaging), O-clawhub (reviewed install only).

The unsafe upstream variants stay out by policy and by test
(tests/test_rejected_matrix8tf.py proves no cloud-memory network path,
no desktop actuators, no cloud browser, no unreviewed auto-install):
full parity means the safe Qubit equivalent is fully implemented, not
that the unsafe behavior was copied.

## BLOCKED rows (0)

None. Former blocks resolved to the Qubit-native implementations above;
the JSON keeps no BLOCKED rows.

## REJECTED rows (0 code-side)

None. Former rejections resolved to safe equivalents above. The unsafe
variants (cloud exfiltration, raw actuators, cloud browser, unreviewed
auto-install) remain refused with typed errors and absence tests.

## Matrix ids

H-ansi-strip, H-sqlite-txn, H-real-exec, H-stream, H-stt, H-tts, H-wake,
H-browser, H-files, H-terminal, H-acp, H-sessions, H-gateway, H-approvals,
H-estop, H-cloudmem, H-desktop, H-cloudbrowser, H-installer, O-secretref,
O-approvals, O-audit, O-sessions, O-cron, O-frames, O-browser, O-skills,
O-board, O-onboard, O-memory, O-models, O-docker, O-clawhub, Q-voice,
Q-broker.
