# QUBIT_OPERATOR_QUALIFICATION_WAVE2 — live qualification commands + packaging results

Status: **append-only evidence report (Wave 2, Agent 3 — packaging / clean install /
migration / rollback / provenance).** This file records what was actually run, the
exact commands, and the verdicts. It does not change any requirement, ledger row,
or terminal status. It does not reclassify any `OPERATOR_ONLY` row.

Authority: `docs/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md` (Final gate),
`docs/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md`,
`docs/QUBIT_GAP_LEDGER.md` (OPERATOR_ONLY section),
`docs/OPERATOR_QUALIFICATION.md`.

Conventions:
- Repo root is written `<repo>` (no absolute developer home paths are recorded here).
- Canonical CLI: `.venv/bin/qubit` (entry `qubit = voicebridge.cli:main`).
- Hermetic proof command: `.venv/bin/python -m pytest -q <proving test>`.
- Live evidence must be stored **outside** the repository in a disposable directory;
  never commit state, tokens, or captures.
- No credentials are recorded in this file.

---

## 1. OPERATOR_ONLY row verification (11 rows)

Every row below: the proving test was executed and **passed** on the current
checkout. Rows 4 and 8 carry a flagged reachability concern (see §3); they were
**not** reclassified.

Hermetic result used throughout:

```
$ .venv/bin/python -m pytest -q tests/test_computer_use8tf.py \
    tests/test_desktop_parity8u.py tests/test_openclaw_closure_wave2_messaging.py \
    tests/test_openclaw_closure_wave2_webmedia.py tests/test_openclaw_closure_wave2_runtime.py \
    tests/test_openclaw_closure_wave3.py tests/test_operator_only_evidence.py
73 passed in 56.89s
```

The 17 exact node IDs named in the ledger / runbook were also run directly and all
17 passed (`17 passed in 3.76s`).

### 1. `OC-13` — Real desktop control (Peekaboo/CUA)
- **needs:** hardware (graphical X11/Wayland session + operator-provisioned pinned actuator).
- **proving test:** `tests/test_computer_use8tf.py` — PASS.
- **hermetic command:** `.venv/bin/python -m pytest -q tests/test_computer_use8tf.py`
- **live commands:**
  - `.venv/bin/qubit computer-use status`
  - `.venv/bin/qubit computer-use doctor`
  - `.venv/bin/qubit computer-use install`
- **expected:** `status` → `control == "DISABLED"` + honest `display_present`/`atspi_reachable`;
  `doctor` → four checks with `OK/DEGRADED/MISSING`, driver `MISSING` headless;
  `install` → `state=REJECTED, code=COMPUTER_USE_NO_DRIVER` (`voicebridge/computer_use.py:29`),
  nothing downloaded.
- **evidence:** JSON output of steps 1–3 + checksum/path of the pinned actuator for the live run.
- **verdict:** genuine operator-only (hardware). Code path present and CLI-reachable.

### 2. `OC-14` — Supervised ghost action framework
- **needs:** hardware (graphical host + explicit `GHOST_SHELL` opt-in + real executors).
- **proving test:** `tests/test_desktop_parity8u.py` — PASS.
- **hermetic command:** `.venv/bin/python -m pytest -q tests/test_desktop_parity8u.py`
- **live commands:**
  - `.venv/bin/qubit ghost status`
  - `.venv/bin/qubit ghost check screenshot`
  - `.venv/bin/qubit ghost execute <capability> --confirm`
- **expected:** adapter disabled by default (`voicebridge/ghost.py:71`); without injected
  executors `execute` returns `ADAPTER_DISABLED` (`voicebridge/actions.py:90`); no live launch.
- **evidence:** disabled status, typed `ADAPTER_DISABLED` receipt, grant/audit receipt for a
  live opt-in run.
- **verdict:** genuine operator-only (hardware). CLI-reachable.

