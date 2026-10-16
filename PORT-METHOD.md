# PORT-METHOD.md — porting upstream hermes-agent's absent production files into `qubit_runtime/`

Audience: a sibling lane porting one chunk of the 299 absent production `.py`
files (`/tmp/prod-files.txt`) plus their companion data/READMEs.

This is the method, not a log. Follow it verbatim; the worked example is
`REPORT-port-5.md`.

---

## 0. Fixed inputs

| Thing | Value |
|---|---|
| Our repo / worktree | your own `git worktree`, branch `port/chunk-N` |
| Upstream clone (READ-ONLY, never modify) | `/home/trick/upstream/hermes-agent` |
| Upstream ref to read | `origin/main` (HEAD **moves** — re-read it every time) |
| Absent-file list | `/tmp/prod-files.txt` (upstream-root-relative) |
| Interpreter | `/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python` (**absolute**) |
| System `python3` | has **no** pytest — never use it for proof |

Get the current upstream HEAD before you start and record it in your report:

```sh
cd /home/trick/upstream/hermes-agent && git rev-parse origin/main
```

My chunk was ported at `53bec2a311979ad6f77580ba491eca261ab988e0` (2026-10-16).

---

## 1. Where a ported file goes

**Mapping rule: upstream root == `qubit_runtime/`.**

```
upstream  gateway/foo.py      ->  qubit_runtime/gateway/foo.py
upstream  evals/bar/baz.py    ->  qubit_runtime/evals/bar/baz.py
upstream  hermes_state.py     ->  qubit_runtime/<renamed>.py   # see §3
```

Verify once against a file we already have, then trust it:

```sh
# upstream file vs our file — sizes should be near-identical modulo rebrand
cd /home/trick/upstream/hermes-agent && ls -l gateway/run.py          # 331139
ls -l /tmp/port-5/qubit_runtime/gateway/run.py                        # 329378
```

Do **not** re-derive the mapping per file. It is `qubit_runtime/` + the upstream
path, with module renames applied (§3).

---

## 2. Read the file at upstream HEAD (never from the working tree, never from the pin)

```sh
cd /home/trick/upstream/hermes-agent
git show origin/main:<path>              # full content
git show origin/main:<path> | wc -l      # size
git show origin/main:<path> | grep -nE '^(import |from |def |class )'   # surface + imports
```

For a big file (hundreds of lines) grep the import/def surface first; only dump
the whole thing when the surface is not enough.

---

## 3. The brand rule (the trap — read before writing any byte)

Authoritative background: `docs/REBRAND-SWEEP-PLAN.md`. Also read
`scripts/qubit_rebrand.py` — it holds the **canonical rename maps** and is the
source of truth, not your intuition.

**Core principle: a rename has two halves; migrating one breaks things.** For a
*newly ported* file there is no legacy compatibility to preserve, so port it with
the **canonical** spelling directly — then grep the whole tree for anyone already
using the other spelling and reconcile.

### 3a. Module renames (apply these; they are mechanical)

From `scripts/qubit_rebrand.py` `MODULE_TOKENS` (longest-first):

| upstream module | ours |
|---|---|
| `hermes_cli` | `qubit_cli` |
| `hermes_state` | `qubit_state` |
| `hermes_state_*` (`_keys`, `_sessions`, `_search`, …) | `qubit_state_*` |
| `hermes_bootstrap` / `hermes_constants` / `hermes_logging` / `hermes_platform` / `hermes_startup_watchdog` | `qubit_*` |
| `hermes_hermes`/`qubit_hermes` (repo pkg) | `qubit_runtime` |

So `from hermes_state import SessionDB` -> `from qubit_state import SessionDB`;
`python -m hermes_cli.main` -> `python -m qubit_cli.main`.

### 3b. Env-var renames

