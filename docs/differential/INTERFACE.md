# FROZEN interface — scripts/differential_parity.py (v1)

Agents 7 (runner), 8 (tests), 9 (fixtures) MUST conform to this exactly.
Never weaken a test. Fail closed. Secrets never in argv/logs/fixtures/reports.

## CLI
python scripts/differential_parity.py inventory --out <json>
python scripts/differential_parity.py run --inventory <json> --fixtures-dir tests/fixtures/differential --out <json> [--family NAME] [--row ID]
python scripts/differential_parity.py verify --report <json>
python scripts/differential_parity.py selftest
python scripts/differential_parity.py --version

Exit codes:
 0 = all executed rows PASS or SAFE_EQUIVALENT; OPERATOR_ONLY allowed and counted
 1 = any FAIL, any gate failure, any unexplained difference
 2 = usage error
 3 = a required fixture/adapter is absent and cannot even be classified (never treat as PASS)
 4 = internal error

## Normalizer allowlist (EXACT keys; anything else is an error)
timestamp, uuid, temp_path, pid, session_token, generated_hash, product_branding
- Normalizers normalize ONLY for comparison; the raw hash is always reported too.
- A row must declare its normalizers in its fixture. Using a normalizer that hides
  a difference outside its documented shape is a gate failure (`unsafe_normalizer`).

## Row schema (inventory manifest entry)
{
  "capability_id": "family.area.name",         # stable unique id
  "family": "<one of: cli api rpc ws sse provider session memory file project
              skill plugin mcp cron gateway messaging browser desktop board task
              media acp webhook auth approval recovery dashboard voice>",
  "upstream_project": "hermes|openclaw|qubit",
  "upstream_commit": "<sha or pin ref>",
  "upstream_path": "<file:line or route>",
  "upstream_symbol": "<symbol/route/selector>",
  "upstream_hash": "sha256:<hex or null if not byte-comparable>",
  "qubit_path": "<file:line or route>",
  "qubit_symbol": "<symbol/route/selector>",
  "disposition": "PASS|FAIL|SAFE_EQUIVALENT|OPERATOR_ONLY|OPEN",
  "command": "<exact command or request spec>",
  "fixture": "tests/fixtures/differential/<path>.json | null",
  "fixture_hash": "sha256:<hex>|null",
  "upstream_result_hash": "sha256:<hex>|null",
  "qubit_result_hash": "sha256:<hex>|null",
  "test_ids": ["tests/test_differential_parity8u.py::test_x"],
  "trace_refs": ["/tmp/qubit-differential/evidence/....json"],
  "operator_prerequisite": "<string|null>",
  "rollback_ref": "git revert <sha> | <file backup hash>",
  "normalizations": ["timestamp","uuid"]
}

## Fixture schema
tests/fixtures/differential/<family>/<name>.json
{
  "schema_version": 1,
  "fixture_id": "<matches capability_id>",
  "family": "<family>",
  "command": {"argv": [...], "stdin": null, "env": {"KEY":"<ref>"}},
  "request": {"method":"GET","path":"/...","headers":{},"body":null},
  "normalizers": ["timestamp","uuid"],
  "expected": {"exit_code":0,"stdout_schema":{},"http_status":200,"json_schema":{},"events":[]},
  "qubit":   {"path":"voicebridge/x.py:1","caller":"<symbol>","approval":null},
  "hermes":  {"path":"<upstream file:line>","symbol":"..."},
  "openclaw":{"path":"<upstream file:line>","symbol":"..."},
  "notes": "..."
}
Tests/fixtures/differential/MANIFEST.json = {"schema_version":1,"fixtures":[{"path":..,"sha256":..,"family":..,"fixture_id":..}]}

## Result hashing
result_hash = "sha256:" + sha256( canonical_json(payload) ).hexdigest()
canonical_json = json.dumps(obj, sort_keys=True, separators=(",",":"), ensure_ascii=True)
Deterministic; same input -> same hash across runs and machines.

## Diagnostics
compare_expected_actual ... -> list of Diagnostic(path="$....", kind=..., detail=...)
On difference print the EXACT JSON path (or CLI field / route / event / DOM
selector / screenshot region / voice operation) and both values (REDACTED if secret-shaped).

## Fail-closed gates (verify must emit a gate result, never silently pass)
unexecuted_row, missing_production_caller, skipped_fixture, mock_replaced_real_path,
refusal_counted_as_success, unsafe_normalizer, visual_only_control,
voice_backend_mismatch, persistence_or_recovery_diff, secret_in_artifact.

## Report JSON top level
{"schema_version":1,"generated_by":"scripts/differential_parity.py","runner_version":"1",
 "rows":[ <row result objects> ],
 "totals":{"total":N,"pass":N,"fail":N,"safe_equivalent":N,"operator_only":N,"open":N},
 "gates":[{"gate":"...","ok":bool,"rows":[...],"detail":"..."}],
 "verdict":"VERIFIED|VERIFIED WITH EXPLICIT LIMITATION|INCOMPLETE"}

