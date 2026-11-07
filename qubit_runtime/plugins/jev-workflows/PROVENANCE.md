# Jev plugin — provenance

Ported from an external project, not written here. Record what was taken, from
where, and what changed, so a future reader can tell upstream code from ours.

| Field | Value |
|---|---|
| Upstream | `github.com/kerpopule/hermes-jev-skills` |
| Pin | `8316358bf60e98a21477582af829ef11c9cf52fb` (2026-10-28) |
| Licence | MIT — Copyright (c) 2026 Steve Darlow |
| Local path | `qubit_runtime/plugins/jev/` |
| Skill | 13 workflow skills under `skills/` |

## What changed from upstream

- **Rebrand.** `hermes-jev` -> `jev`, `metadata.hermes` -> `metadata.qubit`,
  `hermes_home`/`hermes_root`/`HERMES_ALIASES` -> `qubit_*`, and every `~/.hermes`
  fallback replaced with `qubit_constants.get_qubit_home()` so the plugin follows
  the active profile instead of the launch home. Prose in the skills says Qubit.
- **Skill delivery.** Upstream ships the skills as a separate install; here they
  are bundled inside the plugin and registered with `ctx.register_skill()`, so
  they resolve as `jev:<name>` through `skill_view` and need no install step.
  This is the same shape `plugins/browser-companion` already uses.
- **Zen keyless door (a real upstream bug).** `client.ask()` refused with
  `no_key` whenever no credential resolved, BEFORE checking which door it was
  calling. Zen's free tier is the only door a fresh install can open, and it
  answers with NO credential (sending one returns `401 Invalid credential`), so
  the guard made that door unusable. It now exempts `zen`.

## Not ported

- `router-dashboard/` — a separate web dashboard for routing pools; Qubit has its
  own dashboard surfaces and a second one would duplicate them.
- `hermes/plugin/hermes-handoff/` — the handoff plugin is a different product.
- `evals/`, `evidence/` — upstream's own measurement harness, tied to their CI.

## Upstream facts worth remembering

`jev-1.13-free` on Zen is a SMALL model. Upstream's README benchmark numbers
(~0.4s routing, 1.9s search) were measured on the paid TypeSafe model, not this
one. Treat any number in that README as unproven on a free-tier install until it
is re-measured here.
