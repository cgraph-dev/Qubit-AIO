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

**Dry-run is strictly side-effect-free**: `install --dry-run` and
`first-run` (status mode) create no directory, journal, lock file, config,
alias, shortcut, model, or temporary file. The only external interaction is
read-only capability probing (audio-device count, Ollama reachability,
session env), which is documented and hermetically injectable in tests —
verified by before/after filesystem snapshots.

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
  `local-actions.example.yaml`) into the canonical config dir from the
  **installed package data** (never the source checkout) — an existing tuned
  file is **never overwritten** (KEPT), new defaults are merged explicitly,
- creates the legacy compat aliases (`openclaw-voice-bridge` -> the canonical
  tree) so old-path readers resolve to the same physical files,
- records every step in the install journal under the installer lock, and
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
voicebridge product first-run               # plain-language status wizard (read-only)
voicebridge product first-run --apply       # stage defaults + aliases (merge only)
voicebridge product repair                  # one diagnostic/repair command (read-only)
```

`first-run` explains runtime, provider/model, microphone/output,
display/session, storage/paths, dashboard pairing, service owner, permissions,
migration status, and optional capabilities in plain language. Every result has
a typed state, a human-readable explanation, and an exact safe next action, and
never contains a secret, token, private URL, raw audio, browser cookie, or
account content. Existing user configuration is preserved; defaults are merged
explicitly; tuned settings are never overwritten silently.

**Dashboard pairing is truthful.** `first-run` never claims a pairing token was
generated. It reports `REQUIRES_PAIRING` until a non-empty `webui.token`
actually exists in the canonical config, and `PAIRED` afterwards — the token
value is never read out, printed, logged, journaled, or placed in a URL.
Pairing is done in the browser on the local dashboard
(`voicebridge webui --open`); the token lives only in local config/browser
storage.

`product repair` is a read-only diagnostic (it creates no directories); the
actual repair runs through `product install --apply`.

## 4. Branding migration (reversible, backed up)

```bash
voicebridge product migration               # read-only status (FRESH/LEGACY_ONLY/MIGRATED)
voicebridge product migrate                 # dry-run migration plan (zero writes)
voicebridge product migrate --apply --backup-dir <dir>
voicebridge product migrate-rollback --apply --backup-dir <dir>
```

The migration backs up every legacy config/state/data tree first (credential
files — `*.env`, `*.key`, `*.pem`, tokens — are **never** copied into the
backup; the live move preserves all bytes) and writes a **validated backup
manifest** (schema version + per-file SHA-256). It then moves the state to the
canonical paths and leaves a compat alias so the old name keeps resolving to
the same physical tree. It is journaled and idempotent: an interrupted
migration resumes on re-run and never duplicates services, state directories,
hotkey listeners, or dashboard instances. Rollback removes the aliases and
restores the legacy paths through the atomic restore pipeline (§10). Fresh
installs use the canonical Qubit identity; existing installations continue to
work.

## 5. Service ownership

`voicebridge product service` reports the single authoritative service owner:
`systemctl --user` owns the bridge process and hotkey listener — one service,
one listener. The typed diagnostic distinguishes:

| State | Meaning |
|---|---|
| UNIT_MISSING | no service unit installed (installer never enables it) |
| PRESENT | canonical unit installed with a matching `voicebridge run` ExecStart |
| MISMATCH | canonical unit ExecStart does not run `voicebridge run` |
| STALE | only a legacy `openclaw-voice-bridge.service` exists |
| DUPLICATE | canonical and legacy units both exist (exactly one must own the listener) |

`active_state` is never claimed here — it reports
`requires_operator_check: systemctl --user is-active` as the operator step.
The tray and desktop shortcut never start a second bridge. The systemd unit
has no hardcoded user home or `DISPLAY`; X11/Wayland is resolved at runtime
from the user session env, headless PTT stays possible via `--no-hotkey`, a
service failure cannot corrupt state, and an upgrade never silently restarts
or enables services.

## 6. Desktop shortcut

`voicebridge product shortcut --apply` writes a `.desktop` file whose
`Exec` is the **tested** `python -m voicebridge webui --open` command
(`voicebridge webui` is a real CLI subcommand). The command probes the
already-running local dashboard and, with `--open`, opens it in the default
browser — it **never starts a second bridge, hotkey listener, or dashboard
instance**. The token is **never** placed in the URL, in arguments, in
environment, or in the `.desktop` file contents; the shortcut resolves the
installed executable and works outside the source checkout. When no dashboard
is reachable it returns a typed `UNAVAILABLE` state (nothing started) and an
unsupported desktop environment (no graphical session) is reported clearly as
`DEGRADED`/`UNAVAILABLE`. The shortcut is never required for CLI/headless use.

```bash
voicebridge webui                # typed probe of the local dashboard (never starts anything)
voicebridge webui --open         # open it in the default browser when reachable
```

## 7. Optional tray / status controller

`voicebridge product tray` reports the optional tray/status controller. It is
never required, never starts a second bridge, **never creates a second hotkey
listener or dashboard instance**, never exposes tokens, and never reads
browser cookies. When `pystray` is absent the result is `UNAVAILABLE` with
`required: false` — core CLI use is unaffected.

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
acquisition path (`voicebridge/product.py: model_bootstrap`) **fails closed**:
the pinned spec is validated before anything is touched — a non-empty source
with an approved scheme (`http://`/`https://`/`ollama://`), a non-empty
revision/provider digest (64-hex for Ollama), a valid exact 64-character
SHA-256, an exact positive byte size, and an approved license identifier.
Destinations are checked for traversal, downloads are bounded, streamed to a
resumable `.part` file (HTTP Range + bounded retry), fsynced (file and
directory), verified for exact size (even without Content-Length) and SHA-256,
and promoted atomically only after every check passes. A previous valid model
is never destroyed by a failed acquisition; bad hash, corrupt artifact,
oversized download, invalid provider digest, timeout, offline, missing
provider, and low disk/RAM are all typed and fail closed. No model or provider
is ever switched silently; no training or self-modification happens; private
user models and credentials are never bundled.

