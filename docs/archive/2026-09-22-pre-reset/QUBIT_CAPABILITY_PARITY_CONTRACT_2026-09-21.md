# Qubit Capability Parity Contract

**Date:** 2026-09-21

**Status:** AUTHORITATIVE QUBIT CONTRACT — normative and binding.
This document defines the rules by which capability parity is judged. It is not
a status report, not an execution brief, not evidence of completion, and not a
substitute for the ledgers and manifests it names. Where this contract and a
status artifact disagree, this contract governs how the disagreement is read;
the artifact remains the source of the measured values.

---

## 1. Purpose and scope

Qubit must support all supported Hermes and OpenClaw functionality, including
future additions to that functionality, through an adapter-based upgrade
process. Upstream capability is adopted by maintaining Qubit-side adapters and
equivalents against named upstream revisions, not by freezing at a single
snapshot. The upgrade procedure that turns a new upstream revision into adapter
and manifest work is defined in
[QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md](QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md);
this contract consumes that policy and binds the definition of "supported".

Two upstream projects are in scope, each with its own contractual revision:

- Hermes, by Nous Research, MIT licensed.
- OpenClaw.

This contract applies to every capability in the Qubit surface, whether it
originates in Hermes, OpenClaw, or Qubit-native work.

---

## 2. Parity is behavioral, not textual

Parity means observable behavior at least as much as source similarity. A
capability is supported only when its behavior can be traced end to end through
the mandatory behavioral chain below. Source presence, route registration, or
interface shape does not satisfy this contract on its own.

### 2.1 Mandatory behavioral chain

Every capability must be traceable, in order, through all of:

1. **Input / control** — the user-facing control, CLI invocation, HTTP/RPC
   request, WebSocket event, dashboard action, or voice command that starts it.
2. **Authentication** — identity and authorization are established before any
   privileged effect, including token, session, or operator-credential checks.
3. **Canonical Qubit operation** — the request resolves to one canonical Qubit
   operation with a single owner, not a divergent or duplicate path.
4. **Policy / broker / e-stop** — the operation passes through policy checks,
   broker mediation, and emergency-stop enforcement before side effects.
5. **Real runtime / provider** — a real backend, runtime, or provider performs
   the work. A wire-shape stub or a fabricated response is not a runtime.
6. **Persistent state / event** — resulting state is persisted and/or an event
   is emitted, such that the effect is durable and observable.
7. **Correct text / dashboard / voice result** — the user-visible result is
   correct in every declared surface: text, dashboard, and voice where the
   capability declares them.
8. **Error / retry / cancel / recovery / audit behavior** — failure paths are
   typed, retryable where applicable, cancellable, recoverable, and audited,
   with redaction applied.

Each disposition claim must be traceable through this chain. A break anywhere
in the chain means the capability is not terminal and its disposition is OPEN.

---

## 3. Capability dispositions

Dispositions are exactly four. No other value is valid. Every capability
carries exactly one.

### IMPLEMENTED

The real underlying behavior is exercised end to end through the behavioral
chain, with positive evidence. Evidence must show the real backend effect, the
persisted state or emitted event, and the correct result in each declared
surface, produced by an on-disk proving test or equivalent positive artifact.

### SAFE_EQUIVALENT

Real backend behavior is present, plus an explicitly refused unsafe upstream
variant. The allowed counterpart must work through the full behavioral chain.
The refusal must operate under full policy, audit, redaction, confirmation,
e-stop, and resource limits, and must be covered by positive, negative, and
regression tests. A refusal that stands without a working allowed counterpart
is not SAFE_EQUIVALENT; it is OPEN.

### OPERATOR_ONLY

Code is complete and tested, but a genuine live prerequisite prevents
end-to-end execution in the current environment. Legitimate prerequisites
include credentials, hardware, accounts, OAuth, and CAPTCHA or 2FA. An
OPERATOR_ONLY row must have a runbook and must surface a typed unavailable
state to callers. OPERATOR_ONLY is a qualification state, not a claim that the
capability has been exercised live.

### OPEN

Anything not terminal. A capability that is not IMPLEMENTED, not
SAFE_EQUIVALENT, and not a qualified OPERATOR_ONLY is OPEN. OPEN is the
fail-closed default.

