# Test Honesty Audit — a repo-wide hunt for tests that cannot fail

Scope: `tests/test_office_*.py` and `tests/tools/*.py` (112 files).
Method: **scratch-only mutation testing** — the repo is copied into a scratch
worktree, one *behaviour-changing* edit is applied to the source a test claims to
protect, the named test is run, and the copy is restored from the REAL tree
(read-only) after every run. Nothing in `tests/` or any source file under the
real worktree was touched; verified by `git status` + per-file `git diff` (all
mutated files UNCHANGED vs `17da98198`).

Harness: `/home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch/honesty/`
(`mutate.py`, `run_batch.py`, `set_[a-e].py`, `FINAL_results.json`).

The rule this audit enforces, from the repo's own AGENTS.md / skills:

> if an assertion would go RED when the bug is FIXED, it is measuring the bug,
> not the code. — and — Never read source code in tests.

---

## 1. Headline numbers

| measure | count |
|---|---|
| tests sampled by mutation (high-level invariant tests) | **16** |
| genuinely falsifiable (mutation turned them RED) | **14** |
| **decoration — stayed GREEN under a behaviour-breaking mutation** | **2** |
| inconclusive survivors (mutations that were inert, see §3) | 3 |
| change-detector / frozen-inventory assertions found | **7 named sites** |
| source-text assertion tests (office + tools) | **85** (judgements in §4) |

**14 of 16 sampled invariant tests are real.** That is the honest headline: this
suite is *mostly* honest. The two proven decorations are named below with the
exact mutation that failed to turn them red.

---

## 2. The decoration tests (proven to stay GREEN when the behaviour they name is broken)

### DEC-1 — `tests/test_office_tower_plugin.py::test_the_stalled_state_is_surfaced_not_hidden`

Docstring claims: *"A dead turn is visible as a fact rather than drawn as work."*
Assertion (line 415): `assert "stalled" in src and "STALLED" in src` — string
presence in the renderer source, not that a stall reaches the user.

Mutations (both make the stalled state unable to surface), result **GREEN**:

| mutation | this test | sibling behaviour test on the same mutation |
|---|---|---|
| `if (cs.stalled) mark = "!";` → `if (false && cs.stalled) mark = "!";` (dead `!` marker) | **GREEN** | `test_scene_a_stalled_agent_is_marked_and_a_running_one_is_not` → **RED** |
| `picked.stalled` → `false && picked.stalled` (dead inspector STALLED line) | **GREEN** | `test_p5_inspector_renders_from_the_selected_character` → **RED** |

It passes because the literal words "stalled"/"STALLED" survive elsewhere in the
file. A comment containing the word satisfies it. **It cannot fail on the
behaviour it documents.** The behaviour is genuinely covered — by the two sibling
tests above. This test is redundant-to-the-point-of-decorative.

### DEC-2 — `tests/test_office_control_bar.py::test_the_rule_and_the_refusals_are_stated_for_the_next_reader`

Docstring claims the source records *"the six refusals"* so a future editor
cannot mistake them. Assertion (line 164–171): `"C4" in src`, `"C7" in src`,
`"28-HUD-PANELS.md" in src`, and each of `publish/edit/delete/hire/approve/speed`
appears **anywhere** in the file.

Mutation: add a real network write to the module body —
`function _evil(){return fetch('/api/publish',{method:'POST'});}` — i.e. make the
control bar actually publish, the exact act the module is documented to refuse.
Result:

| this test | sibling behaviour test on the same mutation |
|---|---|
| **GREEN** | `test_the_module_never_fetches_anything` → **RED** |

The refusals are only ever checked as **words in the file**. The module could
publish, delete, and hire while every refusal word still appears in a comment.
It is a documentation-presence check wearing a behaviour test's name.

### Nearest miss (kept out of the decoration count, but flag it)

`tests/test_office_tower_plugin.py::test_the_rule_is_stated_in_the_renderer_for_the_next_reader`
(line 335) is the same shape: `assert "C7" in src` and *"no event" / "office_event"*
appears. Mutating the renderer's C7 comment left it **GREEN**, exactly as
expected. Unlike DEC-1/DEC-2 it makes no behavioural claim and no sibling covers
the same ground, so it is honestly labelled as "stated for the next reader" — a
**documentation assertion**, not a behaviour test. Counted as decoration-adjacent,
not as a defect.

---

## 3. Inconclusive survivors (NOT counted as decorations — recorded so the count is honest)

Three early mutations stayed green but are **inert**, not proof of decoration:

- `motion.js _manhattan → return 0` (breaks the A* heuristic). Survivor; but
  *inert*: dropping the heuristic to 0 makes the search uniform-cost (Dijkstra),
  which still returns a shortest path, so `test_a_route_around_a_wall_is_no_longer_than_the_shortest_one`
  correctly stayed green. The test is fine; my mutation did not break the
  property it pins.
