# Qubit Upstream Sync and Adapter Policy

Date: 2026-09-21
Status: Authoritative Qubit policy. This document governs how future Hermes and
OpenClaw upstream updates are fetched, evaluated, adapted, and admitted. It is
binding on implementation and release work until a dated owner amendment
supersedes it. This is a policy statement, not a proof of current repository
state; no part of the current tree is declared VERIFIED, 100%, or 1:1 here.

## 1. Purpose and scope

Qubit vendors a Hermes fork under `qubit_runtime/` and maintains a controlled
compatibility surface for OpenClaw-derived skills, plugins, tools, providers,
and channels. Upstream projects advance independently, so Qubit needs a single
ordered process that:

- preserves the contractual upstream pin unless the owner approves a change;
- prefers adapters over blind copying;
- keeps Qubit policy, branding, broker, audit, e-stop, and voice architecture
  intact across updates;
- produces deterministic, re-runnable evidence that a later independent
  verifier can inspect.

This policy applies to any future update of the two pinned upstreams and to any
new Hermes or OpenClaw capability that Qubit chooses to surface. It does not
authorize changing application source, tests, runtime config, generated
bundles, upstream trees, or frozen contracts. Those changes follow the ordinary
implementation and verification cadence in
[QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](./QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md).

## 2. Contractual pins and observed revisions

These values are recorded from the existing upstream inventory and manifests.
They are the current reference points, not a guarantee that no later upstream
commit exists.

| Item | Recorded value |
| --- | --- |
| Hermes contractual pin | `2237be355906fbe6065ce1815711eee52b2d646e` (tag `v2026.9.7`, MIT, Nous Research) |
| Hermes vendored fork | `qubit_runtime/` (renamed from `qubit_hermes/`) |
| OpenClaw contractual pin | `3928bad9badfcb6c7d140530435e806fb8092190` (`v2026.9.2`) |
| OpenClaw observed ledger pin | `3a9d69db306cd7f081e06254cb89c4bcc14a7107` (`v2026.9.4`) |
| OpenClaw latest released revision observed | `ec9c1a13db8938e5a3eaa51fca2e981cde2395a9` (`v2026.9.5`) |
| OpenClaw latest main observed | `395690e83c32a8e0547211a9b88a37470cf30d8b` |
| Hermes latest released revision observed | `345cd2b057a452236de401d3534b8502a7465e8d` (`v2026.9.14`) |
| Hermes latest main observed | `5bb314fa0148a9879123770a609dab8f87f03fc9` (moving ref, observed 2026-09-21) |

Rules for the pin:

- The contractual pin is the authority for what Qubit builds against. An
  observed ledger pin or a newer upstream release does not silently replace it.
- The pin MUST NOT be replaced as a side effect of a sync, a merge, a generator
  run, or a dependency bump. Changing the pin requires explicit owner approval
  recorded as a dated owner amendment.
- If the working tree and the contractual pin disagree, the disagreement is a
  finding to be recorded and dispositioned, not a reason to move the pin.

## 3. Ordered process for a future Hermes and/or OpenClaw update

Run these steps in order. A later step MUST NOT be used to justify skipping an
earlier one.

1. Fetch the candidate update and record exact revision identifiers.
   Fetch the new upstream commit and record the repository, commit SHA, tag (if
   any), and license before reading any source. Do not fetch into a tree that
   Qubit builds from.
2. Preserve the contractual pin. Confirm the pin above is unchanged. Do not
   write, move, or infer a new pin. If the update is expected to require a pin
   change, stop and obtain owner approval first.
3. Compare against Qubit across surfaces. Diff and catalogue, at minimum:
   source files, public APIs, schemas, CLI commands, tools, skills, plugins,
   configuration keys, and observable behavior. Use the existing inventory and
   manifest generators where applicable (section 7).
4. Classify the delta. For each upstream capability, determine whether it was
   added, removed, renamed, or changed. Record the classification with an exact
   upstream path or symbol and a Qubit counterpart path or symbol.
5. Implement adapters, not copies. Where upstream behavior is wanted, add or
   update an adapter on Qubit's controlled compatibility/import layer (section
   4). Do not paste upstream source into Qubit trees without a recorded
   provenance and license review per
   [docs/archive/2026-09-21/LICENSING.md](archive/2026-09-21/LICENSING.md) and
   [docs/archive/2026-09-21/UPSTREAM_REUSE.md](archive/2026-09-21/UPSTREAM_REUSE.md).
6. Preserve Qubit policy and architecture. The update MUST NOT weaken Qubit
   policy, product branding, the broker boundary, the audit trail, the
   emergency-stop path, or the voice architecture. Any seam that touches these
   is an owner decision, not an implementation detail.
