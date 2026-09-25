# tests/test_rebrand_engine_tokens.py
import importlib.util, pathlib
REPO = pathlib.Path(__file__).resolve().parents[1]
def _engine():
    p = REPO / "scripts" / "qubit_rebrand.py"
    s = importlib.util.spec_from_file_location("qb_rebrand", p)
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
def test_missing_modules_are_mapped():
    mt = _engine().MODULE_TOKENS
    assert mt.get("hermes_state_health") == "qubit_state_health"
    assert mt.get("hermes_yaml") == "qubit_yaml"