- `motion.js interpolate`: removing `if (t > 1) t = 1` left
  `test_interpolate_is_a_pure_function_and_clamps` green — the surviving
  `done = t >= 1` still lands the endpoint for a single-leg path. The mutation
  did not exercise the clamp it targeted.
- `tower.py MOTION += ("idle_blink",)`. Survivor, but inert: no character ever
  carries that animation string, so nothing observable changed.

Each was re-probed or discarded rather than reported as a decoration. **They are
not in the 2.**

---

## 4. The falsifiable 14 (behaviour mutations that correctly went RED)

Every one of these turned RED under a real bug injection — the suite's
high-level invariants are load-bearing. Sample of the exact mutations:

| test | mutation that turned it RED |
|---|---|
| `test_office_hud_panels.py::test_position_progress_resolves_when_a_character_is_mid_walk` | re-introduce the original N11 drop: `state()` no longer copies `position["progress"]` |
| `test_office_motion.py::test_dir_follows_the_same_rule_as_the_backend` | `tower.py _facing`: `abs(dx) >= abs(dy)` → `>` (flips the tie rule) |
| `test_office_motion.py::test_the_stations_are_the_backends_own_map` | `tower.py STATIONS["vault"] = (4,1)` → `(9,9)` |
| `test_office_motion.py::test_the_module_has_no_timer_of_any_kind` | inject `setInterval(...)` into `motion.js` |
| `test_office_work_loop.py::test_only_the_assigned_reviewer_may_accept` | disable the C31 reviewer-authority check in `work.review()` |
| `test_office_work_loop.py::test_a_rejection_without_an_instruction_is_refused` | disable the "reject needs a fix note" guard |
| `test_office_work_loop.py::test_a_task_without_an_exit_check_cannot_be_created` | drop the C17 exit-check guard in `work.create()` |
| `test_office_tower_no_phantom_motion.py::test_the_newest_event_wins` | disable the newest-`ts`-wins compare in `activity_from_events()` |
| `test_office_tower_no_phantom_motion.py::test_with_no_new_events_every_character_is_idle` | animate a stale row anyway (`_animation_for(kind)` with no `fresh` gate) |
| `test_office_tower_no_phantom_motion.py::test_a_killed_turn_stops_animating_and_reports_itself_stalled` | force `"stalled": False` |
| `test_office_tower_no_phantom_motion.py::test_an_org_with_no_rows_renders_empty_never_a_demo_office` | `empty = False` (always draw a populated office) |
| `test_office_determinism.py::test_the_renderer_source_has_no_clock_and_no_randomness` | inject `new Date().getTime()` into the renderer |
| `test_office_tower_plugin.py::test_the_only_timer_is_the_poll` | inject a 2nd `setInterval` into `qubit-office.js` |
| `test_office_no_phantom_authority.py::test_no_unlisted_or_true_tautology` | inject `assert 1 == 2 or True` into an office test file |

The last row matters: the repo already ships a **meta-audit**
(`test_office_no_phantom_authority.py`) that pins the three families of
cannot-fail checks (self-comparison, evidence-erasing filters, assertions weaker
than their message), keeps a `KNOWN` allowlist, records four **fixed** defects in
`FIXED_DEFECTS`, and proves its own detectors can fail. I re-proved it: it catches
a freshly injected `or True`, a non-strict `xfail`, and a bare `assert <module> is
not None`, all RED. It is not decoration.

---

## 5. Change-detectors / frozen-inventory assertions

These freeze a value that is **expected to change** — a count of the product's
own declared surface. They tax every future addition and catch nothing about
behaviour. Proven by adding a *legitimate* new member and watching them go RED
with no behavioural regression:

| test | frozen assertion | proof |
|---|---|---|
| `test_office_hud_panels.py::test_the_spec_file_lists_the_expected_paths` | `assert len(paths) == 87` | add one payload path to `hud-panels.json` → **RED** |
| `test_office_consent.py::test_hardline_list_is_short_and_readable` | `assert len(out["hardline"]) == 6` | add a 7th hardline rule to `consent.HARDLINE` → **RED** |
| `test_office_cli.py::test_consent_and_hardline_from_the_cli` | `assert len(out["hardline"]) == 6` | same mutation → **RED** (2nd copy of the same freeze) |
| `test_office_consent.py:157` / `test_office_cli.py:337` | `tiers == ["hardline","bypass","grant","resolve"]` | freezes the tier list literal |
| `test_office_control_bar.py::test_there_are_exactly_four_buttons` | `== 4` | freezes the button inventory size |
| `test_office_control_bar.py::test_the_four_buttons_are_the_specified_ones` | frozen id/label list | freezes the button catalogue |
| `test_office_cli_completeness.py::test_doc_matches_the_parser` | `f"{len(VERBS)} subcommands" in doc` | freezes the verb count *inside a doc string* |

