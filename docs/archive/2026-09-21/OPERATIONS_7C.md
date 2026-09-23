# Packet 7C — Download-and-run distribution, portable-state export/import, trusted-device enrollment

Implemented in `voicebridge/distribution.py`, `voicebridge/portable.py`,
`voicebridge/device.py`, `voicebridge/cli.py` (`package`, `export-state`,
`import-state`, `device`), and `tests/test_packet7c.py`. Everything is
**read-only/diagnostic about the host** — no service is installed, enabled,
restarted, or mutated; no browser/profile is touched; no state is deleted.

## Verified download-and-run distribution (Ubuntu first)

`voicebridge package`:

```bash
voicebridge package manifest       # versioned manifest (read-only)
voicebridge package doctor         # hardware/resource doctor, typed states
voicebridge package doctor --json  # machine-readable
voicebridge package launch         # the verified launch argv for this host
voicebridge package upgrade        # upgrade behavior plan (preserves state)
voicebridge package uninstall      # uninstall plan (requires confirmation)
```

- `manifest()` records the name/version, the single supported target
  (Ubuntu x86_64, Python >= 3.11), the stack, the model artifact, the launcher,
  and resource guardrails. Windows/macOS are documented future targets — the
  packet makes **no universal single-binary claim**.
- `doctor()` returns typed `OK | DEGRADED | MISSING | NOT_SUPPORTED` states for
  distro, arch, RAM, disk, GPU/VRAM (5 GB ceiling), Ollama, Chromium CDP,
  audio, OpenSSL, and Bubblewrap isolation. CPU-only and missing-Ollama/
  browser/audio are typed — never a false pass.
- `model_artifact_record()` resolves the model contract (alias, url, num_ctx,
  keep_alive, digest) from `runner_contract`; the revision/checksum is the live
  Ollama digest when available and is **never a fabricated value**.
- `upgrade_plan()` / `uninstall_plan()` describe behavior only: re-running the
  installers preserves tuned units with `.bak`, never enables services, and
  uninstall requires explicit operator confirmation and preserves the state dir.

## Portable-state export/import (audited encryption)

```bash
export-state --passphrase ... --out bundle.vbp       # or VOICE_BRIDGE_PASSPHRASE
import-state bundle.vbp --passphrase ...             # dry-run/preview by default
import-state bundle.vbp --passphrase ... --apply     # restore with backup+rollback
```

Contract (`voicebridge/portable.py`):
- **Included**: memory, lessons (incl. preferences), reports, non-secret
  config, model metadata, migration history.
- **Excluded by construction**: API keys, tokens, cookies, Chromium profiles,
  CDP debugger URLs, active approvals, credentials, machine-specific audio/GPU
  paths, and whole secret-bearing config sections (`webui`, `openclaw`,
  `logging`).
- **Encryption**: PBKDF2-HMAC-SHA256 key derivation (RFC 8018, stdlib) →
  AES-256-CBC via the **system OpenSSL CLI** (audited, pinned ≥ 3.0.0) →
  HMAC-SHA256 encrypt-then-MAC (RFC 2104). No home-grown cryptography; no new
  Python dependency. The bundle records `openssl_version` and KDF params and
  verifies them on restore.
- **Restore**: integrity is verified before any write; staging is atomic per
  file (temp + fsync + `os.replace`); a pre-restore backup is kept; failure
  rolls back to the backup; `--apply` requires the device to be enrolled.
  `dry-run`/preview writes nothing.

## Trusted-device enrollment

```bash
voicebridge device status     # current profile (identity + capabilities)
voicebridge device enroll     # EXPLICIT authorization of THIS device
```

- Enrollment is never automatic — a restore to a new device requires
  `device enroll` and re-checks hardware, model alias, optional dependencies,
  and capabilities (`assert_device_compatible` fails closed on a missing
  required capability or model mismatch).
- No live browser profile, cookie, credential, or secret is ever copied;
  `device.json` is 0600 under the private state root.

## User-facing audio settings contract (remediation)

`voicebridge/audio/settings.py` + CLI:

```bash
voicebridge audio list            # enumerate inputs/outputs by stable name/role/backend
voicebridge audio select mic <name>      # persist a stable NAME (never an index)
voicebridge audio select output <name>
voicebridge audio reset           # clear pins -> OS defaults
voicebridge audio status          # typed OK/DEGRADED/MISSING/PERMISSION_DENIED
```

- Devices are identified by a **stable role/name/backend** identifier built
  ONLY from a NORMALIZED name (e.g. `input:HyperX Cloud III: USB Audio:ALSA`).
  Raw ALSA `(hw:N,M)` paths and naked `hw:N,M` tokens are stripped, and a bare
  numeric PortAudio index is never a name. `raw_name`/`index` exist only for
  diagnostics and are never exposed by the CLI/JSON or persisted.
