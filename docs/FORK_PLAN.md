# Qubit FORK_PLAN — sever the upstream link by de-branding first, decoupling last

> **Owner scope ruling applied (2026-10-05, mid-task).** Hermes stays **installed
> and fully intact** and remains the working behavioral reference while Qubit is
> brought to parity. **Severance is the FINAL, DEFERRED phase** — it runs only
> after full parity is verified E2E. Every phase below leaves the pin
> (`/home/trick/.cache/qubit-pins/hermes-agent`), `~/.hermes` (8.5 GB live
> install) and `~/.openclaw` untouched and every running agent working.
>
> This document is a **plan only**. No rename, deletion, or edit outside this file
> has been performed.

Authority: `docs/BRAND.md` (zero legacy brand; legal attribution the required
exception), `docs/STATUS.md`, `docs/PARITY.md`, `docs/MIGRATION.md`,
`docs/AGENTS.md`, and the owner amendment `docs/QUBIT_OWNER_AMENDMENT_2026-09-25_PIN_AND_1TO1.md`.

---

## 0. Hard constraints (do NOT violate in any phase)

| Constraint | Path | Why |
|---|---|---|
| Never delete / move / modify | `/home/trick/.cache/qubit-pins/hermes-agent` | read-only source of record for the 1:1 rebase + rebrand-determinism gate |
| Never touch | `/home/trick/.hermes` (8.5 GB) | live working reference; managed `hermes-dashboard.service` on 127.0.0.1:9119; other agents depend on it |
| Never touch | `~/.openclaw` | live upstream-family install; other agents depend on it |
| Never edit | `docs/differential/INTERFACE.md`, `docs/archive/**` | frozen contracts / provenance records (`AGENTS.md`) |
| Never remove | legal attribution in `LICENSE`, `THIRD_PARTY_NOTICES.md`, upstream copyright headers | license compliance (`BRAND.md` the one exception) |

Severance of the pin is **N/A until Phase 6**. No earlier phase reads, writes,
renames or requires the pin to be gone.

---

## 1. Inventory — category × occurrence count × disposition

Ground truth (2026-10-05, measured in-repo; scope excludes `.git`, `node_modules`,
`dist`, `graft`, `.venv`, `qubit-dist`, `output`, `docs/archive`):

```bash
# Token line/files counts (case-insensitive unless noted; Live = shipped source)
cd /home/trick/Projects/Repos/qubit-voicebridge
E="--exclude-dir=node_modules --exclude-dir=.git --exclude-dir=dist \
   --exclude-dir=graft --exclude-dir=.venv --exclude-dir=__pycache__ \
   --exclude-dir=qubit-dist --exclude-dir=output --exclude-dir=archive"

# → measured output:
# Hermes      lines=14225 files=2796
# hermes      lines=82300 files=6545
# OpenClaw    lines=4010  files=310
# openclaw    lines=7523  files=609
# NousResearch lines=1069 files=447
# NousPortal   lines=114  files=43
# unique HERMES_* env-var names: 927
# unique QUBIT_*  env-var names: 184
```

Per-area line counts (`hermes|openclaw`, case-insensitive):

| Area | Lines | Files | Disposition |
|---|---:|---:|---|
| `qubit_runtime/` | 55,070 | 6,796 | **RENAME** (deep core) — wire/file-compat subset **REQUIRES-COMPAT-SHIM** |
| `docs/` (non-archive) | 109,069 | 232 | **RENAME** internal prose; frozen `docs/differential/INTERFACE.md` and `docs/archive/**` = LEGAL-ATTRIBUTION-KEEP / frozen |
| `tests/` | 4,278 | 481 | **RENAME** (branding tests updated in same phase); fixture wire names **REQUIRES-COMPAT-SHIM** |
| `scripts/` | 1,314 | 70 | **RENAME** (generator/manifest names); `qubit_rebrand.py` map itself **KEEP-AS-INTERNAL** then **RENAME** in 6 |
| `voicebridge/` | 429 | 78 | **RENAME** product surfaces; `qubit_legacy.py`/`env_store.py` legacy reads **REQUIRES-COMPAT-SHIM** |
| `frontend/` | 101 | 32 | **RENAME** source; `frontend/qubit-dist` (built bundle, 12 files) regenerated |
| `skills/` | 15 | 5 | **RENAME** |
| `benchmarks/` | 2 | 2 | **RENAME** |