## 10. Upgrade, uninstall, rollback

`voicebridge product upgrade` reports the upgrade/uninstall/rollback contract:
upgrades preserve user state/config by default, never silently restart or
enable services, never delete model artifacts or browser profiles silently,
never copy credentials into backups, and fail closed on a newer schema.

`voicebridge product rollback [--apply --backup-dir <dir>]` restores the
previous valid state through a genuinely atomic pipeline: the backup is staged
into a same-filesystem temp dir, every file is validated against the backup
manifest (schema version + SHA-256), fsynced, and atomically renamed into
place — the backup is preserved and never modified. A corrupted or newer
backup, a read-only filesystem, disk-full, a partial restore, or an
interruption **fails closed** and reports the exact failed paths and blocks
resume. Repeated rollback is idempotent. Uninstall preserves state by default
and requires separate confirmation for data deletion (existing
`distribution.uninstall_plan` contract).

## 11. Packaging, signing, and the private-source boundary

`voicebridge product boundary` defines exactly what is distributed: the Python
package `voicebridge` (implementation source), the static dashboard frontend,
config examples, systemd templates, report templates, and operator docs.

**Release signing (optional):** `docs/RELEASE_SIGNING.md` documents the
detached-signature workflow — `SHA256SUMS`, per-artifact
`gpg --detach-sign --armor` signatures, and a signed release manifest
(`release-manifest.json` + `.asc`), verified with `gpg --verify` against the
approved **public** 40-hex fingerprint. The private key is never generated,
committed, printed, or exposed. `scripts/release_qualify.py --sign` and
`--verify-signatures` drive the workflow; `release.verify_release_signing`
never claims `SIGNED` unless every detached signature verifies against the
approved fingerprint.

**School-only submission:** this project is a school project. Artifact signing
and private-source distribution are **NOT required** and are reported honestly
as **NOT APPLICABLE** — no signing key is generated or claimed, and the wheel
still ships Python implementation source and is never claimed private
(`private_source_solved: False`, `wheel_has_python_source: True`,
`artifact_signing_requirement` / `private_source_requirement` =
`NOT APPLICABLE (school-only submission)`). Obfuscation is not security and is
never claimed. Browser profiles, live config/state, credentials, private
models, user data, and personal paths are never bundled. Source-hygiene is
verified by the artifact scans in `docs/OPERATIONS_8K.md` (no secrets, user
paths, credentials, browser data, model files, or unrelated dirty files in
wheel/sdist) and the sdist remains a clean, inspectable source distribution
for teacher review.

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