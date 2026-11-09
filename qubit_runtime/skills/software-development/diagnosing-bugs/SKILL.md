---
name: diagnosing-bugs
description: "Diagnosis loop for hard bugs and performance regressions."
version: 1.0.0
author: Matt Pocock (mattpocock), Qubit Agent
license: MIT
platforms: [linux, macos]
metadata:
  qubit:
    tags: [debugging, diagnosis, regression, feedback-loop, performance]
    category: software-development
    related_skills: [requesting-code-review, codebase-design]
---

# Diagnosing Bugs Skill

A discipline for hard bugs: the one that resists a first glance, the intermittent
flake, the regression that crept in between two known-good states. It refuses to
theorise until a **tight feedback loop** exists — one command that already goes red
on *this* bug — then fixes with a regression test. It does not guess-and-patch.

## When to Use

- The user says "diagnose", "debug this", or "why is this broken"
- Something is throwing, failing, or slow and the cause isn't obvious
- An intermittent failure or a regression between two known states

**Don't use for:** bugs whose cause a single `read_file` reveals; greenfield
features with no failing behaviour to reproduce.

## Prerequisites

- Read `GLOSSARY.md` and any ADRs in the area you're touching before exploring, so
  the modules' names mean what you think they mean.
- **Redact every secret before showing anything.** Write `<REDACTED>` in its
  place. Build loops against env vars so credentials stay in the environment, not
  in what you print. Captured artifacts carry auth headers: quote only the lines
  that carry the signal.

## How to Run

The loop comes first; every other phase consumes it.

```text
terminal(command="<the one command that goes red>", timeout=120)
```

If redacted output is not enough to diagnose, say so and ask. Never fabricate an
output you could not produce.

## Procedure

### 1. Build a feedback loop

**This is the skill.** Everything else is mechanical. With a tight pass/fail signal
you will find the cause; without one, no amount of staring at code will save you.
Spend disproportionate effort here. Be aggressive, be creative, refuse to give up.

Ways to construct one, roughly in order:

1. **Failing test** at whatever seam reaches the bug: unit, integration, e2e.
2. **HTTP script** against a running dev server.
3. **CLI invocation** on a fixture input, diffing stdout against a known-good
   snapshot.
4. **Headless browser script** driving the UI, asserting on DOM, console, network.
5. **Replay a captured trace**: save a real request, payload, or event log, replay
   it through the code path in isolation.
6. **Throwaway harness**: a minimal subset of the system exercising the bug path in
   one call.
7. **Property / fuzz loop**: 1000 random inputs looking for the failure mode.
8. **Bisection harness**: automate "boot at state X, check, repeat" so
   `git bisect run` can drive it.
9. **Differential loop**: same input through old vs new (or two configs), diff the
   outputs.
10. **Human-in-the-loop script**: last resort. Use
    `scripts/hitl-loop.template.sh` so a human clicking still produces structured
    captured output that feeds back to you.

Then **tighten** the loop as if it were a product: make it faster (cache setup, skip
unrelated init, narrow scope), make the signal sharper (assert the specific
symptom, not "didn't crash"), make it deterministic (pin time, seed RNG, isolate
filesystem, freeze network). A 30-second flaky loop is barely better than none; a
2-second deterministic one is a superpower.

For non-deterministic bugs the goal is not a clean repro but a **higher
reproduction rate**: loop 100×, parallelise, add stress, narrow timing windows, inject
sleeps. A 50% flake is debuggable; 1% is not.

**Completion criterion** — the loop is tight and red-capable when you can name
**one command** you have **already run at least once** (show the invocation and its
redacted output) that is:

- [ ] **Red-capable**: drives the real code path and asserts the user's exact
      symptom, so it goes red on this bug and green once fixed — not merely "runs
      without erroring"
- [ ] **Deterministic**: same verdict every run (flaky: a pinned high repro rate)
- [ ] **Fast**: seconds, not minutes
- [ ] **Agent-runnable**: runs unattended; a human only via the HITL script

If you catch yourself reading code to build a theory before this command exists,
**stop**. Jumping to a hypothesis is the exact failure this skill prevents.

### 2. Reproduce and minimise

Run the loop, watch it go red. Confirm the loop produces the failure mode the
**user** described, not a nearby one (wrong bug = wrong fix); that it reproduces
across runs; and that you captured the exact symptom for later verification.

Then shrink to the **smallest scenario that still goes red**: cut inputs, callers,
config, data, and steps **one at a time**, re-running after each cut, keeping only
what is load-bearing. Done when removing any remaining element makes the loop go
green.

### 3. Hypothesise

Generate **3–5 ranked hypotheses** before testing any — a single hypothesis anchors
on the first plausible idea. Each must be falsifiable:

> "If <X> is the cause, then <changing Y> will make the bug disappear / <changing Z>
> will make it worse."

If you cannot state the prediction, discard or sharpen it. **Show the ranked list to
the user before testing**: they often re-rank instantly ("we just deployed #3"). Don't
block on it; proceed with your ranking if they're away. Done when every surviving
hypothesis carries a prediction.

### 4. Instrument

Each probe maps to a specific prediction. **Change one variable at a time.** Prefer
a debugger or REPL inspection over logs — one breakpoint beats ten logs. Then
targeted logs at the boundaries that distinguish hypotheses. Never log everything
and filter.

**Tag every debug log** with a unique prefix (`[DEBUG-a4f2]`) so cleanup is one
search. Untagged logs survive; tagged logs die.

**Perf branch:** for performance regressions logs are usually wrong. Establish a
baseline measurement first (timing harness, profiler, query plan), then bisect.
Measure first, fix second. Done when every hypothesis has a verdict from evidence.

### 5. Fix and regression test

Write the regression test **before** the fix, but only where a **correct seam**
exists — one where the test exercises the real bug pattern as it occurs at the call
site. A shallow seam (single-caller test for a multi-caller bug, unit test that
can't replicate the trigger chain) gives false confidence.

**If no correct seam exists, that is itself the finding.** Note it and flag the
architecture as blocking lock-down; that is a job for
`improve-codebase-architecture`.

Where a seam exists:

1. Turn the minimised repro into a failing test at that seam.
2. Watch it fail. If you forced the red by mutating code or a fixture, `diff`
   against a pristine copy to prove the mutation landed before trusting it.
3. Apply the fix.
4. Watch it pass.
5. Re-run the Phase-1 loop against the original, un-minimised scenario.

Done when all five are evidenced.

### 6. Clean up

Required before declaring done:

- [ ] Original repro no longer reproduces (re-run the Phase-1 loop)
- [ ] Regression test passes, or the absent seam is documented
- [ ] All `[DEBUG-...]` instrumentation removed (search the prefix with
      `search_files`)
- [ ] Throwaway prototypes deleted or moved to a clearly marked debug location
- [ ] The correct hypothesis is stated in the commit message, so the next debugger
      learns

## Pitfalls

- **Theorising before the loop exists.** Reading code to form a theory is the
  failure mode; a theory without a red signal is unfalsifiable.
- **Reproducing a different bug.** A nearby failure that happens to be there gets
  fixed while the user's bug ships.
- **Untagged logs.** Untagged debug output survives into production.
- **Trusting a forced red.** Prove the mutation landed, or the "failing" test proves
  nothing.
- **Stopping at a shallow seam.** False confidence is worse than a documented gap.

## Verification

- The Phase-1 command was shown with redacted output, and it went green after the
  fix.
- Every element in the minimised repro is load-bearing.
- The regression test fails before the fix and passes after.
- Searching the debug prefix returns zero hits.
