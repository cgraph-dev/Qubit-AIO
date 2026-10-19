# Qubit BRAND — the zero-legacy-brand rule, its exceptions, and how to rename

**Status:** canonical brand policy. Not `VERIFIED`, not a release verdict.

This is the authority `AGENTS.md` points at for "zero legacy brand on product
surfaces, with legal attribution the required exception." Everything below is
derived from three things that exist: the owner rule already in force, the
measured state of the tree (`docs/REBRAND_INVENTORY.md`,
`docs/HERMES_RESIDUE_INVENTORY.md`), and the resolver code in
`qubit_runtime/qubit_constants.py`. Nothing here is aspirational policy.

---

## 1. The rule

**A user of Qubit must never see the pre-rebrand name on a surface they can
read or operate.**

"Product surface" means anything a user reads, hears, clicks, or types:
runtime locale strings, CLI/TUI output, the dashboard and desktop UI, docs
shipped to users, skill prose, error messages, and the names of Qubit-owned
home directories and files the user is pointed at.

Corollary: the rule is about **what ships to a user**, not about identifiers.
Internal names are not user-facing just because they contain the old word.

## 2. What a rename must never touch

Each of these is load-bearing. Renaming one breaks a running system, and every
one of them has been mistaken for "brand" at least once. They are recorded in
`docs/REBRAND_INVENTORY.md` §2B/§2C, which stays the per-line inventory; this
section is the summary of *reasons*.

| Anchor | Why it stays |
|---|---|
| **`HERMES_*` environment variables** (~745 distinct, ~8035 occurrences) | Set by a parent process, read by a child, or crossed over a socket. Renaming the reader without the writer is precisely the bug class this document exists to prevent. Deliberately not renamed. |
| **`_LEGACY_HOME_DIR = ".hermes"`** (`qubit_runtime/qubit_constants.py`) | The **read-old shim**. The resolver prefers `QUBIT_HOME`, then the platform default, trying `.qubit` before `.hermes`; the legacy name is accepted so a pre-rename install is never orphaned. Rename it and every existing install's state and secrets are stranded at once. |
| **`~/.hermes.pre-retirement`** | The path to the retired upstream tree that `scripts/audit_capability_gaps.py` and `scripts/audit_missing_symbols.py` measure against. Rename it and the audits measure nothing. |
| **Container path `/root/.hermes`** | The Docker sandbox bind-mount target as seen *inside* the container (`file_safety.py`, `terminal_env_provider.py`). Host-side naming and image-side mount path must agree or path translation breaks for every sandboxed command. |
| **Frozen upstream pin (`69948c0057…`)** | Identifies an upstream commit. Rewriting it destroys the audit trail and the sync mechanism. |
| **Upstream attribution** (`LICENSE`, `THIRD_PARTY_NOTICES.md`, vendored headers, `github.com/NousResearch` / portal provenance URLs) | **The required exception to the rule.** Attribution is a legal obligation; the old name in an attribution line is not a brand leak. Removing it is a license defect, not a rebrand. |
| **Upstream model and repo names** | `Hermes 3 & 4` is a **model family Nous Research publishes**; `Nous Research`, `Nous Portal` name the provider who bills. A product name is not a model name. |
| **Plugin manifest `requires_hermes`**, `${HERMES_SKILL_DIR}` / `${HERMES_SESSION_ID}`, Electron IPC channels, marker/lock files, systemd unit fixtures | Contracts with things we do not own or cannot rebuild: third-party plugin `dist` bundles, skills authored in the wild, an in-flight update path written by the previous build. |

**The model-name trap (a real defect fixed on 2026-10-19).** A blind replace
turned the correct warning `"Nous Research Hermes 3 & 4 models are NOT agentic"`
into `"Qubit 3 & 4 models"` in six locale packs (`es`, `ga`, `ja`, `pt`, `zh`,
`zh-hant`). That is not a rebrand — it names a product that does not exist and
tells the user the wrong thing about a real model. Before replacing a word,
ask: **is this our brand, or is it the name of someone else's thing?** If the
latter, it stays.

