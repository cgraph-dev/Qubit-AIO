# Owner amendment 2026-09-19 — deep source rebrand execution scope

Status: additive execution-scope amendment. It does not edit, weaken, reorder,
or reinterpret any frozen acceptance contract, checklist, execution directive,
or prior owner amendment. All product, parity, safety, licensing, and
completion requirements remain in force. It authorizes the **deep source-level
rebrand** the owner directed for the shipped runtime and defines exactly how
upstream names are classified.

## Scope authorized

The shipped Qubit runtime is a Qubit-owned, source-level rebrand/adaptation of
the pinned upstream implementation. The owner directs that the shipped runtime
carry Qubit identity at every layer:

1. **Package / module namespace** — `qubit_hermes/` -> `qubit_runtime/`;
   top-level `hermes_*` modules -> `qubit_*`; Qubit-owned
   `voicebridge/hermes_*.py` -> `voicebridge/qubit_*.py`.
2. **Runtime home** — `HERMES_HOME` -> `QUBIT_HOME`, with a narrow legacy
   migration adapter (`voicebridge/qubit_legacy.py`) that reads the old name
   only to migrate; the old name never becomes the active identity and the
   external `~/.hermes` is never adopted.
3. **Content** — user-facing upstream branding in comments, docstrings,
   log/error/prompt strings and UI strings is rewritten to Qubit/Qubit Labs
   (`scripts/qubit_debrand.py`, classified, word-boundary only).
4. **Frontend** — `frontend/hermes-{web,shared,qubit,dist}` ->
   `frontend/qubit-{web,shared,app,dist}`; the `@hermes/shared` workspace alias
   -> `@qubit/shared`; Vite/TypeScript/package/import/build configuration
   updated; served bundle rebuilt.
5. **Packaging / platform** — Docker, Nix, PowerShell, launcher, setup,
   gitignore, pyproject force-include/artifacts and `/opt/hermes`-style shipped
   runtime paths replaced with Qubit paths.
6. **Entry points** — `qubit-agent` is the primary vendored-runtime launcher;
   the old `qubit-hermes` console name is retained only as a hidden
   compatibility alias. `qubit` remains the canonical product launcher.

## Classification rules (no blind replacement)

Every upstream-name occurrence is classified into exactly one of:

* **internal identifier** — renamed when the rename is self-consistent and not
  wire-visible (package/module/namespace tokens applied by
  `scripts/qubit_rebrand.py`);
* **user-facing prose** — renamed to Qubit/Qubit Labs
  (`scripts/qubit_debrand.py`); identifiers that merely contain the brand word
  (`get_hermes_home`, `synchronous`) are never touched;
* **external wire identifier** — preserved verbatim and, where a caller needs
  it, referenced through an explicit Qubit constant/adapter. This includes
  `__HERMES_BASE_PATH__`, `__HERMES_SESSION_TOKEN__`,
  `__HERMES_AUTH_REQUIRED__`, `__HERMES_PLUGINS__`, `__HERMES_PLUGIN_SDK__`,
  `X-Hermes-Session-Token`, `hermes_flow`, `hermes.tokenReloadAttempted`,
  `hermes-kanban-*`, `hermes-achievements`, `HERMES_QWEN_*`, dotted metric /
  event / object-type / logger names and hyphenated plugin/service ids;
* **legal / provenance / comparison** — preserved verbatim: `LICENSE`,
  `THIRD_PARTY_NOTICES.md`, `voicebridge/upstream/`,
  `docs/QUBIT_HERMES_VENDOR_MANIFEST.json`, `docs/HERMES_SOURCE_COMPARISON*`,
  `docs/QUBIT_SOURCE_INVENTORY.json`, `docs/INVENTORY_*`,
  `docs/QUBIT_GAP_LEDGER*`, the pinned pristine upstream trees, and copyright /
  attribution lines.

The **internal vendored fork modules that are not wire-visible and are consumed
by the verification suite** (`qubit_runtime` runtime internals, e.g.
`get_hermes_home` and its call graph) are preserved in this wave as internal
vendor source; renaming that module graph is a separate, independently
verifiable migration because the byte-provenance manifest and the Qubit guard
suite both key on the current names. This is recorded, not hidden: the
classification is in `docs/QUBIT_DEBRAND_MANIFEST.json` and the rebrand
manifest.

## Provenance model

This amendment builds on
`docs/QUBIT_OWNER_AMENDMENT_2026-09-18_SOURCE_REBRAND_PROVENANCE.md`:

* pristine upstream baselines stay byte-identical and read-only;
* the shipped Qubit runtime is **not** claimed byte-identical to upstream; it
  carries an explicit, owned patch/rename manifest;
* every renamed path is recorded in `docs/QUBIT_REBRAND_MANIFEST.json` so a
  future upstream upgrade can map old -> new deterministically;
* no acceptance criterion, safety policy, terminal status, or product
  requirement is changed.

## Preservation and safety

* Recoverable pre-rename snapshot:
  `/tmp/qubit-pre-rebrand-backup-20260919-184342.tar.gz`.
* Recoverable pre-content snapshot (this wave):
  `/tmp/qubit-deep-rebrand-precontent-20260919-192329.tar.gz`.
* Pristine upstream trees are untouched.
* No commit or push is performed before independent verification passes.