---

## 4. Prohibited completion signals

No capability may be marked complete, terminal, or supported because of any of
the following, alone or in combination:

- a route returns HTTP 200;
- a button or control exists in a surface;
- a catalog or registry entry exists;
- a typed refusal exists without a working allowed counterpart.

A refusal counts only as SAFE_EQUIVALENT when its allowed counterpart works
through the behavioral chain. Presence without exercised behavior is OPEN.

---

## 5. Comparison targets

Four comparison targets are separate and must be compared independently:

1. **Contractual Hermes revision** — pin
   `2237be355906fbe6065ce1815711eee52b2d646e` (tag `v2026.9.7`, MIT, Nous
   Research).
2. **Latest Hermes revision** — observed 2026-09-21 from live
   `git ls-remote`: latest tag `v2026.9.14` =
   `345cd2b057a452236de401d3534b8502a7465e8d`; latest default branch
   `main` = `5bb314fa0148a9879123770a609dab8f87f03fc9`. `main` is a moving
   ref; the value recorded here is the revision observed at the stated time,
   not a permanent pin. (Earlier recorded `3b7eda08…` and `782886f8…` were
   superseded.)
3. **Contractual OpenClaw revision** — pin
   `3928bad9badfcb6c7d140530435e806fb8092190` (tag `v2026.9.2`).
4. **Latest OpenClaw revision** — tracked as two distinct latest points as
   recorded in the deterministic upgrade manifest
   (`docs/archive/2026-09-21/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`):
   latest release `ec9c1a13db8938e5a3eaa51fca2e981cde2395a9` (tag `v2026.9.5`)
   and latest main `395690e83c32a8e0547211a9b88a37470cf30d8b`. A separate
   observed ledger pin is `3a9d69db306cd7f081e06254cb89c4bcc14a7107` (tag
   `v2026.9.4`).

Coverage of a contractual revision does not imply coverage of the latest
revision. Latest-revision capability may be OPEN even when the contractual
revision is covered, and every such gap must be carried as OPEN rather than
folded into contractual-revision counts. The latest Hermes revision is now
recorded (tag `v2026.9.14`, main `5bb314fa…` observed 2026-09-21), but
latest-Hermes parity is NOT VERIFIED because no latest-Hermes comparison has
been executed; the recorded pin->latest delta is 2025 commits to the tag and
7064 commits to main, and Qubit has no counterpart for most of the new surface
(connectors subsystem, browser vault, host/relay gateway modules, new
optional-skills, new state modules, provider listing hooks).

---

## 6. Rebranded source correspondence

Rebranded Qubit source is not expected to be byte-identical to upstream.
Correspondence is proven, not assumed. Four artifacts, together, establish
correspondence:

- **pristine manifests** — the untouched upstream file inventory at the pin;
- **rename manifests** — the mapping of upstream identifiers and paths to
  Qubit identifiers and paths;
- **patch manifests** — the deliberate, reasoned deltas applied on top of the
  rename;
- **behavioral parity evidence** — end-to-end evidence through the section 2
  chain.

Every tracked file and every capability is classified into exactly one byte
class:

| Byte class | Meaning |
| --- | --- |
| IDENTICAL | Byte-for-byte the same as the compared upstream blob. |
| EOL_NORMALIZED | Same content after line-ending normalization only. |
| PATCHED_WITH_REASON | Deliberately changed, with a recorded reason in the patch manifest. |
| MISSING | Present upstream, absent in Qubit, and not otherwise dispositioned. |
| EXTRA | Present in Qubit, absent upstream. |
| NOT_APPLICABLE | Upstream artifact that does not apply to Qubit by design, with justification. |

A byte class of MISSING or an unexplained difference is a parity failure and
must be dispositioned, not silently tolerated.

---

## 7. Completion authority

The completion authority is the current **QUBIT_GAP_LEDGER**, together with its
three machine ledgers:

- `QUBIT_GAP_LEDGER.hermes.json`
- `QUBIT_GAP_LEDGER.openclaw.json`
- `QUBIT_GAP_LEDGER.qubit.json`

The live worktree copy of the ledger and its sections currently resides under
`docs/archive/2026-09-21/`; the frozen copies there remain authoritative by
content until those files are moved. No other document may override a ledger
row's disposition.