## 3. What may remain, stated plainly

These are allowed and do **not** need fixing:

- The legacy home dir name **inside the resolver**, as a read-old fallback
  (`_LEGACY_HOME_DIR`, `_default_home_dir_names()` returning `.qubit` first).
  Fallbacks are not surfaces.
- Documentation and code that **names upstream provenance**: what was vendored,
  from which repo and commit, under which license.
- The plugin-SDK legacy globals `__HERMES_PLUGIN_SDK__` / `__HERMES_PLUGINS__`,
  which stay published *alongside* the canonical `__QUBIT_*` names because
  installed plugin bundles we neither own nor can rebuild read the old ones.
  This is dual-publish, not a leftover: see `§4.3`.
- Paths that point *at a user's pre-migration state* on purpose, e.g. an
  external `~/.hermes/config.yaml` a managed deployment may symlink into a
  dotfiles repo. Preserving an external home is not emitting a brand.

## 4. How to perform a rename

### 4.1 Writer and reader move together, in one commit

A brand name that crosses a boundary exists twice: once where it is **written**
and once where it is **read**. Renaming one side is not a partial rename — it
is a total outage of that feature, and it is invisible to both test suites
because neither can see the other language.

Change, in the same commit: the writer, the reader, every test assertion that
names either side, and any external verification script that asserts the old
spelling.

### 4.2 The two incidents this rule comes from

Both are real, both in this repo's history, and both are the reason §4.1 is
not negotiable.

1. **The model picker died on a half-rename.** A helper's parameter was renamed
   but its call site was not, leaving `name 'qubit_id' is not defined` at
   runtime. The diff looked correct; only a bound-name sweep found it. This is
   why `scripts/check_half_rebrand.py` exists (see its docstring, line 6).
2. **Desktop authentication broke on a split global.** The `qubit serve`
   backend writes `window.__HERMES_SESSION_TOKEN__` from
   `qubit_runtime/qubit_cli/web_server_dashboard.py`, while the SPA it serves —
   `qubit_runtime/web`, built to `qubit_cli/web_dist` and served as
   `WEB_DIST` — reads only `window.__QUBIT_SESSION_TOKEN__`. The shell's token
   extractor reads `__QUBIT_*` too. Both spellings appear in the tree, so a
   grep "finds" the name; nothing in-tree bridges them, so the token is never
   read and `/api/ws` auth fails. Reproduced by
   `tests/qubit_cli/test_web_server.py` (`TestServeIndexMissingIndex`).
   The same split affects `__HERMES_AUTH_REQUIRED__` and
   `__HERMES_BASE_PATH__`.

The lesson both incidents share: **a grep that finds the name proves nothing.**
Prove which side reads which spelling.

### 4.3 Accept-both when the reader is out of your control

When the reader is a third-party artifact you cannot rebuild (an installed
plugin `dist`, a skill authored elsewhere, a browser session that already holds
a cookie), publish **both** names, canonical first, and read-old. Correct
precedents in this tree: the plugin SDK globals with their documented alias
(`frontend/qubit-web/src/plugins/plugin-globals.test.ts`), the
`hermes-*` → `qubit-*` localStorage migration shim, and the ASGI proxy
deriving the session-header name from the *server attribute* rather than
duplicating a literal.

Never "fix" a dual-read into a single read.

### 4.4 Run the guard

```bash
python3 scripts/check_brand_half_rename.py     # exit 1 on a writer/reader split
```

It reports a global written under one brand spelling that nothing reads while
the other spelling is read. It exists because §4.2's second incident passed
every test in the repository. Red on this script is a bug; the fix is always
§4.1, never a rename on one side.

## 5. Verification status

Counts of remaining legacy strings are **not** claimed here — they drift, and
prose goes stale while the tree moves. The measured inventory is
`docs/REBRAND_INVENTORY.md`; regenerate it rather than quoting an old count.
