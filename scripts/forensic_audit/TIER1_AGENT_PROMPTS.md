# Tier-1 agent deep-audit prompts

Tier 0 (`run_audit.py`) is deterministic and cheap. When you need the full
breadth — providers, toolsets, skills, plugins, MCP, sessions, config, cron,
rebrand, future breakage — run this five-agent pass on demand and ingest the
outputs.

## Preconditions

* A clean clone of the upstream reference at the commit under audit
  (e.g. `8623cd4a8403f020090c9c3e8feae943b4dac55e`).
* The Qubit vendored pin source (`d337b736aa1e8ebecfab043842d13e4a2d2f48a3`).
* **Read-only** rules: never modify the product tree, services, DBs, configs,
  credentials, or `~/.hermes`; redact secrets; write only to an audit dir.

## Output contract (per agent)

Write JSON with a `findings` list. Each row: `id`, `area`, `title`,
`hermes_file_line`, `qubit_file_line`, `difference`, `repro`, `observed`,
`severity` (`CRITICAL|HIGH|MEDIUM|LOW|INFO`), `classification` (the enum in
`schema.py`), `cause_type`. Then ingest:

```bash
.venv/bin/python scripts/forensic_audit/ingest_agent_findings.py <A1.json> <A2.json> ...
```

## The five agents

1. **Source/module parity** — compare the upstream clone vs `qubit_runtime/`
   through the `hermes_*`→`qubit_*` rename map: Python modules/packages, CLI
   command registration, gateway lifecycle, agent conversation loop, entry
   points, dependency pins.
2. **Providers/models + toolsets + skills + plugins** — provider registry,
   model metadata and context windows, toolset definitions and the default
   bundle, skill discovery/loading/invocation, plugin catalog and invocation.
3. **MCP + sessions + config/env/secrets + cron/hooks/channels/webhooks/pairing/
   devices/files** — route↔handler wiring, single-store authority, secret
   resolution, config schema/precedence, and the operational surfaces.
4. **Runtime/config forensics + fresh-install + real catalog** — all reasons a
   capability can fail despite source existing (wrong defaults, `~/.hermes`
   fallback, stale units, shadowed routes, read/write split-brain, visible but
   non-invocable catalog entries); plus a clean-room install matrix and
   representative invocations of each catalog.
5. **Frontend + packaging/update + rebrand + future breakage** — UI route/API
   wiring, built-bundle consistency, installer/service/update behavior, legacy
   brand classification, and forward-looking breakage (version skew, races,
   stale manifests, unpinned deps, platform gaps).

## Merge semantics

Tier-1 fingerprints are `tier1_<agent>/<id>`, so they do not collide with Tier-0
`<check>/<slug>`. On ingest, a finding already in the ledger keeps its status;
new ones enter as `NEW`. Re-render with `ingest_agent_findings.py --render`.

## Rules of evidence

A route that returns 200 is not a working capability; a typed refusal is not
implemented; a catalog row is not a function until a safe invocation succeeds.
Every claim needs a command + observed output.
