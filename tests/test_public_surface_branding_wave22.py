"""Wave 22 — public-surface branding scan guard.

``scripts/qubit_public_surface_scan.py`` is a deterministic, read-only scanner
that checks only the surfaces a user/operator can reach and emits one JSON
report with a final ``public_leaks`` count:

``cli``, ``routes``, ``update_receipt`` (hermetic ``full_apply``), ``voice``,
``bundles`` (``frontend/qubit-dist`` + ``frontend/qubit-app``) and
``source_strings`` — the update modules/CLI/voice blocks plus **every** public
desktop/web source file under ``qubit_runtime/apps/desktop/src``,
``qubit_runtime/apps/desktop/electron`` and ``frontend/qubit-web/src``.

This test encodes the owner directive: **zero** public Hermes / OpenClaw /
NousResearch branding leaks. It runs the scanner's own core functions, so it
fails while other agents are still closing a leak — that is correct; the
failure message is the exact file:line + token list they need.

Classification contract (source/bundle surfaces):

- PUBLIC (user-facing URL / route / prose) -> counted leak;
- INTERNAL wire/filesystem/module identifier -> allowlisted, reported;
- GUARD (legacy URL / reference-service refusal) -> allowlisted, reported;
- COMMENT/DOCSTRING -> allowlisted, reported;
- test/E2E/story fixtures -> allowlisted, reported.

So the suite can only pass when the public surface is genuinely clean *and* the
scanner actually saw and classified real internal material (not an empty scan).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCANNER = REPO / "scripts" / "qubit_public_surface_scan.py"

#: Surfaces the scan must cover (the receipt surface is exercised too).
EXPECTED_SURFACES = (
    "cli", "routes", "update_receipt", "voice", "bundles", "source_strings",
)
#: Public desktop/web source roots the source surface must sweep.
EXPECTED_SOURCE_ROOTS = (
    REPO / "qubit_runtime" / "apps" / "desktop" / "src",
    REPO / "qubit_runtime" / "apps" / "desktop" / "electron",
    REPO / "frontend" / "qubit-web" / "src",
)
#: Allowlist classes the classifier must actually produce.
EXPECTED_ALLOW_CLASSES = ("wire_identifier", "comment", "test_fixture", "guard")


def _load():
    name = "qubit_public_surface_scan_wave22"
    spec = importlib.util.spec_from_file_location(name, SCANNER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    assert SCANNER.is_file(), "scripts/qubit_public_surface_scan.py is missing"
    return _load()


@pytest.fixture(scope="module")
def report(mod):
    return mod.run_scan()


def _format(leaks):
    lines = []
    for item in leaks:
        loc = item["file"] + (f":{item['line']}" if item.get("line") else "")
        lines.append(
            f"  [{item['surface']}/{item['classification']}] {loc} "
            f"token={item['token']!r} :: {item['text']}")
    return "\n".join(lines)


def _observed_classes(report):
    classes = set()
    for key in report["allowlisted"]["observed_counts"]:
        parts = key.split(":")
        if len(parts) >= 2:
            classes.add(parts[1])
    return classes


# ---------------------------------------------------------------- surfaces
def test_report_covers_every_public_surface(report):
    assert set(report["surfaces"]) >= set(EXPECTED_SURFACES)
    assert report["schema"] == "qubit-public-surface-scan/1"
    assert "categories" in report["allowlisted"]


@pytest.mark.parametrize("surface", EXPECTED_SURFACES)
def test_surface_has_no_public_leaks(report, surface):
    leaks = report["surfaces"][surface]["findings"]
    assert report["surfaces"][surface]["leak_count"] == len(leaks)
    assert not leaks, (
        f"legacy public branding leaked on the {surface!r} surface:\n"
        + _format(leaks))


def test_zero_public_leaks(report):
    """The owner directive: zero public Hermes / OpenClaw branding leaks."""
    assert report["public_leaks"] == 0, (
        f"{report['public_leaks']} public branding leak(s) remain:\n"
        + _format(report["leaks"]))


# ------------------------------------------------------------- source sweep
def test_source_surface_scans_all_public_desktop_web_roots(mod):
    """The source surface must sweep the full desktop + web source tree."""
    assert all(root.is_dir() for root in EXPECTED_SOURCE_ROOTS), (
        "one or more public source roots are missing")
    sources = mod._iter_ts_sources()
    assert len(sources) >= 1000, (
        f"source sweep found only {len(sources)} files — roots not covered")
    for root in EXPECTED_SOURCE_ROOTS:
        prefix = str(root.relative_to(REPO)) + "/"
        assert any(s.startswith(prefix) for s in sources), (
            f"source sweep missed {prefix}")


# --------------------------------------------------------------- allowlist
def test_allowlist_is_explicit(report):
    cats = report["allowlisted"]["categories"]
    for required in ("legal_attribution", "frozen_records",
                     "generated_artifacts", "wire_identifiers",
                     "source_guards", "comments", "test_fixtures"):
        assert required in cats, f"allowlist category missing: {required}"


def test_allowlist_classes_are_actually_observed(report):
    """Guard against a vacuous pass: the classifier must have run on real
    internal/commented/fixture material and reported it, not seen nothing."""
    observed = _observed_classes(report)
    for required in EXPECTED_ALLOW_CLASSES:
        assert required in observed, (
            f"allowlist class {required!r} was never observed — the scanner "
            f"did not actually classify real material (observed: "
            f"{sorted(observed)})")
    counts = report["allowlisted"]["observed_counts"]
    assert sum(counts.values()) > 0


# ------------------------------------------------ classifier contract (pure)
def _write(tmp_path: Path, rel: str, text: str) -> None:
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_classifier_separates_public_from_internal(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    _write(tmp_path, "web/login.tsx", "\n".join([
        "const url = 'https://portal.nousresearch.com';",   # public
        "const home = 'HERMES_HOME';",                       # internal env
        "// see the legacy hermes portal for context",       # comment
    ]))
    findings, allow = mod._scan_ts_file("web/login.tsx")
    assert [f["classification"] for f in findings] == ["upstream_url"], findings
    assert findings[0]["line"] == 1
    assert any("wire_identifier" in k for k in allow), allow
    assert any("comment" in k for k in allow), allow


def test_classifier_allowlists_host_predicates(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    _write(tmp_path, "electron/health.ts",
           "return host.endsWith('.agents.nousresearch.com')\n")
    findings, allow = mod._scan_ts_file("electron/health.ts")
    assert findings == [], findings
    assert any("wire_identifier" in k for k in allow), allow


def test_classifier_allowlists_test_fixtures(mod, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    _write(tmp_path, "web/login.test.ts",
           "const url = 'https://portal.nousresearch.com'\n")
    findings, allow = mod._scan_ts_file("web/login.test.ts")
    assert findings == [], findings
    assert any("test_fixture" in k for k in allow), allow


# ------------------------------------------------------- determinism (pure)
def _input_fingerprint(mod):
    """Fingerprint every worktree input the non-receipt scan reads. Other
    agents edit this worktree concurrently (findings N34/N35), so determinism
    is only measurable while the inputs are unchanged."""
    import hashlib

    paths = list(mod._iter_ts_sources())
    paths += list(mod.UPDATE_MODULES)
    paths += ["voicebridge/qubit_compat.py", "voicebridge/voice_commands.py",
              "voicebridge/cli.py"]
    for root in (mod.DIST, mod.APP_OVERLAY):
        if root.is_dir():
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    paths.append(str(p.relative_to(mod.REPO_ROOT)))
    h = hashlib.sha256()
    for rel in sorted(set(paths)):
        try:
            st = (mod.REPO_ROOT / rel).stat()
        except OSError:
            continue
        h.update(f"{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def test_non_receipt_scan_is_deterministic(mod):
    before = _input_fingerprint(mod)
    first = mod.run_scan(skip_receipt=True)
    if before != _input_fingerprint(mod):
        pytest.skip("worktree mutated by a concurrent writer mid-scan")
    second = mod.run_scan(skip_receipt=True)
    if before != _input_fingerprint(mod):
        pytest.skip("worktree mutated by a concurrent writer mid-comparison")
    assert first == second, "public-surface scan is nondeterministic"


def test_cli_help_is_scanned(mod):
    findings, _ = mod.scan_cli()
    assert isinstance(findings, list)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
