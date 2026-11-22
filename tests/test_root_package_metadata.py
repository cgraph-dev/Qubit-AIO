"""Guard root packaging metadata against the setuptools license-table regression.

This uses only Python 3.12 stdlib and deliberately does not import the agent.
The packaging/E2E jobs remain responsible for actually building the wheel.
"""
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_root_license_is_pep639_expression():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    license_expression = metadata["license"]
    assert isinstance(license_expression, str), (
        "PEP 639 requires project.license to be a string; a legacy {text=...} "
        "table fails setuptools metadata validation"
    )
    assert re.fullmatch(r"LicenseRef-[A-Za-z0-9.-]+", license_expression)
    assert "LICENSE" in metadata["license-files"]
    assert "THIRD_PARTY_NOTICES.md" in metadata["license-files"]
    for name in metadata["license-files"]:
        assert (ROOT / name).is_file(), name
