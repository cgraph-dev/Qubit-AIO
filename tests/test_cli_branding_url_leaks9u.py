"""9U+ — non-allowlisted upstream URLs removed from owned CLI surfaces.

An independent scan found upstream docs URLs rendered from
``qubit_runtime/qubit_cli`` help/print/HTML paths. The frozen allowlist covers
only wire tokens, and the ``help_overlay`` render boundary deliberately leaves
any line containing ``https://`` untouched as legal/provenance, so those URL
literals were never rewritten. This wave drops the upstream docs URL literals
and neutralises the served config defaults, while the runtime keeps its internal
fetch endpoints.

The shipped ``hey_hermes`` wake-word model name is NOT renamed: it names the
real bundled artifact (``tools/wakewords/hey_hermes.{onnx,tflite}``) and is a
preserved model identifier, not product branding.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "qubit_runtime"))

CLI = REPO / "qubit_runtime" / "qubit_cli"

# Files whose rendered help/print/HTML text must carry no upstream docs host.
RENDERED_FILES = (
    "subcommands/worktree.py",
    "subcommands/secrets.py",
    "subcommands/fallback.py",
    "subcommands/egress.py",
    "fallback_cmd.py",
    "portal_cli.py",
    "kanban_parser.py",
    "setup_platforms.py",
    "setup_whatsapp_cloud.py",
    "dashboard_auth/login_page.py",
)


def test_rendered_cli_surfaces_have_no_upstream_docs_url():
    hits = []
    for rel in RENDERED_FILES:
        text = (CLI / rel).read_text(encoding="utf-8")
        if "hermes-agent.nousresearch.com" in text:
            hits.append(rel)
    assert not hits, f"upstream docs URL still rendered by: {hits}"


def test_served_config_defaults_expose_no_upstream_endpoint():
    from qubit_cli.config_defaults import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["model_catalog"]["url"] == ""
    assert DEFAULT_CONFIG["telemetry"]["shared_metrics"]["endpoint"] == ""
    assert DEFAULT_CONFIG["cron"]["chronos"]["portal_url"] == ""

    blob = json.dumps(DEFAULT_CONFIG)
    assert "nousresearch.com" not in blob, \
        "served config defaults leak an upstream endpoint"
    assert not re.search(r"https?://[^\"']*(?:hermes|nous)", blob, re.I), blob


def test_upstream_endpoints_remain_internal_runtime_fallbacks():
    """Emptying the served defaults must not drop the runtime fallback."""
    from qubit_cli import model_catalog
    from qubit_cli.observability import shared_metrics_send_config as sm

    assert model_catalog.DEFAULT_CATALOG_URL, "internal catalog fallback missing"
    assert sm.DEFAULT_ENDPOINT, "internal telemetry fallback missing"
    # The internal fallback is what an unset/empty served default resolves to.
    assert model_catalog._load_catalog_config()["url"] == \
        model_catalog.DEFAULT_CATALOG_URL


def test_hey_hermes_wakeword_model_identifier_is_preserved():
    """``hey_hermes`` names a shipped model artifact, not product branding."""
    from qubit_cli.config_defaults import DEFAULT_CONFIG
    from tools import wake_word

    assert DEFAULT_CONFIG["wake_word"]["openwakeword"]["model"] == "hey_hermes"
    assert wake_word._BUNDLED_MODEL_NAME == "hey_hermes"
    artifacts = Path(wake_word.__file__).parent / "wakewords"
    assert (artifacts / "hey_hermes.onnx").is_file()
    assert (artifacts / "hey_hermes.tflite").is_file()
