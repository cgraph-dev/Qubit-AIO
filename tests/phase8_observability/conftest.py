"""P8-04 fixtures: root import path, script loaders and one verifier run."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import pytest

from _p8_helpers import A11Y, ROOT, VERIFIER, _load


@pytest.fixture(scope="session")
def verifier():
    return _load("p8_verify_observability", VERIFIER)


@pytest.fixture(scope="session")
def a11y_audit():
    return _load("p8_accessibility_audit", A11Y)


@pytest.fixture(scope="session")
def tensor():
    from voicebridge.business import observability as OBS
    from voicebridge.business import otel

    redactor = lambda attrs: otel.redact_attributes(  # noqa: E731
        attrs, extra_secrets=("test-exact-vault-value-991",)
    )
    return OBS, otel, redactor


@pytest.fixture(scope="session")
def verifier_report():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.setdefault("TMPDIR", "/tmp")
    proc = subprocess.run(
        [sys.executable, str(VERIFIER), "--json"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)
