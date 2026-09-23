# Pinned Hermes byte manifest (in-repo)

`HERMES_BYTE_MANIFEST.json` is the deterministic byte-identity manifest of the
pristine pinned Hermes baseline. It backs the differential runner's fail-closed
`byte_parity` gate (`scripts/differential_parity.py::_byte_parity_gate`), so the
gate is fully in-repo; when absent the runner falls back to the legacy
`/tmp/qubit-differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json`, and
when neither exists the gate fails closed.

Owner decision 2026-09-23 (#5): the manifest is **regenerated from the real
pinned source**, never hand-written or guessed. Regenerate with:

```bash
python scripts/hermes_byte_manifest.py          # write the manifest + sha256
python scripts/hermes_byte_manifest.py --check  # verify it is up to date
```

- Pin: `d337b736aa1e8ebecfab043842d13e4a2d2f48a3` (Hermes `v0.21.4`).
- Source: the read-only pinned clone (default
  `/tmp/opencode/pinned/hermes-agent-d337b736-git`; override with
  `--upstream`/`QUBIT_PINNED_HERMES`).
- Each tracked file records its pinned git blob, SHA-256 (canonical blob
  content), and a classification. Every actionable entry is `IDENTICAL`;
  untracked generated/runtime artifacts are `NOT_APPLICABLE` (gate-exempt).
  A CRLF checkout of a `text eol=crlf` file normalizes to the same blob and is
  recorded as `eol_normalized` metadata, still `IDENTICAL`.
- If the pinned source is unavailable the generator stops with
  `BLOCKED — PINNED SOURCE REQUIRED` naming the prerequisite.
- `manifest.sha256` pins the manifest file itself; the gate verifies it when
  present.
