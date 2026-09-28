# Differential parity fixtures (v1)

Sanitized, isolated fixtures and JSON Schemas consumed by
`scripts/differential_parity.py` under the frozen interface in
`/tmp/qubit-differential/INTERFACE.md`. Nothing here is a test by itself; the
runner and `tests/test_differential_parity8u.py` execute these rows and apply
the fail-closed gates.

## Layout

```
tests/fixtures/differential/
  MANIFEST.json              # {"schema_version":1,"fixtures":[{path,sha256,family,fixture_id}]}
  README.md                  # this file
  sanitize.py                # stdlib scrubber for captured artifacts
  schema/row.schema.json     # inventory Row (draft 2020-12)
  schema/fixture.schema.json # Fixture object (draft 2020-12)
  negative/expected_diff.json# intentional NON-approved difference (must FAIL)
  <family>/<name>.json       # one fixture per capability; family is one of the 28
```

Each `family/` directory holds representative fixtures. `fixture_id` is the
stable `capability_id` the inventory row points at, so a fixture can never
drift from its row.

## Adding a fixture

1. Pick the family directory and a stable id `family.area.name`.
2. Copy an existing fixture in that family and keep the keys from the interface:
   `schema_version, fixture_id, family, command|request, normalizers, expected,
   qubit, hermes, openclaw, notes`.
3. Use *either* a `command` block (CLI `argv`, run by the qubit adapter) or a
   `request` block (HTTP/RPC/WS/SSE/voice; executed only by a dedicated
   transport adapter — otherwise the row is `OPERATOR_ONLY`, never PASS). The
   unused block is `null`.
4. Declare only normalizers from the allowlist below. A normalizer that hides a
   difference outside its documented shape is a gate failure
   (`unsafe_normalizer`).
5. Set `qubit.caller` to the production symbol; it feeds the
   `missing_production_caller` gate. `expected` is the contract the actual
   result is compared against, reporting the exact JSON path.
6. Sanitize real captures first:
   `python tests/fixtures/differential/sanitize.py capture.json fixture.json`.
7. Regenerate the manifest (and recompute hashes) after any fixture edit:
   ```bash
   python - <<'PY'
   import hashlib, json, pathlib
   root = pathlib.Path("tests/fixtures/differential")
   fs = []
   for p in sorted(root.glob("*/*.json")):
       if p.parent.name in ("schema", "negative"):
           continue
       doc = json.loads(p.read_text())
       fs.append({"path": f"tests/fixtures/differential/{p.relative_to(root)}",
                  "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                  "family": doc["family"], "fixture_id": doc["fixture_id"]})
   (root / "MANIFEST.json").write_text(json.dumps(
       {"schema_version": 1, "fixtures": fs}, indent=2) + "\n")
   PY
   ```

## Normalizer allowlist (exact, 7 keys)

| key | collapses | runner shape |
|---|---|---|
| `timestamp` | ISO-8601 / RFC-2822 strings and time-keyed epochs | `_norm_timestamp` |
| `uuid` | canonical UUID strings | `_norm_uuid` |
| `temp_path` | absolute paths under `/tmp`, `/var/tmp`, `TMPDIR`, … | `_norm_temp_path` |
| `pid` | integers/digit strings under `*pid`-shaped keys | `_norm_pid` |
| `session_token` | opaque tokens under token keys or `sess_`/`tok_` shape | `_norm_session_token` |
| `generated_hash` | `sha256:` values or hex digests under hash-ish keys | `_norm_generated_hash` |
| `product_branding` | the words hermes/openclaw/qubit | `_norm_product_branding` |

Normalizers normalize **for comparison only**; the raw result hash is always
reported too. Any other key is an error.

## No-secrets rule

- Secrets never appear in argv, env values, fixtures, logs, or reports.
- Credentials are references only: `$NAME` or `env:NAME`. The sanitizer reads
  no environment value; it emits the **name**.
- Use `sanitize.py` on every capture before committing it as a fixture. It
  fails closed if a secret shape survives.
- `scripts/differential_parity.py` scans fixtures and payloads; a hit sets
  disposition `FAIL` and raises the `secret_in_artifact` gate.

## Schemas and the UNVERIFIED policy

The schemas validate the Row and Fixture objects and use only a stdlib-checkable
subset of JSON Schema 2020-12 (`type`, `required`, `properties`,
`additionalProperties`, `enum`, `const`, `items`, `uniqueItems`, `pattern`,
`oneOf`, `$defs`/`$ref`). `additionalProperties` is open because the runner
merges defaults and adds bookkeeping fields.

Upstream (`hermes`/`openclaw`) mappings are `null` with
`"mapping_status":"UNVERIFIED"` whenever the pinned comparison clone has no
working tree. Do **not** invent a `file:line`. A row that depends on an
UNVERIFIED mapping must be classified `OPEN` by the runner until it is verified
against the pinned commits (hermes `2237be3`, openclaw `3a9d69d` / `3928bad`).

## The negative fixture

`negative/expected_diff.json` is deliberately **not** listed in `MANIFEST.json`.
It carries an `expected`/`actual` pair with an intentional unapproved
difference; feeding them to `compare_expected_actual` must emit diagnostics at
`$.json.state` (kind `value`) and `$.json.scopes["session:read"]` (kind
`missing_scope`). A runner that reports PASS for it is wrong.
