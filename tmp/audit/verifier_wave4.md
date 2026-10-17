# Verifier wave 4 — five concurrent agents, path/symbol/import/skip truth

Window: `git log --since='3 hours ago' --name-only --diff-filter=AM` → 443 .py paths touched;
366 still on disk, 77 exist only in git history (reverted). Upstream pin
`43faacfb8de7b29548547a7fbdedad0cc6e7391c` extracted to /tmp/upin for exact-path lookups.

## CHECK 1 — PATH EXISTENCE
- Enumerated all .py touched in the window (443). Split on-disk (366) vs reverted-gone (77).
- Mapped each `qubit_runtime/<p>` → upstream with segment/basename brand rules
  (qubit_cli→hermes_cli, qubit_platform→hermes_platform, qubit_state*→hermes_state*, qubit*→hermes*)
  and asserted existence against the extracted pin.
- 338 on-disk qubit_runtime files: **336 have an upstream path; 2 do not.**
  - `qubit_runtime/plugins/memory/honcho/cli.py` — upstream has NO honcho plugin at all
    (`plugins/memory/` = byterover, retaindb, query_rewrite). Qubit-owned 18-file package. LEGIT divergence.
  - `qubit_runtime/qubit_state_detached_results.py` — no upstream path, no upstream symbol
    (`SessionDetachedResultsMixin` absent everywhere upstream). See check 4.
- The 77 "gone" files are the ALREADY-REVERTED 715693a58 batch (75 fabricated) + 1182f25b3 reverts;
  they are not live. NOT new fabrications.
- **No live fabricated-path file found.**

## CHECK 2 — SYMBOL FIDELITY
- Indexed 73,177 top-level def/class names across the whole pin; compared each on-disk touched
  qubit_runtime file against its mapped upstream file (337 compared), then checked every
  ours-only name against the GLOBAL upstream index with brand normalization.
- 49 ours-only-and-absent-everywhere symbols surfaced; on inspection **all are Qubit-original
  helpers or public API called from elsewhere** (verified call sites), not injected decorations.
  No fabricated issue numbers in live source: distinct `#NNNNNN` refs in added lines = 149;
  only 126351/139002/139257/140183 are absent upstream, and NONE appear in any current source
  file (only git history/revert commits). The prior fabrication revert held.
- **REAL fidelity defect (pin drift):** `qubit_runtime/tui_gateway/contracts/server_requests.py`
  is at upstream ANCESTOR `41a406a7` state: it defines `SetupChooseKind/Option/RequestParams/Result`
  and `server_request("setup_choose")`, while the PIN defines the whole `SetupBeat` family
  (SetupBeat/SetupOption/SetupNameCard/SetupThemeCard/SetupOptionsCard/SetupConnectorsCard/
  SetupPluginsCard/SetupBeatRequestParams/SetupNameAnswer/SetupThemeAnswer/SetupLayoutAnswer/
  SetupConnectorsAnswer/SetupPluginsAnswer/SetupTourAnswer/SetupBuildAnswer/SetupBeatResult) —
  16 pin types MISSING from our file. Our repo still uses the pin API (`tools/setup_beat_tool.py`,
  `gateway/local_desktop_ui.py` reference `setup_beat_callback`/`setup_beat` requests). File is
  stale vs pin and inconsistent with the repo's own setup_beat usage.

## CHECK 3 — IMPORT HEALTH
- Imported all 338 on-disk touched qubit_runtime modules with an ABSOLUTE sys.path (cwd=qubit_runtime,
  PYTHONPATH=.). **337/338 import clean. 1 failure:**
  - `ui-tui/scripts/native_canonical_probe.py` → `ModuleNotFoundError: No module named
    'tests.gateway.fixtures'` (line 24 `from tests.gateway.fixtures.shared_authority_peer import ModelPeer`).
    The file is a faithful port of upstream (same import line), but its dependency
    `tests/gateway/fixtures/` exists upstream and was NEVER ported into qubit_runtime. Probe-only,
    no live importers; real but low severity.

## CHECK 4 — SKIP-REASON TRUTH (and prior-defect confirmation)
- **Known defect 6e33c522d: FIXED.** `plugin_validate_private_imports.py:18` now imports
  `_qubit_top_level` (defining file exports it). Module imports clean.
- **Same-class sweep re-run independently:** all 86 `from <mod> import _hermes*/_qubit*` sites
  across qubit_runtime resolve (all are the correct rebound `_qubit*` names); importing each
  owning module succeeds. 0 unsound private-import. The fix commit's "0 instances" claim CONFIRMED.
- **Prior fix 6f90f0ba2 / 1182f25b3 CONFIRMED:** `retire_spillover` appears NOWHERE in the current
  tree; `qubit_state_mutation_retirement.py:53` matches upstream verbatim
  (`retire_media(conn, json.loads(row['payload_json']), row['request_id'])`) and
  `qubit_state_media.py:10 def retire_media(conn, payload, request_id='')` matches upstream signature.
- **715693a58 CONFIRMED held:** sampled reverted paths (session_hosted_members.py, acp_adapter/
  gateway_media.py, plugins/platforms/discord/gateway_env.py, tools/delegation_redaction.py) all absent.
- **UNSOUND SKIP / mislabeled file — `qubit_state_detached_results.py`:** docstring claims it was
  "Split out of ``qubit_state_messages`` and bound onto SessionDB through ``SessionMessagesMixin``".
  FALSE on both counts: `qubit_state_messages.py:120 class SessionMessagesMixin` does NOT bind
  `SessionDetachedResultsMixin`, and `qubit_state_messages.py:414` still contains its OWN inline
  `append_delegation_delivery` (faithful copy of upstream hermes_state_messages.py:423). The split
  never happened; the file has ZERO importers anywhere (dead duplicate). Its cited `#134889` IS a
  real upstream issue (genuine), so not a fabricated citation — but the file is unwired duplicated
  code with a false provenance claim.
- `honcho` skip as "intentionally divergent": VALID — Qubit owns the whole package upstream lacks.

## COVERAGE / CAVEATS
- Check 1: full (all 443 touched paths classified; 338 on-disk asserted vs pin).
- Check 2: full path-level (337 files compared); symbol-level targeted (global-index sweep of all
  ours-only names + manual triage of 49; did NOT read every body of all 337 files line-by-line).
- Check 3: full (338/338 attempted).
- Check 4: all prior fixes + 4 issue-number fabrications + all 86 private imports + the one suspect file.
