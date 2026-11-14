"""P8-04 constants and script loaders shared by the tests and conftest."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

VERIFIER = ROOT / "scripts" / "verify_observability.py"
A11Y = ROOT / "scripts" / "accessibility_audit.py"
PLUGIN = ROOT / "frontend" / "qubit-app" / "plugins" / "qubit-office"

IDS = {
    "tenant": "ten_test",
    "owner": "own_test",
    "agent": "agt_test",
    "run": "run_test",
    "task": "tsk_test",
    "session": "ses_test",
    "provider_request": "prq_test",
    "artifact": "art_test",
    "approval": "apr_test",
}
SECRET = "test-exact-vault-value-991"
PROMPT = "PROMPT_SENTINEL_NOT_EXPORTED"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module




def make_observability(tensor, **kwargs):
    OBS, otel, redactor = tensor
    options = {
        "sampling": otel.SamplingPolicy(baseline_rate=1.0),
        "redactor": redactor,
    }
    options.update(kwargs)
    return OBS.Observability(**options)