Per-category table:

| Category (glob/dir) | Occurrence count | Disposition |
|---|---:|---|
| `qubit_runtime/**/hermes_*.py` module basenames | 927 env + module tokens (map in `scripts/qubit_rebrand.py:42-100`) | **RENAME** — mostly already done; residual `hermes_*` identifiers in `qubit_runtime/tools`, `skills/*/scripts`, `qubit_cli/*` are the debt (`docs/QUBIT_VENDOR_DELTA_2026-09-22.md:87-92`, 66 `.py` files) |
| `HERMES_*` env-var reads/writes across `voicebridge` + `qubit_runtime` | 927 unique names; e.g. `HERMES_HOME` (714 refs), `HERMES_FRONTEND_MAP` (1175), `HERMES_KANBAN_*` (729/685/674) | **RENAME** for the ~184 already-aliased set; rest **REQUIRES-COMPAT-SHIM** (mirror) then RENAME in Phase 6 |
| `~/.hermes` path literals (`Path.home()/".hermes"`) | 74 files reference `HERMES_HOME`; guards in `voicebridge/qubit_runtime.py:330-338`, `voicebridge/qubit_session_store.py:194-228` | **REQUIRES-COMPAT-SHIM** — must stay as the *refused external source*, never product surface |
| `~/.hermes/hermes-agent` reference-install literals in docs/tests | `tests/test_rebrand_determinism.py:54,61`, `docs/PARITY.md:18`, `docs/MIGRATION.md` | **KEEP-AS-INTERNAL** (docs) / **REQUIRES-COMPAT-SHIM** (test fallbacks) — cleared in Phase 6 |
| `/home/trick/.cache/qubit-pins/hermes-agent` pin path | 2 code refs (`tests/test_rebrand_determinism.py:54,61`) + docs | **KEEP-AS-INTERNAL** until Phase 6 (deferred) |
| `nousresearch.com` / `hermes-agent.nousresearch.com` URLs | `inference-api.nousresearch.com/v1` (89), `portal.nousresearch.com` (84+), `hermes-agent.nousresearch.com/install.sh` (22), `nousresearch.com` (19) | **RENAME** public URLs (product surface) in Phase 3; wire endpoint host may **REQUIRES-COMPAT-SHIM** |
| `NousPortal`, `Nous Research`, `@NousResearch` | 1,069 lines / 447 files; `_LEGACY_PORTAL_HOST = "nousresearch.com"` (`voicebridge/qubit_compat.py:10028`) | **RENAME** product surface |
| `hermes-agent-dev` skill, `hermes-kanban-*` service ids, `@hermes/ink` aliases, `X-Hermes-Session-Token` | see `scripts/qubit_rebrand.py:187-190` + `CLI_WIRE_IDENTIFIER_RE` (`scripts/qubit_public_surface_scan.py:80-84`) | **REQUIRES-COMPAT-SHIM** — wire/service ids preserved (see §5) |
| `frontend/qubit-web/src/**` source | 116 files | **RENAME**, rebuild `qubit-dist` |
| `frontend/qubit-dist/**` built bundle | 12 files | **RENAME** via rebuild (never hand-edit) |
| `LICENSE`, `THIRD_PARTY_NOTICES.md`, upstream copyright headers, `docs/archive/2026-09-21/LICENSING.md`, `UPSTREAM_REUSE.md`, `QUBIT_HERMES_VENDOR_MANIFEST.json`, `HERMES_SOURCE_COMPARISON.json` | protected-parts set (`scripts/qubit_rebrand.py:243-255`) | **LEGAL-ATTRIBUTION-KEEP** |
| Legacy on-disk names: `openclaw-voice-bridge` (`voicebridge/config.py:37`, `foundation.py:27`), `hermes-runtime`/`hermes` home dirs (`voicebridge/qubit_runtime.py:335-338`), `hermes_env_refs.json` (`voicebridge/env_store.py:30`), `hermes_config_overrides.json` (`voicebridge/qubit_compat.py:13324`), `hermes_persona.json` | enumerated in `docs/MIGRATION.md:63-67` | **REQUIRES-COMPAT-SHIM** — read-old/write-new; never hot-renamed (breaks existing on-disk state) |

**Total occurrence lines counted (all six tokens, live scope): 149,241.**
Unique files touched by any legacy token (live scope): **~8,600 union** (the six
files-counts overlap; the dominant `hermes` 6,545-file set subsumes most).

