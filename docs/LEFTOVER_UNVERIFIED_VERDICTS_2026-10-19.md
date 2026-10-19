# Leftover unverified items — verdicts with literal evidence

Base: `main` @ `ed2cba6be`. Branch `fix/leftover-unverified`. Every claim below is a command whose
output is reproduced here.

| # | Item | Verdict |
|---|------|---------|
| 1 | Managed-worker live leg end-to-end | **BLOCKED** — exact prerequisite below |
| 2 | SIGNAL / BLUEBUBBLES / QQBOT / YUANBAO connection rows | **CLOSED — all four correct**, no fix |
| 3 | `_probe_laneA_wiring_runtime.py` type errors | **CLOSED** — cleaned, committed |
| 4 | `test-port/integration` depends on `pytest.ini pythonpath = tests` | **CLOSED — real and safe; kept and documented** |

---

## 1. Managed-worker live leg — BLOCKED

### What works

The child process is real and spawns: `python -m agent.managed_worker` introduces itself over the
frame pipe.

```
HELLO: {"type":"hello","pid":4041294,"birth":1792424443.28,"ancestors":[4041289,4041287,3410293]}
RC: 1
CHANNEL AFTER BOOTSTRAP: '{"type":"error","reason":"managed_worker_failed"}\n'
STDERR TAIL:
  File ".../agent/managed_worker.py", line 483, in main
    execute(frame, channel)
  File ".../agent/managed_worker.py", line 352, in execute
    adopted = rpc('worker.adopt', **{k: v for k, v in scope.items() if k != 'epoch'})
  File ".../agent/runtime_session_store.py", line 127, in _connection
    raise WorkerPersistenceError('owner_unavailable')
```

### Why it cannot be driven here

`execute()`'s first act after binding policy is `WorkerRPC('worker.adopt')`. That RPC discovers a
gateway endpoint over the control socket and requires `runtime_protocol == 1`, `state == "ready"`
and `capabilities` containing `session-authority-v1`
(`qubit_cli/gateway_runtime.py:129-160`). The worker is unusable without a live *session-authority*
owner.

**The gateway this fork actually starts does not build a session authority.** `qubit gateway run`
resolves to `gateway/run.py:start_gateway` (`qubit_cli/gateway.py:4646`, `:4678`). The identity it
publishes carries none of the runtime-descriptor keys:

```
identify payload keys: ['code_sha','code_version','kind','launch_supervisor','pid','profile','protocol','qubit_home','start_time','supervisor']
  runtime_protocol     ABSENT
  state                ABSENT
  capabilities         ABSENT
  served_profiles      ABSENT
  instance_id          ABSENT
  authority_epoch      ABSENT
```

Those keys are added only by `gateway/run_bootstrap.py`'s `_identify_runtime()`, from
`runner.session_runtime_descriptor`, which only `gateway/run_runtime.py:initialize_gateway_runtime()`
populates. `gateway/run.py` contains **no** reference to `initialize_gateway_runtime`,
`session_runtime_descriptor` or `SessionAuthorities`:

```
$ grep -n "initialize_gateway_runtime\|session_runtime_descriptor\|SessionAuthorities" gateway/run.py
(no output)
```

So the consequence holds even with a live gateway listening:

```
discover_gateway_endpoint -> state='incompatible' reason_code='runtime_protocol' endpoint=None
worker.adopt raised: WorkerPersistenceError : owner_unavailable
```

### Second, independent blocker

`gateway/run_bootstrap.py` — the path that *would* build the authority — cannot complete a boot.
It duplicates `_start_gateway_claim_pid_file` from `gateway/run.py` (a faithful copy plus two
added lines) and calls it at `:779`, **after** `_start_gateway_reserve_profiles` at `:770`, which
takes a non-reentrant `flock` on the same `<home>/gateway.lock`:

```
[acquire] 9 -> True                      # runtime_ownership.reserve(): <home>/gateway.lock
  ... gateway/run_bootstrap.py:770 in start_gateway   _start_gateway_reserve_profiles
  ... gateway/runtime_ownership.py:167 in reserve     _try_acquire_file_lock(handle)
[acquire] /tmp/gwra/gateway.lock -> False   # get_running_pid() probe: "someone holds it"
[acquire] /tmp/gwra/gateway.lock -> False   # acquire_gateway_runtime_lock()
RESULT: False
```

Counterfactual in one process, no boot involved:

```
owns after reserve: True
acquire_gateway_runtime_lock() after ownership reserve -> False
after ownership release -> True
```

`flock` is per open-file-description, so the process's own reservation blocks its own runtime-lock
claim; `get_running_pid()` then reports the (dead, self-owned) record as a foreign owner.

**Not fixed on purpose.** Whether upstream orders these the other way could not be checked — there
is no upstream checkout in this environment and the pin (`43faacfb8de7`) is not fetchable offline.
Fixing it means restructuring gateway boot in a working tree shared with a live agent tree, which is
outside this item's scope and would be an unverified change to the boot path everything depends on.

### Missing prerequisite (exact)

A **live session-authority gateway** for the worker's home, i.e. one whose `identify` reports
`runtime_protocol=1`, `state="ready"` and `session-authority-v1` in `capabilities`. Producing one
requires first fixing the `reserve()` → `claim_pid_file` lock inversion in `gateway/run_bootstrap.py`
and wiring `qubit gateway run` to that module (or to `initialize_gateway_runtime`) instead of
`gateway/run.py`. Until then `worker.adopt` raises `owner_unavailable` before any turn runs, so no
ledger keys can reach the frame via the subprocess leg.

