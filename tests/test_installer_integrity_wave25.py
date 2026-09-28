"""Installer download-integrity tests (Wave 25).

Forensic finding (Medium): ``scripts/install.sh`` downloaded model/voice/
tooling assets with ``curl -sL`` and no integrity verification, so a tampered
or truncated download would be installed silently.

These tests are hermetic (no network, the installer is never executed):

* **source-level** — every real ``curl`` download in ``install.sh`` goes
  through the verified ``dl`` helper, which is covered by a pinned sha256 and a
  typed checksum-mismatch failure (``ASSET_CHECKSUM_MISMATCH`` / ``exit 9``);
  every ``dl`` call passes a pinned constant and no bare download remains;
* **behavioral** — the ``verify_sha256`` / ``dl`` helpers extracted from the
  installer are exercised in a throwaway bash harness with a fake ``curl``: a
  matching digest installs, a tampered/truncated one exits 9 and leaves no file
  behind, and a verified existing asset is not re-downloaded.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INSTALL_SH = REPO / "scripts" / "install.sh"
BASH = shutil.which("bash") or "/bin/bash"

HEX64 = re.compile(r"^[0-9a-f]{64}$")
CURL_DOWNLOAD = re.compile(r"^[ \t]*curl\b")
DL_CALL = re.compile(r"^[ \t]*dl\s")
PINNED_CONST = re.compile(r'^([A-Z0-9_]+_SHA256)="([0-9a-f]{64})"', re.M)
FUNC_START = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{")

# Every asset the installer fetches, and the constant that pins it.
PINNED_ASSETS = (
    "VOICE_SHA256",
    "VOICE_JSON_SHA256",
    "MOON_SHA256",
    "PKT_SHA256",
    "SILERO_V5_SHA256",
    "SILERO_V6_SHA256",
    "ERES2NET_SHA256",
)


def _read() -> str:
    return INSTALL_SH.read_text(encoding="utf-8")


def _function_blocks(src: str) -> dict[str, str]:
    """Map shell function name -> body, assuming a column-0 ``}`` terminator."""
    blocks: dict[str, str] = {}
    lines = src.splitlines()
    i = 0
    while i < len(lines):
        m = FUNC_START.match(lines[i])
        if not m:
            i += 1
            continue
        body = [lines[i]]
        i += 1
        while i < len(lines) and lines[i] != "}":
            body.append(lines[i])
            i += 1
        if i < len(lines):
            body.append(lines[i])
            i += 1
        blocks[m.group(1)] = "\n".join(body)
    return blocks


def _extract(name: str) -> str:
    blocks = _function_blocks(_read())
    assert name in blocks, f"install.sh has no {name}() function"
    return blocks[name]


# ===========================================================================
# 1. Source-level: every curl download is verified, pinned, typed.
# ===========================================================================
def test_every_curl_download_goes_through_a_verified_helper():
    src = _read()
    blocks = _function_blocks(src)
    # A real download invocation starts (after whitespace) with `curl`; the
    # echoed uv hint and the commented-out optional lines do not qualify.
    downloads = [ln for ln in src.splitlines() if CURL_DOWNLOAD.match(ln)]
    assert downloads, "install.sh has no curl download to check"

    for line in downloads:
        owners = [n for n, body in blocks.items() if line in body]
        assert owners, f"curl download outside any helper function: {line!r}"
        for owner in owners:
            body = blocks[owner]
            assert "verify_sha256" in body, (
                f"{owner} downloads with curl but never verifies a sha256")
            assert "ASSET_CHECKSUM_MISMATCH" in body, (
                f"{owner} must fail typed on a checksum mismatch")
            assert re.search(r"exit\s+[1-9]", body), (
                f"{owner} must exit non-zero on a checksum mismatch")
    # verification itself must use the POSIX sha256sum check
    verifier = _extract("verify_sha256")
    assert "sha256sum -c" in verifier, (
        "verify_sha256 must verify with `sha256sum -c` (POSIX)")


def test_no_bare_download_bypasses_the_helper():
    src = _read()
    # exactly one curl invocation (inside dl); anything more must also live in a
    # verifying helper, which the previous test proves.
    downloads = [ln for ln in src.splitlines() if CURL_DOWNLOAD.match(ln)]
    assert len(downloads) == 1, (
        "all downloads must funnel through the single verified `dl` helper; "
        f"found {len(downloads)}: {downloads}")


def test_every_pinned_sha256_constant_is_wellformed():
    consts = dict(PINNED_CONST.findall(_read()))
    for name in PINNED_ASSETS:
        assert name in consts, f"missing pinned checksum constant: {name}"
    assert len(consts) >= len(PINNED_ASSETS)
    for name, value in consts.items():
        assert HEX64.match(value), f"{name} is not a 64-hex sha256: {value!r}"


def test_every_dl_call_passes_a_pinned_sha256():
    # join line-continuations so a multi-line `dl` call is one logical line
    joined = _read().replace("\\\n", " ")
    calls = [ln for ln in joined.splitlines() if DL_CALL.match(ln)]
    assert calls, "install.sh has no dl call sites"
    for call in calls:
        assert re.search(r"\$[A-Z0-9_]+_SHA256\b", call), (
            f"dl call without a pinned sha256 constant: {call.strip()}")


def test_checksum_provenance_and_unavailable_fallback_are_documented():
    src = _read()
    # upstream-published provenance is named ...
    assert "checksum.txt" in src, "sherpa checksum.txt provenance not named"
    assert "LFS" in src, "piper HuggingFace LFS provenance not named"
    # ... and the authoring-observed ones are labelled honestly ...
    assert "observed on the pinned" in src
    # ... and the no-checksum fallback is documented as NOT verified.
    assert "NOT verified" in src
    assert "dl <dest> <url> -" in src


def test_installer_is_syntax_clean():
    r = subprocess.run([BASH, "-n", str(INSTALL_SH)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


# ===========================================================================
# 2. Behavioral: the extracted helpers actually enforce the digest.
# ===========================================================================
FAKE_CURL = r'''
curl() {
  local out="" prev=""
  for a in "$@"; do
    [ "$prev" = "-o" ] && out="$a"
    prev="$a"
  done
  cp "$FAKE_BODY" "$out"
}
'''


def _harness(extra_fns: tuple[str, ...], body: str) -> str:
    parts = ["set -uo pipefail"]
    parts.extend(_extract(n) for n in ("_sha256_hex", "verify_sha256"))
    parts.extend(extra_fns)
    parts.append(body)
    return "\n".join(parts)


def _run(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script], capture_output=True,
                          text=True, timeout=60)


def test_verify_sha256_accepts_matching_digest(tmp_path: Path):
    f = tmp_path / "asset.bin"
    f.write_bytes(b"integrity probe\n")
    good = hashlib.sha256(b"integrity probe\n").hexdigest()
    r = _run(_harness((), f'verify_sha256 "{f}" "{good}" && echo VERIFY_OK'))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "VERIFY_OK" in r.stdout


def test_verify_sha256_rejects_mismatch_with_typed_error(tmp_path: Path):
    f = tmp_path / "asset.bin"
    f.write_bytes(b"integrity probe\n")
    r = _run(_harness((), f'verify_sha256 "{f}" "{"0" * 64}"'))
    assert r.returncode != 0
    assert "ASSET_CHECKSUM_MISMATCH" in r.stderr


def test_dl_installs_a_matching_asset(tmp_path: Path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    good = hashlib.sha256(b"payload").hexdigest()
    dest = tmp_path / "models" / "asset.onnx"
    dest.parent.mkdir()
    body = (f'FAKE_BODY="{src}"\n' + FAKE_CURL +
            f'\ndl "{dest}" "https://example.invalid/a" "{good}" "test"')
    r = _run(_harness((_extract("dl"),), body))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert dest.read_bytes() == b"payload"
    assert not list(dest.parent.glob("*.tmp.*")), "temp file leaked"


def test_dl_rejects_a_tampered_or_truncated_asset_with_exit_9(tmp_path: Path):
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"tampered")  # a truncated/different body
    good = hashlib.sha256(b"payload").hexdigest()
    dest = tmp_path / "models" / "asset.onnx"
    dest.parent.mkdir()
    body = (f'FAKE_BODY="{bad}"\n' + FAKE_CURL +
            f'\ndl "{dest}" "https://example.invalid/a" "{good}" "test"')
    r = _run(_harness((_extract("dl"),), body))
    assert r.returncode == 9, (r.returncode, r.stdout, r.stderr)
    assert "ASSET_CHECKSUM_MISMATCH" in r.stderr
    assert not dest.exists(), "a tampered asset must never be installed"
    assert not list(dest.parent.glob("*.tmp.*")), "temp file leaked"


def test_dl_skips_an_already_verified_asset(tmp_path: Path):
    dest = tmp_path / "models" / "asset.onnx"
    dest.parent.mkdir()
    dest.write_bytes(b"payload")
    good = hashlib.sha256(b"payload").hexdigest()
    # A fake curl that would fail if it were (needlessly) invoked.
    failing_curl = 'curl() { return 1; }'
    body = (f'FAKE_BODY="/nonexistent"\n' + failing_curl +
            f'\ndl "{dest}" "https://example.invalid/a" "{good}" "test"')
    r = _run(_harness((_extract("dl"),), body))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert dest.read_bytes() == b"payload"
    assert "sha256 verified" in r.stdout
    assert "downloading" not in r.stdout


def test_dl_repairs_an_asset_whose_pinned_hash_no_longer_matches(tmp_path: Path):
    dest = tmp_path / "models" / "asset.onnx"
    dest.parent.mkdir()
    dest.write_bytes(b"stale-corrupt")
    src = tmp_path / "src.bin"
    src.write_bytes(b"payload")
    good = hashlib.sha256(b"payload").hexdigest()
    body = (f'FAKE_BODY="{src}"\n' + FAKE_CURL +
            f'\ndl "{dest}" "https://example.invalid/a" "{good}" "test"')
    r = _run(_harness((_extract("dl"),), body))
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert dest.read_bytes() == b"payload", "a corrupt cache must be repaired"
