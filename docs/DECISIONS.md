# Qubit DECISIONS — owner rulings in force

**Status:** index of rulings that bind current work. **Not a verdict.** Every entry names the
document that carries the ruling's full text; this file restates only enough to identify it and
does not add policy of its own.

## In force

1. **Identity is product fact, not lineage.** Qubit is a personal AI agent — an all-in-one
   assistant that absorbs other agents' capabilities and becomes more than any one of them. It
   is not described as "a fork"; upstream lineage is provenance, named only where load-bearing.
   → Full text: `docs/IDENTITY.md` §"Qubit is not a fork", §"Absorbs, then exceeds".

2. **Zero legacy brand on product surfaces.** Legal attribution is the required exception. A
   rename must never touch license/copyright notices or attribution text.
   → Full text: `docs/BRAND.md` §1 "The rule", §2 "What a rename must never touch",
   §3 "What may remain, stated plainly".

3. **English is the authoring language** (owner ruling, 2026-10-10) — for docs and the product.
   Pre-existing runtime locale packs are **kept**; no new language packs are authored.
   → Full text: `docs/IDENTITY.md` §"Language policy (owner ruling, 2026-10-10)".

4. **The finish line is a frozen pin, not upstream HEAD.** Target
   `43faacfb8de7b29548547a7fbdedad0cc6e7391c` (2026-10-16; **superseded for the pin
   value by ruling 13**). Ports are literal 1:1 against that
   revision, including features this fork dropped on purpose; exclusions are pruned by the owner
   *after* the gap closes, never skipped mid-port by an agent. The pin does not move until
   hermes is retired, and post-freeze upstream commits are out of scope until then.
   → Full text: `docs/FROZEN_TARGET.md`.

5. **`VERIFIED` is the sole release verdict.** `M1` / `M2` are implementation-progress markers
   only, never release claims, and a typed refusal is never a working capability.
   → Standing statement: `AGENTS.md` §"Documentation authority". The owner amendment that first
   set the staged gates was purged in `eafb54e26`; its text is retrievable, not authoritative
   by path:
   `git show eafb54e26^:docs/QUBIT_OWNER_AMENDMENT_2026-09-22_STAGED_GATES_AND_RULINGS.md`

6. **The local reference dashboard is the behavioral oracle.** Everything that works at
   `http://127.0.0.1:9119/?profile=default` must work in Qubit and be commandable by voice; the
   reference instance is read-only and must not be modified.
   → Standing statement: `AGENTS.md` §"Documentation authority".