---

## 2. Env-var migration table — `HERMES_X` → `QUBIT_X`

The mechanism already exists: `voicebridge/qubit_legacy.py` mirrors `QUBIT_*` ⇄
`HERMES_*` for *unset counterparts only* (`_mirror`, lines 61-80), with an
explicit home map (`LEGACY_ENV_MAP = {"HERMES_HOME": "QUBIT_HOME"}`,
`qubit_legacy.py:33-35`) that **refuses** the external `~/.hermes` default
(lines 53-58, 100-102). 184 `QUBIT_*` names already exist; 927 `HERMES_*` names
do not yet all have aliases.

### 2a. Must migrate (product-facing / high-refcount), rename in Phase 4

| Old | New | Refs | Notes |
|---|---|---:|---|
| `HERMES_HOME` | `QUBIT_HOME` | 714 | already mapped; shim in place |
| `HERMES_RUNTIME` / `HERMES_RUNTIME_DIR` | `QUBIT_RUNTIME` / `QUBIT_RUNTIME_DIR` | 24 / 158 | `voicebridge/route_ownership.py:11`, `scripts/detached_run.py:61` |
| `HERMES_INSTALL_ROOT` | `QUBIT_INSTALL_ROOT` | 58 | |
| `HERMES_MODEL` / `HERMES_INFERENCE_MODEL` / `HERMES_INFERENCE_PROVIDER` | `QUBIT_*` | 88 / 43 / 43 | |
| `HERMES_TUI_RESUME` / `HERMES_TUI_PROVIDER` | `QUBIT_TUI_*` | 42 / 23 | PTY/TUI wire, shim both |
| `HERMES_SESSION_*` (ID/KEY/PLATFORM/CHAT_ID/…) | `QUBIT_SESSION_*` | 129/123/109/41… | |
| `HERMES_DESKTOP_*` (12 names) | `QUBIT_DESKTOP_*` | 89… | |
| `HERMES_DASHBOARD_*` (11 names) | `QUBIT_DASHBOARD_*` | 42… | |
| `HERMES_PROFILE`, `HERMES_MAX_ITERATIONS`, `HERMES_TIMEZONE`, `HERMES_LANGUAGE`, `HERMES_GID/UID` | `QUBIT_*` | 78/83/55/18/48… | |
| `HERMES_KANBAN_*` (BOARD/DB/TASK/…) | `QUBIT_KANBAN_*` | 674/685/729/… | data-store keys — shim required |
| `HERMES_PAYLOAD_TAG`, `HERMES_PAYLOAD_VERSION` | `QUBIT_*` | 99/13 | wire — shim |

### 2b. Compat shim — keep accepting `HERMES_*` (duration: until Phase 6 severance **and** one release cycle after)

- Full 927-name set is mirrored by `qubit_legacy.install()` for *unset*
  counterparts. **Rule:** the shim is read-old only; `QUBIT_*` is always the
  written name. Never delete a legacy reader while any on-disk state
  (`~/.local/state/qubit-voicebridge/**`) or any running service still exports
  the old name.
- `_MIRROR_EXCLUDE = {"HERMES_HOME","QUBIT_HOME"}` (`qubit_legacy.py:43`) stays —
  the external-home guard must survive every phase.
- `HERMES_QWEN_*` is intentionally kept per `docs/STATUS.md:7` — **KEEP-AS-INTERNAL**.

### 2c. Keep permanently (wire/compat), never rename

`X-Hermes-Session-Token`, `hermes:<route>` IPC channels, `@hermes/[pkg]`
specifiers, `hermes-kanban-*` unit ids — enumerated in
`scripts/qubit_public_surface_scan.py:80-84` (`CLI_WIRE_IDENTIFIER_RE`) and
`scripts/qubit_rebrand.py:187-190`. These already render as allowlisted, not as
leaks.

---

## 3. Severing the pin — precise mechanism and what breaks

### 3a. Every code path that reads the pin path

Exhaustive (grep, not top-N):

```bash
grep -rn '/home/trick/.cache/qubit-pins' --include='*.py' --include='*.md' \
     --include='*.sh' --include='*.toml' --include='*.json' .
```

Result — **2 code refs, both in one test**:

