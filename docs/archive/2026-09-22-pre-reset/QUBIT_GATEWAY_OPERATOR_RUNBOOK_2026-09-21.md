# Qubit Gateway / Service Operator Runbook — 2026-09-21

**Status:** operator procedure for the Qubit-owned gateway/service supervision
surface. This is a runbook, **not** completion evidence and **not** a
verification claim. Read alongside
[QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md](QUBIT_ACTIVE_EXECUTION_BRIEF_2026-09-21.md)
and [QUBIT_LOCAL_RUNTIME_AUDIT_2026-09-21.md](QUBIT_LOCAL_RUNTIME_AUDIT_2026-09-21.md).
The live supervisor is `voicebridge/gateway_supervisor.py`; the dashboard routes
are wired in `voicebridge/qubit_compat.py` (`h_gateway`,
`h_gateway_supervision`, `h_gateway_restart`).

> **Warning:** the dashboard Start/Stop/Restart actions act on the **service
> unit that serves the request you are making**. `restart`/`stop` are
> dispatched detached, so the HTTP response is delivered first and the process
> is replaced ~2 s later. Expect the dashboard/WebSocket to drop and reconnect
> on a restart. Never run these controls against a second Qubit instance you do
> not intend to replace.

---

## 1. Identity and scope

| Item | Value |
| --- | --- |
| Canonical unit | `qubit-voicebridge.service` (default; `DEFAULT_UNIT`, `voicebridge/gateway_supervisor.py:29`) |
| Scope | `user` by default (`systemctl --user`); `system` is opt-in |
| Config keys | `gateway.service_unit`, `gateway.system_scope` |
| Unit template | `systemd/qubit-voicebridge.service` (`ExecStart=…/.venv/bin/python -m voicebridge run`, `Restart=on-failure`, `RestartSec=3`, `StartLimitBurst=5` / 300 s) |
| Canonical CLI launcher | `qubit` (alias `voicebridge`) |
| Dashboard surface | System page → Gateway card |
| Read route | `GET /api/gateway` |
| Control routes | `POST /api/gateway/start`, `POST /api/gateway/stop`, `POST /api/gateway/restart` |

The Qubit-owned process is the only thing these controls supervise. External
peers/gateways are separate records and use the approval-gated
`qubit gateway supervision-request <id> <action> --confirm` path
(`voicebridge/gateway_peers.py`); that path never touches the Qubit-owned unit.

## 2. Runtime home and paths

| Item | Path |
| --- | --- |
| Config | `~/.config/qubit-voicebridge/voice.yaml` |
| State dir | `~/.local/state/qubit-voicebridge/` (`state.db`, `profiles.json`, `approvals.jsonl`, `events.jsonl`) |
| **Qubit runtime home (canonical)** | `~/.local/state/qubit-voicebridge/runtime/` (`voicebridge.qubit_runtime.default_home()`; holds `skills/`, `plugins/`, `state.db`, `config.yaml`) |
| Installed user unit | `~/.config/systemd/user/qubit-voicebridge.service` |
| Data / cache | `~/.local/share/qubit-voicebridge/`, `~/.cache/qubit-voicebridge/` |

`~/.hermes/` is the local Hermes install and is a **read-only migration
source**, never a Qubit authority. The bridge sets
`QUBIT_HOME=<state_dir>/runtime`; do not point Qubit at `~/.hermes`.

## 3. Read the current state (safe, read-only)

Dashboard: open the System page; the Gateway card shows state, unit, PID, port
and any stale/conflict detail. The card's `supervision` field is the control
signal:

- `supervision == ""` → systemd is available; Start/Stop/Restart are enabled.
- `supervision == "GATEWAY_SUPERVISION_UNAVAILABLE: …"` → no usable
  `systemctl`; the controls are disabled with a typed reason.

CLI / operator:

```sh
qubit status                          # gateway_running / gateway_state / unit / detail
qubit product service                 # read-only service-ownership contract (unit, ExecStart, DUPLICATE/STALE/UNIT_MISSING)
systemctl --user status qubit-voicebridge.service
systemctl --user is-active qubit-voicebridge.service
```

`GET /api/gateway` also returns `process.port_in_use`, read from a real TCP
probe of `webui.host:webui.port` (default `127.0.0.1:8890`).

## 4. Control semantics

All three routes run `gateway_supervisor.control`
(`voicebridge/gateway_supervisor.py:205`):

| Condition | Result |
| --- | --- |
| Action not in `start/stop/restart` | `400 GATEWAY_BAD_INPUT` |
| Emergency stop latched | `403 GATEWAY_ESTOP` |
| `confirm` is falsy | `409 GATEWAY_CONFIRM_REQUIRED` |
| Real control disabled (test guard / `QUBIT_GATEWAY_SUPERVISION_DISABLED=1`) | `503 GATEWAY_SUPERVISION_UNAVAILABLE` |
| `systemctl` not usable | `503 GATEWAY_SUPERVISION_UNAVAILABLE` |
| `start` on an already-active unit | `200 OK`, `noop: true` (idempotent) |
| `stop` on an already-inactive unit | `200 OK`, `noop: true` (idempotent) |
| `start` on an inactive unit | runs `systemctl --user start <unit>`; `200 OK` or `500 GATEWAY_CONTROL_FAILED` |
| `restart` | spawns detached `sh -c "sleep 2.0; systemctl --user restart <unit>"`; `200` with `state: "RESTARTING"`, `detached: true` |
| `stop` on an active unit | spawns detached `sh -c "sleep 2.0; systemctl --user stop <unit>"`; `200` with `state: "STOPPING"`, `detached: true` |