7. Add voice metadata for every new capability. Every newly surfaced capability
   that can be spoken, invoked by voice, or confirmed by voice MUST declare its
   voice intents and confirmation requirements (section 4), consistent with
   [QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md](./QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md).
8. Add migration support for changed state or config. If the update changes
   persisted state, config keys, env names, or on-disk layout, add a migration
   that handles the transition and records what it moved. No silent state
   reinterpretation.
9. Run the deterministic generators. Regenerate the manifests and inventories
   in section 7 and commit the regenerated artifacts with the change.
10. Run focused verification. Run the scoped checks that exercise the changed
    adapter, manifest, migration, and voice metadata. This is a focused check of
    the touched area, not the final matrix.
11. Run the final independent verification before release. The update may not
    ship until the final matrix in
    [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](./QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md)
    is run by an independent verifier and the release gate passes.

## 4. Controlled compatibility/import layer contract

Qubit maintains one controlled compatibility/import layer as the only admission
point for external capabilities. Future Hermes skills, OpenClaw skills, Hermes
plugins, OpenClaw plugins, tools, providers, and channels MUST be imported
through this layer. Nothing in that set may be loaded directly from an upstream
tree.

Every import MUST expose a declared manifest. A manifest that omits any
required field is invalid and MUST NOT be admitted. The required fields are:

| Field | Meaning |
| --- | --- |
| `name` | Stable, unique capability identifier at the import layer. |
| `version` | Capability version string; part of the identity, not a pin for upstream. |
| `capabilities` | The concrete behaviors the capability offers, each named. |
| `required_permissions` | Permissions the capability needs before it may act. |
| `configuration` | Config keys and env names it reads, and their expected shape. |
| `voice_intents` | Spoken intents/utterances the capability can serve. |
| `confirmation_requirements` | Which actions require explicit user confirmation. |
| `audit_categories` | Audit categories its actions emit into the audit trail. |
| `provider_model_requirements` | Provider and model prerequisites, if any. |
| `migration_metadata` | State/config migration hooks and the versions they bridge. |

Additional constraints:

- The layer MUST fail closed: a malformed or incomplete manifest rejects the
  import rather than loading a partial capability.
- Manifest fields follow the existing plugin manifest vocabulary recorded in
  `qubit_runtime/qubit_cli/plugins_manifest.py` (for example `name`,
  `version`, `capabilities`, `requires_env`, `provides_tools`,
  `config_schema`) so the import layer does not invent a parallel schema.
- `required_permissions`, `audit_categories`, and
  `confirmation_requirements` are load-bearing for Qubit policy. A capability
  that cannot declare them in good faith is out of scope until it can.

## 5. Manifest schema sketch and worked example

Schema sketch (field name, type, meaning). The concrete serialization is JSON;
field names are exact.

```
name                         string   unique import-layer identifier
version                      string   capability version
capabilities                 array    {id, summary} entries
required_permissions         array    permission strings
configuration                object   {keys: [...], env: [...]}
voice_intents                array    {id, utterances, requires_confirmation}
confirmation_requirements    array    {action, level}   level: none|confirm|strong
audit_categories             array    category strings
provider_model_requirements  object   {providers: [...], models: [...]}
migration_metadata           object   {from_versions: [...], handler, notes}
```

Worked example (illustrative shape only; not a claim that this capability
exists in any registry):

```json
{
  "name": "code-review",
  "version": "1.2.0",
  "capabilities": [
    { "id": "review.diff", "summary": "summarize a repository diff" }
  ],
  "required_permissions": ["repo.read"],
  "configuration": {
    "keys": ["review.model"],
    "env": ["QUBIT_REVIEW_MODEL"]
  },
  "voice_intents": [
    {
      "id": "review.diff.summarize",
      "utterances": ["review my diff", "summarize these changes"],
      "requires_confirmation": false
    }
  ],
  "confirmation_requirements": [
    { "action": "review.post_comment", "level": "confirm" }
  ],
  "audit_categories": ["code-review.read"],
  "provider_model_requirements": {
    "providers": ["local", "remote"],
    "models": ["any-chat-capable"]
  },
  "migration_metadata": {
    "from_versions": ["1.0.0", "1.1.x"],
    "handler": "review.migrate_config_v1",
    "notes": "moves review.model into the canonical config store"
  }
}
```

## 6. Removed and renamed upstream capabilities

Silent removal is prohibited. An upstream capability that disappears, is
renamed, or changes contract MUST receive a recorded disposition before the
update is accepted. The disposition vocabulary is shared with the frozen
differential interface at
[docs/differential/INTERFACE.md](./differential/INTERFACE.md) and is limited to:

| Disposition | Meaning |
| --- | --- |
| `IMPLEMENTED` | Qubit provides the capability, with an adapter and evidence. |
| `SAFE_EQUIVALENT` | Qubit provides a behavior-preserving alternative; the difference is documented. |
| `OPERATOR_ONLY` | The capability is intentionally not automated; an operator performs it manually. |
| `OPEN` | Not yet dispositioned. `OPEN` is a debt, not an accepted end state. |

