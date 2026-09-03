# Packet 8K-P — productized installation and desktop integration (Qubit Voice Agent)

Status: **IMPLEMENTED** — hermetic product surface. Everything in this packet
is implemented and verified against isolated temporary HOME/XDG/state roots; it
never touches a live service, browser, audio device, timer, model, account, or
production state. Live enablement remains a separate operator-authorized step.
Source contract: `docs/UNIFIED_AGENT_PLAN.md` §8K-P and the Packet 8K-P
implementation prompt.

The public product name is **Qubit Voice Agent**. The internal Python import
package stays `voicebridge`. The old `openclaw-voice-bridge` config/state/data
paths are preserved through an explicit, reversible, backed-up migration and a
compatibility alias — the runtime can never read one tree while the installer
writes another.

## 1. One supported installer (from a release artifact)

`voicebridge product install` is the single supported installer. It works from
an installed release artifact (wheel) — it needs no source checkout and no
repository-local virtualenv. It probes prerequisites, stages packaged defaults,
creates the legacy compat alias, and is lock + journal guarded and idempotent.
It never enables a live service.

```bash
voicebridge product install                  # dry-run: typed prerequisite probe
voicebridge product install --apply          # stage config/resources + aliases
voicebridge product install --apply --service-owner systemctl --user
```

Typed results — the installer NEVER reports COMPLETE/READY when a required
component is missing:

| State | Meaning |
|---|---|
| READY | all required components present; install applied (or would) |
| DEGRADED | a required component is partial (never a pass) |
| MISSING | a required component is absent (python/audio/provider/session) |
| UNAVAILABLE | platform/optional capability unsupported |
| BLOCKED | a blocker (e.g. legacy tree not yet migrated, installer lock held) |
| FAILED | an apply step failed |

The apply phase (hermetic, idempotent):

- creates only the owned per-user directories (see §2),
- stages the packaged defaults (`voice.yaml`, `task-templates.yaml`,
  `local-actions.example.yaml`) into the canonical config dir — an existing
  tuned file is **never overwritten** (KEPT), new defaults are merged
  explicitly,
- creates the legacy compat aliases (`openclaw-voice-bridge` -> the canonical
  tree) so old-path readers resolve to the same physical files,
- records every step in the install journal, and
- installs the service only through the documented owner
  (`systemctl --user`); the unit is **not enabled** by the installer.

Two concurrent installers are serialized by the installer lock; an interrupted
install resumes from the append-only journal and never leaves a half-active
service.

## 2. Portable path resolver (XDG on Linux)

`voicebridge product paths [--json]` reports the effective path map. Every
category is a deterministic, per-user, OS-aware location — never a repository
path, `/home/<user>` literal, device index, or guessed application folder.
macOS and Windows return an explicit `UNSUPPORTED` result before any install
step.

| Category | Location (Linux, honours `$XDG_*`) |
|---|---|
| config | `$XDG_CONFIG_HOME/qubit-voice-agent` |
| user data | `$XDG_DATA_HOME/qubit-voice-agent` |
| runtime state | `$XDG_STATE_HOME/qubit-voice-agent` |
| model files | `$XDG_DATA_HOME/qubit-voice-agent/models` |
| cache/temp | `$XDG_CACHE_HOME/qubit-voice-agent` |
| logs | `$XDG_STATE_HOME/qubit-voice-agent/logs` |
| exports | `$XDG_STATE_HOME/qubit-voice-agent/exports` |
| backups | `$XDG_STATE_HOME/qubit-voice-agent/backups` |
| install journal | `$XDG_STATE_HOME/qubit-voice-agent/install-journal` |
| packaged resources | installed package `voicebridge/_data` (checkout fallback) |
| legacy (compat alias) | `$XDG_CONFIG_HOME/openclaw-voice-bridge` etc. |

The path map is stable across restart, upgrade, rollback, uninstall, multiple
users, and multiple graphical sessions (proven by tests). Only owned
directories are created, with documented permissions: config/state are `0700`,
data/cache/logs are `0755`.

## 3. First-run and repair

```bash
voicebridge product first-run               # plain-language status wizard
voicebridge product first-run --apply       # stage defaults + aliases (merge only)
voicebridge product repair                  # one diagnostic/repair command
```

`first-run` explains runtime, provider/model, microphone/output,
display/session, storage/paths, dashboard pairing, service owner, permissions,
migration status, and optional capabilities in plain language. Every result has
a typed state, a human-readable explanation, and an exact safe next action, and
never contains a secret, token, private URL, raw audio, browser cookie, or
account content. Existing user configuration is preserved; defaults are merged
explicitly; tuned settings are never overwritten silently.

## 4. Branding migration (reversible, backed up)

```bash
voicebridge product migration               # read-only status (FRESH/LEGACY_ONLY/MIGRATED)
voicebridge product migrate                 # dry-run migration plan
voicebridge product migrate --apply --backup-dir <dir>
voicebridge product migrate-rollback --apply --backup-dir <dir>
```

