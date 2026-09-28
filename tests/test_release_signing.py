"""Packet 8K-P: release signing + source-hygiene workflow tests.

Signing is OPTIONAL and school-only compatible. These tests use a hermetic
throwaway GPG home (a generated test key); the private key is never exposed,
committed, or printed. No claim of SIGNED is ever made without an actual
detached signature that verifies (gpg VALIDSIG) against the approved PUBLIC
fingerprint. Tested matrix: valid signature, invalid signature (tampered
artifact), wrong signer, missing signature, modified artifact (checksum), and
missing gpg.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Tuple

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from voicebridge import release as R  # noqa: E402


def _gpg(args, *, check: bool = False, env=None):
    return subprocess.run(["gpg", *args], capture_output=True, text=True,
                          timeout=180, env=env, check=check)


def _fingerprint(home: str) -> str:
    r = _gpg(["--homedir", home, "--with-colons", "--list-secret-keys"],
             check=True)
    for line in r.stdout.splitlines():
        if line.startswith("fpr:"):
            return line.split(":")[9]
    raise AssertionError("no secret key fingerprint found")


@pytest.fixture(scope="module")
def signer_key(tmp_path_factory) -> Tuple[str, str]:
    """Hermetic GPG home + the generated key's PUBLIC 40-hex fingerprint."""
    home = str(tmp_path_factory.mktemp("gnupg-signer"))
    Path(home).chmod(0o700)
    _gpg(["--homedir", home, "--batch", "--passphrase", "",
          "--quick-generate-key", "Signer <sign@example.com>", "ed25519",
          "sign", "0"], check=True)
    return home, _fingerprint(home)


def _env(home: str, fp: str = "") -> Dict[str, str]:
    env = dict(os.environ)
    env["GNUPGHOME"] = home
    if fp:
        env["RELEASE_SIGNER_FINGERPRINT"] = fp
    return env


def _artifacts(tmp_path: Path) -> Path:
    d = tmp_path / "out"
    d.mkdir(parents=True)
    (d / "qubit_voice_agent-1.0.0-py3-none-any.whl").write_bytes(b"whl-bytes")
    (d / "qubit_voice_agent-1.0.0.tar.gz").write_bytes(b"sdist-bytes")
    return d


def test_valid_signature_verifies_signed(tmp_path, signer_key):
    home, fp = signer_key
    d = _artifacts(tmp_path)
    R.generate_checksums(d)
    for a in R.collect_artifacts(d):
        res = R.sign_detached(a, homedir=Path(home), env=_env(home))
        assert res["state"] == "SIGNED", res
        assert Path(str(a) + ".asc").exists()
    mf = R.generate_signed_manifest(d, homedir=Path(home), env=_env(home))
    assert mf["state"] == "SIGNED", mf
    agg = R.verify_release_signing(d, expected_fp=fp, homedir=Path(home),
                                   env=_env(home))
    assert agg["state"] == "SIGNED", agg
    whl = R.collect_artifacts(d)[0]
    v = R.verify_artifact_signature(whl, expected_fp=fp, env=_env(home))
    assert v["state"] == "SIGNED"
    assert v["approved"] is True


def test_invalid_signature_after_tamper(tmp_path, signer_key):
    home, fp = signer_key
    d = _artifacts(tmp_path)
    R.generate_checksums(d)
    for a in R.collect_artifacts(d):
        R.sign_detached(a, homedir=Path(home), env=_env(home))
    whl = R.collect_artifacts(d)[0]
    whl.write_bytes(whl.read_bytes() + b"tamper")
    v = R.verify_artifact_signature(whl, expected_fp=fp, env=_env(home))
    assert v["state"] == "INVALID", v
    agg = R.verify_release_signing(d, expected_fp=fp, homedir=Path(home),
                                   env=_env(home))
    assert agg["state"] != "SIGNED"
    ck = R.verify_checksums(d)
    assert ck["state"] == "FAILED"
    assert any("whl" in m for m in ck["mismatches"])


