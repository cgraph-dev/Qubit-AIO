# Upstream Reuse Record — Packet 7C additions

Packet 0A's `docs/UPSTREAM_REUSE.md` records upstream-derived units for
Packets 0A–2B; it is a pre-existing untracked artifact and is not modified by
Packet 7C. This file carries the 7C-specific upstream decision so it travels
with the 7C commit.

## R16 — Audited portable-state encryption (Packet 7C)

```text
LOCAL REQUIREMENT: versioned portable-state export/import (memory, lessons,
preferences, reports, non-secret config, model metadata, migration history) with
audited, pinned encryption and deterministic wrong-passphrase detection. Never
home-grown cryptography; no new Python dependency (cryptography package is NOT
installed in the project venv).
UPSTREAM REPOSITORY + COMMIT: system OpenSSL, pinned by the installed binary.
Recorded at implementation time: `openssl version` -> OpenSSL 3.0.13
(30 Jan 2024). Key derivation PBKDF2-HMAC-SHA256 (RFC 8018, Python stdlib
hashlib) and HMAC-SHA256 (RFC 2104, Python stdlib hmac) are audited standard
constructions; the AES-256-CBC ciphertext is produced/consumed ONLY by the
system `openssl enc` binary (audited), invoked with explicit -K/-iv and a
pinned minimum version gate (`voicebridge/distribution.py::openssl_ok`,
OPENSSL_REQUIRED = "3.0.0").
SOURCE FILES + SYMBOLS: none copied from OpenSSL source; the local wrapper is
voicebridge/portable.py (PBKDF2 + openssl enc + HMAC encrypt-then-MAC).
UPSTREAM TESTS/FIXTURES: n/a (binary contract); conformance proven by
tests/test_packet7c.py roundtrip / wrong-passphrase / corrupted-bundle /
schema-migration / rollback vectors.
LICENSE: OpenSSL is Apache-2.0; used as a system binary over subprocess, no
OpenSSL code is shipped or modified — no notice file obligation.
DECISION: WRAP (system audited binary) + stdlib KDF/MAC — no vendored crypto.
LOCAL DESTINATION: voicebridge/portable.py, voicebridge/distribution.py.
DEPENDENCIES ADDED: none (stdlib hashlib/hmac + system openssl subprocess).
BEHAVIOR KEPT: AES-256-CBC confidentiality; HMAC-SHA256 encrypt-then-MAC
integrity with deterministic wrong-passphrase failure; bundle records
openssl_version and KDF params for verification on restore.
BEHAVIOR INTENTIONALLY OMITTED: AEAD (GCM) — `openssl enc` does not expose AEAD
at the CLI; CBC + HMAC is used instead with an explicit integrity MAC. No
OS-key-store integration in this packet (explicit passphrase path; a key store
would be a future audited adapter, not home-grown).
WHY THIS IS THE SMALLEST FIT: avoids adding the `cryptography` package to the
venv (a fresh-install/dependency cost) while using only audited primitives from
the existing Ubuntu stack (system OpenSSL + stdlib).
COMPATIBILITY DRIFT: OpenSSL CLI options (-aes-256-cbc, -K, -iv) are stable
across 3.x; the version gate rejects older/unknown binaries.
```