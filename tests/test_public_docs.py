"""Public documentation guard.

The root documentation set is shipped to users. These tests keep it present
and keep operator-specific paths and internal-only documents out of it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

PUBLIC_DOCS = (
    "README.md",
    "INSTALL.md",
    "TROUBLESHOOTING.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
)

# Built at runtime so the operator marker never appears verbatim in the
# public tree that scripts/export_public_tree.py scans.
PERSONAL_MARKERS = (
    "/home/" + "trick",
    "/Users/",
    "C:\\Users\\",
)

INTERNAL_DOC_MARKERS = (
    "docs/STATUS.md",
    "docs/OPEN_THREADS.md",
    "docs/DECISIONS.md",
    "docs/FROZEN_TARGET.md",
    "docs/PLAN.md",
    "AGENTS.md",
    "graft/",
)


@pytest.mark.parametrize("name", PUBLIC_DOCS)
def test_public_doc_exists_and_is_nonempty(name: str) -> None:
    path = REPO / name
    assert path.is_file(), f"missing public doc: {name}"
    assert path.read_text(encoding="utf-8").strip(), f"empty public doc: {name}"


@pytest.mark.parametrize("name", PUBLIC_DOCS)
def test_public_doc_has_no_operator_paths(name: str) -> None:
    text = (REPO / name).read_text(encoding="utf-8")
    for marker in PERSONAL_MARKERS:
        assert marker not in text, f"{name} contains an operator path marker: {marker!r}"


@pytest.mark.parametrize("name", PUBLIC_DOCS)
def test_public_doc_does_not_reference_internal_docs(name: str) -> None:
    text = (REPO / name).read_text(encoding="utf-8")
    for marker in INTERNAL_DOC_MARKERS:
        assert marker not in text, f"{name} references an internal doc: {marker!r}"


# Root installer options that the public install guide must keep documenting.
# `scripts/install.sh --help` is the source of truth; this list is the
# user-facing subset that must never silently drop out of INSTALL.md.
INSTALLER_OPTIONS = (
    "--help",
    "--dry-run",
    "--with-systemd",
    "--with-launchd",
    "--without-models",
    "--without-dashboard",
    "--prefix",
    "--yes",
    "--launcher-only",
)

OLD_XDG_PATHS = (
    "~/.config/qubit-voicebridge",
    "~/.local/share/qubit-voicebridge",
    "~/.local/state/qubit-voicebridge",
    "~/.cache/qubit-voicebridge",
)


def test_install_md_documents_every_root_installer_option() -> None:
    text = (REPO / "INSTALL.md").read_text(encoding="utf-8")
    for option in INSTALLER_OPTIONS:
        assert option in text, f"INSTALL.md does not document {option}"


def test_new_install_paths_are_documented_as_the_default() -> None:
    install = (REPO / "INSTALL.md").read_text(encoding="utf-8")
    trouble = (REPO / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
    for name, text in (("INSTALL.md", install), ("TROUBLESHOOTING.md", trouble)):
        assert "$QUBIT_HOME/voice/config" in text, name


def test_old_xdg_paths_are_always_described_as_read_old() -> None:
    for name in PUBLIC_DOCS:
        text = (REPO / name).read_text(encoding="utf-8")
        for block in text.split("\n\n"):
            if not any(path in block for path in OLD_XDG_PATHS):
                continue
            low = block.lower()
            assert any(word in low for word in
                       ("read-old", "existing", "legacy", "xdg")), (
                f"{name} presents an old XDG path without read-old context: "
                f"{block!r}")


def test_troubleshooting_documents_local_brain_unload_and_reload() -> None:
    text = (REPO / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
    for phrase in ("stop the brain", "wake up the brain",
                   "ollama stop qubit-brain"):
        assert phrase in text, f"missing local-brain guidance: {phrase!r}"


def test_macos_voice_is_documented_as_unsupported() -> None:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    low = text.lower()
    assert "macos" in low and "voice" in low
    assert any(marker in low for marker in
               ("not supported", "unsupported", "not yet supported"))