The migration backs up every legacy config/state/data tree first (credential
files — `*.env`, `*.key`, `*.pem`, tokens — are **never** copied into the
backup; the live move preserves all bytes), moves the state to the canonical
paths, and leaves a compat alias so the old name keeps resolving to the same
physical tree. It is journaled and idempotent: an interrupted migration resumes
on re-run and never duplicates services, state directories, hotkey listeners,
or dashboard instances. Rollback removes the aliases and restores the legacy
paths. Fresh installs use the canonical Qubit identity; existing installations
continue to work.

## 5. Service ownership

`voicebridge product service` reports the single authoritative service owner:
`systemctl --user` owns the bridge process and hotkey listener — one service,
one listener. The tray and desktop shortcut never start a second bridge.
Stale-service detection is typed (a leftover `openclaw-voice-bridge.service`
is reported as `STALE` with an exact next action). The systemd unit has no
hardcoded user home or `DISPLAY`; X11/Wayland is resolved at runtime from the
user session env, headless PTT stays possible via `--no-hotkey`, a service
failure cannot corrupt state, and an upgrade never silently restarts or enables
services.

## 6. Desktop shortcut

`voicebridge product shortcut --apply` writes a `.desktop` file that opens the
local dashboard/pairing page. The token is **never** placed in the URL or in
the desktop-file arguments; the shortcut resolves the installed executable,
and an unsupported desktop environment (no graphical session) is reported
clearly as `DEGRADED`/`UNAVAILABLE`. The shortcut is never required for
CLI/headless use.

## 7. Optional tray / status controller

`voicebridge product tray` reports the optional tray/status controller. It is
never required, never starts a second bridge, never duplicates a hotkey
listener, and never exposes tokens. When `pystray` is absent the result is
`UNAVAILABLE` with `required: false` — core CLI use is unaffected.

## 8. Capability defaults

`voicebridge product capabilities` reports every integration (voice, audio,
provider, display, browser, tray) as an explicit typed capability state —
`DISABLED`/`AVAILABLE`/`ENABLED`/`BLOCKED`/`UNAVAILABLE`/`DEGRADED`/`FAILED`.
Detection is never permission: a microphone, speaker, display, browser
profile, account, application, or model is never silently selected, rerouted,
or enabled. Each capability carries an explicit enable/repair action.

## 9. Model/provider bootstrap

The local SLM stays behind the existing provider contract
(`voicebridge/distribution.py`, `model_artifact_record`). The safe model
acquisition path (`voicebridge/product.py: model_bootstrap`) uses the XDG model
location, resumable `.part` downloads (HTTP Range) with bounded safe retry,
size validation, SHA-256 verification, provenance/license metadata
(`ModelSpec`), atomic promotion only after verification, and disk/RAM preflight
(bad hash, corrupt file, low disk, offline, missing provider, provider timeout,
and provider crash are all typed and fail closed). No model or provider is ever
switched silently; no training or self-modification happens; private user
models and credentials are never bundled.

## 10. Upgrade, uninstall, rollback

`voicebridge product upgrade` reports the upgrade/uninstall/rollback contract:
upgrades preserve user state/config by default, never silently restart or
enable services, never delete model artifacts or browser profiles silently,
never copy credentials into backups, and fail closed on a newer schema.
`voicebridge product rollback [--apply --backup-dir <dir>]` restores the
previous valid state from a timestamped backup; a rollback that cannot restore
every touched path is a typed failure that blocks resume. Uninstall preserves
state by default and requires separate confirmation for data deletion (existing
`distribution.uninstall_plan` contract).

## 11. Packaging and the private-source boundary

`voicebridge product boundary` defines exactly what is distributed: the Python
package `voicebridge` (implementation source), the static dashboard frontend,
config examples, systemd templates, report templates, and operator docs. It
reports honestly that **private-source distribution is NOT solved by Packet
8K-P**: the wheel still exposes Python implementation source, and obfuscation
is not security. This is a release blocker for the private-source goal and is
never claimed as solved. Browser profiles, live config/state, credentials,
private models, user data, and personal paths are never bundled.

## 12. What is never done

- No live service enablement, restart, or timer; no account/browser/audio/
  model access during implementation or hermetic verification.
- No `curl | sh`, no root writes, no undocumented global writes.
- No silent device/model/provider/browser/display/account selection.
- No secret ever appears in a command line, desktop file, unit file, log,
  report, artifact, or diagnostic.

## 13. Verification

```bash
python -W error -m pytest tests/test_packet8kp.py      # focused 8K-P
python -m pytest tests/test_packet8k.py                # 8K regressions
python -m compileall -q voicebridge scripts tests      # byte-compile
python -m pip check                                     # dependency integrity
git diff --check                                        # whitespace
systemd-analyze verify systemd/qubit-voice-agent.service
python scripts/release_qualify.py --static --json       # 8K static certification
graft build                                             # repo context graph refresh
```

Build + inspect the wheel/sdist and install into a clean HOME from a clean
clone with `scripts/release_qualify.py --build` / `--install` / `--reproduce`
(see `docs/OPERATIONS_8K.md`).