# Qubit operator hygiene record — 2026-09-19 (PRA-07)

Status: operator/environment record. Not a code requirement, not an
acceptance-contract change, not a completion claim.

## Observed environment (read-only inspection; nothing stopped)

The current worktree's runtime is not the only listener on this host. The
independent pre-rebrand audit observed:

| Item | Evidence | Owner classification |
|---|---|---|
| Stale Qubit process | `qubit run --no-hotkey --mute` pid 1329028, started 2026-09-17, listening `0.0.0.0:8890`; predates the current worktree | environment |
| External Hermes dashboard | `hermes dashboard --port 9119 --host 127.0.0.1` pid 3043422, listening `127.0.0.1:9119` (`~/.hermes/hermes-agent/...`), unit `hermes-dashboard.service` | environment |
| Stale user unit | `~/.config/systemd/user/qubit-voice-agent.service` is **disabled but installed** and points at the old checkout (`ExecStart=<legacy-repo-path>/.venv/bin/python -m voicebridge run`) | environment |
| Canonical unit | `~/.config/systemd/user/qubit-voicebridge.service` is installed and enabled | Qubit |
| Other enabled upstream units | `openclaw-gateway.service`, `openclaw-dashboard-stress.service` | environment |
| Legacy unit | `openclaw-voice-bridge.service` (disabled) | legacy |

No service was stopped, disabled, masked, or removed by the auditor.

## Qubit cannot silently depend on the external runtime

The product code confines its runtime home and rejects the external install:

- `voicebridge/hermes_runtime.py` `runtime_home_for_cfg` / `use_qubit_home`
  resolve the Qubit-owned home; `_external_default_home()` (`~/.hermes`) is
  used only to *reject* it.
- `voicebridge/hermes_runtime.py::assert_safe_test_home` refuses the operator's
  real home from a test process.
- `voicebridge/pty_ws.py::_default_spawn` sets `HERMES_HOME` to the Qubit home
  before spawning the vendored TUI.
- `voicebridge/hermes_asgi.set_home` points the in-process backend at the Qubit
  home before the fork imports.
- `voicebridge/hermes_session_store.qubit_state_db_path(cfg)` follows
  `cfg.state_dir()`.
- No product source file references the operator's external Hermes home or
  `hermes-agent/venv` (guarded by `tests/test_host_hygiene_pra07.py`).

## Exact operator commands for final hygiene

Run these once, as the owner, to leave a single current authority. They are
destructive to stale processes/units only; each is safe to skip if you intend
to keep the external install running.

```bash
# 1. Stop the stale 2026-09-17 Qubit process holding :8890 (replace PID if the
#    current one differs; verify with: ss -ltnp | grep :8890).
kill 1329028

# 2. Stop the external Hermes dashboard on :9119 (and keep it stopped).
systemctl --user disable --now hermes-dashboard.service

# 3. Remove/redirect the stale legacy unit that points at the old repo.
systemctl --user daemon-reload
rm -f ~/.config/systemd/user/qubit-voice-agent.service
systemctl --user daemon-reload

# 4. Optionally stop other upstream units not needed by Qubit.
systemctl --user disable --now openclaw-gateway.service openclaw-dashboard-stress.service openclaw-communications-monitor.timer 2>/dev/null || true

# 5. Start the canonical unit and confirm exactly one Qubit listener.
systemctl --user enable --now qubit-voicebridge.service
systemctl --user status qubit-voicebridge.service --no-pager
ss -ltnp | grep -E ':8890|:9119'   # expect only the canonical Qubit listener

# 6. Re-run the final verification matrix against that single instance.
```

Notes:

- Run one Qubit instance per state directory
  (`$XDG_STATE_HOME/qubit-voicebridge`); do not point two instances at one home.
- If the external Hermes data must be kept, import it first with
  `qubit product hermes-migrate --apply` (read-only source; see
  `docs/QUBIT_HERMES_CUTOVER_MIGRATION.md`), then stop the external listener.
