# RETIREMENT CERTIFICATE — can `~/.hermes` be retired?

**Wave:** p6 (final retirement verification)
**Date:** 2026-10-08T11:27 EEST
**Base commit:** `a9fb9ce3` (branch `p6`, worktree `/tmp/qb-p6`)
**Frozen target:** `ad12263a5ed4` (docs/FROZEN_TARGET.md)
**Author role:** adversarial verifier of the "port is done, retire hermes" claim.

> **VERDICT: NO-GO.**
> One deciding reason: **the port is NOT at production parity — the frozen-target
> instrument reports `6 production missing`**, and retirement deletes the only copy
> of features Qubit has not yet grown. The retirement gate refuses on this guard
> (and, independently, on guard (b): `hermes-dashboard.service` is still
> enabled+active). All other criteria pass, and the move is proven **reversible**.

---

## 1. The four certification criteria — raw evidence

### (a) Frozen-target production parity — **FAIL (6 production missing)**

Command:
```
rm -f .frozen_target_progress_cache.json && .venv/bin/python scripts/frozen_target_progress.py
```
Verdict line, verbatim:
```
VERDICT: NOT AT TARGET -- 6 production missing (509 test missing, non-blocking)
```
Full burndown:
```
  metric                                now   base   delta
  --------------------------------------------------------
  absent files                          156    261    -105
  short_port files                       75    111     -36
  short_port symbols                    197    272     -75
  behind_head files                      79    214    -135
  behind_head symbols                   162    703    -541
  TOTAL missing symbols                 359    975    -616

  split: production vs tests
    absent:      prod    3   tests  153
    short_port:  prod    2 sym / 1 files   tests  195 sym / 74 files
    behind_head: prod    1 sym / 1 files   tests  161 sym / 78 files

  DELTA vs freeze: +616 symbols closed (359 missing now, 975 at freeze)
```
`rc=1`. **6 production symbols remain** — this is the whole campaign's blocker.
(Tests do not block retirement; production does.)

### (b) Adversarial half-port sweep, EXTENDED — **PASS (0 half-ports)**

Two independent methods, 10 modules ported in the last two waves.

**Method 1 — dynamic (execute real code paths with plausible BAD input).**
Harness: `half_port_sweep_p6.py`. A HALF-PORT is NameError, or an AttributeError
naming a symbol the module references but never defines (the `settle_lost_completion`
class of bug). A SANE result raises a real error type (TypeError/ValueError/…) on
the bad input.

| module | symbol probed | verdict |
|---|---|---|
| `qubit_platform.resolver.app` | `AppLocation` | SANE (bad-input TypeError) |
| `qubit_platform.resolver.app` | `LocationSpec` | SANE |
| `qubit_constants_scratch` | `_PruneLogHandler` | SANE |
| `qubit_constants_scratch` | `subtree_touched_since(bad)` | SANE |
| `qubit_constants_scratch` | `reap_processes_rooted_in(bad)` | SANE |
| `qubit_cli.cli_commands_session_tools` | `_command_arg(12345)` | SANE |
| `qubit_cli.cli_commands_session_tools` | `_shlex_args(None)` | SANE |
| `qubit_cli.cli_commands_session_tools` | `_probe(missing,missing,dflt)` | SANE |
| `qubit_cli.update_cmd_fleet_verify` | `_fleet_row_identity_pending(None,None)` | SANE |
| `qubit_cli.update_cmd_fleet_verify` | `_restarted_units_gone(None)` | SANE |
| `qubit_cli.update_cmd_fleet_verify` | `_live_gateway_pids_from_fleet(None)` | SANE |
| `qubit_state_sessions` | `_expand_compression_lineage_ids(None,[a,b])` | SANE |
| `qubit_bootstrap` | `_settle_interrupted_update()` | SANE |
| `tools.approval_detection` | `_HOME_PREFIX` | SANE |
| `tools.approval_detection` | `_SECRET_VAR` | SANE |
| `agent.turn_failure_copy` | `stamp_failure(None,reason,True)` | SANE |
| `agent.turn_failure_copy` | `failed_turn_notice(None)` | SANE |
| `agent.turn_failure_copy` | `provider_label_for(None)` | SANE |
| `scripts.ci.required_results` | `evaluate_gate(None)` | SANE |
| `scripts.ci.required_results` | `compact_results(None)` | SANE |
| `plugins.web.keyless_mcp` | `_is_rate_limitish(None)` | SANE |

