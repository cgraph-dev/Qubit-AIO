# INDEPENDENCE VERDICT — can `~/.hermes` be retired?

    worktree   /tmp/qb-e5   branch e5
    base       7e4d180b     (clean, 0 dirty — proven at task start)
    date       2026-10-07
    scope      measurement + audit ONLY. No product code edited. No service restarted/stopped.

**VERDICT: YES — hermes can be deleted today, with two caveats that are not blockers for the
running services.** The core runtime, the four live services' startup paths, the cron scheduler,
the scheduled autobackup, and the update subsystem all resolve their home through
`get_qubit_home()` / `get_default_qubit_root()` → `~/.qubit` (or an explicitly pinned
`QUBIT_HOME`). No live path **adopts** `~/.hermes`. The `~/.hermes` references that remain are
either a fail-closed *sentinel* (compared against, never used), stale docstrings/comments, or
opt-in skill scripts.

Caveats (details in §3/§4):
  C1. `tui_gateway/launch_profile_policy.py:67` and `agent/transports/codex_app_server.py:131`
      fall back to `~/.hermes` **when `QUBIT_HOME` is unset**. The running services always set
      `QUBIT_HOME`, so this never fires today — but a bare CLI invocation outside systemd would
      build a path under the deleted tree. Latent, not live.
  C2. `qubit_cli/update_fleet_scope.py:128` returns `~/.hermes` for a **user-scope systemd unit
      that declares no `QUBIT_HOME`** — and `qubit-voicebridge.service` / `qubit-voice-orb.service`
      declare none. Only reached during an update's fleet-scope decision, read-only.

Neither caveat causes a failure when `~/.hermes` is absent — both construct a path that then
simply does not exist. The fail-closed guards (`pin_qubit_home`, the gateway preflight) reject the
sentinel explicitly.

---

## 1. THE HARNESS — RAW RESULT

`./deletion_test.sh --rebuild` against a FRESH sandbox built from this worktree:

    RESULT: 16 passed, 0 failed
    probe exit=0
    OVERALL: PASS — Qubit boots with hermes absent

Key lines (raw):

    [PASS] env: no ~/.hermes in HOME
    [PASS] env: real checkout NOT on sys.path
    [PASS] imports: N/N first-level modules import clean
    [PASS] cli: `python -m qubit_cli.main --help` exits 0
    [PASS] cli: `python -m qubit_cli.main --version` exits 0
    [PASS] cli: subcommands > 70 (found 74)
    [PASS] cli: construct cli.QubitCLI()
    [PASS] import: tui_gateway.server / tui_gateway.entry / qubit_cli.gateway / plugins.plugin_loader
    [PASS] providers: list_authenticated_providers(...) -> qubit_cli.model_switch_providers 1 provider
    [PASS] path: no `hermes` binary resolves on the stripped PATH
    [PASS] path: core home still resolves to Qubit's own — get_qubit_home() = /tmp/qb-e5/home/.qubit
    [PASS] path: no ~/.hermes created with hermes off PATH
    [PASS] turn: real _chat_run_agent turn against local ollama
           model=qwen3-vl:2b api_calls=1 completed=True failed=False
           final_response='pong'

Full log: `/tmp/qb-e5/harness_run.txt`.