The projection half is proven, and only the projection half: `accept_result` carries all nine keys
(`scripts/_probe_laneA_wiring_runtime.py`, arm 2):

```
managed-worker leg missing: []
managed-worker usage: {'input_tokens': 11, 'output_tokens': 4, 'total_tokens': 15,
 'estimated_cost_usd': 0.004, 'cost_status': 'ok', 'cost_source': 'provider',
 'cache_read_tokens': 7, 'cache_write_tokens': 2, 'reasoning_tokens': 1,
 'provider': 'custom:work', 'turn_exit_reason': 'complete', 'service_tier': 'standard'}
```

That is `accept_result` applied to a *synthetic* frame — exactly the gap this item was meant to
close, and it remains open.

---

## 2. SIGNAL / BLUEBUBBLES / QQBOT / YUANBAO — CLOSED, all four correct

All four are plugin-owned in this fork, and each plugin entry supplies
`config_connected_fn=config_connected_extra(...)`. `GatewayConfig._is_platform_connected`
(`gateway/config.py:694`) consults `_PLATFORM_CONNECTED_CHECKERS` first — none of the four is in it
— then resolves the registry entry's `config_connected_fn` *before* the generic `token or api_key`
branch. Probe: `scripts/_probe_plugin_platform_rows.py`.

```
platforms in _PLATFORM_CONNECTED_CHECKERS: ['api_server','msgraph_webhook','relay','webhook','whatsapp_cloud']

=== SIGNAL ('signal')
  in _PLATFORM_CONNECTED_CHECKERS: False
  registry entry found: True | name='signal'
  entry.config_connected_fn: <function config_connected_extra.<locals>.config_connected ...>
  FULL extra {'http_url': 'http://127.0.0.1:8080'} -> connected: True
  EMPTY extra -> connected: False
  DECOY  decoy_http_url instead of http_url -> connected: False

=== BLUEBUBBLES ('bluebubbles')
  in _PLATFORM_CONNECTED_CHECKERS: False
  registry entry found: True | name='bluebubbles'
  FULL extra {'server_url': 'http://x', 'password': 'p'} -> connected: True
  EMPTY extra -> connected: False
  PARTIAL ['password'] -> connected: False
  PARTIAL ['server_url'] -> connected: False
  DECOY  decoy_server_url instead of server_url -> connected: False
  DECOY  decoy_password instead of password -> connected: False

=== QQBOT ('qqbot')   FULL {'app_id','client_secret'} -> True; EMPTY -> False; both PARTIALs -> False; both DECOYs -> False
=== YUANBAO ('yuanbao') FULL {'app_id','app_secret'} -> True; EMPTY -> False; both PARTIALs -> False; both DECOYs -> False
```

Verdict per platform: **(a) correct via the plugin entry** — strict on every key, not permissive on
empty or partial config, and a decoy key never satisfies it. The `extra` key names match what each
adapter actually reads (`signal/http_url`, `bluebubbles/server_url+password`,
`qqbot/app_id+client_secret`, `yuanbao/app_id+app_secret`). **No `_needs_extra` row is needed and
none was added.**

---

## 3. `_probe_laneA_wiring_runtime.py` — CLOSED

Found untracked in worktree `wt-verify14` (never in any branch — `git log --all --oneline --
qubit_runtime/scripts/_probe_laneA_wiring_runtime.py` was empty). Committed on this branch as
`qubit_runtime/scripts/_probe_laneA_wiring_runtime.py`.

Before (basedpyright 1.40.2, `basic`, `extraPaths=<worktree>/qubit_runtime`): **5 errors**, including
the two the task named — `No overloads for "abspath" match the provided arguments` and
`Argument of type "str | None" cannot be assigned to parameter "path"`. After: **errorCount 0,
warningCount 0**. All six arms still run, exit 0.

The two `abspath` errors were not cosmetic: a module with a `None` or absent `__file__` raised
`AttributeError`/`TypeError` in the `sys.path` cleanup. The three remaining errors were
`server._profile_default_cwd` / `_is_other_profiles_cwd` / `_completion_cwd` — functions that reach
`server` only through `method_ctx.bind_module`, so no checker can ever see them as attributes. Arm 6
exists precisely to prove that absence, so those three are reached via `getattr` rather than by
weakening the checker.

---

## 4. `pytest.ini pythonpath = tests` — CLOSED, real and safe

Removing only that line breaks collection immediately:

```
$ pytest ... -c /tmp/pytest_nopp.ini --co
ImportError while loading conftest '.../tests/conftest.py'.
tests/conftest.py:15: in <module>
    import tmp_policy
E   ModuleNotFoundError: No module named 'tmp_policy'
```

With it, the same target collects (4 tests) and a sampled run is green: `16 passed in 9.10s`.

It cannot affect the other tree — `qubit_runtime/` is a separate rootdir with its own configfile:

```
cwd=qubit_runtime -> rootdir: .../qubit_runtime   configfile: pyproject.toml
cwd=repo root     -> rootdir: .../wt-leftover     configfile: pytest.ini
```

and `qubit_runtime/tests/gateway/test_whatsapp_cloud_connected.py` passes there (`2 passed`).
The one `tests/qubit_platform/` collection error is unrelated and pre-existing
(`cannot import name 'looks_like_nvidia_arm_soc'` — absent from `products.py` at HEAD).

**Kept.** Documented in `test-port/integration`'s `docs/TEST_PORT_RECOVERY_2026-10-19.md`.
