"""Fresh-install config seed must not expose the legacy product brand.

``scripts/install.sh`` and ``scripts/install.ps1`` seed the root
``config.yaml`` by copying ``qubit_runtime/cli-config.yaml.example``. That file
is therefore user-visible on a brand-new install and must name Qubit, not the
pre-rebrand product.

The scan is intentionally narrow: legacy product tokens are rejected unless the
same line is an attribution/provenance line or the token is a declared
compatibility identifier (``HERMES_*`` env names, ``ai.hermes.gateway*`` unit
names). Provider names such as ``Nous Research`` and model names are not
product brand and are left alone.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / "qubit_runtime"
EXAMPLE = RUNTIME_ROOT / "cli-config.yaml.example"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

_LEGACY_PRODUCT = re.compile(r"hermes|openclaw", re.IGNORECASE)
# Attribution/provenance lines may name the upstream project legally.
_ATTRIBUTION = re.compile(
    r"Nous Research|Nous Portal|Copyright|SPDX|License|provenance|upstream",
    re.IGNORECASE,
)
# Declared compatibility identifiers that must NOT be renamed.
_COMPAT_IDENTIFIERS = (
    re.compile(r"HERMES_[A-Z0-9_]+"),
    re.compile(r"ai\.hermes\.[A-Za-z0-9_.*-]+"),
)


def _compat_stripped(line: str) -> str:
    for pattern in _COMPAT_IDENTIFIERS:
        line = pattern.sub("", line)
    return line


def _legacy_lines(text: str) -> list[tuple[int, str]]:
    """Return (line number, line) for user-visible legacy product mentions."""
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if _ATTRIBUTION.search(line):
            continue
        if _LEGACY_PRODUCT.search(_compat_stripped(line)):
            found.append((number, line))
    return found


def test_example_names_only_qubit_on_user_visible_lines():
    text = EXAMPLE.read_text(encoding="utf-8")
    assert "Qubit Agent CLI Configuration" in text
    assert "~/.qubit/config.yaml" in text
    assert _legacy_lines(text) == []


def test_seeded_fresh_config_parses_and_is_brand_clean(tmp_path):
    from qubit_cli.config import seed_config_file

    seeded = tmp_path / "config.yaml"
    assert seed_config_file(seeded, template=EXAMPLE) is True
    assert seeded.is_file()

    text = seeded.read_text(encoding="utf-8")
    assert _legacy_lines(text) == []

    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    # The fresh-install local Ollama contract survives the prose cleanup.
    model = data["model"]
    assert isinstance(model, dict)
    assert model["provider"] == "ollama"
    assert model["default"] == "qubit-brain"
    assert str(model["base_url"]).startswith("http://127.0.0.1:11434")
