# Packet 8T-F foundation matrix (human companion; historical narrow scope)

This matrix is a narrow foundation record, not the current full-parity gate.
The authoritative current status is `docs/QUBIT_GAP_LEDGER.md` and its
machine-readable sections. The owner-approved runtime directive is
`docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`. The old all-green summary
below must not be used to claim that Hermes/OpenClaw/Qubit parity is complete.

Machine-readable: `docs/MATRIX_8T_F.json` (authoritative only for this narrow
foundation axis; every id below is parity-tested). Unsafe upstream variants
stay refused with typed errors and absence tests. (The older
`FEATURE_MATRIX_8U.json` rates upstream reusability for future 8U tickets
— a different axis, not foundation integration.)

## Legacy foundation runtime record — superseded by the owner amendment

The former record below described a Qubit-native replacement that did not
execute the pinned Hermes source. That architecture is superseded by the
owner-approved requirement to vendor and execute the complete pinned Hermes
source from the Qubit-owned tree. It is retained only as historical evidence
of the earlier foundation work; it is not the current runtime claim:

1. `voicebridge/textutil.py` — Qubit-owned output sanitizing, wired into
   every `voicebridge/agent.py` turn/session reply (differential-tested
   vs the pinned oracle in `tests/test_textutil8u.py`).
2. `voicebridge/sqlstore.py` — Qubit-owned SQLite helpers; the kanban
   store keeps explicit-transaction discipline (`BEGIN IMMEDIATE` +
   rollback, atomic-claim concurrency proven).
3. Qubit-native agent turns/sessions (`voicebridge/agent.py`
   `turn`/`run_session` over the local model with timeout, redaction,
   and receipts); no external assistant process is launched or
   supervised (`tests/test_agent8u.py`). Operator installs untouched.

## Legacy foundation rows (35 — not full parity)

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

## Historical row summary (not the completion gate)

The narrow foundation matrix contains no rows in those states, but the current
gap ledger contains open Hermes, OpenClaw, and Qubit rows.

## Historical rejected-row summary (not the completion gate)

The former rejections were mapped to safe equivalents for this narrow matrix.
That does not close unrelated full-parity rows or prove every CLI, dashboard,
and voice workflow.

## Matrix ids

H-ansi-strip, H-sqlite-txn, H-real-exec, H-stream, H-stt, H-tts, H-wake,
H-browser, H-files, H-terminal, H-acp, H-sessions, H-gateway, H-approvals,
H-estop, H-cloudmem, H-desktop, H-cloudbrowser, H-installer, O-secretref,
O-approvals, O-audit, O-sessions, O-cron, O-frames, O-browser, O-skills,
O-board, O-onboard, O-memory, O-models, O-docker, O-clawhub, Q-voice,
Q-broker.