7. **`docs/archive/` is historical-only.** Active tests and generators must never read or write
   it; they target live docs or `docs/generated/`.
   → Provenance: pre-purge `docs/README.md` (Owner Decision #6, 2026-09-23):
   `git show eafb54e26^:docs/README.md`
   (Neither `docs/generated/` nor `docs/differential/` exists in the tree today; the ruling still
   forbids reading or writing `docs/archive/`.)

8. **Exclusions require owner sign-off; the 2026-10-21 F5 set was rejected, not
   re-affirmed.** The 21 rows agents had added to `docs/FROZEN_TARGET.md` were ordered
   ported literally instead; wave 16 restored and wired the 20 production symbols,
   `_tool_prefix` was already live (stale row), and the `legacy_digests` test helper was
   restored and used. Only `gateway/status.py::_gateway_lock_handle` remained excluded, by
   explicit owner sign-off the same day (retired 2026-10-24, ruling 10) (the pin defines it only as an unread `= None`;
   the live acquire/release machinery is post-freeze and would double-flock against
   `runtime_ownership`).
   → Provenance: owner session 2026-10-21; evidence in `docs/STATUS.md` (wave 16) and
   `docs/FROZEN_TARGET.md` §"Declared exclusions".

9. **A control-socket bind failure is fatal at gateway bootstrap (F4).** This matches the
   frozen pin's `gateway/run_bootstrap.py`, which raises when the listener is unavailable;
   the "non-fatal" body is post-freeze `69948c0057`, and session-ticket attach has no scan
   fallback. The `GatewayControlServer` docstring records the split (server reports `None`;
   the bootstrap escalates), pinned by
   `qubit_runtime/tests/gateway/test_control_listener_fatal.py`.
   → Provenance: owner ruling 2026-10-21 after the read-only investigation in wave 16.

13. **Re-pin to upstream main `088f657ecf` (owner ruling, 2026-10-30).** The finish line
    is now `088f657ecf77674c7b8e2ededf4e3576d82e042c` (2026-10-29T20:29:59Z), superseding
    the `43faacfb8de7` pin in ruling 4 for the pin value. Ports are literal 1:1 against the
    new pin; post-pin upstream commits are out of scope until the owner re-pins again.
    Measured gap at the re-freeze: **1,968 production missing** (244 absent production
    files + 1,724 behind-head production symbols across 497 files); tests add 1,908
    non-blocking missing symbols. Evidence: `scripts/frozen_target_progress.py` and the
    audit JSON at `088f657ecf`. → Full text: `docs/FROZEN_TARGET.md`.

## Why this file is not the original

The previous `docs/DECISIONS.md` (214 lines, including then-current `OPERATOR_ONLY` counts) was
deleted in `eafb54e26` (owner English-only ruling). It is **not** restored: its numbers were
measured against a state that no longer exists, and re-typing them would assert stale counts as
current. This index carries only rulings that are still in force and still stated in a file you
can open.

Verify the purge and its scope:

```bash
git show --no-patch --format='%h %ad%n%s%n%b' eafb54e26
git show --diff-filter=D --name-only --format='' eafb54e26 | grep -c '^docs/'   # 340
```

(The commit message says 341 files under `docs/`; the name-only deletion count is 340. Cite the
command, not the message.)

Verify every ruling source above still exists:

```bash
for f in docs/IDENTITY.md docs/BRAND.md docs/FROZEN_TARGET.md AGENTS.md; do
  printf '%-28s %s\n' "$f" "$(test -f "$f" && echo present || echo MISSING)"
done
```

10. **The B1 exclusion is retired (owner ruling, 2026-10-24).** The frozen pin's
    `gateway/status.py:44` defines `_gateway_lock_handle = None` and never reads or writes it;
    that inert literal is now present. No acquire/release/owns machinery is ported — runtime
    ownership remains `gateway.runtime_ownership.process_ownership`, so there is no behavior
    change and no double-flock risk. This supersedes ruling 8 for the B1 row; the completeness
    instrument now reports `AT TARGET`. Evidence: `docs/STATUS.md` wave 43.

11. **Windows is out of scope for the W49 milestone (owner ruling, 2026-10-25).** The
   GitHub-hosted Windows jobs never acquire a runner, so they cannot be made green from
   this repo; the owner directed that they are not chased. Windows-only suites are
   documented but excluded from the matrix and the release verdict. Linux remains in
   scope. Evidence and the milestone record: `docs/STATUS.md` wave 47 and
   `docs/FROZEN_TARGET.md` §"Platform scope".

12. **P8 owner decision (2026-10-27).** The owner reviewed the measured
    inventory and directed: keep D1–D7, D9–D11, D13–D15, D20 and D22 as-is;
    verify D8/D12/D21; rebrand D16 and D19; clean up D17 and D18; never touch
    D15 legal attribution. Execution evidence: D8 now dual-publishes the
    canonical and legacy plugin-SDK globals in the vendored SPA; D12 probes
    `qubit-gateway*` units before the legacy names; D21's write-only set was
    re-derived (19 line-level / 18 true writers, not 22) and every writer is
    kept because a reader or child inheritance exists; D16 rebranded the
    vendored website (469 files; brand guard OK); D19 rebranded only the
    document framing while preserving raw evidence; D17/D18 changed comments
    and prose only (brand guard OK). No `HERMES_*`, `.hermes`,
    `/root/.hermes`, plugin-global, model-name or attribution contract was
    renamed. Evidence: `docs/REBRAND-WEBSITE-2026-10-27.md`,
    `docs/REBRAND-PROSE-2026-10-27.md`,
    `docs/REBRAND-EVIDENCE-2026-10-27.md`, `docs/PHASE8-D21-AUDIT.md`.
    → Provenance: owner session 2026-10-27; measure/decision pack in
    `docs/PHASE8-SIGNOFF-PACK.md`.


13. **`/qubit-chat` is absorbed, not replaced (2026-11-06).** The owner asked for
    hermes-webui to fully replace `/qubit-chat`. Investigated against both
    codebases (`docs/CHAT_REPLACE_SCOPE.md`), a drop-in replacement was refused
    because it would break three things the product is built on:
    (a) hermes-webui has **no speech recognition and no TTS anywhere** — the
    entire voice path (`useTranscriptionController`, `startVoice`/`stopVoice`/
    `toggleVoice`, the `ControlRight` listener, `useDesktopVoiceHelper`,
    `ReadAloudButton`) lives only in `ChatPage.tsx`;
    (b) it **eagerly imports `run_agent.AIAgent`** (`api/streaming.py`);
    (c) it opens **`state.db` read-write** (`api/routes.py`), a second writer
    beside `CHAT_SESSION_AUTHORITY` (`QubitSessionStore`) — the exact corruption
    class the single-authority rule exists to prevent.
    What shipped instead: the context-token ring absorbed into the existing
    React surface (997 insertions, **zero deletions**; 142 tests over 16 files;
    mutation-proved). `ChatPage.tsx` was deliberately NOT deleted, and must not
    be: it IS the replacement surface.
    → Provenance: owner session 2026-11-06 ("2. full replace"); refusal
    evidence and file:line citations in `docs/CHAT_REPLACE_SCOPE.md`.
    A future agent must not read "full replace" as authority to delete the
    voice path; re-raise with the owner if the voice requirement changes.

14. **Owner-purchased LimeZu art may be used in Qubit Office (owner ruling, 2026-11-09).**
    The owner purchased the LimeZu packs inventoried in `THIRD_PARTY_NOTICES.md`
    §"LimeZu — purchased office art" and directed that they be used to upgrade
    Qubit Office ("its free to use in your game"). The Modern Interiors licence
    permits editing and use in any commercial or non-commercial project and
    requires credit to `limezu.itch.io`; it forbids reselling or distributing
    the asset to others. Rules for agents, in order:
    (a) credit LimeZu on any surface that ships the art;
    (b) record a pack's name, sha256 and licence text in
    `THIRD_PARTY_NOTICES.md` before using it;
    (c) the purchase does **not** resolve exposing the raw `.zip` packs through
    the public repo or release — do not add the downloaded packs themselves to
    the tree; assets used by the product are chosen files/slices with their
    provenance recorded;
    (d) if a specific asset still cannot be shipped, stop with
    `BLOCKED — LICENSE REVIEW REQUIRED` and name the asset; never silently strip
    or substitute it.
    → Provenance: owner session 2026-11-09; inventory, licence quotes and
    sha256s in `THIRD_PARTY_NOTICES.md`. Source files live at
    `the owner's local download (2026-11-09)` and are not part of the repo.

15. **Habblaud stays vendored and unpatched for UI work (owner ruling, 2026-11-09).**
    The Office UI upgrade must **not** modify, patch, rebuild, or re-vendor the
    vendored Habblaud client (`frontend/qubit-app/plugins/qubit-office/habblaud/`)
    for theming. UI work happens in the Qubit-owned shell around the iframe:
    agent portraits in the Agents panel, the host docks, the injected menu icons,
    and append-only runtime overrides of a **documented allowlist** of the
    office's own `.ui-root` CSS variables. Everything stays off by default
    (`office.theme: classic`) and is removable without a rebuild. Room/tile art
    inside the canvas would require a re-vendor, so it is explicitly **deferred**
    and needs a new owner decision. Gate: `python3 scripts/vendor_habblaud.py
    --check` must stay green and the vendored hashes unchanged in every lane.
    → Provenance: owner session 2026-11-09 ("dont fuck habblaud we just update ui
    to make it better and easier to upgrade in the future").
