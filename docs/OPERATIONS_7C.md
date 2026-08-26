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

## Tests

`tests/test_packet7c.py` (30): manifest versioning/platform explicitness,
typed doctor states (all-OK, CPU-only + missing deps, GPU-OK), model artifact
metadata (never a fake checksum), launcher/upgrade/uninstall contracts,
export/import roundtrip with sanitization, wrong-passphrase rejection,
corrupted-bundle rejection, bad-magic, unsupported schema, machine-path and
secret-section exclusion, low-disk rejection, dry-run writes nothing, atomic
apply + backup, restore-failure rollback, bundle-info header read, device
profile/enroll/require-enrolled/compatibility, CLI doctor/export/import/device.

## Rollback

Revert the Packet 7C commit and remove `voicebridge/distribution.py`,
`voicebridge/portable.py`, `voicebridge/device.py`, the new CLI commands,
`tests/test_packet7c.py`, and `docs/OPERATIONS_7C.md`. No service, timer,
model, browser, audio, config, or state data is changed by any 7C command;
export/import only reads the state dir and (on `--apply`) keeps a pre-restore
backup that can restore the prior state.