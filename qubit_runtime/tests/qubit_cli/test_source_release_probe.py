"""A desktop check must not reinterpret unreadable channel config as main."""
from pathlib import Path
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("content", ["update: [", "- not-a-mapping", b"\xff"])
def test_invalid_config_refuses_desktop_channel_probe(tmp_path, content):
    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.yaml"
    config.write_bytes(content.encode() if isinstance(content, str) else content)
    # Qubit keeps the tests under qubit_runtime/tests/..., one level deeper than
    # upstream's tests/..., so the install root is the repository root above it.
    root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "qubit_cli.source_check", "--install-root", str(root), "--home", str(home)],
        cwd=root, env={**os.environ, "QUBIT_HOME": str(home), "HERMES_IGNORE_USER_CONFIG": "0"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert '"channel": "main"' not in result.stdout
    assert "config" in result.stderr
    assert config.read_bytes() == (content.encode() if isinstance(content, str) else content)