- `tests/test_rebrand_determinism.py:54` — `PINNED_SOURCE` default candidate
  `/home/trick/.cache/qubit-pins/hermes-agent` (overridable via `QUBIT_PINNED_SOURCE`).
- `tests/test_rebrand_determinism.py:61` — `REFERENCE_HOME` default candidate
  (overridable via `QUBIT_REFERENCE_HOME`), falling back to `~/.hermes/hermes-agent`.

Both fall back through `_first_existing()` (line 40-46) to ephemeral
`/tmp/qubit-1to1-*` and `/tmp/opencode/pinned/hermes-agent` paths. Full pin
inventory also exists at `/home/trick/.cache/qubit-pins/` (harnessrouter,
openclaw, openmuse, hermes-agent-pristine — the non-Hermes pins in
`docs/QUBIT_NONHERMES_UPSTREAM_PINS.md`).

### 3b. The `QUBIT_VENDOR_MANIFEST` / `HERMES_VENDOR_MANIFEST` mechanism

Two manifests, both **generated**, both named `*_HERMES_VENDOR_MANIFEST.json`:

| Manifest | Path | Producer | Consumer |
|---|---|---|---|
| Vendor manifest (canonical) | `docs/generated/QUBIT_HERMES_VENDOR_MANIFEST.json` | `scripts/hermes_vendor_manifest.py:1873` | `voicebridge/qubit_runtime.py:179`, `scripts/config_binding_manifest.py:34,174`, `scripts/forensic_audit/checks.py:206`, `scripts/source_inventory.py:64` |
| Vendor manifest (archived/frozen) | `docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json` (11 MB) | frozen | `voicebridge/qubit_runtime.py:182`, `scripts/release_qualify.py:386` |
| Byte manifest | `docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json` (6 MB) | `scripts/hermes_byte_manifest.py:56` | `scripts/differential_parity.py:482-496,5685-5774` |
| Source comparison | `docs/generated/HERMES_SOURCE_COMPARISON.json` | `scripts/source_inventory.py` | `differential_parity.py` |

`voicebridge/qubit_runtime.py:179-182` prefers the canonical generated manifest,
falling back to the archived frozen one — an in-repo read, **not** a pin read.

### 3c. Parity / diff tooling referenced by docs/PARITY.md and docs/MIGRATION.md

- `scripts/differential_parity.py` (~8,500 lines) — byte/surface diff against the
  pinned reference; env overrides `QUBIT_DIFFERENTIAL_BYTE_MANIFEST`
  (line 5685), reads via `qubit_runtime.ensure_path()` (lines 4838, 5024-5025).
  Needs the reference tree present when run in `reference` mode.
- `scripts/parity_inventory.py:361` — calls `qubit_runtime.ensure_path()`.
- `scripts/source_inventory.py` — emits the source comparison + vendor manifest.
- `docs/QubitVSupstream.md` — the Tier-0 audit harness.
- `scripts/release_qualify.py:404` — `hr.ensure_path()` release gate.

### 3d. What breaks if the pin is deleted

1. `tests/test_rebrand_determinism.py` **hard-fails** — `_assert_pinned_revision()`
   (line 102+) requires a git checkout at `69948c0057`; both `PINNED_SOURCE` and
   `REFERENCE_HOME` default candidates vanish (unless `QUBIT_PINNED_SOURCE` /
   `QUBIT_REFERENCE_HOME` are exported, or `~/.hermes/hermes-agent` still
   resolves).
2. `scripts/differential_parity.py` in `reference` mode loses its oracle →
   byte-parity verification cannot run until the byte manifest is re-frozen as
   the sole oracle.
3. `scripts/test_rebrand_*` / `test_rebase_task14_brand.py` determinism suite can
   no longer re-derive the transform.
4. **Not broken:** runtime boot, dashboard, voice, gateway. They read the
   in-repo `qubit_runtime/` tree and the in-repo manifests, never the pin.
   (Confirmed: `qubit_runtime.ensure_path()` + `use_qubit_home(cfg)` in
   `voicebridge/qubit_compat.py:2298-2300` operate on the repo tree only.)

---

## 4. Ordered, dependency-correct execution phases

Each phase: **Change → Verification → Rollback.** Phases 1–5 leave the pin,
`~/.hermes`, `~/.openclaw` fully intact. Phase 6 is deferred severance.

### Phase 1 — Branding-scan baseline + test reconciliation *(safe, first)*

