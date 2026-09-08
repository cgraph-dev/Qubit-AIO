# Packet 8T-F — Qubit foundation migration (implementation record)

Status: implemented (code-side). Packet 8U is NOT started.

## 1. Identity applied in this packet

- Public identity: **Qubit VoiceBridge**; product/runtime identity: **Qubit**.
- Canonical install identity: **qubit-voicebridge**; import package:
  **voicebridge**; `openclaw-voice-bridge`: legacy compat only.
- Single source: `voicebridge/foundation.py` (identity constants + pins).
- Repairs: `scripts/install.sh` header (was "Install the OpenClaw Voice
  Bridge"); `systemd/qubit-voice-agent.service` marked LEGACY REFERENCE /
  DO NOT INSTALL; `scripts/check_deployment.py` default template is now the
  canonical unit; `README.md`, `docs/AUDIO_SETUP.md`, `docs/WEBSITES_8E.md`,
  `docs/PRODUCT_UX_8L.md`, `docs/VOICE_BRIDGE_TROUBLESHOOTING.md`,
  `docs/OPERATIONS_8KP.md` primary paths corrected to canonical.
- Untouched as ambiguous/overlapping: `voicebridge/cli.py` description,
  `voicebridge/webui.py`, plan docs with concurrent external edits, and
  `config/*.yaml` live examples (operator-owned).

## 2. Upstream acquisition (8T-F, fresh clones)

| | Hermes | OpenClaw |
|---|---|---|
| URL | https://github.com/NousResearch/hermes-agent | https://github.com/openclaw/openclaw |
| Tag | v2026.9.7 | v2026.9.2 |
| Commit | 2237be355906fbe6065ce1815711eee52b2d646e | 3928bad9badfcb6c7d140530435e806fb8092190 |
| Clone | /tmp/upstream-8tfr2/hermes-agent | /tmp/upstream-8tfr2/openclaw |
| License | MIT © 2025 Nous Research | MIT © 2026 OpenClaw Foundation |
| Notices/locks | uv.lock | pnpm-lock.yaml, THIRD_PARTY_NOTICES.md |

Acquired 2026-09-08 (UTC); tags re-verified newest-stable at acquisition.
Both clones read-only inputs, `git status` clean at implementation end;
operator's live `~/.hermes` / `~/.openclaw` installs never touched. No
production code copied from either clone (contract shapes only). Model/plugin/
driver caveats: Porcupine key terms, cloud STT/TTS ToS, unpinned cua-driver,
Playwright Chromium, Electron, cloud-memory ToS, extension models/voices —
each needs its own review before any use (see UPSTREAM_REUSE R29/R30).

## 2b. Real Hermes execution (close-out)

No vendoring: no Hermes module is small and valuable enough to justify a
copy (leaf candidates are trivial contextvars or httpx-bound), so Qubit
executes the pinned implementation itself instead of imitating it:

- Isolated install: `/tmp/hermes-isolated/venv` (editable install of a
  hash-verified copy of the pinned clone; `src/cli.py` sha256 identical to
  the clone), `HERMES_HOME=/tmp/hermes-isolated/home` with a local-only
  config (`provider: ollama`, `huihui_ai/llama3.2-abliterate:3b`, 128K
  override). Recreate with: `uv venv /tmp/hermes-isolated/venv`,
  `cp -a <clone> /tmp/hermes-isolated/src`,
  `uv pip install --python .../venv/bin/python -e .../src`.
- Proven: `hermes --version` → `Hermes Agent v0.21.1 (2026.9.7)` from the
  isolated path; `chat -q/-Q/--continue/--create-if-missing/--provider/-m`
  flags read from the REAL `chat --help`; stdout carries only the reply,
  session metadata on stderr, missing sessions rc=1 typed.
- Qubit supervisor: `voicebridge/runtime/real_hermes.py`
  (`RealHermesRuntime`): explicit binary/home (DISABLED without them),
  e-stop refusal before spawn, scrubbed allowlist env (no *KEY*/*TOKEN*/
  *SECRET* passthrough, XDG redirected), timeout kill/reap, redacted
  replies/errors/receipts with binary+pin provenance.
- Live proof: real turns returned exact markers (`HERMES_LOCAL_OK`,
  `QUBIT_REAL_OK`, `BRANCH_CHECK`) via Qubit's supervisor; helper model
  stopped after each run (`ollama stop`) for VRAM hygiene. Note: loading
  the helper evicts the resident `qubit-brain4` from VRAM; it reloads on
  the next Qubit request (operator-visible as one slow first turn).
- Qubit-backend argv conformance is asserted against the real `--help`
  output; OpenClaw `CliBackend` argv asserted against pinned
  `docs/cli/agent.md` (`--agent/--session-key/--message-file/--json/
  --timeout`).
- THIRD_PARTY_NOTICES.md unchanged: nothing vendored or distributed; the
  isolated install is operator-ephemeral tooling, provenance recorded here
  and in UPSTREAM_REUSE R29/R30.

## 3. Feature matrix (8T-F delta on FEATURE_MATRIX_8U.json)

- ADAPT implemented (OpenClaw): server-side `SecretRef`
  (`voicebridge/providers/credentials.py`) wired into the frontier gateway
  (`providers/gateway.py` accepts `SecretRef`/mapping `key_ref`, normalized
  to the same refusal/redaction contract — string behavior unchanged).
- ADAPT implemented (Hermes): real adapted runtime (`voicebridge/runtime/`:
  `HermesRuntime` supervisor + stdio `worker` speaking the pinned CLI
  turn/session contract; minimal scrubbed environment; timeout/cancel/reap;
  redacted receipts; e-stop refusal before spawn). Qubit starts it,
  VoiceBridge text goes in, replies return to the Qubit TTS contract.
- MISSING implemented: singularity audit (`voicebridge/foundation.py`);
  `collect_runtime_state` + `singularity_report` + `assert_foundation` +
  `disabled_competing_paths`); competing Hermes/OpenClaw voice paths are
  refused by policy, not executed.