### 3. `OC-19` — Broad channel set (~29 plugins)
- **needs:** creds (per-channel bot/app tokens + accounts; iMessage needs a macOS bridge).
- **proving test:** `tests/test_openclaw_closure_wave2_messaging.py` + `tests/test_openclaw_closure_wave3.py` — PASS.
- **hermetic command:**
  `.venv/bin/python -m pytest -q tests/test_openclaw_closure_wave2_messaging.py tests/test_openclaw_closure_wave3.py -k oc19`
- **live commands (corrected — see §3.3):**
  - `.venv/bin/qubit channels doctor`
  - `.venv/bin/qubit channels catalog`
  - `.venv/bin/qubit msg account-add <provider> ... --confirm`
  - `.venv/bin/qubit msg draft <account> --to <target> "<text>" --confirm`
  - `.venv/bin/qubit msg approve <msg_id> --confirm`
  - `.venv/bin/qubit msg send <msg_id> --confirm`
- **expected:** channel `AVAILABLE` with a real provider message id; absent
  credential/transport → typed `MSG_TRANSPORT_UNAVAILABLE` (`voicebridge/messaging.py:175`)
  / `MSG_NO_CREDENTIAL` (`:172`) with the message left queued.
- **evidence:** per-channel catalogue status + delivery receipt + typed refusal + redaction check.
- **verdict:** genuine operator-only (creds). CLI-reachable; the runbook's one-line
  `msg send --account/--to` form is inaccurate (the product uses the outbox flow).

### 4. `OC-48` — Markdown workspace memory / dreaming / external providers
- **needs:** creds (external LanceDB/Wiki/Honcho provider endpoint).
- **proving test:** `tests/test_openclaw_closure_wave2_webmedia.py` + `tests/test_openclaw_closure_wave3.py` — PASS.
- **hermetic command:**
  `.venv/bin/python -m pytest -q tests/test_openclaw_closure_wave2_webmedia.py tests/test_openclaw_closure_wave3.py -k oc48`
- **live commands (Python API only — no CLI/voice/dashboard surface exists):**
  `write_note` / `read_note` / `list_notes` / `dream(confirmed=True)` / `providers()` in
  `voicebridge/memory/workspace.py`.
- **expected:** local note round-trip `OK`; without an external credential the providers
  report `UNAVAILABLE` + `MEMORY_PROVIDER_UNAVAILABLE` (`voicebridge/memory/workspace.py:51`).
