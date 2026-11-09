---
name: codebase-design
description: "Deep-module vocabulary: interface, depth, seam, leverage."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  qubit:
    tags: [architecture, deep-modules, interfaces, seams, design-vocabulary]
    category: software-development
    related_skills: [improve-codebase-architecture, domain-modeling, grilling]
---

# Codebase Design Skill

The shared vocabulary for designing **deep modules**: a lot of behaviour behind a
small interface, placed at a clean seam, testable through that interface. This
skill supplies words and principles only — it proposes no interfaces until you
design one. Reach for it whenever code is being designed or restructured, or when
another skill needs the deep-module language.

## When to Use

- Designing or improving a module's interface
- Deciding where a seam goes, or whether one is real
- Making code more testable, or more navigable for an agent
- Another skill asks for the deep-module vocabulary
  (`improve-codebase-architecture` and `grill-with-docs` both do)

**Don't use for:** naming domain concepts — that is `domain-modeling`. Do not
substitute "component", "service", "API", or "boundary" for these terms; the
consistent language is the entire point.

## Prerequisites

None. Deeper material is in `references/`, loaded on demand:
`references/DEEPENING.md` (dependency categories, seam discipline,
replace-don't-layer testing) and `references/DESIGN-IT-TWICE.md` (the parallel
sub-agent pattern for alternative interfaces).

## Quick Reference

| Term | Meaning | Avoid |
|---|---|---|
| **Module** | Anything with an interface and an implementation, at any scale | unit, component, service |
| **Interface** | Everything a caller must know: signature, invariants, ordering, error modes, config, performance | API, signature |
| **Implementation** | What's inside. Distinct from *adapter*, which describes role | — |
| **Depth** | Leverage at the interface: behaviour per unit of interface learned | — |
| **Seam** | A place you can alter behaviour without editing in that place | boundary |
| **Adapter** | A concrete thing satisfying an interface at a seam | — |
| **Leverage** | What callers get from depth | — |
| **Locality** | What maintainers get from depth | — |

## Procedure

### 1. Name the module and its interface

State what the module is and everything a caller must know to use it. Done when
the list includes invariants, ordering, and error modes — not just the signature.

### 2. Test the depth

Ask whether the interface is nearly as complex as the implementation. If yes, the
module is **shallow**. Done when you can say deep or shallow and name the specific
interface members that make it so.

### 3. Apply the deletion test

Imagine deleting the module. If the complexity vanishes, it was a pass-through; if
it reappears across N callers, it was earning its keep. Done when you have a
verdict per suspected pass-through.

### 4. Place the seam

Decide *where* the interface lives, separately from what goes behind it. Done when
you can name at least two things that actually vary across the seam — one adapter
is a hypothetical seam, two make it real.

### 5. Check testability through the interface

The interface is the test surface: callers and tests cross the same seam. If you
need to test *past* it, the module is the wrong shape. For dependency and
deepening mechanics, load `references/DEEPENING.md`. For exploring several
interfaces at once, load `references/DESIGN-IT-TWICE.md`.

## Principles

- **Depth is a property of the interface, not the implementation.** A deep module
  may be internally composed of small, mockable, swappable parts; they are simply
  not part of the interface.
- **Internal seams vs external seams.** A module can have private seams used by its
  own tests. Don't expose them through the interface just because tests use them.
- **Accept dependencies, don't create them.** `processOrder(order, gateway)` is
  testable; `new StripeGateway()` inside the body is not.
- **Return results, don't produce side effects.** A function that computes is
  testable; one that mutates its argument is not.
- **Small surface area.** Fewer methods, simpler params, fewer tests to write.
- **Rejected framing:** depth as implementation-lines over interface-lines rewards
  padding. Use depth-as-leverage instead.

## Pitfalls

- **Drifting to "component", "service", "API", "boundary".** Each carries a
  different meaning in the reader's head, which defeats the vocabulary.
- **Interface read as the type signature only.** That reading misses invariants and
  error modes, which is where most shallow modules hide.
- **Introducing a seam with one adapter.** That is indirection with no payoff.

## Verification

- Every architecture term used appears in the Quick Reference table, spelled the
  same way.
- Each claimed shallow module names which interface member is as complex as the
  implementation.
- Each proposed seam names at least two things that vary across it.