- Primary runtime: Hermes CLI contract (`hermes chat -q/-Q/--continue`),
  already adapted in `voicebridge/hermes/backend.py`, behind Qubit broker,
  permissions, confirmation, audit, and e-stop. One agent/session authority
  (Qubit), one mic listener, one voice session, one TTS path, one broker,
  one audit path, one e-stop — audited hermetically (tests), live
  single-listener proof remains operator-only.
- REJECTED rows stand (cloud browser, unpinned drivers, Electron shell,
  cloud memory/Hub, ClawHub auto-install, upstream dashboard/installer).

## 4. Voice integration (code-side proof)

- Existing Qubit VoiceBridge is the only voice/audio/session path: mic
  (`audio/mic.py`), ASR, Piper/TTS (`orchestrator._make_tts`, Piper
  default), Right Ctrl (`hotkeys.py`), dashboard voice (`webui
  submit_talk/handle_talk`), cancellation/barge-in, temp-audio cleanup,
  typed degraded states — all unchanged and re-proven by the existing
  audio/orchestrator suites in the full run.
- `singularity_report` returns OK only when every observable kind counts
  exactly one; MULTIPLE fails closed via `assert_foundation`.

## 5. Dashboard

No dashboard code changed (webui.py overlaps unrelated dirty work):
existing authenticated routes cover chat/stream, voice, approvals,
permissions, actions, power, setup/diagnostics, reports. Missing surfaces
(browser/desktop context, files/projects, IDE diffs, sessions/tasks/
schedules, provider status, adapter health, mobile/a11y) remain listed 8U
follow-ups in FEATURE_MATRIX_8U.md §3.

## 6. Migration rehearsal (hermetic, isolated HOME)

- `product migrate` dry-run: FRESH with no legacy trees, creates nothing.
- Apply with a synthetic legacy tree: OK, canonical paths + compat aliases,
  validated backup; rerun idempotent (FRESH); `migrate-rollback --apply`
  restores the legacy tree byte-identical (`notes.txt` "keepme" intact).
- User-data rules held: no cookies/profiles/keys/tokens/credentials/audio/
  private text copied (synthetic fixture only); secrets excluded from backup
  by existing `_CREDENTIAL_GLOBS` (8S, re-proven by its suite).

## 7. 8U task capsule pointer

Use `docs/FEATURE_MATRIX_8U.json` verdicts: REUSE rows are 8U transport/
cancel/schedule/approval/audit/secret/e-stop patterns; each ADAPT row needs
a broker-wrapped ticket with fixtures + negative/security tests; MISSING
rows are 8U–8Z implementation; REJECTED rows need new review + explicit
authorization to re-enter.
