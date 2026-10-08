# Cosmetic Rebrand — Plan (cosmetic only, no wire changes)

**Author:** Qubit Agent · **Date:** 2026-10-08 · **Status:** PROPOSED, awaiting scope sign-off
**Method:** `rebrand-wording-sweep` skill (identity vs mechanism triage) + `rebrand-normalized-symbol-port`

---

## 0. The finding that reshapes this plan

**The rebrand is largely ALREADY DONE.** `docs/IDENTITY.md` exists, is marked canonical, is linked
from **both** authority surfaces (`docs/README.md:19` and `AGENTS.md:44`), and already states the
skill's core doctrine:

> *"Qubit is not 'a fork'. Qubit began from upstream source and still carries the upstream lineage in
> its history, its vendored runtime, and its required legal attribution. That is **provenance**, not
> identity."*

**So this is NOT a fresh sweep.** It is a **gap-closure** against an existing canonical statement.
That changes the shape of the work: the deliverable is not "write the identity", it is "make the tree
agree with the identity that is already written."

---

## 1. SCOPE RULE (the boundary — read this first)

**Cosmetic only means: change what the product SAYS, never what the code is NAMED.**

| | IN SCOPE (edit) | OUT OF SCOPE (never touch) |
|---|---|---|
| Markdown prose in `docs/`, `qubit_runtime/website/docs/` | ✅ | |
| Python **docstrings and comments** describing the product | ✅ | |
| User-facing UI copy (labels, headings in `web/src`) | ✅ | |
| Product self-description ("a fork of X", "Hermes fork") | ✅ | |
| **Identifiers** (`hermes_cli`, `hermes_state`) | | ❌ rename sweep — different task |
| **`HERMES_*` env vars** (721 distinct tokens) | | ❌ wire contract |
| **systemd unit names** (`hermes-gateway*`) | | ❌ live contract |
| **lock/marker names** (`.hermes-update-*`) | | ❌ cross-process |
| **`hermes-pycache`** (`PYTHONPYCACHEPREFIX`, baked into generated launchers) | | ❌ generated-artifact name |
| **Frozen/archived docs** (`docs/archive/**`) | | ❌ add POINTER only |
| **`qubit_runtime/website/i18n/zh-Hans/**`** | | ❌ translations — separate decision (§6) |

**The rule that prevents the failure class:** grep the word, **triage EVERY hit as IDENTITY or
MECHANISM**, edit only IDENTITY. Most hits are legitimate technical uses. This campaign has already
lost the model picker (the `qubit_id` half-rebrand) and 29 test files (a stale `HERMES_HOME` rename
in test bodies) to exactly this mistake.

**A half-done rebrand is a LIVE BUG.** One site fixed and its siblings left stale is the single
highest-yield defect class this campaign has found — it produced the `/chat` live-update bug
(two hooks blanked `lastSeq = null` while a third was already fixed).

---

## 2. MEASURED SURFACE (real numbers, not estimates)

**Prose HIT counts** (grep, case-insensitive, `*.md`/`*.mdx`):

```
docs/                     4,103 hits   in 264 md files
qubit_runtime/           19,246 hits
  ├─ website/docs        11,579       English source docs
  ├─ website/i18n         6,683       zh-Hans TRANSLATIONS  (out of scope, §4)
  └─ rest of tree           983       markdown outside website/docs
                          ───────
  English source total:  12,562  (11,579 + 983)
  Out-of-scope i18n:      6,683
```

⚠️ **A prose hit is NOT an edit.** The overwhelming majority are legitimate technical uses
(`hermes_cli` module paths in code fences, env-var names, unit names quoted in reference docs).
**Only IDENTITY-bucket hits are edits, and that bucket is not yet counted — Step 1 produces it.**

`docs/IDENTITY.md` — EXISTS, 72 lines, canonical, linked from `docs/README.md:19` **and** `AGENTS.md:44`.

**Already-correct (do NOT rewrite — verify and leave):**
- `docs/ARCHITECTURE.md` opener — *"built on vendored upstream source renamed into the Qubit
  namespace… Upstream is provenance, not identity."* Points at `IDENTITY.md`. ✅
- `docs/IDENTITY.md` §"Qubit is not 'a fork'" — the doctrine, already written. ✅
- `docs/BRAND.md` — referenced by IDENTITY.md as the engineering-detail home. ✅

**Identity-framing phrase counts** (grep targets, NOT edit counts):

| Phrase | Hits | Disposition |
|---|---|---|
| `Qubit-owned` | 245 | **triage** — "the Qubit-owned fork" = identity; "Qubit-owned state dir" = technical |
| `vendored fork` | 212 | **triage** — skill maps to `the vendored runtime` / `built on vendored upstream source` |
| `fork of` | 41 | **triage** — mix of identity and mechanism |
| `Hermes fork` | 10 | **mostly identity** |
| `rebranded fork` | 2 | **pure identity** |