- **evidence:** local round-trip JSON, provider status before/after credentialing, typed refusal.
- **verdict:** **FLAGGED (not reclassified).** The proving test is green, but no production
  caller for `write_note/read_note/list_notes/dream/workspace.providers` was found; the
  functions are only re-exported by `voicebridge/memory/__init__.py:15-16`. Under
  execution-directive rule 4 ("a route/alias/refusal/mock without a real caller … is open
  work") this is a candidate code-side gap for the owning agent, not a validated
  operator-only surface. See §3.1.

### 5. `OC-51` — Logbook screen work-journal
- **needs:** hardware (live display grabber + operator-supplied vision model).
- **proving test:** `tests/test_openclaw_closure_wave2_webmedia.py` + `tests/test_openclaw_closure_wave3.py` — PASS.
- **hermetic command:**
  `.venv/bin/python -m pytest -q tests/test_openclaw_closure_wave2_webmedia.py tests/test_openclaw_closure_wave3.py -k oc51`
- **live commands:**
  - `.venv/bin/qubit logbook entries` (read-only reader)
  - `.venv/bin/qubit computer-use screen --confirm` (goes through
    `voicebridge/computer_use.py:555 snapshot` → `capture_to` in
    `voicebridge/evaluation/logbook.py`)
  - Python `capture_series(...)` for the bounded multi-frame loop
- **expected:** without an actuator → `LOGBOOK_CAPTURE_UNAVAILABLE`
  (`voicebridge/evaluation/logbook.py:19`); unconfirmed → `REFUSED`, journals nothing.
- **evidence:** typed-unavailable receipt; entry count/summaries with the live actuator;
  confirmation/refusal receipt; redaction check.
- **verdict:** genuine operator-only (hardware). Reader/one-shot capture are CLI-reachable;
  `capture_series` itself has no caller (minor, noted in §3).

### 6. `OC-56` — Paired node host
- **needs:** hardware (real paired node + screen/audio/camera actuators + transport).
- **proving test:** `tests/test_openclaw_closure_wave2_runtime.py` + `tests/test_openclaw_closure_wave3.py` — PASS.
- **hermetic command:**
  `.venv/bin/python -m pytest -q tests/test_openclaw_closure_wave2_runtime.py tests/test_openclaw_closure_wave3.py -k oc56`
- **live commands (corrected — see §3.3):**
  - `.venv/bin/qubit node register --name studio --endpoint https://<host>:8443 --confirm`
  - `.venv/bin/qubit node capabilities <id>`
  - `.venv/bin/qubit node dispatch <node_id> <capability>`
- **expected:** local reads `READY`; remote actuators `UNAVAILABLE` +
  `NODE_CAPABILITY_UNAVAILABLE` (`voicebridge/node_host.py:58`); missing transport/confirmation →
  `NODE_DISPATCH_REFUSED` (`:59`) / `NODE_HOST_UNAVAILABLE` (`:51`).
- **evidence:** enrollment record, capability rows, refusal receipts, live dispatch result.
- **verdict:** genuine operator-only (hardware). The runbook's `node enroll` and
  `node dispatch … --confirm` spellings do not match the shipped CLI
  (`node register`; `dispatch` takes no `--confirm`).

### 7. `OC-D15` — iMessage private API, media, rich messages
- **needs:** creds/hardware (macOS Messages private-API bridge; not runnable on Linux).
- **proving test:** `tests/test_openclaw_closure_wave3.py` — PASS.
- **hermetic command:** `.venv/bin/python -m pytest -q tests/test_openclaw_closure_wave3.py -k ocd15`
- **live commands (corrected — see §3.3):**
  - `.venv/bin/qubit msg link <account_id>`
  - `.venv/bin/qubit msg draft <account> --to <target> "<text>" --confirm`
  - `.venv/bin/qubit msg approve <msg_id> --confirm`
  - `.venv/bin/qubit msg send <msg_id> --confirm`
- **expected:** bridge reachable → link available + delivery receipt; absent bridge →
  `state=UNAVAILABLE, code=MSG_TRANSPORT_UNAVAILABLE` and the message stays `queued`.
- **evidence:** bridge reachability, delivery receipt, typed-unavailable receipt + queued state.
- **verdict:** genuine operator-only (creds/hardware). The runbook's
  `msg link --provider imessage` flag does not exist; `msg link` takes an `account_id`.

### 8. `OC-D16` — Feishu dynamic agents, advanced configuration
- **needs:** creds (hosted Feishu open-platform app secret + event subscription + app hosting).
- **proving test:** `tests/test_openclaw_closure_wave3.py` — PASS.
- **hermetic command:** `.venv/bin/python -m pytest -q tests/test_openclaw_closure_wave3.py -k ocd16`
- **live commands:** the runbook cites `.venv/bin/qubit msg feishu-agent add …` and
  `.venv/bin/qubit msg send-as-agent … --confirm`. **Those subcommands do not exist** in the
  shipped `qubit msg` parser (subcommands: `account-add, account-list, account-show,
  account-remove, draft, approve, send, retry, link, list, show, inbox, inbox-show,
  summarize, draft-reply`).
- **expected:** absent secret → `MSG_NO_CREDENTIAL` (`voicebridge/messaging.py:172`);
  absent transport → `MSG_TRANSPORT_UNAVAILABLE` (`:175`); off-allowlist chat → refusal.
- **evidence:** agent record, allowlist refusal, live agent-scoped send, typed refusals.
- **verdict:** **FLAGGED (not reclassified).** Proving test is green, but
  `feishu_agent_add` / `feishu_agents` / `feishu_agent_remove` / `feishu_send_as_agent`
  (`voicebridge/messaging.py:1568-1678`) have **no caller** anywhere in
  `voicebridge/`/`frontend/`/`scripts/` (only their definitions and the ledger-rebuild
  metadata reference them), and the runbook's CLI commands are not implemented. This is a
  candidate code-side gap for the owning agent. See §3.2.

### 9. `computer_doctor` — Desktop health (voice)
- **needs:** hardware (graphical session + accessibility bus).
- **proving test:** `tests/test_computer_use8tf.py::test_doctor_checklist_typed` — PASS.
- **hermetic command:**
  `.venv/bin/python -m pytest -q tests/test_computer_use8tf.py::test_doctor_checklist_typed`
- **live commands:** voice "desktop health" **or** `.venv/bin/qubit computer-use doctor`.
- **expected:** four typed checks (`OK/DEGRADED/MISSING`); headless → display `MISSING`,
  accessibility `DEGRADED`, driver `MISSING`, overall `DEGRADED`
  (`voicebridge/computer_use.py:102-126`). No process spawned, no state written.
- **evidence:** typed checklist JSON + redaction check + no-state-change proof.
- **verdict:** genuine operator-only (hardware). Voice entry
  `_E("computer_doctor", …)` at `voicebridge/voice_commands.py:5975`; CLI-reachable.

### 10. `computer_install_refusal` — Desktop driver install (voice)
- **needs:** hardware (a host where a real driver could otherwise be installed).
- **proving test:** `tests/test_computer_use8tf.py::test_install_refused` — PASS.
- **hermetic command:**
  `.venv/bin/python -m pytest -q tests/test_computer_use8tf.py::test_install_refused`
- **live commands:** voice "install desktop driver" **or** `.venv/bin/qubit computer-use install`.
- **expected:** `state=REJECTED, code=COMPUTER_USE_NO_DRIVER`
  (`voicebridge/computer_use.py:29`, `refuse_install` at `:144`); no binary downloaded.
- **evidence:** refusal JSON + absence of any new binary/network fetch.
- **verdict:** genuine operator-only (hardware). Voice entry at `voicebridge/voice_commands.py:5985`.

### 11. `computer_permissions` — Desktop permissions (voice)
- **needs:** hardware (a real host to demonstrate control stays disabled).
- **proving test:** `tests/test_computer_use8tf.py::test_permissions_record` — PASS.
- **hermetic command:**
  `.venv/bin/python -m pytest -q tests/test_computer_use8tf.py::test_permissions_record`
- **live commands:** voice "desktop permissions" **or** `.venv/bin/qubit computer-use permissions`.
- **expected:** `state=OK, control="DISABLED", owner="packet-8V"`
  (`voicebridge/computer_use.py:131-141`); rule list includes the pinned-source and
  e-stop-before-control rules. No control path active.
- **evidence:** policy-record JSON + redaction check.
- **verdict:** genuine operator-only (hardware). Voice entry at `voicebridge/voice_commands.py:5980`.

---

## 2. Packaging / lifecycle / install gates (exact results)

All commands run from `<repo>`. No operator `$HOME`, live service, unit, or browser/audio
state was touched; throwaway roots live under `/tmp`.

| # | command | rc | result |
| --- | --- | --- | --- |
| 1 | `bash scripts/migration_rehearsal.sh` | **0** | PASS: migration → upgrade → rollback → uninstall; 7 synthetic files preserved byte-for-byte at every stage; credentials excluded from backup; uninstall requires confirmation |
| 2 | `bash scripts/clean_install_check.sh --require-network` | **0** | PASS: wheel built + installed with deps into throwaway venv; `qubit`/`voicebridge`/`qubit-dev`/`qubit-hermes --help` all ok; dashboard served from installed package data (plugins=11); Hermes runtime imports; VAD `webrtc-fallback` |
| 3 | `bash scripts/verify_installed_dashboard.sh` | **0** | PASS: isolated `--no-deps` install; `root->/chat`, plugins=11, ops=523; installed runtime pin `2237be35…` verified |
| 4 | `uv build --out-dir <tmp>/dist <isolated tracked clone + dashboard>` | **0** | wheel `qubit_voicebridge-1.0.0-py3-none-any.whl` (27.9 MB) + sdist `qubit_voicebridge-1.0.0.tar.gz` (29.8 MB) |
| 5 | `release_qualify.inspect_artifacts(<tmp>/dist)` | **wheel OK / sdist FAIL** | sdist blocked by two defects outside this agent's files — see §3.4 and §4 |
| 6 | opt-in archive scans (`QBPV2_ARTIFACT_DIR`/`QBPV_ARTIFACT_DIR`) | **0** | 19 passed (fork runtime, dashboard, plugins, licenses, metadata, no `node_modules`, no dev home path) |
| 7 | `bash scripts/install.sh --launcher-only` | **0** | launcher rendered into throwaway `XDG_BIN_HOME`, points at the authoritative `<repo>/.venv/bin/qubit`, executes under a clean `PATH` |
| 8 | `bash scripts/install_systemd_unit.sh <repo>` (throwaway `SYSTEMD_UNIT_DIR`) | **0/0/0** | atomic install; idempotent re-run untouched; divergent unit preserved + timestamped backup + `template-new` |
| 9 | `python scripts/rollback_drill.py --json` (throwaway HOME/XDG) | **0** | `all_ok=True`, `user_data_preserved=True`, 6 feature flags flipped and restored |

Owned pytest files (primary run):

```
$ .venv/bin/python -m pytest -q tests/test_qbpv2_pkg_artifacts.py tests/test_qbpv2_pkg_launcher.py \
    tests/test_qbpv_pkg5_packaging.py tests/test_qbpv_pkg5_security.py
31 passed, 3 skipped
$ .venv/bin/python -m pytest -q tests/test_qbpv2_pkg_lifecycle.py
3 passed
$ .venv/bin/python -m pytest -q tests/test_lifecycle_wave4.py
16 passed
$ .venv/bin/python -m pytest -q tests/test_clean_clone_wave5.py
9 passed
```

Supplementary package/migration tests discovered by the required grep:

```
$ .venv/bin/python -m pytest -q tests/test_hermes_package8u.py \
    tests/test_hermes_package_runtime8u.py tests/test_provider_migration.py
28 passed
```

`tests/test_package_identity8u.py` and `tests/test_deployment.py` do **not** exist in
this checkout (the closest analogues are `tests/test_hermes_package8u.py` /
`tests/test_hermes_package_runtime8u.py` and `scripts/check_deployment.py`).

### Environmental / operator blocks (reported separately, not counted as passes)
- Full `./scripts/install.sh` first run was **not** executed end-to-end: it would download
  ~1 GB of ASR/TTS models and pull Ollama models (`huihui_ai/phi4-mini-abliterated`,
  `llama3.2-abliterate:3b`, `nomic-embed-text`, `qubit-brain4`). Exact operator command:
  `./scripts/install.sh` (append `--with-systemd` for the user unit). Prerequisites: network,
  `uv`, `ollama`. The launcher, dashboard-degradation gate, syntax, and unit install were
  proven hermetically (rows 7–8, and `test_clean_clone_wave5`).
- A live `systemctl --user enable/start` was not run (would touch the operator's real unit);
  the atomic installer path was proven with a throwaway `SYSTEMD_UNIT_DIR`.
- `clean_install_check.sh` without `--require-network` returns DEGRADED (4) when dependency
  install cannot reach the index; with `--require-network` that becomes FAIL (2). This run
  had network (`pypi:200`, `huggingface:200`) and PASSed.

---

## 3. Findings

### 3.1 OC-48 workspace-memory functions have no production caller (FLAG)
`write_note` (`voicebridge/memory/workspace.py:208`), `read_note` (`:245`),
`list_notes` (`:266`), `forget_note` (`:281`), `dream` (`:313`) and `providers` (`:390`)
are exercised only by their hermetic tests; the only other references are the re-exports in
`voicebridge/memory/__init__.py:15-16`. No CLI (`qubit memory …` uses `MemoryStore`/
`SemanticMemory`), voice, or dashboard caller exists. Candidate open work under
execution-directive rule 4. Not reclassified.

### 3.2 OC-D16 dynamic-agent routing has no caller and its runbook CLI is not implemented (FLAG)
`feishu_agent_add` (`voicebridge/messaging.py:1568`), `feishu_agents` (`:1606`),
`feishu_agent_remove` (`:1616`) and `feishu_send_as_agent` (`:1632`) have no incoming
caller in `voicebridge/`, `frontend/`, or `scripts/`. The runbook's
`qubit msg feishu-agent add` / `qubit msg send-as-agent` subcommands are absent from the
`qubit msg` parser. Candidate open work for the owning agent. Not reclassified.

### 3.3 Runbook command inaccuracies (documentation, frozen file — not edited)
- OC-19 / OC-D15: `qubit msg send --account <acct> --to <target> "<text>" --confirm` is not the
  shipped form; `msg send` takes a single `msg_id` (use the `msg draft` → `msg approve` →
  `msg send <msg_id>` outbox flow). `msg link` takes an `account_id`, not `--provider`.
- OC-56: runbook uses `node enroll`; the shipped verb is `node register`. `node dispatch`
  takes `<node_id> <capability>` and has no `--confirm` flag.
These are recorded here because the frozen runbook cannot be edited by an agent.

### 3.4 Release artifact gate — sdist FAIL from two defects in `voicebridge/release.py`
The built sdist is blocked by `release_qualify.inspect_artifacts` (wheel OK, sdist FAIL) for
two reasons, both reproducible on the committed tree (the two offending files are tracked and
unmodified):

1. **Stale sdist-root prefix breaks the eval-corpus fixture exemption.**
   `voicebridge/release.py:623` strips the sdist source-root prefix only when it starts with
   `qubit_voice_agent-`, but `project.name` is now `qubit-voicebridge`, so real sdist members
   look like `qubit_voicebridge-1.0.0/…`. `artifact_member_class`
   (`voicebridge/release.py:615`) therefore classifies `voicebridge/evaluation/eval_set.py`
   as `noncore` instead of `fixture`, and the synthetic `sk-`-shaped example at
   `voicebridge/evaluation/eval_set.py:58` is reported as `secret shapes in members`
   (`openai_key`). The stale prefix is asserted by `tests/test_packet8k.py:280-284`, which
   uses the old name and so does not catch this.
2. **Over-broad forbidden-member match.** `ARTIFACT_FORBIDDEN` contains `"DESIGN.md"`
   (`voicebridge/release.py:582`) and the matcher uses `low.endswith(pl)`
   (`voicebridge/release.py:609`), so the tracked skill doc
   `skills/bencium-controlled-ux-designer/RESPONSIVE-DESIGN.md` matches and is reported as a
   `forbidden member`.

Both fixes live in `voicebridge/release.py` (not an Agent-3-owned file), so they are reported,
not edited. Net effect: `release_qualify` cannot return a clean sdist result until the owning
agent updates the prefix and narrows the `DESIGN.md` match (e.g. exact root-level path).

---

## 4. Scope, rollback, and provenance

- **Files inspected:** the five owned scripts, the seven owned tests, the 11-row ledger section,
  `docs/OPERATOR_QUALIFICATION.md`, the proving tests and their production modules
  (`computer_use.py`, `actions.py`, `ghost.py`, `messaging.py`, `memory/workspace.py`,
  `evaluation/logbook.py`, `node_host.py`, `voicebridge/cli.py`, `voicebridge/release.py`).
- **Files changed by this agent:** only this new evidence file
  (`docs/QUBIT_OPERATOR_QUALIFICATION_WAVE2.md`). No owned script or test was modified;
  sha256 before/after are identical (recorded in the handoff).
- **Rollback:** delete this file. Qualification and packaging runs wrote only under
  disposable `/tmp` trees which are removed by the scripts' own traps.
- **Provenance:** no upstream source was copied or adapted by this report; the vendored
  runtime pin `2237be355906fbe6065ce1815711eee52b2d646e` and its notices are unchanged.
- **Limitations:** hardware/credential/live-service qualifications (OC-13/14/19/48/51/56,
  OC-D15/16, computer_*) remain unanswered until an operator supplies the live prerequisite;
  this report proves the hermetic code path and the exact live procedure only.
