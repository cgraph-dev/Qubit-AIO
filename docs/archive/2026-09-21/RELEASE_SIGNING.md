# Release Signing — detached-signature workflow (Packet 8K-P)

Signing is **optional** and fully compatible with a school-only release:
unsigned artifacts stay clearly marked `NOT_SIGNED` and never block a
school submission unless the operator asks for a signed release. Nothing is
ever claimed `SIGNED` unless a real detached signature verifies with
`gpg --verify` against the approved **public** fingerprint.

## Safety rules

- The **private signing key is never generated, committed, printed, uploaded,
  or exposed** here. Only the public 40-hex fingerprint is recorded.
- Never claim `SIGNED` without an actual detached signature that verifies.
- A valid signature from any key other than the approved one is
  `UNTRUSTED_SIGNER` and never counts as signed.
- A modified/tampered artifact (checksum or signature mismatch) is
  `INVALID` / `FAILED`, never `SIGNED`.
- Missing gpg or a missing approved fingerprint is `DEGRADED`, never a pass.

## 1. One-time signer setup (operator)

Create a dedicated release-signing key and keep its secret key private:

```bash
gpg --full-generate-key        # ed25519/certify+sign, no expiry or long expiry
gpg --list-secret-keys --keyid-format=long
# copy the FULL 40-hex fingerprint of the signing subkey
gpg --armor --export <fingerprint>   # public key — safe to share
```

Record the **public** fingerprint where the certification can read it:

```bash
echo "<40-hex fingerprint>" > config/release-signer.fingerprint
# or: export RELEASE_SIGNER_FINGERPRINT=<40-hex fingerprint>
```

`config/release-signer.example.fingerprint` documents the format. Never put a
private key in the repository.

## 2. Build from a clean clone only

```bash
git clone <repo> /tmp/release && cd /tmp/release
git status --porcelain          # MUST be clean
python -m build --wheel --sdist --outdir dist/release-8k/build1 .
```

## 3. Sign (generate SHA256SUMS, detached signatures, signed manifest)

```bash
python scripts/release_qualify.py --sign \
    --out dist/release-8k \
    --signer-fingerprint <40-hex fingerprint>
```

This produces, in the build dir:

- `SHA256SUMS` — `<sha256>  <filename>` for every wheel/sdist;
- `<artifact>.asc` — detached ASCII-armored signature per artifact
  (`gpg --detach-sign --armor`);
- `release-manifest.json` — public manifest (names, sha256, sizes, public
  signer fingerprint) plus `release-manifest.json.asc`.

Signing uses the operator's secret key from the default gpg keyring (or
`--gpg-homedir DIR`). If any step fails, the aggregate is `DEGRADED`, never
`SIGNED`.

## 4. Verify

Automated (checksums + every detached signature, bound to the approved
fingerprint):

```bash
python scripts/release_qualify.py --verify-signatures \
    --out dist/release-8k --signer-fingerprint <40-hex fingerprint>
```

Manual:

```bash
cd dist/release-8k/build1
sha256sum -c SHA256SUMS
gpg --verify qubit_voice_agent-1.0.0-py3-none-any.whl.asc \
              qubit_voice_agent-1.0.0-py3-none-any.whl
gpg --verify release-manifest.json.asc release-manifest.json
```

Typed results: `SIGNED` (all artifacts verify against the approved
fingerprint), `NOT_SIGNED` (no signatures), `INVALID` (crypto/checksum
failure), `UNTRUSTED_SIGNER` (valid signature from the wrong key),
`DEGRADED` (gpg or approved fingerprint missing).

## 5. Full qualification

```bash
python scripts/release_qualify.py --all --out dist/release-8k \
    --signer-fingerprint <40-hex fingerprint> --json
```

Without signing configured, artifacts are reported `NOT_SIGNED` and the
build step BLOCKs the qualification unless `--unsigned-ok` records an explicit
approved unsigned-release policy (the school-only default). A signed release
never uses `--unsigned-ok`.

## 6. School-only note

This is a school project. Artifact signing is **NOT required** and is reported
honestly as **NOT APPLICABLE** for the school submission unless the operator
explicitly sets up a key. The workflow above exists so a signed release is
possible and verifiable, but unsigned artifacts remain clearly marked
`NOT_SIGNED` and are not misrepresented.

## Tests

`tests/test_release_signing.py` covers: valid signature, invalid signature
(tampered artifact), wrong signer, missing signature, modified artifact
(checksum), missing gpg, public-fingerprint-only manifest, and reproducible
checksums — all hermetic with a throwaway gpg home.