- **Change.** Resolve the `tests/test_branding_bundle_scan9u.py` (requires
  de-branding) vs `tests/test_branding_leaks_fixes.py` (requires strings present)
  conflict per `docs/BRAND.md:56-62` → **Option A** (policy wins; upstream refs
  only in legal/provenance). Capture a fail-closed baseline.
- **Verification.**
  `.venv/bin/python scripts/qubit_public_surface_scan.py --json` →
  record `public_leaks` count as the pre-change baseline; must equal the value in
  `docs/BRAND.md:34-45`.
- **Rollback.** `git checkout -- tests/test_branding_bundle_scan9u.py tests/test_branding_leaks_fixes.py`
  (no other files touched).

### Phase 2 — De-brand product prose & identifiers in `voicebridge/` + docs

- **Change.** Product-facing strings (`Hermes`/`OpenClaw`/`NousPortal`/
  `Nous Research`/`@NousResearch`) → Qubit on reachable surfaces. Keep wire ids
  (§2c). Update internal-doc prose per `BRAND.md:28-32` (name upstream only where
  load-bearing). **Do not** touch `docs/differential/INTERFACE.md` or
  `docs/archive/**`.
- **Verification.**
  `.venv/bin/python scripts/qubit_public_surface_scan.py` (cli/routes/voice/
  bundles/source_strings) → `public_leaks == 0` for the `voicebridge` and docs
  surfaces.
- **Rollback.** `git checkout -- voicebridge/ docs/` ; bundle/routes unchanged in
  this phase.

### Phase 3 — De-brand URLs, bundle, and public routes

- **Change.** Rewrite `nousresearch.com` / `hermes-agent.nousresearch.com` public
  URLs (§1) to Qubit equivalents; update `_LEGACY_PORTAL_HOST`
  (`voicebridge/qubit_compat.py:10028`). Rebuild `frontend/qubit-dist` + `qubit-app`
  from `frontend/qubit-web/src` (116 source files). Re-run doctype/route tables.
- **Verification.**
  `.venv/bin/python scripts/qubit_public_surface_scan.py --json` → `bundles` and
  `routes` surfaces show `public_leaks == 0`; then
  `npm --prefix frontend/qubit-web run build` exits 0 and the freshly built chunk
  serves.
- **Rollback.** Restore prior `frontend/qubit-dist`, `frontend/qubit-app` from git;
  `git checkout -- voicebridge/qubit_compat.py frontend/qubit-web/src`.

### Phase 4 — Env-var rename to the `QUBIT_*` namespace (shim-backed)

- **Change.** Extend the `QUBIT_*` alias set to the full §2a table; switch written
  names to `QUBIT_*` everywhere in `voicebridge` + `qubit_runtime` (via the
  deterministic map in `scripts/qubit_rebrand.py:180-182` extended). Keep
  `qubit_legacy.install()` mirroring `HERMES_*` read-old. Re-run generators
  (`hermes_vendor_manifest.py`, `source_inventory.py`) to regenerate in-repo
  manifests — **new filenames `QUBIT_VENDOR_MANIFEST.json`** with the old
  archived one preserved.
- **Verification.**
  `QUBIT_HOME=/tmp/probe .venv/bin/python -c "import os,voicebridge.qubit_legacy as l; l.install(); print(os.environ.get('HERMES_HOME'))"`
  (expect the QUBIT value mirrored) **and** a boot smoke:
  `.venv/bin/python -m voicebridge.cli --help` exits 0 with zero `HERMES_`/`Hermes`
  on rendered help. Re-run branding scan → 0 leaks.
- **Rollback.** `git checkout -- voicebridge/ qubit_runtime/qubit_cli/ scripts/ docs/generated/`;
  restore the prior env map. The shim keeps old installs working at every point.

### Phase 5 — Deep `qubit_runtime/` identifier debt (the 66-file core)

- **Change.** Re-run `scripts/qubit_rebrand.py --apply` + `scripts/qubit_debrand.py
  --apply` over the residual `hermes_*` identifiers (`qubit_runtime/tools/*`,
  `skills/*/scripts/*`, `qubit_cli/*`) per `docs/QUBIT_VENDOR_DELTA_2026-09-22.md:87-92`.
  Run against a **staged copy outside `~/.hermes`** (worker rule) — the live tree
  is never the build base. Verify idempotent (byte-identical across two runs).