- Selection is persisted to the config by the **normalized stable name** with
  a timestamped backup; legacy numeric index keys are cleared so a shifting
  index is never stored, and a persist-time guard rejects any value containing
  `hw:` or a numeric index.
- A configured device that disappears (hot unplug) is reported **MISSING with
  "no silent fallback"** — the contract never guesses a replacement.
- `reset` returns output to the system default sink and mic to the default
  source, preserving the working HyperX rules (mono USB mic, empty output pins).
- Changing settings is a pure config write: **no service restart, no recording
  stream is started/stopped, no subprocess is spawned**.
- Enabling the HyperX mono input and default output rules is preserved by the
  existing `mic.channels: 1` + empty `audio.output_device*` pins; the settings
  module only manages the name pins.

## Model metadata verification (remediation)

```bash
voicebridge package model            # model artifact record (fail closed)
voicebridge package doctor           # includes a typed `model` check
```

- The model digest/revision is the **actual Ollama digest** resolved at runtime
  (`/api/show` `X-Ollama-Digest`, falling back to `/api/tags` digest). It is
  never a fabricated placeholder.
- When the digest cannot be obtained the record **fails closed** with state
  `MISSING` (revision/checksum `None`) and the doctor's `model` check is
  MISSING — no claim of verification without evidence.
- License and source provenance are recorded (`Apache-2.0 (Qwen3-4B-Instruct-
  2507-derived; see docs/UPSTREAM_REUSE.md)`).
- `verify_artifact(path, expected_sha256)` and `artifact_checksum(path)`
  provide SHA-256 verification for every downloaded model artifact, with typed
  `OK / MISSING / FAIL` (tampered) results.

## Distributable artifact completeness (remediation)

```bash
voicebridge package distributable     # manifest + completeness check
```

- `distribution.distributable_manifest()` explicitly lists the launcher
  (`scripts/run.sh`), installer (`scripts/install.sh`,
  `scripts/install_systemd_unit.sh`), config template
  (`config/voice.example.yaml`), systemd template, and metadata
  (`pyproject.toml`, `docs/OPERATIONS_7C.md`) with SHA-256 of each.
- The install/launch paths are relocatable: `PROJECT_DIR` is derived from the
  script location and state/config/data use `XDG_*`/`$HOME` — proven by tests
  that stage the distributable outside the repository and outside the
  developer home and launch it there.
- **No universal binary is claimed**; Ubuntu x86_64 is the single supported
  target, Windows/macOS are documented future targets.

## Tests

`tests/test_packet7c.py` (30): manifest versioning/platform explicitness,
typed doctor states (all-OK, CPU-only + missing deps, GPU-OK), model artifact
metadata (never a fake checksum, fail-closed without a digest), launcher/
upgrade/uninstall contracts, export/import roundtrip with sanitization,
wrong-passphrase rejection, corrupted-bundle rejection, bad-magic, unsupported
schema, machine-path and secret-section exclusion, low-disk rejection, dry-run
writes nothing, atomic apply + backup, restore-failure rollback, bundle-info
header read, device profile/enroll/require-enrolled/compatibility, CLI
doctor/export/import/device.

`tests/test_packet7c_audio.py` (33, remediation): audio enumeration with stable
name/role/backend identifiers, **normalization that strips raw ALSA
`(hw:N,M)` paths and bare numeric indices**, persisted-config/stable-ID audit
(no `hw:`, no index selection), default flags, permission-denied typing, select
by name/substring/stable-id/backend, invalid-selection rejection, never-match-
by-index, reset-to-defaults, persistence across restart, hot-plug MISSING (never
a silent fallback), OK status for present devices, no-restart/no-recording on
settings change, model digest fail-closed vs OK, artifact checksum
OK/tampered/missing, model doctor check fail-closed, distributable manifest
completeness, missing-file reporting, no-universal-binary claim,
relocatable install/launch from outside repo+home, and the CLI audio/package
surfaces.

## Rollback

Revert the Packet 7C commit and remove `voicebridge/distribution.py`,
`voicebridge/portable.py`, `voicebridge/device.py`,
`voicebridge/audio/settings.py`, the new CLI commands,
`tests/test_packet7c.py`, `tests/test_packet7c_audio.py`, and
`docs/OPERATIONS_7C.md`. No service, timer, model, browser, audio, config, or
state data is changed by any 7C command; export/import only reads the state
dir and (on `--apply`) keeps a pre-restore backup that can restore the prior
state. Audio settings changes keep `<config>.audio.<ts>.bak` backups and are
pure config writes.