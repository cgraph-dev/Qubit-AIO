# Qubit IDENTITY — what Qubit is

**Status:** canonical identity statement. Not `VERIFIED`, not a release verdict.
Identity is a product fact, not a milestone; it does not claim capability parity.

> **Nobody knows what it will become.**

Qubit is a **personal AI agent** — an all-in-one assistant that works as your
coding agent, caretaker, teacher, personal bot, and business organizer, among
whatever else you need it to be. It is one agent, and it keeps evolving.

## Qubit is not "a fork"

Qubit began from upstream source and still carries the upstream lineage in its
history, its vendored runtime, and its required legal attribution. That is
**provenance**, not identity.

A fork is defined by what it was copied from. Qubit is defined by what it does
and what it becomes. Calling Qubit "a fork" is a description of how the code
arrived, not of what the product is — and it stops being true the moment Qubit
outgrows the source it came from.

The relationship to upstream is therefore stated once, as engineering fact
(which install is the behavioral reference, which files were vendored), and
nowhere else. See [BRAND.md](BRAND.md) §"Internal engineering docs".

## Absorbs, then exceeds

Qubit adapts to the work of other agents — and takes it in.

When Qubit meets another agent's output, method, or capability, it does not
merely route to it or wrap it. It **absorbs that capability into itself**, so
the ability becomes Qubit's own and is reachable the same way every other Qubit
capability is. The result is an agent that becomes **more than any one of the
agents it learned from** — not a coordinator standing between them, but the
thing they added up to and then surpassed.

This is the mechanism behind "keeps evolving with time": each absorbed
capability raises the floor, and the floor never comes back down.

**Boundary — what absorbing is not.** An absorbed capability is real work
through the one authority path (broker · policy · audit · confirmation ·
e-stop), reachable by both text and voice. A wrapper that only forwards to
another agent, or a typed refusal dressed as a feature, is **not** absorption —
it is an unwired route. Absorption must be *verified by behavior*, not declared.
This is the same rule as [ARCHITECTURE.md](ARCHITECTURE.md) "Parity is behavior"
and it does not relax for identity claims.

## One agent, one authority

Identity does not split the product. Everything Qubit absorbs, whatever surface
invoked it, runs through the same single backend and the same authority chain,
under one home (`QUBIT_HOME`, one `state.db`). Multiple roles, one agent. See
[ARCHITECTURE.md](ARCHITECTURE.md).

## The roles are open-ended

"Your imagination is the limit" is how this reads from the outside — but it is
a *reading*, not a promise. Qubit claims no capability it has not built. The
only commitments that bind are the ones verified in
[STATUS.md](STATUS.md), [PARITY.md](PARITY.md), and
[CHECKLIST.md](CHECKLIST.md). What is unbounded is the *ambition*, not the
current capability list.

## Related

- [ARCHITECTURE.md](ARCHITECTURE.md) — the layers, the authority path, the
  provenance/overlay split.
- [BRAND.md](BRAND.md) — zero legacy brand on product surfaces; legal
  attribution the one required exception.
- [PARITY.md](PARITY.md) — the behavioral target and how parity is judged.
- [STATUS.md](STATUS.md) — the evidence-backed truth of what works today.