```
PROBES: 21   SANE: 21   HALF-PORT: 0   VERDICT: PASS -- no half-ports
```

**Method 2 — static (stdlib `symtable`, F821-grade undefined-name check).**
Harness: `f821_check.py`. `symtable` is CPython's own scope analyzer: a name that is
`GLOBAL_IMPLICIT` in a function but bound neither at module scope nor as a builtin is
an undefined name — the exact half-port signature. This avoids the re-export /
co_filename confound of a naive code-object walk (which produced 50 phantom hits;
those came from *inherited stdlib methods* of `@dataclass`/`RotatingFileHandler`
subclasses — e.g. CPython's `dataclasses._recursive_repr` — and are NOT half-ports).
```
FILES: 10   UNDEFINED-NAME HITS: 0   VERDICT: PASS
```
Files checked: `qubit_platform/resolver/app.py`, `qubit_constants_scratch.py`,
`qubit_cli/cli_commands_session_tools.py`, `qubit_cli/update_cmd_fleet_verify.py`,
`qubit_state_sessions.py`, `qubit_bootstrap.py`, `tools/approval_detection.py`,
`agent/turn_failure_copy.py`, `scripts/ci/required_results.py`,
`plugins/web/keyless_mcp.py`.

Both methods agree: **no half-port in the last two waves.** The previously-shipped
`settle_lost_completion` class is confirmed fixed — `settle_lost_completion` is now
defined in `qubit_cli/update_completion.py:245` and both call sites in `update_cmd.py`
resolve it.

### (c) Deletion harness — **PASS (16/16)**

Command:
```
rm -rf sandbox home qhome && mkdir -p home && bash deletion_test.sh --rebuild
```
Isolation: `HOME=/tmp/qb-p6/home` (empty), `QUBIT_HOME=/tmp/qb-p6/qhome`, no
`~/.hermes`, `hermes` stripped from PATH. `rc=0`.

```
RESULT: 16 passed, 0 failed
```
Per-check:
```
[PASS] env: no ~/.hermes in HOME
[PASS] env: real checkout NOT on sys.path
[PASS] imports: 63/63 first-level modules import clean
[PASS] cli: `python -m qubit_cli.main --help` exits 0
[PASS] cli: `python -m qubit_cli.main --version` exits 0
[PASS] cli: subcommands > 70 (found 74)
[PASS] cli: construct cli.QubitCLI()
[PASS] import: tui_gateway.server
[PASS] import: tui_gateway.entry
[PASS] import: qubit_cli.gateway
[PASS] import: plugins.plugin_loader
[PASS] providers: list_authenticated_providers(...) returns providers
[PASS] path: no `hermes` binary resolves on the stripped PATH
[PASS] path: core home still resolves to Qubit's own with hermes off PATH
[PASS] path: no ~/.hermes created with hermes off PATH
[PASS] turn: real _chat_run_agent turn against local ollama  (qwen3-vl:2b -> 'pong')
```
Home-resolution probe with `QUBIT_HOME` unset and empty HOME:
`get_qubit_home() = /tmp/qb-p6/home/.qubit` → **OK** (never `~/.hermes`).

**Zero real `.hermes` writes from the sandbox run.** The only `~/.hermes` files
modified during the verification window were `logs/mcp-stderr.log`, `logs/agent.log`,
`state.db-wal`, `state.db-shm` — all held open by **PID 2408 = hermes-dashboard.service
itself** (confirmed via `fuser`), i.e. live-service churn, not sandbox writes. The
sandbox wrote only under `/tmp/qb-p6/home` and `/tmp/qb-p6/qhome`.

### (d) Retirement gate, dry — **GATE CLOSED** (guard (a) + (b) fail)

Command:
```
bash scripts/retire_hermes_check.sh        # dry, print-only
```
Guard states:
```
(a) production parity at frozen target ad12263a5ed4
      VERDICT: NOT AT TARGET -- 6 production missing (509 test missing, non-blocking)
  [FAIL] instrument does not report 'AT TARGET' (rc=1)

(b) hermes-dashboard.service must be DISABLED before anything moves
      is-enabled=enabled  is-active=active
  [FAIL] hermes-dashboard.service is 'enabled'

(c) four Qubit units must be enabled + active
  [ok]   qubit-voicebridge: enabled + active
  [ok]   qubit-voice-typing: enabled + active
  [ok]   qubit-voice-orb: enabled + active
  [ok]   qubit-gateway: enabled + active

GATE CLOSED -- one or more guards failed. Nothing was moved or deleted.
```
Guard (a): **FAIL**. Guard (b): **FAIL**. Guard (c): **4/4 OK**.

---

## 2. Service side — what a human must run (DO NOT RUN THESE AUTOMATICALLY)

Confirmed live state (2026-10-08T11:27 EEST):

| unit | is-enabled | is-active |
|---|---|---|
| `hermes-dashboard.service` | **enabled** | **active** (since 2026-10-03, PID 2408) |
| `qubit-voicebridge` | enabled | active |
| `qubit-voice-typing` | enabled | active |
| `qubit-voice-orb` | enabled | active |
| `qubit-gateway` | enabled | active |

`hermes-dashboard.service` (installed user unit) — the restart-storm hazard, verbatim:
```
ExecStart=/home/trick/.local/bin/hermes dashboard --port 9119 --host 127.0.0.1 --no-open
Restart=always
RestartSec=5
EnvironmentFile=/home/trick/.hermes/.env
WorkingDirectory=/home/trick
```
`/home/trick/.local/bin/hermes` is a shim that `exec`s
`/home/trick/.hermes/hermes-agent/venv/bin/hermes`. Once `~/.hermes` moves aside,
that path is dead (exit 127) **and** `EnvironmentFile=~/.hermes/.env` fails to load —
with `Restart=always`/`RestartSec=5` that is a restart every 5 seconds, forever.
**It MUST be disabled first.** The four Qubit units have no `EnvironmentFile` and no
`~/.hermes` reference — they are unaffected.

### Exact human command sequence (in order)

Preconditions:
```bash
df -h /                                                      # >= 8 GB free (have 72 G / 83% used)
cd /home/trick/Projects/Repos/qubit-voicebridge
scripts/gate_all.py                                          # expect PASS 4/4
./deletion_test.sh --rebuild                                 # expect "RESULT: 16 passed, 0 failed"
```
Step 0 — record the rollback anchor:
```bash
systemctl --user list-units --type=service | grep -E 'qubit|hermes' > /tmp/retire-before.txt
readlink -f ~/.hermes
du -sh ~/.hermes ~/.hermes/hermes-agent 2>/dev/null
systemctl --user status qubit-gateway qubit-voicebridge qubit-voice-orb qubit-voice-typing --no-pager | tail -40
```
Step 1 — retire the stray hermes unit FIRST (move the file, do not delete):
```bash
systemctl --user disable --now hermes-dashboard.service
mkdir -p ~/.config/systemd/user/retired-2026-10-08
mv ~/.config/systemd/user/hermes-dashboard.service ~/.config/systemd/user/retired-2026-10-08/
systemctl --user daemon-reload
systemctl --user is-active hermes-dashboard.service           # expect: inactive/failed
```
Step 2 — move the tree aside (atomic, reversible; do NOT delete):
```bash
mv ~/.hermes ~/.hermes.retired-$(date +%F)
systemctl --user status qubit-gateway qubit-voicebridge qubit-voice-orb qubit-voice-typing --no-pager | tail -40
journalctl --user -u qubit-gateway --since '2 min ago' | tail -30
```
Step 3 — restart the four units (the real test):
```bash
systemctl --user restart qubit-gateway.service
systemctl --user restart qubit-voicebridge.service
systemctl --user restart qubit-voice-typing.service
systemctl --user restart qubit-voice-orb.service
sleep 5
systemctl --user is-active qubit-gateway qubit-voicebridge qubit-voice-typing qubit-voice-orb
```
Step 4 — verify each surface (manual, human confirms): dashboard; `/qubit-chat`;
`/chat`; voice (Right-Ctrl + wake word). Plus:
```bash
qubit --version && qubit --help | head
qubit chat "reply with one word: pong"
qubit cron list
test -e ~/.hermes && echo "LEAK: ~/.hermes recreated" || echo "clean"
```
Step 5 — ONLY after Step 4 is green (the single irreversible step):
```bash
rm -rf ~/.hermes.retired-*
```

---

## 3. Rollback — is retirement REVERSIBLE? **YES**

**What returns the system to its current state** (any time before Step 5, or after):
```bash
systemctl --user stop hermes-dashboard.service 2>/dev/null
mv ~/.hermes.retired-* ~/.hermes
mv ~/.config/systemd/user/retired-2026-10-08/hermes-dashboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hermes-dashboard.service
systemctl --user restart qubit-gateway qubit-voicebridge qubit-voice-typing qubit-voice-orb
```
The move (`mv`) is a rename — instant and fully reversible. Nothing deletes until
Step 5, and even then the four Qubit units need nothing from `~/.hermes`.

**Is any state LOST by the move?** Checked four ways:

1. **The four Qubit units do not read `~/.hermes`.** Live-unit environments:
   - `qubit-gateway`: `Environment=QUBIT_HOME=/home/trick/.local/state/qubit-voicebridge/runtime`
   - `qubit-voice-typing`: same `QUBIT_HOME`
   - `qubit-voicebridge`: no `QUBIT_HOME` in the unit; resolves at runtime via
     `voicebridge.qubit_runtime.default_home()` → `$XDG_STATE_HOME/qubit-voicebridge/runtime`
     (i.e. the same XDG home), and `pin_qubit_home()` fail-closes any external
     `~/.hermes` value. Live PID environ confirms `QUBIT_HOME=…/runtime` for all.
   - `qubit-voice-orb`: same resolution.
   Every live Qubit process carries `QUBIT_HOME=/home/trick/.local/state/qubit-voicebridge/runtime`.
2. **Nothing WRITES into `~/.hermes` from Qubit production code.** A scan for
   create/write into `.hermes` finds only `tests/` fixtures and `evals/` sandboxes;
   the two production-looking hits are `os.environ.get("QUBIT_HOME", "~/.hermes")`
   *fallbacks* in optional-skills scripts, which never fire because `QUBIT_HOME` is
   always set by the units.
3. **`migrate_legacy_homes` is read-only toward `~/.hermes`.** In
   `voicebridge/qubit_runtime.py`, `_LEGACY_HOME_NAMES` scanning targets the
   **parent of the runtime home** (`~/.local/state/qubit-voicebridge/`), not
   `~` — so `~/.hermes` is not even a migration source it sweeps. It copies
   *into* the canonical home and never removes or writes the source.
4. **Empty-HOME / no-`~/.hermes` boot is clean.** `voicebridge.gateway_daemon --check`
   under `HOME=/tmp/…/emptyhome` with no `~/.hermes` returns `OK`
   (`runtime_home=…/runtime`, `importable=True`). The deletion gate reproduces this:
   `get_qubit_home()` with `QUBIT_HOME` unset → `~/.qubit`, and
   `no ~/.hermes created`.

**Could a unit write into a *recreated* `~/.hermes` after the move?** No. The only
thing that writes `~/.hermes` today is `hermes-dashboard.service` (PID 2408). Once
Step 1 disables it, no process recreates `~/.hermes`. The one guard to watch: if a
human skips Step 1, hermes-dashboard restart-storms and its child re-materialises
`~/.hermes/logs/*` — hence Step 1 is mandatory and ordered first.

**Conclusion:** the move is **reversible and lossless** for Qubit. `~/.hermes` is
hermes's own home (hermes-agent checkout, venv, installs, tool python) plus its own
runtime state — no Qubit-owned state lives there.

---

## 4. GO / NO-GO

**NO-GO.**

Single deciding reason: **the frozen-target instrument reports `6 production missing`**
— the port is not at production parity, so retiring hermes would delete the only copy
of features Qubit has not yet grown. The retirement gate independently refuses on
guard (b) (`hermes-dashboard.service` still enabled+active), which a human fixes at
retirement time; guard (a) is NOT a mechanical fix and must be closed by porting
those 6 production symbols first.

Supporting checks all pass and establish that, **once parity lands**, retirement is
safe and reversible:
- (b) half-port sweep: 21/21 SANE dynamic + 0 undefined names static, across 10
  modules from the last two waves.
- (c) deletion harness: 16/16 PASS, boots with hermes absent, real turn returns.
- (d) gate: guard (c) 4/4 OK; guard (b) is a known, ordered Step-1 action.
- Rollback: proven reversible; no Qubit state is lost by the move.

**Path to GO:** close the 6 production symbols, re-run
`scripts/frozen_target_progress.py` to `AT TARGET`, then a human runs the §2 sequence.