The ledger's completion predicate is:

```
complete = code_ready and not operator_rows
```

Code-readiness is not product completion. A ledger may report code-side open
rows at zero while operator-qualified rows remain outstanding; that state is
code-ready, not fully complete.

As recorded at the time of writing:

- Total rows: **1423**
- IMPLEMENTED: **1262**
- SAFE_EQUIVALENT: **150**
- OPERATOR_ONLY: **11**
- Code-side open rows: **0**
- By source: Hermes **573**, OpenClaw **104**, Qubit **746**

These counts describe code disposition. They do not assert product completion,
and they do not cover the unresolved contradiction in section 8.

---

## 8. Contractual-pin OpenClaw method disposition (resolved code-side)

The contractual-pin OpenClaw gateway surface is **434** methods. The
authoritative per-method disposition is `QUBIT_OPENCLAW_UPGRADE_MANIFEST.json`,
key `pinned_surface_disposition` (regenerated 2026-09-21; byte-identical across
two generator runs): IMPLEMENTED **252**, SAFE_EQUIVALENT **87**,
OPERATOR_ONLY **95**, OPEN **0** (434/434 disposed) over the
`gateway_method_surface.contractual_pin` scope. The manifest's stated policy is
fail-closed to OPEN and treats OPEN as reported-but-not-a-disposition.

As of regeneration, **0 of 434** contractual-pin methods remain OPEN: every
method resolves to a registered handler with a terminal disposition
(IMPLEMENTED, SAFE_EQUIVALENT, or OPERATOR_ONLY). Earlier reports of 433/434
OPEN, 301/434 disposed, and 102/434 OPEN are stale; this document's counts now
match the regenerated manifest and
[QUBIT_OPENCLAW_METHOD_INVENTORY.json](QUBIT_OPENCLAW_METHOD_INVENTORY.json),
which carries the per-method work list.

Code-side disposition is **not** behavioral parity. The 95 OPERATOR_ONLY rows
are typed refusals naming an exact prerequisite (external service/credential/
hardware, or an upstream server registry/transport Qubit intentionally does not
run). The architecture-gated subset of those rows requires owner review before
it can be treated as parity. The 87 SAFE_EQUIVALENT rows each name a real local
canonical counterpart whose contract differs and are verified only at the
source level; none is E2E-verified here.

---

## 9. Normative references

Active sibling documents:

- [QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
- [QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md](QUBIT_VOICE_ACTIONABILITY_CONTRACT_2026-09-21.md)
- [QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md](QUBIT_REBRAND_AND_PUBLIC_SURFACE_POLICY_2026-09-21.md)
- [QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md](QUBIT_UPSTREAM_SYNC_AND_ADAPTER_POLICY_2026-09-21.md)
- [QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md](QUBIT_FINAL_VERIFICATION_AND_RELEASE_GATE_2026-09-21.md)
- [QUBIT_DOCUMENTATION_INDEX_2026-09-21.md](QUBIT_DOCUMENTATION_INDEX_2026-09-21.md)

Frozen contracts and owner records physically stored at
`docs/archive/2026-09-21/` that remain authoritative by content:

- [QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md](archive/2026-09-21/QUBIT_FROZEN_ACCEPTANCE_CONTRACT.md)
- [QUBIT_FULL_PARITY_CONTRACT.md](archive/2026-09-21/QUBIT_FULL_PARITY_CONTRACT.md)
- [QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md](archive/2026-09-21/QUBIT_EXECUTION_DIRECTIVE_2026-09-12.md)
- [QUBIT_OWNER_AMENDMENT_2026-09-13_TERMINAL_STATUSES.md](archive/2026-09-21/QUBIT_OWNER_AMENDMENT_2026-09-13_TERMINAL_STATUSES.md)
- [QUBIT_SAFE_EQUIVALENTS.md](archive/2026-09-21/QUBIT_SAFE_EQUIVALENTS.md)
- [QUBIT_GAP_LEDGER.md](archive/2026-09-21/QUBIT_GAP_LEDGER.md)

Differential interface contract:

- [docs/differential/INTERFACE.md](differential/INTERFACE.md)
