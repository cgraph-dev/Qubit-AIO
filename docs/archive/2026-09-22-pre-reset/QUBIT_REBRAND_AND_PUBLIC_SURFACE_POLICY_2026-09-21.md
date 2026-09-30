# Qubit rebrand and public-surface policy

Date: 2026-09-21

Status: **Authoritative Qubit policy.** This document states the binding
rebrand requirement, the public-surface vs internal/wire-identifier
distinction, the fail-closed allowlist mechanism, the hard route rules, and the
deep-rebrand mapping. It is policy, not evidence: statements here are
requirements and recorded facts, not independent verification. Where a fact is
not confirmed it is marked UNKNOWN or NOT VERIFIED. Nothing in this document is
claimed VERIFIED, 100%, complete, or 1:1.

Related documents (siblings in this policy set):

- [QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](./QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
- [QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](./QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md)
- [QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md](./QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md)
- [QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md](./QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md)
- [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](./QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md)
- [QUBIT_DOCUMENTATION_INDEX_2026-09-21.md](./QUBIT_DOCUMENTATION_INDEX_2026-09-21.md)

Governing records referenced by this policy:

- `docs/archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-19_DEEP_REBRAND.md`
- `docs/archive/2026-09-21/QUBIT_REBRAND_MANIFEST.json`
- `docs/archive/2026-09-21/QUBIT_DEBRAND_MANIFEST.json`
- `docs/archive/2026-09-21/QUBIT_DEBRAND_CLASSIFICATION.json`
- `docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md`
- `docs/archive/2026-09-21/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md`

## 1. Complete rebrand requirement

### 1.1 Public identity

The public product identity is **Qubit** / **Qubit Labs**. The public identity
strings used for the product are **"Qubit Agent"** and **"Qubit
VoiceBridge"**. Qubit is the name the product presents to users at every layer.

### 1.2 Prohibited names

The names **Hermes**, **Nous**, and **OpenClaw** must not appear as user-facing
product names. They must not be presented as the name of the product, the
assistant, a route prefix, a URL, a page title, a navigation label, a CLI
command, help text, an installer/service/log/report label, or a dashboard path.

### 1.3 Where prohibited names must not appear

User-facing output must be free of the prohibited names in all of the
following, without exception:

- UI (served SPA shell and rendered application surface);
- CLI help and command text;
- runtime banners;
- logs;
- errors and error messages;
- voice responses;
- package metadata (including distribution name, description, and author);
- desktop metadata (Electron `productName`, `executableName`, `artifactName`,
  `appId`, window/application titles);
- shipped bundles (built frontend bundles and packaged runtime artifacts).

### 1.4 Permitted retention of old identifiers

Old identifiers (Hermes / Nous / OpenClaw tokens) may remain **only** for one
or more of the following reasons:

- **wire compatibility** — a token that an external client or peer already
  sends or reads and that cannot change without breaking the protocol;
- **migration** — a legacy input read once to migrate state, after which the
  new identity is active;
- **legal** — license, attribution, and copyright notices;
- **provenance** — vendor/source-comparison manifests and pinned upstream
  trees;
- **upstream comparison** — deterministic mapping between pinned upstream and
  the Qubit tree.

### 1.5 Compatibility leak rule

Compatibility identifiers must **never** leak into normal user-facing output.
A wire token that is technically required internally is still a violation if it
is rendered to a user, printed in help, echoed in a banner, surfaced in an
error, or spoken in a voice response. Retention is permitted at the wire,
migration, legal, provenance, and comparison boundaries only; it is not
permitted on a public surface.

### 1.6 Upstream update gate

Every future upstream update must pass a **public branding scan** before it is
accepted into the Qubit tree. An upstream update that reintroduces a prohibited
name on a public surface fails the gate. The scan is the mechanism defined in
sections 3 and 4; the allowlist is the only permitted exemption.

## 2. Public surfaces vs internal/wire identifiers

### 2.1 Public surfaces (must be clean)

Public surfaces are everything a user can see, read, or hear through normal
product use. This includes, at minimum:

- the route table served by the web UI and the SPA shell it returns, including
  the `href`/`src`/`action` URLs it points at;
- the built frontend bundle served to the browser;
- CLI help, command names, and terminal output;
- runtime banners, logs, and errors;
- voice responses;
- desktop/Electron user-visible metadata and strings;
- package metadata and desktop metadata shipped to users;
- user-facing documentation.

These surfaces must contain no prohibited name except where an explicit
allowlist entry applies (section 4).

### 2.2 Internal / wire identifiers (allowed when technically required)

Internal and wire identifiers are not product names. They are permitted to
retain upstream tokens when renaming them would break a client, a migration, a
legal notice, or a provenance comparison. The preserved wire identifiers
recorded in the debrand manifest are:

- `__HERMES_BASE_PATH__`
- `__HERMES_SESSION_TOKEN__`
- `X-Hermes-Session-Token`
- `hermes_kanban`
- `HERMES_QWEN_`
- `qubit-hermes`

The debrand manifest additionally records `__HERMES_AUTH_REQUIRED__`,
`__HERMES_PLUGINS__`, `__HERMES_PLUGIN_SDK__`, `hermes_flow`,
`hermes.tokenReloadAttempted`, `hermes-flow`, `hermes_achievements`,
`hermes-achievements`, and the `refs/hermes` checkpoint ref namespace as
preserved internal/wire identifiers. These must be referenced through explicit
Qubit constants or adapters, never rendered to users.

## 3. Allowlist mechanism (fail-closed)

The allowlist is an explicit, reviewed file:

`docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md`

### 3.1 Public-surface rule set recorded in the allowlist

The allowlist records these tokens as the allowlisted occurrences removed from
the scanned text before the upstream-name check:

| Token | Reason recorded |
| --- | --- |
| `__HERMES_BASE_PATH__` | Internal compat identifier the vendored frontend reads; server injects `""`. |
| `__HERMES_SESSION_TOKEN__` | Wire-protocol identifier for the loopback session token. |
| `X-Hermes-Session-Token` | Wire-protocol auth header the vendored SPA sends. |

### 3.2 Fail-closed behavior

The scan is **fail-closed**: it fails on every prohibited-name occurrence on a
scanned public surface that is not on the explicit allowlist. New occurrences
must be justified by adding them to the allowlist or removed. An occurrence
that is allowed only in a specific context must fail when it appears outside
that context — an allowed token appearing in a disallowed context is a
violation, not an exemption.

### 3.3 Out-of-scope by contract

The allowlist records that legal notices (`LICENSE`, `THIRD_PARTY_NOTICES.md`,
`voicebridge/upstream/ATTRIBUTION.md`, `docs/archive/2026-09-21/LICENSING.md`), provenance /
comparison manifests, pinned upstream trees, migration-only compatibility code,
and internal Python identifiers/module names legitimately retain upstream names
and are not part of the public-surface scan.

## 4. Rebrand checklist (scan targets and pass criteria)

Each surface below is a scan target. Pass criteria state the required outcome;
the exact scanner implementation and its current result are NOT VERIFIED here.

| # | Surface | Concrete scan target | Pass criteria |
| --- | --- | --- | --- |
| 1 | Python source | `voicebridge/`, `qubit_runtime/` Python modules; user-facing strings in banners, logs, errors, prompts | No prohibited name in any user-facing string; wire/legal/provenance tokens only where allowlisted. |
| 2 | TypeScript/JavaScript source | `frontend/qubit-*`, `ui-tui`, desktop TS/TSX source | No prohibited name in rendered strings, titles, labels, or URLs. |
| 3 | Frontend bundles | built bundle under `frontend/qubit-dist/assets/*.js` | No prohibited name in the shipped bundle except allowlisted wire tokens (reported: only `X-Hermes-Session-Token` remains). |
| 4 | TUI | `packages/qubit-ink`, `ui-tui/dist/entry.js` | No prohibited name in TUI chrome, prompts, or output. |
| 5 | CLI | `qubit_cli/`, `voicebridge/cli`, console-script names, `--help` text | No prohibited name in command names or help; launcher is `qubit`. |
| 6 | Desktop/Electron | `qubit_runtime/apps/desktop` metadata and user-visible strings, i18n, `package.json` | No prohibited name in `productName`, `executableName`, `artifactName`, `appId`, titles, or user-visible strings. |
| 7 | Docker/Nix/system files | Dockerfiles, Nix files, PowerShell, launcher, setup scripts, gitignore, `/opt/hermes`-style paths | No prohibited name in shipped paths, service labels, or installer text. |
| 8 | Documentation | user-facing docs; rebrand-allowlisted archive excluded | No prohibited name on a user-facing page; provenance/legal docs excluded by contract. |
| 9 | Environment/configuration names | env names and config keys | Active identity is `QUBIT_*`; `HERMES_HOME` appears only in the migration adapter. |
| 10 | Voice responses | spoken/streamed assistant text and TTS output | No prohibited name spoken to the user. |
| 11 | Logs and errors | runtime log lines, tracebacks, error messages surfaced to users | No prohibited name in user-visible log or error output. |

## 5. Hard route rules

These rules are binding:

- `/hermes` and `/hermes/*` must return **404** — no route at all, not a
  redirect. The `/hermes` mount is a compatibility identifier that must stay a
  hard 404.
- `/` redirects to `/chat`.
- Every other public route returns the SPA shell.

The allowlist records the same rules, and focused route tests
(`tests/test_qbpv2_rebrand_routes.py`) assert them.

## 6. Deep-rebrand mapping

The owner amendment `2026-09-19_DEEP_REBRAND` authorizes the deep source
rename. The mapping to state:

| Old | New |
| --- | --- |
| `qubit_hermes/` | `qubit_runtime/` |
| top-level `hermes_*` modules | top-level `qubit_*` modules |
| `voicebridge/hermes_*.py` | `voicebridge/qubit_*.py` |
| `HERMES_HOME` | `QUBIT_HOME` (with narrow migration adapter `voicebridge/qubit_legacy.py`) |
| `frontend/hermes-{web,shared,qubit,dist}` | `frontend/qubit-{web,shared,app,dist}` |
| `@hermes/shared` workspace alias | `@qubit/shared` |
| `qubit-hermes` console script | hidden compatibility alias; primary vendored-runtime launcher `qubit-agent` |
| canonical product launcher | `qubit` (compatibility alias `voicebridge`) |
| public identity | "Qubit Agent" / "Qubit VoiceBridge" |

`qubit-agent` is the primary vendored-runtime launcher; `qubit-hermes` is
retained only as a hidden compatibility alias. `qubit` is the canonical product
launcher and `voicebridge` remains only a compatibility alias with the same
parser/backend.

## 7. Recorded facts (cite, not independently verified here)

- The owner amendment `2026-09-19_DEEP_REBRAND` authorizes the deep source
  rename.
- The rebrand manifest records **1646** content files changed. The debrand
  manifest records `class_counts` phrase **215** / prose-word **1595**, and
  `files_changed` **1646**.
- Preserved wire identifiers recorded in the debrand manifest:
  `__HERMES_BASE_PATH__`, `__HERMES_SESSION_TOKEN__`,
  `X-Hermes-Session-Token`, `hermes_kanban`, `HERMES_QWEN_`, `qubit-hermes`.
- Public-surface rule set from `QUBIT_REBRAND_ALLOWLIST.md`: allowlisted tokens
  `__HERMES_BASE_PATH__`, `__HERMES_SESSION_TOKEN__`,
  `X-Hermes-Session-Token`; hard route rules `/hermes` and `/hermes/*` return
  **404**, `/` -> `/chat`.

## 8. Known leaks and open items

These are recorded honestly as OPEN. They are not hidden or waived by this
policy. Source: `/tmp/qubit-reconciliation-final/FINAL_RECONCILIATION.md`
section 8 (dated 2026-09-21).

- **OPEN** — `qubit_runtime/pyproject.toml` still has `name = "hermes-agent"`;
  `hermes-agent` / `hermes-acp` console scripts are retained as
  packaging/compat aliases.
- **OPEN** — `qubit_runtime/qubit_cli/banner.py` release URL and
  `qubit_runtime/qubit_cli/debug.py` GitHub/Discord URLs still point upstream
  (recorded as real provenance endpoints, not fabricated).
- **OPEN** — the debrand classification manifests were not regenerated after
  the latest deep-rebrand wave.
- **OPEN (reported)** — some desktop React strings outside the i18n/electron
  set were not rebranded. The current extent is NOT VERIFIED here.

## 9. Unknowns / NOT VERIFIED

- Whether every scan target in section 4 currently passes its stated criteria
  is NOT VERIFIED by this document. This policy states requirements; it is not
  proof of completion.
- The complete inventory of remaining prohibited-name occurrences outside the
  known leaks above is UNKNOWN pending a regenerated classification manifest.
- Whether the sibling documents listed at the top of this file currently exist
  in the tree is UNKNOWN at authoring time; the links state the intended policy
  set.

## 10. Enforcement summary

- Public surfaces must be clean; internal/wire identifiers are allowed only
  where technically required and only through explicit Qubit
  constants/adapters.
- The allowlist file
  (`docs/archive/2026-09-21/QUBIT_REBRAND_ALLOWLIST.md`) is the only permitted
  exemption, and the scan is fail-closed.
- `/hermes` and `/hermes/*` return 404; `/` redirects to `/chat`.
- Every upstream update passes a public branding scan before acceptance.