## Row-result schema (v1.1 addendum — previously undocumented)
run_inventory returns one result object per row with AT LEAST these fields:
{
  "capability_id": str,
  "declared_disposition": "PASS|FAIL|SAFE_EQUIVALENT|OPERATOR_ONLY|OPEN",
  "disposition": "PASS|FAIL|SAFE_EQUIVALENT|OPERATOR_ONLY|OPEN",
  "executed": bool,               # a real production path was invoked
  "qubit_executed": bool,
  "reference_executed": bool,
  "fixture_present": bool,
  "adapter_mock": bool,           # a mock replaced the real production path
  "refused": bool,                # typed refusal/denial occurred
  "unsafe_normalizers": [str],    # normalizers that collapsed an out-of-shape diff
  "visual_only": bool,            # dashboard control reached no canonical op
  "voice_mismatch": bool,         # voice resolved a different canonical op
  "persistence_diff": bool,
  "secret_in_artifact": bool,
  "production_caller": str|null,  # Qubit symbol that really invokes the op
  "diagnostics": [{"path":str,"kind":str,"detail":str}],
  "operator_prerequisite": str|null,
  "upstream_result_hash": str|null,
  "qubit_result_hash": str|null,
  "fixture_hash": str|null,
  "unclassifiable": bool          # absent adapter/fixture that cannot be classified
}

## Gate semantics (v1.1 clarification)
- `unexecuted_row`: fires ONLY when a row CLAIMS PASS/SAFE_EQUIVALENT while
  executed is False. An OPEN/OPERATOR_ONLY unexecuted row is NOT a success
  claim; OPEN omissions are caught by the all-inventory coverage gate/test.
- `skipped_fixture`: fixture_present True, executed False, disposition != OPERATOR_ONLY.
- A test that wants `unexecuted_row` must set disposition PASS (not OPEN).
- `openclaw_undisposed_addition`: fires when a row from the OpenClaw
  latest-release/default-branch delta has no Qubit disposition and proof.

## Plan §8/§15 coverage additions (v1.1 required)
Row/evidence must additionally carry (null allowed, but the key must exist and
be populated for any PASS/SAFE_EQUIVALENT row):
  voice_utterance, dashboard_route, dashboard_control, request_schema,
  broker_policy_decision, runtime_provider, store_state_assertion,
  output_event_assertion, error_codes, retry_recovery_proof,
  audit_receipt_proof, security_resource_proof, package_proof, response_headers
Runner must compare, where the adapter exposes them: HTTP response headers
(non-nondeterministic only), persisted-state assertions, help text (assert the
CLI help contains the canonical Qubit command), DOM/accessibility/screenshot
evidence refs, and OpenClaw latest-vs-default-branch delta rows.

Note on the normalizer allowlist: the owner brief for this wave explicitly
authorizes SEVEN fields — timestamps, UUIDs, temporary paths, process IDs,
session tokens, generated hashes expected to differ, and product branding.
`generated_hash` is therefore in-scope for this wave even though plan §8 lists
six; record the authorization in the evidence report.

## Required runner API surface (importable)
load_inventory(path)->list[dict]
run_inventory(rows, fixtures_dir, adapters=None)->list[dict]
apply_fail_closed_gates(rows, results)->list[dict]
normalize(obj, normalizers:list[str])->obj
result_hash(payload)->str
compare_expected_actual(expected, actual, path="$")->list[Diagnostic]
ALLOWED_NORMALIZERS: dict[str, callable]  # exact 7 keys above
selftest()->int

## TRANSPORT ADAPTER CONTRACT (v1.2 — FROZEN for wave 4)
Adapter.execute(spec) -> CallResult. `spec.kind` one of: cli, http, ws, sse, rpc.
CallResult fields: exit_code:int|None, http_status:int|None, headers:dict,
json:any, stdout:str, stderr:str, events:list, persisted_state:any,
duration_ms:int|None, transport:str.

- http: spec {kind:"http", method, path, headers, body}. Base URL from
  QUBIT_DIFFERENTIAL_<PROJECT>_URL (QUBIT/REFERENCE), token header name from
  fixture; token VALUE from env only, never in fixture/log/argv.
- sse:   spec {kind:"sse", method, path, headers, body}; events preserve
  server order in `events`; each event {event,id,data,retry}.
- ws:    spec {kind:"ws", path, headers, messages:[...]}; capture handshake,
  scopes, events, reconnect attempts in `events`/`persisted_state`.
- rpc:   spec {kind:"rpc", method, params}; capture result/error envelope,
  scope checks, reconnect semantics.
- Reference transports use the same shape with project=hermes|openclaw.
- No transport adapter may invent a network path; if no URL/token resolves,
  the row is OPERATOR_ONLY with an operator_prerequisite (never PASS).

## TRANSPORT FIXTURE ADDITIONS (v1.2)
Request fixtures may add:
  "transport": "http|sse|ws|rpc",
  "base_url_ref": "env:QUBIT_DIFFERENTIAL_QUBIT_URL",
  "auth": {"header":"X-Api-Token","value_ref":"env:QUBIT_DIFFERENTIAL_QUBIT_TOKEN"},
  "sequence": [ {step spec}, ... ],   # for multi-step handshake/reconnect
  "events_expected": [ {"event":..., "order":N} ],
  "persisted_state_schema": {...}
Schema files: tests/fixtures/differential/schema/{row,fixture,row_result}.schema.json
must accept these keys. Family fixtures live only in tests/fixtures/differential/<family>/.
