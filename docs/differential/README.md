# Differential parity: in-repo defaults

`scripts/differential_parity.py` resolves its defaults **inside the repository**
so a run is deterministic and reproducible without the untracked
`/tmp/qubit-differential/` analyst tree. The legacy `/tmp` locations are still
accepted: every `--inventory` / `--source` / `--fixtures-dir` / `--out` override
and an explicit `/tmp` path keep working, and
`QUBIT_DIFFERENTIAL_BYTE_MANIFEST` still overrides the byte manifest.

## Frozen interface contract (in-repo)

`docs/differential/INTERFACE.md` is the canonical in-repo copy of the frozen
runner interface (formerly only at `/tmp/qubit-differential/INTERFACE.md`). It
defines the CLI, row/fixture/result schemas, normalizer allowlist, transport
adapter contract, and fail-closed gate semantics the runner must conform to.

## OpenClaw contractual-pin -> latest upgrade/diff manifest

`scripts/openclaw_upgrade_manifest.py` deterministically generates
`docs/archive/2026-09-21/QUBIT_OPENCLAW_UPGRADE_MANIFEST.json` read-only from a pristine OpenClaw
clone. It records the contractual pin, observed pin, latest release, and latest
default branch; the gateway method-surface delta; per-area added files; the
license; and a mechanical disposition overlay that stamps a delta method
`IMPLEMENTED` only on an exact Qubit RPC-registry match (every other delta stays
`OPEN`). No timestamps are embedded, so the output is byte-deterministic. The
contractual pin is never replaced (owner decision 2026-09-18).

## Default locations

| input / output | in-repo default |
|---|---|
| fixtures dir | `tests/fixtures/differential` |
| inventory seed (run/merge anchor) | `tests/fixtures/differential/SEED_INVENTORY.json` |
| declarations seed | `tests/fixtures/differential/SEED_DECLARATIONS.json` |
| inventory drop dir | `tests/fixtures/differential/inventory/` |
| merged inventory output | `docs/differential/reports/inventory.json` |
| parity report output | `docs/differential/reports/differential_report.json` |
| byte manifest (preferred, else `/tmp` fallback) | `docs/differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json` |

The byte manifest is gated: if the in-repo copy is absent the runner falls back
to `/tmp/qubit-differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json`, and
if neither exists the `byte_parity` gate fails closed. No manifest is
fabricated.

## Seed vs. full inventory

`SEED_INVENTORY.json` is a tiny, committed reproducibility anchor: a single row
per representative family, every row `OPEN` / `UNDISPOSED`, with no fabricated
PASS or SAFE_EQUIVALENT. It is **not** the full inventory. The canonical
inventory is still produced by merging the real analyst sources:

```bash
python scripts/differential_parity.py inventory \
  --source /tmp/qubit-differential/inventory \
  --out docs/differential/reports/inventory.json
```

## Self-check

`self-check` (equivalently `--self-check`) validates the in-repo defaults, the
committed seed schema and runner determinism **without** executing the
inventory, opening a socket or spawning a subprocess:

```bash
python scripts/differential_parity.py self-check
python scripts/differential_parity.py --self-check
```

It exits `0` when all checks pass and `1` on any failure. It does not replace
the full `selftest` (which exercises the runner internals); it is the cheap,
hermetic guard that the reproducibility anchor is intact.