Rules:

- Every removed or renamed upstream capability MUST appear in the relevant
  manifest with a disposition from the table above, an evidence pointer, and —
  where applicable — a migration path for state or config that referenced it.
- A rename MUST be recorded in a rename manifest so that the old identifier and
  the new identifier are both resolvable.
- `OPERATOR_ONLY` entries MUST name the operator prerequisite and the Qubit
  counterpart surface.
- `OPEN` entries block release admission under the release gate until they are
  dispositioned.

## 7. Source correspondence and evidence

Qubit must be able to show how its tree corresponds to each pinned upstream.
Correspondence is proven with:

- pristine manifests — the untouched upstream file/revision inventory;
- rename manifests — the full old-to-new identifier mapping produced by the
  rebrand/debrand tooling;
- patch manifests — the exact local modifications layered on the pinned source;
- behavioral parity evidence — the recorded evidence that equivalent behavior
  holds where byte identity does not.

Byte identity to upstream is NOT required. Byte-for-byte equality is neither the
goal nor the acceptance criterion; capability correspondence is. The governing
contract is
[QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](./QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md),
which defines parity in terms of behavior and evidence rather than identical
bytes.

Existing deterministic generators and evidence scripts that produce this
material:

- `scripts/source_inventory.py`
- `scripts/hermes_vendor_manifest.py`
- `scripts/differential_parity.py`
- `scripts/openclaw_upgrade_manifest.py`
- `scripts/config_binding_manifest.py`
- `scripts/route_ownership_matrix.py`
- `scripts/tri_surface_parity.py`
- `scripts/qubit_rebrand.py`
- `scripts/qubit_debrand.py`

Existing manifests preserved under `docs/archive/2026-09-21/`:
`QUBIT_HERMES_VENDOR_MANIFEST.json`, `QUBIT_REBRAND_MANIFEST.json`,
`QUBIT_DEBRAND_MANIFEST.json`, and `QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`.

The differential interface is frozen at
[docs/differential/INTERFACE.md](./differential/INTERFACE.md): exit codes
`0`/`1`/`2`/`3`/`4`, and a normalizer allowlist of exactly seven keys —
`timestamp`, `uuid`, `temp_path`, `pid`, `session_token`, `generated_hash`,
`product_branding`. Normalizers normalize only for comparison and the raw hash
is always reported.

## 8. Binding to generators, branding scan, and release gate

- Step 9 of the process is mandatory: after any accepted upstream change, run
  the deterministic generators in section 7 and commit the regenerated
  manifests. Manifests are generated, not hand-edited.
- No upstream update may bypass the branding scan. The rebrand/debrand tooling
  (`scripts/qubit_rebrand.py`, `scripts/qubit_debrand.py`) and its manifests
  are part of the admission path; an update that would reintroduce an upstream
  brand string without a recorded mapping does not pass.
- No upstream update may bypass the final release gate. The gate defined in
  [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](./QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md)
  is run once, after implementation is frozen, by an independent verifier.
  Until that gate passes, the update is not released and is not committed to a
  release branch.

## 9. Known open items and unknowns

- KNOWN OPEN (owner review): the regenerated OpenClaw upgrade manifest records
  0 of 434 contractual-pin methods `OPEN` and 434 disposed (252 `IMPLEMENTED`,
  87 `SAFE_EQUIVALENT`, 95 `OPERATOR_ONLY`) in the pinned-surface disposition.
  The 95 `OPERATOR_ONLY` rows are typed refusals naming a prerequisite; the
  architecture-gated subset (an upstream server registry/transport Qubit does
  not run) needs owner review before it can be treated as behavioral parity.
  These remain release debt under section 6 until dispositioned.
- UNKNOWN / NOT VERIFIED: the exact upstream revisions that will exist at the
  time of a future sync. The pins in section 2 are the current reference, not a
  statement about later upstream history.
- UNKNOWN / NOT VERIFIED: the set of new capabilities a future Hermes or
  OpenClaw update will introduce, and therefore the adapter, voice-metadata,
  and migration work it will require.
- NOT VERIFIED: the current tree has not, in this document, been re-checked
  against the pins or run through the final matrix.

## 10. Related documents

- [QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](./QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
- [QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md](./QUBIT_CAPABILITY_PARITY_CONTRACT_2026-09-21.md)
- [QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md](./QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md)
- [QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](./QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md)
- [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](./QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md)
- [QUBIT_DOCUMENTATION_INDEX_2026-09-21.md](./QUBIT_DOCUMENTATION_INDEX_2026-09-21.md)
