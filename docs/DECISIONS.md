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
   `43faacfb8de7b29548547a7fbdedad0cc6e7391c` (2026-10-16). Ports are literal 1:1 against that
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

