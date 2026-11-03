# Scripts

Operational and audit tooling. Product runtime scripts that ship with Qubit live under
`qubit_runtime/scripts/`; this directory holds repository-level tooling.

- Top level: maintained entry points (build, install, release, verification, docs).
- Retired one-off probes, audits, and migrations live in the external Kingston archive.
- `forensic_audit/`: audit ingestion and reporting package.
- `merge_v0215/`: upstream merge driver.

If a script is still referenced by CI, a systemd unit, or `AGENTS.md`, it stays at the
top level. New one-off investigations belong in `legacy/` (or an external archive), not
at the root.