`HERMES_HOME` -> `QUBIT_HOME` is **the** one that matters. Proof it is the
canonical target (run this, don't take my word):

```sh
grep -n 'def get_process_qubit_home' -A 10 /tmp/port-5/qubit_runtime/qubit_constants.py
# -> reads os.environ.get("QUBIT_HOME") ONLY. No HERMES_HOME read.
grep -rln 'QUBIT_HOME' --include=*.py /tmp/port-5/qubit_runtime/evals/ | wc -l   # 102
```

`scripts/qubit_rebrand.py` `ENV_TOKENS = {"HERMES_HOME": "QUBIT_HOME"}`; the only
legacy reader is the migration adapter `voicebridge/qubit_legacy.py`. So a ported
file **writes and reads `QUBIT_HOME`**.

### 3c. Filesystem-path renames

Default home `~/.hermes` -> `~/.qubit` (see `get_process_qubit_home`'s platform
default). `Path.home() / ".hermes"` -> `Path.home() / ".qubit"`, and a bare
`.hermes` dir literal in a fixture -> `.qubit`. Match whatever the *sibling* code
in the same directory already does.

### 3d. What is NOT brand — do NOT touch

- The frozen pin `69948c0057…` and every `scripts/` file naming it
  (deny-list in the sweep tool). It is an upstream provenance anchor.
- `~/.cache/qubit-pins/hermes-agent` — provenance path.
- `hermes_state_keys` **the identifier** in upstream source (until you apply the
  mechanical module rename of §3a) — it is a real module name, not decoration.
- Wire/service ids like `hermes-kanban-*`, `@hermes/ink`, and serialized JSON
  keys such as `"hermes_home"` (the sweep's `_BARE_HOME_RE` deliberately
  preserves `"hermes_home"`).
- A **CLI binary named `hermes`** in a fixture. Some in-tree `.ts` fixtures still
  name the shim `hermes`; if a ported file is a *new* file, use the surrounding
  file's convention and note it. (My chunk had one such line:
  `session_readers_cli_ab.py` invokes `qubit_cli.main`, the module — no `/hermes`
  binary — so no ambiguity arose.)

**Do not blanket-rename.** Classify first. When in doubt, grep for the name in
the *same directory's siblings* and copy their spelling.

---

## 4. Detect a shim before you spend effort

Upstream performed a large "facade + siblings" split. Many "new" files are tiny
re-export stubs. A shim whose underlying module **we do not have** is useless on
its own.

Signals:
- Under ~20 lines, docstring + one `from X import Y` + `__all__`.
- The imported `X` is another file in `/tmp/prod-files.txt`, or maps to a module
  that does not exist in `qubit_runtime/`.

Worked example (real, from the 299-list):

```sh
cd /home/trick/upstream/hermes-agent && git show origin/main:gateway/session_admission.py
```

```python
"""Canonical payload identity ..."""
from hermes_state_keys import admission_fingerprint
__all__ = ['admission_fingerprint']
```

8 lines. Its dependency `hermes_state_keys.py` is **itself absent** (line 242 of
`/tmp/prod-files.txt`), and `qubit_runtime/qubit_state_keys.py` does not exist —
`grep -rn admission_fingerprint qubit_runtime/` returns nothing.

**Verdict: `shim-skipped`, reason `dependency-absent`, dependency named
`hermes_state_keys.py` (in the 299-list).** Record it — that is how lanes get
ordered. Do not port a stub that cannot resolve; a file that imports is worth ten
that do not.

---

## 5. Resolve imports (do this for EVERY file, before porting it)

For each `import X` / `from X import Y` in the file:

1. Apply the §3a module rename to get our module path.
2. Check it exists:

```sh
test -e /tmp/port-5/qubit_runtime/<mapped>.py && echo EXISTS || echo MISSING
```

3. If MISSING, check whether it is another of the 299:

```sh
grep -n '<upstream-module>.py' /tmp/prod-files.txt
```

- Present in the 299 -> record it as `dependencies-missing` and **name it** in
  your report; the port is incomplete without it, but port your file anyway if
  its own top-level import of that dep is *inside a function* (deferred), because
  the module will still import and the dep is orderable. If the import is
  **top-level**, the module cannot import until the dep lands — say so plainly.
- Not in the 299 and not on disk -> genuine gap; escalate, do not invent.

Companion **data files** are dependencies too. `verify_receipt.py` defaults
`--manifest` to `cases.json` `Path(__file__).with_name('cases.json')` — a runtime
dependency that is *not* in the `.py` list. Port the data file with the code, or
the tool cannot run. Same for a `README.md` that documents the harness.

---

## 6. Prove the import

The mapped path means upstream root == `qubit_runtime/`, so `PYTHONPATH` is
`qubit_runtime/` and modules import under their mapped dotted path.

```sh
PY=/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python
cd /tmp/port-5/qubit_runtime
PYTHONPATH=/tmp/port-5/qubit_runtime "$PY" -c 'import <dotted.path>'
```

Two honest failure modes:

