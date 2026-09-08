# Packet 8T-F close-out matrix (human companion)

Machine-readable: `docs/MATRIX_8T_F.json` (authoritative; every id below is
parity-tested). Gate rule enforced in tests: every row with verdict
REUSE/ADAPT has status IMPLEMENTED with proof; anything else is verdict
DEFER/REJECTED/MISSING. (The older `FEATURE_MATRIX_8U.json` rates upstream
reusability for future 8U tickets — a different axis, not foundation
integration.)

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

## IMPLEMENTED rows (16)

H-ansi-strip, H-sqlite-txn, H-real-exec, H-approvals (Qubit confirmations),
H-estop (latch+cancel; cross-wire to `hermes pause` deferred),
O-secretref (wired), O-approvals (expiry/settle-once/no-renewal),
O-audit (+ new receipt `runtime` provenance), O-sessions (cancellation
paths), O-cron (monitor validation), O-skills (7E manifests),
O-onboard (8T setup), O-memory (5B/8P stores), O-models (8Q gateway),
Q-voice + Q-broker (Qubit-owned, suites cited).

## DEFERRED rows (14)

H-stream/streaming (Qubit owns; httpx dep), H-stt/H-tts (Qubit contracts
authoritative), H-wake (second listener + license), H-browser (8U),
H-files (8W), H-terminal incl. approval_detection (no shell-string path
exists by construction — vendoring it unwired would imitate integration),
H-acp (8X), H-sessions store (would duplicate memory), H-gateway (8D/8I
own), O-frames (no WS transport; 8U work), O-browser (8U), O-board
(dashboard scope), O-docker (8K owns).

## REJECTED rows (5)

H-cloudmem, H-desktop, H-cloudbrowser, H-installer, O-clawhub — reasons in
JSON; re-entry needs new license/security review + operator authorization.

## Matrix ids

H-ansi-strip, H-sqlite-txn, H-real-exec, H-stream, H-stt, H-tts, H-wake,
H-browser, H-files, H-terminal, H-acp, H-sessions, H-gateway, H-approvals,
H-estop, H-cloudmem, H-desktop, H-cloudbrowser, H-installer, O-secretref,
O-approvals, O-audit, O-sessions, O-cron, O-frames, O-browser, O-skills,
O-board, O-onboard, O-memory, O-models, O-docker, O-clawhub, Q-voice,
Q-broker.