- **Verification.**
  `.venv/bin/python -m pytest tests/test_rebrand_determinism.py tests/test_rebrand_engine_tokens.py -x`
  (determinism gate — pin still present, so it can run) → pass. Then
  `.venv/bin/python scripts/qubit_public_surface_scan.py` → 0 public leaks in
  `source_strings`.
- **Rollback.** `git checkout -- qubit_runtime/` (single tracked tree; 16,084
  files). Generators re-run to restore manifests.

### Phase 6 — SEVERANCE *(DEFERRED — only after full parity is verified E2E)*

> **Status: DEFERRED.** Do not run until every row in §5's E2E checklist is
> ticked with a command + observed output, and the owner signs off.

- **Change.** Remove the pin dependency so Qubit needs nothing from upstream:
  (a) re-point `tests/test_rebrand_determinism.py` `PINNED_SOURCE` /
  `REFERENCE_HOME` to a committed, in-repo frozen fixture or retire the test;
  (b) re-freeze `docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json`
  as the sole parity oracle and make `scripts/differential_parity.py`
  `reference`-mode optional; (c) rename the manifest with `hermes` in its name;
  (d) retire `qubit_legacy` legacy readers only after one release cycle.
- **Preconditions that MUST hold before Phase 6 is safe:**
  1. §5 checklist fully green (command + observed output per row).
  2. `scripts/qubit_public_surface_scan.py` → `public_leaks == 0` on all surfaces.
  3. No runtime/test path resolves a symbol from the pin (proven by running the
     full suite with `QUBIT_PINNED_SOURCE=` unset and `~/.hermes` renamed to a
     temp name in an isolated namespace — the `docs/MIGRATION.md:80-81` test).
  4. Byte-parity oracle re-frozen in-repo; `differential_parity.py` green against
     it with the pin absent.
  5. Owner sign-off recorded as a dated amendment (`AGENTS.md` amendment rule).
  6. Backup snapshot of the pin taken (copy only — never move/delete the
     original) and the reference install confirmed still running for other agents.
- **Verification.** Full gate: `.venv/bin/python -m pytest -q` (907-pass
  baseline per `docs/STATUS.md:7`) + branding scan + differential parity against
  the re-frozen in-repo oracle + `qubit --help` / dashboard / voice smoke.
- **Rollback.** Restore the pin references from git; the original pin directory
  is untouched throughout (we never delete it — we only stop reading it), so
  rollback is `git checkout -- tests/ scripts/ docs/generated/` plus re-exporting
  `QUBIT_PINNED_SOURCE`.

---

## 5. E2E "Qubit works 100% standalone" checklist (tick each with evidence)

Draws on `docs/PARITY.md` §rule 1–6. Each row needs a **command + observed
output**, not assertion.

| # | Capability | Proof command | Pass = |
|---|---|---|---|
| 1 | CLI-for-CLI (163 `qubit agent` cmds) | `qubit agent --help`; diff vs reference `hermes --help` | every reference subcommand present or dispositioned |
| 2 | Route-for-route (415 `qubit_compat.ROUTES`) | `curl -s localhost:8890/openapi.json` + compare route literals | every reference route has a working counterpart |
| 3 | Control-for-control (562 `dashboard_ops` + 35) | Playwright E2E over the dashboard | each control drives the real backend |
| 4 | Tool-for-tool (14 skills, 65 MCPs) | enumerate + invoke each | every skill/MCP/tool works |
| 5 | Browser / terminal / IDE actions | run each action | real side effect observed |
| 6 | Model/provider ops | `qubit agent` provider list + inference call | provider ops succeed |
| 7 | Voice-for-everything (510 `voice_commands`) | speak each intent | each capability reachable by voice |
| 8 | Migration completeness (memories 0/4, cron 3/7, config, creds) | `docs/MIGRATION.md` count/row equality | reference parity per item |
| 9 | One-home invariant (`QUBIT_HOME`, one `state.db`) | `ls ~/.local/state/qubit-voicebridge/` | no legacy-named active home |
| 10 | Zero legacy brand on public surfaces | `scripts/qubit_public_surface_scan.py --json` | `public_leaks == 0` |
| 11 | No `~/.hermes` runtime fallback | rename `~/.hermes` in an isolated ns, run suite | suite passes without it |
| 12 | Clean-room parity oracle | fresh install @ pinned version `0.21.3`/`0.21.5` | behavior matches, no data confound |