Note the `hardline == 6` freeze exists in **two** tests (unit + CLI) — a single
legitimate rule addition costs two red tests.

**Legitimate frozen values that look like change-detectors but are not** (kept,
do not touch):

- `test_office_tower_stream.py::test_fingerprint_is_a_hex_digest_not_a_python_hash`
  — `len(fp) == 32` is a *contract* of a sha256 hex digest, not an inventory.
- `tests/tools/test_schema_sanitizer.py` — the many
  `assert out == {..exact JSON..}` are *input→output* contract checks on a pure
  sanitiser, the strongest form of assertion, not change-detectors.
- `test_office_work_loop.py:301` — `assert len(out["results"]) == 2, "one turn
  spent, then refused"` is a *behavioural multiplicity* for a fixed scenario, not
  a catalog count.
- `test_office_plugin_wiring.py::...` — `len(values) == 1` asserts the three
  fleet declarations AGREE; it is an invariant, not an inventory.

---

## 6. Source-text assertions — keep/drop judgement

**The rule is not "any `read_text()` is banned."** The rule is: an assertion must
be capable of failing, and must assert *behaviour*, not source *shape* — with one
carve-out the repo itself states: asserting on a **generated/contract artifact**
(file text that *is* the product) is a real contract. 85 tests in scope read a
product source file and assert a substring. Judgement, in four groups:

**KEEP — the property is genuinely a text property (a comment/name cannot be
checked behaviourally).** Each was proven falsifiable by injecting the exact
violation; every one went RED:

- No-timer rules: `test_the_module_has_no_timer_of_any_kind`,
  `test_the_only_timer_is_the_poll`, `test_office_control_bar.py::test_the_module_owns_no_timer`,
  `test_office_game_packaging.py::test_the_host_does_not_own_a_timer`.
  Injecting `setInterval` → RED. (A behavioural timer check needs a browser and
  still misses the timer a future commit adds.)
- No-network/no-write rules (C4): `test_the_module_issues_no_mutating_request`,
  `test_the_module_never_fetches_anything`, `test_the_tower_issues_no_mutating_request`.
  Injecting a real `fetch(...POST)` → RED.
- No-randomness/no-clock: `test_the_renderer_source_has_no_clock_and_no_randomness`.
  Injecting `new Date()`/`Math.random()` → RED.

**KEEP — asserting on a GENERATED artifact (the text *is* the contract).**
`test_office_bundle.py`, `test_office_vendor_habblaud.py`,
`test_office_vendor_stream_rewrite.py`, the vendor-manifest checks: the vendored
bundle is the shipped product, so a grep for a forbidden token in it is a real
contract, and rebuilding is the only way it changes.

**DROP / DEMOTE — presence-of-word checks with no behaviour behind them.**
- `test_office_tower_plugin.py::test_the_stalled_state_is_surfaced_not_hidden` (DEC-1).
- `test_office_control_bar.py::test_the_rule_and_the_refusals_are_stated_for_the_next_reader` (DEC-2).
- `test_office_tower_plugin.py::test_the_rule_is_stated_in_the_renderer_for_the_next_reader` (documentation only).
  These three assert that *a word exists in the file*. They belong in a doc-lint,
  not beside behavioural tests. Their claims are already covered by the real
  tests that went RED in §2.

**KEEP, with a caveat — naming/provenance/attribution checks.**
`test_office_sprites.py::test_the_module_names_its_upstream_sources_and_their_licence`
and friends assert required attribution strings exist. These serve a licence
obligation, not a behaviour; keep, but recognise they cannot fail on a code bug.

---

## 7. What to do with this audit

1. **Delete or demote DEC-1 and DEC-2** (or move to a `test_office_docs.py`
   lint that does not pretend to be a behaviour test). The behaviour they
   "protect" is already protected by tests that actually go RED.
2. **Relax the change-detectors** in §5 to `>=` / range checks (an inventory may
   only grow), or delete the duplicated copy (`hardline == 6` appears twice), so a
   legitimate addition costs zero tests instead of two.
3. **Keep the §6 KEEP groups** — they are the reason the timer-free / no-write /
   no-clock invariants survive future edits. Every one was proven able to fail.
4. Re-run this harness on any file before trusting a new green: a test that has
   never been shown to fail is a claim, not evidence.

*All mutations lived only in the scratch worktree; the real tree at `17da98198`
is byte-identical in every file this audit read (git-verified).*