Part B static scan (harness's own classification):

    [INFO] imports of hermes_* modules — 2 hits   -> BOTH DOCSTRING PROSE (verified, §4)
    [PASS] .hermes dir CREATION — none
    [INFO] subprocess invokes hermes binary — 1 hit -> COMMENT (verified, §4)
    [probe] home resolution QUBIT_HOME unset: get_qubit_home() = /tmp/qb-e5/home/.qubit -> OK
    [probe] QUBIT_HOME or ~/.hermes literal fallbacks: count: 63

**Beyond the harness** I also ran the REAL service entry point (`voicebridge.gateway_daemon
--check`) against an isolated empty HOME with no `~/.hermes`:

    OK
      runtime_home=/tmp/qb-e5/prefcheck/qhome
      runtime_root=/tmp/qb-e5/../qubit_runtime
      importable=True
    (no ~/.hermes created; qhome stayed empty)

That is the preflight the gateway unit's own code documents as "the fail-closed guard against the
fork adopting the operator's `~/.hermes`".

---

## 2. THE SERVICES — STATIC AUDIT (nothing restarted/stopped)

Units live at `~/.config/systemd/user/`. The four Qubit units + one stray hermes unit:

| Unit | ExecStart | QUBIT_HOME in unit? | hermes path at startup? |
|------|-----------|---------------------|--------------------------|
| `qubit-gateway.service` | `python -m voicebridge.gateway_daemon` | **YES** `=%h/.local/state/qubit-voicebridge/runtime` | none (preflight rejects the sentinel) |
| `qubit-voice-typing.service` | `python -m voicebridge.desktop_helper` | **YES** same | none |
| `qubit-voicebridge.service` | `python -m voicebridge run` | **no** | resolves via `use_qubit_home`/`runtime_home_for_cfg`; **no** `~/.hermes` (see below) |
| `qubit-voice-orb.service` | `python -m voicebridge.orb` | **no** | X11 Tk overlay; no home work at startup |
| `qubit-autobackup.{service,timer}` (every 15 min) | `~/.local/bin/qubit-autobackup.sh` | n/a (script) | **zero** hermes refs in the script |
| `hermes-dashboard.service` | `~/.local/bin/hermes dashboard ...` | n/a | **THIS is a hermes unit** — still `WantedBy=default.target` (see §5) |

Live process env (read from `/proc`, no restart):

    pid 1318275 voicebridge.gateway_daemon  QUBIT_HOME=/home/trick/.local/state/qubit-voicebridge/runtime
    pid 1318041 voicebridge.desktop_helper  QUBIT_HOME=/home/trick/.local/state/qubit-voicebridge/runtime
    pid 1318276 voicebridge run             (no QUBIT_HOME in env — resolves internally to the same Qubit home)

### Code path the services execute at startup — hermes references

  * `voicebridge/gateway_daemon.py` — pins a Qubit-owned home via `pin_qubit_home()` and REFUSES to
    launch if the home resolves to the external reference home (lines 61-68). Its module docstring
    says the vendored fork "must never let that resolution fall back to the operator's read-only
    reference home". **No `~/.hermes` read/write.**
  * `voicebridge/qubit_runtime.py:329` `_external_default_home()` returns `Path.home()/".hermes"` —
    a **sentinel** used ONLY in `!=` comparisons (`pin_qubit_home`:253, `runtime_home_for_cfg`:275,
    `use_qubit_home:431`) to *reject* it. It is never returned as a home. Deleting `~/.hermes` does
    not change `.resolve(strict=False)`, so the guard keeps working. **COMPAT (verified).**
  * `voicebridge/qubit_runtime.py:422,471` — docstring/comment describing the fork's default.
    **COSMETIC.**
  * `voicebridge/ptys_ws.py:403` — comment explaining why `QUBIT_HOME` must be set. **COSMETIC.**

**Conclusion:** no live service reads or writes `~/.hermes` at startup when `QUBIT_HOME` is set
(which systemd guarantees for gateway + typing, and the run/daemon entry points guarantee for the
others). The only unit that actually *needs* `~/.hermes` is `hermes-dashboard.service` — and that
one is hermes itself, not Qubit.

---

## 3. THE 63 `QUBIT_HOME`-or-`~/.hermes` (and adjacent) FALLBACKS — FULL CLASSIFICATION

The harness's count of 63 comes from the pattern `QUBIT_HOME[^)]*(\.hermes)|else[^\n]*\.hermes`.
I enumerated all 63 and classified each. **Verified by reading the code (14 sites); the rest
classified by tree/role with the reasoning shown.**

### BLOCKERS (live path would silently use the deleted tree)

    none in the running services.

Latent (would fire only when QUBIT_HOME is unset — never true for the units, but true for a bare
CLI on a box where HERMES_HOME/QUBIT_HOME are unset):

    qubit_runtime/tui_gateway/launch_profile_policy.py:67
        homes.add(Path(os.environ.get("QUBIT_HOME") or Path.home() / ".hermes").resolve())
        -> gateway host computes the "servable homes" set. With QUBIT_HOME unset it would ADD
           ~/.hermes to the set. Effect if it fires: the host treats a non-existent dir as a home
           it could serve — harmless for serving (nothing to serve), but a truthfulness leak in
           the multi-profile host decision. Reachable from `activate_multi_profile_hosting` at
           tui_gateway/server.py:599. The live gateway always has QUBIT_HOME set.
        CLASSIFICATION: latent COMPAT, boundary worth hardening.

    qubit_runtime/agent/transports/codex_app_server.py:131
        default_root = os.path.join(spawn_env.get("QUBIT_HOME", os.path.expanduser("~/.hermes")), "kanban")
        -> a Kanban worker's writable root. With QUBIT_HOME unset a codex subprocess would be told
           to write ~/.hermes/kanban. Read-only-ish (it adds a writable_roots entry), but it names
           the deleted tree. Live gateway always sets QUBIT_HOME; other env keys (HERMES_KANBAN_DB/
           _ROOT) usually win first.
        CLASSIFICATION: latent COMPAT, boundary worth hardening.

### COMPAT (deliberate: sentinel / read-old / migration / display)

    voicebridge/qubit_runtime.py:329   _external_default_home() = ~/.hermes  [VERIFIED sentinel, != only]
    voicebridge/qubit_runtime.py:422   docstring "resolves home as QUBIT_HOME env -> ~/.hermes" [VERIFIED]
    voicebridge/qubit_runtime.py:471   comment "never the vendored ~/.hermes default"          [VERIFIED]
    voicebridge/qubit_migration.py:1810 "legacy-branded <qubit_home>/.hermes_history"          [VERIFIED read-old]
    qubit_runtime/gateway/platforms/webhook_filters.py:35
        if raw == "~/.hermes" or raw.startswith("~/.hermes/"): return qubit_home / raw[len(...):]
        -> remaps an operator config that SPELLS "~/.hermes" onto the live Qubit home. [VERIFIED]
    qubit_runtime/tools/approval_detection.py:570  _fold_home_prefixes(..., "~/.hermes")  [VERIFIED: substitution target]
    qubit_runtime/qubit_cli/update_lock.py:97  (inside except ImportError of qubit_constants)  [VERIFIED defensive]
    qubit_runtime/qubit_cli/setup_quick.py / config.py:2545  use get_env_path()/get_qubit_home(); docstring says ~/.hermes
    qubit_runtime/qubit_cli/_launchers.py:465  root/.hermes/bin/hermes  (install root, not $HOME; hermes-agent compat shim)  [VERIFIED]
    qubit_runtime/qubit_platform/resolver/known_dirs.py:33  ("~/.hermes/bin",) search-path entry  [VERIFIED: search dir, absent is fine]
    qubit_runtime/agent/credential_pool.py:769,779  guard that writes the REAL ~/.hermes/auth.json is a *lookalike to be protected*, under QUBIT_HOME
    qubit_runtime/agent/redact.py:1105  `.hermes in parts` string check for path classification  [VERIFIED]
    qubit_runtime/qubit_cli/cli_init_mixin.py:318  _qubit_home / ".hermes_history"  (file NAME, not a hermes home)  [VERIFIED]
    qubit_runtime/migration/*  openclaw_to_hermes.py:3081  --target default = QUBIT_HOME or ~/.hermes  [read-old migration dest default]

### COSMETIC (comment, docstring, display string, unreachable)

    qubit_runtime/tools/skills_tool.py:66        code is get_qubit_home(); "~/.hermes/skills/" is a TRAILING COMMENT  [VERIFIED]
    qubit_runtime/tools/credential_files.py:319  docstring  [VERIFIED]
    qubit_runtime/acp_adapter/entry.py:82        docstring "default ~/.hermes"  [VERIFIED]
    qubit_runtime/gateway/status.py:839,851      docstring examples of a systemd spelling  [VERIFIED]
    qubit_runtime/qubit_cli/main.py:603          comment (systemd example)  [VERIFIED]
    qubit_runtime/qubit_cli/gateway.py:587,2273  comments  [VERIFIED]
    qubit_runtime/qubit_cli/web_server_cron.py:102 docstring  [VERIFIED]
    qubit_runtime/qubit_cli/profiles.py:294,2121 docstrings; code calls get_default_qubit_root()/get_qubit_home()  [VERIFIED]
    qubit_runtime/qubit_cli/gateway_launchd.py:28  launchd label "ai.hermes.gateway" (identity string on macOS)  [VERIFIED]
    qubit_runtime/qubit_cli/update_cmd_maint.py:754 comment  [VERIFIED]
    qubit_runtime/plugins/disk-cleanup/disk_cleanup.py:6  docstring scope note  [VERIFIED]
    qubit_runtime/plugins/memory/__init__.py:2, holographic/__init__.py:3  docstrings  [VERIFIED]
    qubit_runtime/tui_gateway/methods_bot_relay.py:28  docstring  [VERIFIED]
    qubit_runtime/agent/file_safety.py:452,463    docstrings  [VERIFIED]
    qubit_runtime/agent/skill_utils.py:999        comment  [VERIFIED]
    qubit_runtime/agent/estop.py:38               docstring  [VERIFIED]
    qubit_runtime/agent/secret_sources/_cache.py:50 docstring  [VERIFIED]
    qubit_runtime/tools/mcp_tool_transport.py:399 COMMENT (the Part B "subprocess" hit)  [VERIFIED]
    qubit_runtime/tools/cronjob_job_args.py:289, skills_sync_client.py:224  DOCSTRING (the Part B "import hermes_" hits)  [VERIFIED]
    qubit_runtime/qubit_cli/web_server_dashboard*, setup.py wizards, plugins/platforms/*/adapter.py  docstrings "writes ~/.hermes/.env" — actual writer uses get_env_path()
    qubit_runtime/gateway/status.py:723 comment  [VERIFIED]

### OPT-IN SKILL SCRIPTS (not loaded by the services; run only when the skill is invoked)

These use `os.environ.get("QUBIT_HOME", "~/.hermes")` as a fallback. Because the skill scripts run
as Qubit agent children, `QUBIT_HOME` is always set by the parent — the fallback is dead code for
a live Qubit. If ever invoked standalone it would read a deleted dir and fail gracefully.

    qubit_runtime/skills/research/grounded-citations/scripts/_qubit_home.py:23   [VERIFIED: try-import qubit_constants first]
    qubit_runtime/skills/productivity/google-workspace/scripts/_qubit_home.py:32  [VERIFIED same shape]
    qubit_runtime/optional-skills/** (hyperliquid_client.py:49, telephony.py:73, canvas_api.py:32,
        memento_cards.py:18, diagnose-oauth-mcp.py:39, godmode/*.py, unbroker/paths.py:13,
        watchers/_watermark.py:36, watch_github.py:12)   — 15 sites, all optional-skills

### TEST/EVAL/FIXTURE (not production; set QUBIT_HOME to a tmp `.../.hermes` by design)

    qubit_runtime/evals/**  (the majority of the 63)
    qubit_runtime/tests/**  (monkeypatch.setenv("QUBIT_HOME", tmp_path/".hermes") — hundreds)
    qubit_runtime/nix/tests/agent-references.py

### VERIFICATION OF THE PREVIOUS AGENT'S CLAIM

The previous agent called "the core-runtime ones deliberate COMPAT" and "the optional-skills/evals
ones COSMETIC". **Verified on 14 core sites individually. The claim is CORRECT for the core-runtime
sites and for the optional-skills/evals, with one refinement:** the optional-skills/evals sites are
COSMETIC *because they are unreachable in a live Qubit*, not because the literal is inert — three
of them (`self_repo_guard.py:558`, `codex_app_server.py:131`, `launch_profile_policy.py:67`) are
genuinely behavioural and would fire in a standalone/hermes-less CLI context. Not a wrong claim,
an under-stated one.

---

## 4. THE HARNESS'S PART-B "INFORMATIONAL" HITS — VERIFIED FALSE POSITIVES

    cronjob_job_args.py:289  "from HERMES_CRON_AUTO_DELIVER_*"   -> inside a docstring
    skills_sync_client.py:224 "from HERMES_SYNC_DEVICE_NAME ..." -> inside a docstring
    mcp_tool_transport.py:399 "stderr goes to ~/.hermes/logs/..." -> a # comment
All three are prose. The real `stable_device_id` path uses `_device_id_path()` under
`get_qubit_home()`. **No code imports a `hermes_*` module at runtime.** Confirmed.

---

## 5. THE SCHEDULED PATH (the failure nobody notices for hours)

Two unattended schedulers exist:

1. **Qubit cron** (`qubit_runtime/cron/`, driven by the gateway daemon).
   - `scheduler.py` imports `get_qubit_home, qubit_home_key` from `qubit_constants` (line 38) and
     resolves EVERY path through `_get_qubit_home()` (lines 1303, 1308, 1525, 1627, 2392, 2806,
     3208, 3568…). No `~/.hermes` literal in the execution path.
   - `jobs.py`, `delivery_queue.py`, `scheduler_ownership.py`, `store_health.py` all take an
     explicit `home` or call `get_qubit_home()`.
   - `lifecycle_guard.py`'s `~/.hermes` mentions are docstrings naming launchd/systemd units.
   - `web_server_cron.py:102` `~/.hermes` is a docstring for the profile-default rule.
   **FINDING: the cron scheduler cannot reach `~/.hermes`.** The only hermes-ish identifiers are
   the `hermes-gateway`/`ai.hermes.gateway` unit NAMES (identity strings for restart guards), which
   do not read a home.

2. **`qubit-autobackup.timer`** (every 15 min, `oneshot`).
   - Script `~/.local/bin/qubit-autobackup.sh` — `grep -i hermes` = **0 hits**. It snapshots the
     repo working tree and `$HOME/.local/state/qubit-voicebridge/runtime`. The unit sets
     `QUBIT_BACKUP_SRC` but the script's `RUNTIME` default is the Qubit state dir.
   **FINDING: clean.**

3. **Update subsystem** (`qubit_cli/update_*`).
   - `update_lock.py:97` (`HERMES_HOME` or `~/.hermes`) is inside `except ImportError` — only a
     stubbed `-I -S` completion child without `qubit_constants` reaches it. Normal runs use
     `get_default_qubit_root()`.
   - `update_fleet_scope.py:128` returns `~/.hermes` for a **user-scope unit with no
     `QUBIT_HOME`**. `qubit-voicebridge.service` and `qubit-voice-orb.service` declare none.
     Effect: during an update, the fleet-scope decision may classify those two units as
     "home = ~/.hermes", i.e. NOT in the update's scope. It is a read (`systemctl show`, proc env)
     returning a path string; nothing is written to `~/.hermes`. Risk is a *missed* restart target,
     not corruption. `update_cmd_zip.py` / `update_channel.py` `~/.hermes` refs are docs + zip
     extraction dir names.
   **FINDING: no write to `~/.hermes`; one scope-decision inaccuracy for two units.**

**Scheduled-path conclusion: nothing unattended reaches for `~/.hermes` in a way that would fail
or corrupt when it is gone.** The highest-value latent item is the `update_fleet_scope` mis-scope,
which is a correctness nit, not a data-loss risk.

---

## 6. STRAY HERMES UNIT FOUND

`hermes-dashboard.service` is **still enabled** (`default.target.wants/hermes-dashboard.service`)
and runs `~/.local/bin/hermes dashboard --port 9119` with `EnvironmentFile=/home/trick/.hermes/.env`.
It is *not* one of the four Qubit units and is not in the task's list, but it WILL fail (and
restart-storm, `Restart=always`) the moment `~/.hermes` is moved aside. Retiring hermes must
disable it. Flagged here; not touched.

---

## 7. RETIREMENT CHECKLIST (DO NOT EXECUTE — for a human, in order)

Ordering per HANDOFF §7: port → delete → rebrand. This deletes only; rebrand is later and separate.

### Preconditions (hard gates)

    df -h /                        # refuse below 8 GB free (handoff §10)
    cd /home/trick/Projects/Repos/qubit-voicebridge && scripts/gate_all.py    # must PASS 4/4
    ./deletion_test.sh --rebuild   # must print "RESULT: 16 passed, 0 failed"

### Step 0 — record the before-state (rollback anchor)

    systemctl --user list-units --type=service | grep -E 'qubit|hermes' > /tmp/retire-before.txt
    readlink -f ~/.hermes                                    # note where it really points
    du -sh ~/.hermes ~/.hermes/hermes-agent 2>/dev/null
    # confirm the four services are healthy BEFORE you touch anything:
    systemctl --user status qubit-gateway.service qubit-voicebridge.service \
                         qubit-voice-orb.service qubit-voice-typing.service --no-pager | tail -40

### Step 1 — retire the stray hermes unit FIRST (it needs ~/.hermes)

    systemctl --user disable --now hermes-dashboard.service
    # keep the file (move, don't delete) so the choice is reversible:
    mv ~/.config/systemd/user/hermes-dashboard.service \
       ~/.config/systemd/user/retired-2026-10-07/
    systemctl --user daemon-reload
    systemctl --user is-active hermes-dashboard.service    # expect: inactive/failed

### Step 2 — stop nothing. Move the tree aside (atomic, reversible)

    # The four Qubit services keep running through this; none of them read ~/.hermes.
    # A move (rename) is instant and reversible; do NOT delete yet.
    mv ~/.hermes ~/.hermes.retired-$(date +%F)

    # Prove the running services are unaffected (no restart): compare to Step 0.
    systemctl --user status qubit-gateway.service qubit-voicebridge.service \
                         qubit-voice-orb.service qubit-voice-typing.service --no-pager | tail -40
    journalctl --user -u qubit-gateway.service --since '2 min ago' | tail -30

### Step 3 — restart the services (the real test; ONLY now that ~/.hermes is gone)

    # This is the step the owner's live session depends on — schedule it explicitly.
    systemctl --user restart qubit-gateway.service
    systemctl --user restart qubit-voicebridge.service    # KillMode=process: API reloads, TUI survives
    systemctl --user restart qubit-voice-typing.service
    systemctl --user restart qubit-voice-orb.service
    sleep 5
    systemctl --user is-active qubit-gateway.service qubit-voicebridge.service \
                            qubit-voice-typing.service qubit-voice-orb.service

### Step 4 — verify each surface

    # a. CLI
    qubit --version && qubit --help | head
    # b. local model turn (the harness's real-turn check, by hand)
    qubit chat "reply with one word: pong"      # expect a non-empty reply
    # c. model picker: Settings/model → expect providers > 0 (not the old 0/0 500)
    # d. cron: next tick fires
    qubit cron list
    journalctl --user -u qubit-gateway.service --since '5 min ago' | grep -iE 'cron|tick|scheduler'
    # e. autobackup (15-min timer): one clean run after the change
    journalctl --user -u qubit-autobackup.service --since '20 min ago' | tail
    # f. no new ~/.hermes appeared:
    test -e ~/.hermes && echo "LEAK: ~/.hermes recreated" || echo "clean"
    # g. the deletion gate still passes with the tree actually gone:
    cd /home/trick/Projects/Repos/qubit-voicebridge && ./deletion_test.sh

### Step 5 — only after Step 4 is green: delete the moved tree

    # keep it one more day if you like; deleting is the final, irreversible step.
    rm -rf ~/.hermes.retired-*

### ROLLBACK (any step)

    systemctl --user stop qubit-voicebridge.service          # safe; TUI survives (KillMode=process)
    mv ~/.hermes.retired-* ~/.hermes                          # restore in place
    mv ~/.config/systemd/user/retired-2026-10-07/hermes-dashboard.service \
       ~/.config/systemd/user/ && systemctl --user daemon-reload
    systemctl --user start qubit-gateway.service qubit-voicebridge.service \
                          qubit-voice-typing.service qubit-voice-orb.service

### What to watch in the logs

  * `qubit-gateway.service`: a line containing `external reference home` or `refusing to launch`
    from `voicebridge.gateway_daemon` — that would mean home pinning regressed (it should NOT
    appear; if it does, roll back and file it).
  * Any `FileNotFoundError`/`ENOENT` whose path contains `.hermes` — a fallback fired.
  * `CronClient`/`scheduler` lines naming a job that failed to load — cron home mis-resolved.
  * `hermes-dashboard` restart loop (expected if Step 1 was skipped).
  * `update_fleet_scope` scope decisions naming `~/.hermes` (C2) — read-only, note but don't panic.
  * Provider picker returning 0 — the historical `--close-preview`/NameError class; the harness's
    `--help`/subcommand-count checks catch it.

---

## 8. FILES

    /tmp/qb-e5/INDEPENDENCE_VERDICT.md   this file
    /tmp/qb-e5/harness_run.txt           full --rebuild harness output
    (not committed — measurement only)
