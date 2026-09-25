"""Task R4 (v0.21.5 rebase) — Anthropic SDK dependency for the SDKless test env.

Regression: the v0.21.5 vendored runtime eagerly builds a native Anthropic
client inside ``agent.agent_init._init_anthropic_client`` and the vendored
suite asserts on real SDK client shape. In the product test venv the optional
``anthropic`` extra is absent and PM cannot lazy-install (``pm-runtime`` is not
installed, lazy installs disabled), so every such test died at
``agent/anthropic_adapter.py:63`` with::

    ImportError: The 'anthropic' package is required for the Anthropic provider:
    pm-runtime: not installed and lazy installs are disabled ...

Root cause: a missing *dependency*, not a code regression. The SDK belongs to
the vendored runtime and is declared there as an optional extra and in the test
group; the product test environment must carry the same reviewed pin. The
adapter's import stays guarded/lazy so module import never requires the SDK.

Hermetic: no network, no model, no venv. The vendored module is imported
in-process; HOME/QUBIT_HOME are throwaway when the suite is invoked.
"""
from __future__ import annotations

import importlib
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "qubit_runtime"
VENDORED_PYPROJECT = RUNTIME / "pyproject.toml"
PRODUCT_PYPROJECT = REPO / "pyproject.toml"
ADAPTER = RUNTIME / "agent" / "anthropic_adapter.py"

# The reviewed pin shared by the vendored extra/test group and the product env.
ANTHROPIC_PIN = "anthropic==0.87.0"


def _load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _normalized(dep: str) -> str:
    name = dep.split(";")[0].strip()
    name = re.sub(r"\[.*?\]", "", name)
    name = re.split(r"[<>=!~ ]", name, maxsplit=1)[0]
    return name.lower().replace("_", "-").strip()


def test_vendored_runtime_declares_the_anthropic_sdk():
    doc = _load_toml(VENDORED_PYPROJECT)
    extra = doc["project"]["optional-dependencies"]["anthropic"]
    assert extra == [ANTHROPIC_PIN], extra
    test_group = doc["dependency-groups"]["test"]
    assert ANTHROPIC_PIN in test_group, test_group


def test_product_test_env_carries_the_same_anthropic_pin():
    """The env the vendored suite runs in must install the reviewed SDK."""
    doc = _load_toml(PRODUCT_PYPROJECT)
    product_test = doc["project"]["optional-dependencies"]["test"]
    by_name = {_normalized(d): d for d in product_test}
    assert by_name.get("anthropic") == ANTHROPIC_PIN, product_test


def test_anthropic_pin_matches_between_vendored_and_product():
    vendored = _load_toml(VENDORED_PYPROJECT)["project"]["optional-dependencies"]["anthropic"]
    product = _load_toml(PRODUCT_PYPROJECT)["project"]["optional-dependencies"]["test"]
    vendored_pin = next(d for d in vendored if _normalized(d) == "anthropic")
    product_pin = next(d for d in product if _normalized(d) == "anthropic")
    assert vendored_pin == product_pin, (vendored_pin, product_pin)


def test_adapter_import_is_guarded_and_sdk_lazy():
    """Importing the adapter must not require the optional SDK (guarded import).

    The SDK costs ~220 ms and is provider-optional, so it is imported lazily at
    first use; only the client-construction paths raise when it is absent.
    """
    sys.path.insert(0, str(RUNTIME))
    try:
        adapter = importlib.import_module("agent.anthropic_adapter")
    finally:
        sys.path.pop(0)
    assert callable(adapter._require_sdk)
    assert callable(adapter._get_anthropic_sdk)
    source = ADAPTER.read_text(encoding="utf-8")
    assert not re.search(r"^import anthropic\b", source, re.MULTILINE), (
        "a module-level SDK import freezes startup; keep it lazy"
    )
    assert re.search(r"^\s+import anthropic\b", source, re.MULTILINE), (
        "the lazy `import anthropic` inside _get_anthropic_sdk is expected"
    )