- **Dependencies absent** -> report it, do not paper over it. A clean import of
  three files beats ten that raise `ModuleNotFoundError`.
- **Top-level script** -> a file with no `if __name__ == "__main__"` that runs
  `argparse` at import time will *exit on import* (it parses `sys.argv`). That is
  a property of the file, not a port defect. Prove it two other ways instead:

```sh
"$PY" -m py_compile evals/shared_gateway/session_readers_cli_ab.py          # compiles
PYTHONPATH=/tmp/port-5/qubit_runtime "$PY" evals/.../session_readers_cli_ab.py --help   # runs
```

Go further when you can: **exercise the code**. My chunk ran
`verify_receipt.py` PASS against the ported `cases.json` (47 cases) and FAIL on a
receipt with one case removed — that proves the companion data resolves *and*
that the checker still discriminates.

---

## 7. Commit — small, attributable, named

Repo rule (AGENTS.md): commit each coherent unit; never end a turn holding
uncommitted work; `wip(...)` if unfinished.

```sh
cd /tmp/port-5
git add <files>
git commit -q -m "port(<area>): chunk-N from upstream origin/main <SHORT_SHA>

Upstream paths (all absent from our tree, 299-file delta):
  <upstream/path.py> -> qubit_runtime/<mapped>.py
  ...

Brand: HERMES_HOME -> QUBIT_HOME; ~/.hermes -> ~/.qubit; hermes_state ->
qubit_state; hermes_cli.main -> qubit_cli.main. <verbatim files named> left
verbatim (zero brand tokens)."
```

Commit code and companion data separately when they are separable
(`port(evals): chunk-5 …` then `port(evals): chunk-5 companion data + READMEs`).
Attribution of the upstream commit is in the message body, not the subject.

---

## 8. First moves for a new lane (copy-paste)

```sh
# 0. inputs
UP=/home/trick/upstream/hermes-agent
PY=/home/trick/Projects/Repos/qubit-voicebridge/.venv/bin/python
WT=<your worktree>
cd "$UP" && git rev-parse origin/main           # record it

# 1. per file in your chunk
F=gateway/foo.py
git show origin/main:"$F" | wc -l
git show origin/main:"$F" | grep -nE '^(import |from |def |class )'
#   -> shim? (§4)   -> deps missing? (§5)   -> port with §3 renames

# 2. prove
cd "$WT/qubit_runtime" && PYTHONPATH="$WT/qubit_runtime" "$PY" -c 'import <dotted>'

# 3. commit (§7) and record a row in PORTED.json
```

`PORTED.json` row shape:

```json
{"path": "qubit_runtime/evals/shared_gateway/verify_receipt.py",
 "upstream_path": "evals/shared_gateway/verify_receipt.py",
 "upstream_lines": 63, "status": "ported",
 "dependencies_missing": [], "imports_clean": true}
```

`status` ∈ `ported` | `shim-skipped` | `dep-missing`. Keep the upstream path so a
later audit can re-diff against the moving `origin/main`.

---

## 9. Obstacles this method names honestly (learned on chunk-5)

1. **The rename has two halves; only one is obvious.** `HERMES_HOME` still
   appears in ~30 production files as prose, docstrings, or a *legacy* read
   (`qubit_cli/update_lock.py:100` reads `HERMES_HOME **or**` a default). Porting
   with `QUBIT_HOME` is correct, but grep the tree before claiming the name is
   unified anywhere — a lane that renames a subset creates a fourth
   inconsistency (see `docs/REBRAND-SWEEP-PLAN.md`, 152 dual-spelled symbols).
2. **`hermes_state_keys` is a module name, not branding.** The task's own
   example calls it provenance; it also appears in the 299-list as a real absent
   module. Keep identifier vs decoration straight: rename it mechanically per
   §3a, do not "clean" it as prose.
3. **A shim's dependency can itself be in the 299-list.** `gateway/
   session_admission.py` is only useful once `hermes_state_keys.py` lands. The
   right output is a named dependency and an ordering, never a ported stub that
   imports nothing.
4. **Companion data files are invisible dependencies.** The `.py` list does not
   contain `cases.json`; without it `verify_receipt.py` cannot run. Port data
   with code.
5. **Top-level-script files cannot be "import-proved".** `session_readers_cli_ab.py`
   parses args at import. Prove with `py_compile` + `--help` + (where possible) a
   real execution path instead of a misleading `import` failure.