**721 distinct `HERMES_*` tokens in `qubit_runtime` → ALL OUT OF SCOPE.**

---

## 3. WORK BREAKDOWN

### Step 1 — TRIAGE FIRST (no edits)
Grep each target phrase tree-wide, **read every hit**, bucket IDENTITY vs MECHANISM. Deliverable: a
triage table with the MECHANISM bucket listed explicitly and its reason, so a reviewer does not
"finish the job" and corrupt `os.fork` call sites or rename a CLI argument.
**Gate: triage table reviewed before any edit.**

### Step 2 — The generated-artifact check (BEFORE editing any string)
For each IDENTITY string, check whether it is **emitted into a generated file** (a reason string in a
manifest, a label baked into an inventory). If yes: either regenerate the artifact in the same
change, or leave it and say so.
⚠️ **This tree already has the proof this matters:** `hermes-pycache` is baked into
`scripts/build/launchers.py`'s generated output. A string that flows into output is not prose.
**Gate: any generator/artifact pair identified; regeneration included or the string left alone.**

### Step 3 — Fix only contradicting ACTIVE docs
Per the skill: **do NOT rewrite docs wholesale.** Correct the specific sentences that contradict
`IDENTITY.md`, and point them at it. Expected small: `ARCHITECTURE.md`-class openers that still say
"a rebranded fork of X".

### Step 4 — Frozen/archived docs: POINTER, never rewrite
`docs/archive/2026-09-21/` and `docs/archive/2026-09-22-pre-reset/` exist. Add **ONE dated note** at
the top of each archive dir's README (create if absent): wording superseded, canonical statement is
`docs/IDENTITY.md`, **and this note does not rewrite any frozen file.**
**One pointer per directory beats 64 file edits** — reviewable, revertable, respects the freeze.

### Step 5 — UI copy (user-visible)
Sweep `qubit_runtime/web/src/**` for user-visible `hermes` strings. This is the surface the owner
actually sees. **Highest value per edit.**

### Step 6 — Verify
- `gate_all.py` PASS 4/4 (must stay green)
- Re-run the specific tests touching any file edited
- **For any suite that goes red: prove pre-existing** by reproducing with the change stashed
  (⚠️ use `git stash push -- <explicit files>`, **never** bare `git stash` — repo-global, corrupted
  two worktrees in this campaign)
- Confirm zero identifier/env-var/unit-name changes (`git diff --stat` on the branch)

---

## 4. WHAT THIS PLAN DELIBERATELY DOES NOT DO

- **No `HERMES_*` → `QUBIT_*` rename.** 721 tokens, all wire contracts. Separate project requiring a
  migration path for persisted state, or it breaks live sessions and cross-process locks.
  *(Note: `HERMES_HOME → QUBIT_HOME` is already done and correct.)*
- **No module/directory renames** (`hermes_cli`, `hermes_state`). That is `upstream-port` scope and
  collides with the pin-based sync.
- **No i18n rewrite.** `website/i18n/zh-Hans/**` is machine-adjacent translation; rebranding
  translated prose needs a translation pass, not a find-and-replace.
- **No frozen-file edits.**

---

## 5. OPEN QUESTIONS FOR THE OWNER

1. **Scope confirm:** cosmetic-only = prose + comments + UI copy, **excluding** all identifiers, env
   vars, unit names and lock markers. Correct?
2. **i18n (`zh-Hans`, 745 files worth of tree):** leave entirely, or rebrand the English source only
   and mark translations stale?
3. **The 3 dead `hermes` PATH shims and 9 dead paths have been DELETED.** Confirm you do not want a
   `qubit` command installed on PATH to replace them (none exists today).

---

## 6. RISK REGISTER

| Risk | Mitigation |
|---|---|
| Blind find-and-replace corrupts technical prose | Triage-first gate (Step 1); MECHANISM bucket reported explicitly |
| Editing a string that flows into a generated artifact | Step 2 check before editing; regenerate or skip |
| Half-done rebrand → live bug | Sweep **every sibling site** of each pattern, not just the first |
| Rewriting frozen docs | Pointer-only rule (Step 4) |
| Proving a failure isn't ours | Reproduce with change stashed; **never** bare `git stash` |
| Rebranding a wire name by accident | §1 boundary table; Step 6 `git diff` audit for identifier changes |

---

## 7. DEFINITION OF DONE

- [ ] Triage table produced; MECHANISM bucket listed with reasons
- [ ] Every IDENTITY edit applied; **every sibling site of the same pattern swept**
- [ ] Generated-artifact pairs checked; regenerated or deliberately skipped with a note
- [ ] Contradicting active docs corrected and pointed at `docs/IDENTITY.md`
- [ ] Archive dirs carry ONE dated pointer note each; no frozen file rewritten
- [ ] UI copy swept
- [ ] `gate_all.py` PASS 4/4
- [ ] `git diff --stat` proves no identifier / env var / unit name / lock marker changed
- [ ] Any red suite proven pre-existing (stashed reproduction cited)