def test_wrong_signer_is_untrusted(tmp_path, signer_key):
    home, fp = signer_key
    other = tmp_path / "other-gnupg"
    other.mkdir(mode=0o700)
    _gpg(["--homedir", str(other), "--batch", "--passphrase", "",
          "--quick-generate-key", "Other <other@example.com>", "ed25519",
          "sign", "0"], check=True)
    other_fp = _fingerprint(str(other))
    assert other_fp != fp
    d = _artifacts(tmp_path)
    R.generate_checksums(d)
    for a in R.collect_artifacts(d):
        R.sign_detached(a, homedir=other, env=_env(str(other)))
    whl = R.collect_artifacts(d)[0]
    # signature is cryptographically valid but from the WRONG key
    v = R.verify_artifact_signature(whl, expected_fp=fp, env=_env(str(other)))
    assert v["state"] == "UNTRUSTED_SIGNER", v
    agg = R.verify_release_signing(d, expected_fp=fp, homedir=other,
                                   env=_env(str(other)))
    assert agg["state"] == "UNTRUSTED_SIGNER"


def test_missing_signature_is_not_signed(tmp_path):
    d = _artifacts(tmp_path)
    R.generate_checksums(d)
    agg = R.verify_release_signing(d, expected_fp="A" * 40)
    assert agg["state"] == "NOT_SIGNED"
    whl = R.collect_artifacts(d)[0]
    v = R.verify_artifact_signature(whl, expected_fp="A" * 40)
    assert v["state"] == "NOT_SIGNED"


def test_modified_artifact_checksum_fails(tmp_path, signer_key):
    home, fp = signer_key
    d = _artifacts(tmp_path)
    R.generate_checksums(d)
    for a in R.collect_artifacts(d):
        R.sign_detached(a, homedir=Path(home), env=_env(home))
    whl = R.collect_artifacts(d)[0]
    whl.write_bytes(whl.read_bytes() + b"x")
    ck = R.verify_checksums(d)
    assert ck["state"] == "FAILED"
    agg = R.verify_release_signing(d, expected_fp=fp, homedir=Path(home),
                                   env=_env(home))
    assert agg["state"] == "FAILED"


def test_missing_gpg_degrades(tmp_path, monkeypatch):
    d = _artifacts(tmp_path)
    R.generate_checksums(d)
    whl = R.collect_artifacts(d)[0]
    # a signature file exists but gpg is unavailable -> DEGRADED (never SIGNED)
    Path(str(whl) + ".asc").write_text("placeholder")
    import shutil
    orig = shutil.which

    def fake(name, *a, **k):
        if name == "gpg":
            return None
        return orig(name, *a, **k)

    monkeypatch.setattr(shutil, "which", fake)
    v = R.verify_artifact_signature(whl, expected_fp="A" * 40)
    assert v["state"] == "DEGRADED"
    sd = R.sign_detached(whl, env=dict(os.environ))
    assert sd["state"] == "DEGRADED"


def test_manifest_records_public_fingerprint_only(tmp_path, signer_key):
    home, fp = signer_key
    d = _artifacts(tmp_path)
    R.generate_checksums(d)
    for a in R.collect_artifacts(d):
        R.sign_detached(a, homedir=Path(home), env=_env(home))
    mf = R.generate_signed_manifest(d, homedir=Path(home),
                                    env=_env(home, fp))
    assert mf["state"] == "SIGNED", mf
    assert mf["signer_fingerprint"] == fp
    data = (d / R.RELEASE_MANIFEST_NAME).read_text()
    assert fp in data
    assert "PRIVATE KEY" not in data
    for art in R.release_manifest(d)["artifacts"]:
        assert art["sha256"]
        assert art["size_bytes"] > 0


def test_sign_artifacts_aggregate_and_checksums_reproducible(tmp_path,
                                                             signer_key):
    home, fp = signer_key
    d = _artifacts(tmp_path)
    agg = R.sign_artifacts(d, homedir=Path(home), env=_env(home, fp), key=fp)
    assert agg["state"] == "SIGNED", agg
    assert (d / "SHA256SUMS").exists()
    assert (d / R.RELEASE_MANIFEST_NAME).exists()
    assert (d / (R.RELEASE_MANIFEST_NAME + ".asc")).exists()
    before = (d / "SHA256SUMS").read_text()
    R.generate_checksums(d)
    assert (d / "SHA256SUMS").read_text() == before
    verified = R.verify_release_signing(d, expected_fp=fp,
                                        homedir=Path(home), env=_env(home))
    assert verified["state"] == "SIGNED"


def test_signature_status_never_claims_signed_without_key():
    """artifact_signature_status is static capability detection only and can
    never claim an artifact is signed."""
    st = R.artifact_signature_status()
    assert st["state"] in ("SIGNING_CAPABLE", "NOT_CONFIGURED")
    assert st["state"] != "SIGNED"
    assert "approved_fingerprint_set" in st
    assert "gpg_available" in st