---

## 6. Must NOT be renamed — explicit list with reasons

| Item | Location | Reason |
|---|---|---|
| Upstream copyright + license line | `LICENSE`, `THIRD_PARTY_NOTICES.md` | License compliance — the one legal exception (`BRAND.md:11-19`). Removing is a violation; `AGENTS.md:95-98` requires `BLOCKED — LICENSE REVIEW REQUIRED` on uncertainty. |
| `Copyright (c) 2025 Nous Research` (MIT) at pin | upstream headers carried in vendored files | Same — required attribution. |
| `docs/archive/2026-09-21/LICENSING.md`, `UPSTREAM_REUSE.md` | frozen provenance | `AGENTS.md:60-63`, `BRAND.md:21-26` — frozen, never edited. |
| `docs/differential/INTERFACE.md` | frozen contract | `AGENTS.md:63` — contains legacy tokens by definition of what it compares against. |
| `docs/archive/**` (incl. `QUBIT_HERMES_VENDOR_MANIFEST.json`, `HERMES_SOURCE_COMPARISON.json`, `QUBIT_REBRAND_MANIFEST.json`) | frozen records | `BRAND.md:21-26`, protected set at `qubit_rebrand.py:243-255`. |
| `X-Hermes-Session-Token` header | wire protocol (`qubit_public_surface_scan.py:82`) | Rename breaks live dashboard/PTY session auth. |
| `hermes:<route>` IPC channels, `@hermes/[pkg]` package specifiers | wire (`qubit_public_surface_scan.py:83-84`) | Rename breaks IPC + npm resolution. |
| `hermes-kanban-*` service/unit ids | `scripts/qubit_rebrand.py:187-190` | Live systemd/service identity; renaming breaks running units. |
| `~/.hermes` path literal as *refused external source* | `voicebridge/qubit_runtime.py:330-338`, `voicebridge/qubit_session_store.py:194-228` | Must keep refusing it; renaming the guard would silently adopt the external home (authority split). |
| `~/.local/state/qubit-voicebridge/{hermes,hermes-runtime,qubit}` legacy dir names + `hermes_env_refs.json`, `hermes_config_overrides.json`, `hermes_persona.json`, `.hermes_history` | `qubit_runtime.py:335-338`, `env_store.py:30`, `qubit_compat.py:13324`, `cli.py:752` | Existing on-disk state — hot rename loses user data. Read-old/write-new only (`docs/MIGRATION.md:63-67`). |
| `openclaw-voice-bridge` / `qubit-voice-agent` legacy config/service names | `voicebridge/config.py:37-40`, `foundation.py:27`, `product.py:226-231` | Live XDG dirs + systemd units other agents use; rename only via migration, not sweep. |
| `HERMES_QWEN_*` | `docs/STATUS.md:7` | Intentionally kept with recorded evidence. |
| `verify`/`state.db` schema keys, `state.db` filename | on-disk SQLite | DB schema compatibility — renaming breaks every existing session store. |
| `_hermes_home` private module → stays a redirected import | `qubit_rebrand.py:206-211` | Renamed module; import path must keep resolving. |
| `/home/trick/.cache/qubit-pins/*` | pin dirs | **Never renamed/moved/deleted** — read-only source of record (§0). |

---

## 7. Riskiest finding (summary)

The single highest-risk coupling is **not** the pin — it is the **927 `HERMES_*`
env-var names** read throughout `qubit_runtime/` (55,070 legacy-token lines in
that tree alone). Only 184 `QUBIT_*` names exist today, and the sole bridge is
`voicebridge/qubit_legacy.py`'s in-process mirror (`_mirror`, lines 61-80), which
only covers *unset* counterparts. A naive global rename that misses the
`_MIRROR_EXCLUDE` guard (`qubit_legacy.py:43`) or the external-home refusal
(`qubit_legacy.py:53-58`) would either (a) break live services mid-flight, or
(b) silently adopt `~/.hermes` as Qubit's active home — the exact authority-split
the product forbids (`docs/MIGRATION.md` invariant "One home, one `state.db`, one
authority"). This is why env renaming is Phase 4 (shim-backed) and severance is
Phase 6 (deferred): the risk lives in the runtime's env contract, and it must be
retired read-old/write-new, never swept.