**Confirmation:** the dashboard Gateway buttons POST with no body; the handler
treats a missing `confirm` as `true` (`bool(b.get("confirm", True))`), i.e.
clicking the button is the operator consent. A programmatic caller that wants
to require the confirmation gate must send `confirm: false` (or omit it
deliberately at the peer path). There is **no staged confirmation journal**
for the Qubit-owned unit start/stop/restart; the button is the gate.

**Idempotency:** re-issuing a `start`/`stop` that is already in the target state
is a successful no-op; it does not error and does not re-run systemctl.

**Detachment:** `restart`/`stop` never run synchronously in the request. The
handler spawns with `start_new_session=True` (a new process group) and returns
immediately, which is why the response survives this process being replaced.

All transitions are audited (`gateway.supervisor.<op>`) and typed failures are
redacted, never fabricated.

## 5. Operator procedures

### Restart the Qubit service

1. Save any in-flight work; the dashboard/WebSocket will drop.
2. Dashboard → System → Gateway → **Restart**.
3. Response is `RESTARTING` immediately; the unit restarts ~2 s later.
4. Wait ~5 s, then reload the dashboard and confirm `gateway_running` is true
   and the port is accepting connections.
5. If the unit does not come back, inspect `systemctl --user status
   qubit-voicebridge.service` and `journalctl --user -u qubit-voicebridge.service -n 100`.

### Start a stopped service

Dashboard → **Start**. If the unit is already active the call is a no-op
(`noop: true`). If `systemctl` is unavailable the card is disabled with
`GATEWAY_SUPERVISION_UNAVAILABLE`.

### Stop the service

Dashboard → **Stop**. Active unit → detached stop, `state: "STOPPING"`; already
stopped → no-op. Use stop only when you intend to take the bridge (and its
microphone) offline.

### Emergency stop

The gateway controls are refused (`403 GATEWAY_ESTOP`) while the shared e-stop
latch is engaged. Reset the latch through the canonical power/e-stop control
before any start/stop/restart.

## 6. Stale process and port-conflict symptoms

`GET /api/gateway` / `qubit status` surface two typed detail strings from the
status probe (`voicebridge/gateway_supervisor.py:155-161`):

- **"port is in use but the service is not active (stale process?)"** — a
  process (or another instance) owns `webui.host:webui.port` while the unit is
  inactive. Symptoms: `qubit` refuses to bind, dashboard won't load, two
  listeners. Remediation: identify the owner
  (`ss -ltnp | grep :8890`, `systemctl --user status qubit-voicebridge.service`,
  `pgrep -af 'voicebridge run'`), stop the stray/manual instance, then start the
  single canonical unit. Never run two active listeners.
- **"service active but the configured port is not accepting connections; the
  listener may still be starting"** — wait a few seconds and re-read; if it
  persists, check the unit's journal for a bind failure or a config error.
- A **DUPLICATE** service-ownership state (canonical unit plus a legacy unit
  file, or two active units) is reported by `qubit product service`; disable
  legacy units explicitly so exactly one service owns one listener.
- `systemctl show` failing with "could not be found" yields `NOT_INSTALLED`;
  any other failure yields `DEGRADED` with the exact (redacted) message.

Host hygiene from the 2026-09-21 audit (operator-only, not code-side): stop the
stray Hermes `:9119` listener, run a single Qubit instance, remove stale units.

## 7. Rollback / recovery

- The supervisor's transitions are service-control actions, not data
  mutations; there is no data rollback to perform for a start/stop/restart.
- To reverse a stop: use **Start** (dashboard) or
  `systemctl --user start qubit-voicebridge.service`.
- To recover from a bad unit edit: the installer backs up the installed unit
  before overwrite; restore the backup and run
  `systemctl --user daemon-reload && systemctl --user restart qubit-voicebridge.service`.
- Migration/rollback of paths is a separate, backup-based flow:
  `qubit product rollback` / `qubit product migrate-rollback` (dry-run unless
  `--apply`). See `docs/archive/2026-09-21/` rollback records.
- Do not use `git checkout -- <file>` to revert worktree changes; the worktree
  is mid deep-rebrand and may carry unrelated uncommitted changes.

## 8. Running the focused tests

The supervisor unit tests and the full-dispatch integration tests are hermetic:
an injected fake `systemctl` on `PATH` and an injected detached spawner are
used, and the fail-closed guard is disabled only inside the test process. **No
host service is touched.**

```sh
python -m pytest tests/test_gateway_supervisor9u.py \
                 tests/test_gateway_control_integration9u.py -q
```

Recorded result for this runbook: **13 passed** (6 supervisor + 7 integration).
The integration cases drive `voicebridge.qubit_compat.dispatch`:
`GET /api/gateway` (empty vs typed supervision), start on inactive/active,
detached restart (`RESTARTING`, no synchronous `systemctl restart`), stop
(`STOPPING`), and the unconfirmed-refusal path.

Set `QUBIT_GATEWAY_SUPERVISION_DISABLED=1` to force the guard on for any manual
exercise; the guard is also on automatically inside pytest
(`voicebridge/gateway_supervisor.py:188`).

## 9. Cautions and uncertainty

- **Do not make a live mutating HTTP call to a running Qubit/Hermes server
  outside pytest.** A prior exploratory `POST /api/gateway/restart` actually
  restarted `qubit-voicebridge.service` once; the guard only applies inside a
  test process.
- The Qubit-owned unit's process control is currently exposed **only** through
  the dashboard routes; there is no dedicated `qubit` CLI subcommand that calls
  `gateway_supervisor.control`. The CLI `qubit gateway` family manages records
  and the approval-gated peer supervision path, not the local service unit.
  Operator-level control of the local unit remains `systemctl --user`.
- The control handler treats a missing `confirm` as true; the dashboard button
  is the consent. Treat programmatic callers with care.
