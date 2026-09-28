"""Wave 25 — rebranded file renames in the vendored runtime.

The ledger gap ``tier1_b2`` (``legacy installer name``) recorded that the
rebrand engine left the upstream setup scripts verbatim
(``setup-hermes.sh`` / ``setup-hermes.ps1``) while the canonical Qubit name is
``setup-qubit.sh`` / ``setup-qubit.ps1``. Wave 25 completes the rename:

* the files are ``setup-qubit.*``;
* every in-repo consumer (activation, nix, CI classification, Windows build
  helpers, the setup tests) points at the Qubit name;
* the vendored-manifest rename map still records the upstream -> shipped
  mapping so the pinned-source byte comparison reconciles read-old.

Hermetic: plain file reads only; no network, no services, no ``~/.hermes``.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

RENAMED = {
    "setup-hermes.sh": "setup-qubit.sh",
    "setup-hermes.ps1": "setup-qubit.ps1",
}

#: In-repo consumers that used to name the upstream setup scripts. Every one
#: must now name the Qubit spelling (the manifest map below is the deliberate
#: read-old exception, so it is not included here).
CONSUMERS = (
    "qubit_runtime/activate",
    "qubit_runtime/activate.ps1",
    "qubit_runtime/nix/lib.nix",
    "qubit_runtime/scripts/build/windows-deps.ps1",
    "qubit_runtime/scripts/ci/classify_changes.py",
    "qubit_runtime/scripts/gen-bootstrap-pins.py",
    "qubit_runtime/scripts/install.sh",
    "qubit_runtime/scripts/windows-build-deps.ps1",
    "qubit_runtime/website/docs/reference/package-management.md",
    "qubit_runtime/tests/ci/test_classify_changes.py",
    "qubit_runtime/tests/pm/test_activate_scripts.py",
    "qubit_runtime/tests/pm/test_activation_runtime.py",
    "qubit_runtime/tests/pm/test_activation_setup.py",
    "qubit_runtime/tests/pm/test_setup_lock_format.py",
    "qubit_runtime/tests/qubit_cli/test_setup_hermes_script.py",
    "qubit_runtime/tests/qubit_cli/test_setup_pin_values.py",
    "qubit_runtime/tests/scripts/test_bootstrap_mirror.py",
    "qubit_runtime/tests/scripts/test_fresh_source_install.py",
    "qubit_runtime/tests/scripts/test_install_sh_python_pin_indent.py",
    "qubit_runtime/tests/scripts/test_powershell_script_syntax.py",
)


def test_setup_scripts_are_renamed_to_the_qubit_spelling():
    for old, new in RENAMED.items():
        assert (REPO / "qubit_runtime" / new).is_file(), new
        assert not (REPO / "qubit_runtime" / old).exists(), old


def test_no_consumer_still_names_the_legacy_setup_scripts():
    offenders = {}
    for rel in CONSUMERS:
        p = REPO / rel
        assert p.is_file(), rel
        hits = [
            f"{rel}:{n}"
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if "setup-hermes" in line
        ]
        if hits:
            offenders[rel] = hits
    assert not offenders, offenders


def test_activation_invokes_the_renamed_scripts():
    sh = (REPO / "qubit_runtime" / "activate").read_text(encoding="utf-8")
    ps1 = (REPO / "qubit_runtime" / "activate.ps1").read_text(encoding="utf-8")
    assert "setup-qubit.sh" in sh
    assert "$_HERMES_REPO/setup-qubit.sh" in sh
    assert "setup-qubit.ps1" in ps1
    assert r"$repo\setup-qubit.ps1" in ps1


def test_vendor_manifest_rename_map_records_both_setup_scripts():
    import re

    src = (REPO / "scripts" / "hermes_vendor_manifest.py").read_text(
        encoding="utf-8")
    m = re.search(r"_EXTRA_NAME_RENAMES[^{]*\{(?P<body>[^}]*)\}", src, re.S)
    assert m, "_EXTRA_NAME_RENAMES block not found"
    body = m.group("body")
    for old, new in RENAMED.items():
        assert f'"{old}": "{new}"' in body, (old, new)


def test_setup_script_tests_resolve_the_renamed_paths():
    # The vendored tests that copy/invoke the script must reference the Qubit
    # path (a stale upstream path would copy/voke a nonexistent file).
    for rel in (
        "qubit_runtime/tests/scripts/test_bootstrap_mirror.py",
        "qubit_runtime/tests/scripts/test_fresh_source_install.py",
        "qubit_runtime/tests/qubit_cli/test_setup_pin_values.py",
        "qubit_runtime/tests/pm/test_setup_lock_format.py",
    ):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "setup-qubit.sh" in text, rel
        assert "setup-hermes" not in text, rel
