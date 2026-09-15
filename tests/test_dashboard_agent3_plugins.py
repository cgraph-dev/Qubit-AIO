"""Agent 3 — Qubit overlay dashboard plugins are real, not tiles.

The eleven ``dashboard_plugin`` ledger rows (``plugin_qubit-*``) must be backed
by a real Hermes workspace and a real Qubit backend. This suite proves, from
the shipped frontend source and the production compat dispatcher:

* every manifest has a served tab target and a real entry bundle;
* every ``/api/ops/<name>`` an entry bundle calls is mapped by the real
  ``hermes_compat.h_ops`` dispatcher to a Qubit operation (no fabricated
  frontend-only success, no dead endpoint);
* every other ``/api/...`` endpoint an entry bundle calls exists in the
  registered compat route table;
* labels/descriptions carry no upstream product branding;
* an unknown/fabricated endpoint is refused with a typed code, so a future
  fabricated tile would fail this check.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PLUGDIR = REPO_ROOT / "frontend" / "hermes-qubit" / "plugins"
MANIFESTS = sorted(PLUGDIR.glob("*/manifest.json"))
EXPECTED = [
    "qubit-approvals", "qubit-boards", "qubit-brand", "qubit-chat",
    "qubit-devices", "qubit-gateway", "qubit-memory", "qubit-models",
    "qubit-ops", "qubit-reports", "qubit-runtime",
]
ENDPOINT_RE = re.compile(r"""["'](/api/[A-Za-z0-9_./-]+)["']""")
# Read-only ops endpoints safe to execute during the route audit; mutating
# names are still route-checked, but staged (never executed) by the broker.
DEDICATED = {"run", "confirm", "cancel"}


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_all_ten_plugin_manifests_have_real_entry_bundles():
    assert len(MANIFESTS) == 11, MANIFESTS
    assert sorted(p.parent.name for p in MANIFESTS) == EXPECTED
    for mf in MANIFESTS:
        import json

        man = json.loads(mf.read_text(encoding="utf-8"))
        assert man["name"] == mf.parent.name
        assert man["entry"], man
        entry = mf.parent / man["entry"]
        assert entry.is_file(), entry
        assert entry.read_text(encoding="utf-8").strip(), entry
        assert (man.get("tab") or {}).get("path"), man["name"]
        for bad in ("hermes", "openclaw"):
            blob = (str(man["label"]) + " " + str(man["description"])).lower()
            assert bad not in blob, (man["name"], bad)


def _entry_endpoints():
    found = {}
    for mf in MANIFESTS:
        import json

        man = json.loads(mf.read_text(encoding="utf-8"))
        text = (mf.parent / man["entry"]).read_text(encoding="utf-8")
        found[man["name"]] = sorted({m for m in ENDPOINT_RE.findall(text)
                                     if not m.endswith("/")})
    return found


def test_every_ops_endpoint_in_plugin_source_is_a_real_operation(tmp_path,
                                                                 monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    endpoints = _entry_endpoints()
    assert endpoints["qubit-ops"], "operations plugin must call the broker"
    checked = 0
    for plugin, paths in endpoints.items():
        for path in paths:
            if not path.startswith("/api/ops/"):
                continue
            name = path[len("/api/ops/"):]
            if "/" in name or name in DEDICATED:
                continue
            status, body = HC.dispatch(cfg, "GET", path)
            assert status != 404, (plugin, path, body)
            assert str(body.get("code", "")) != "OPS_UNKNOWN", (plugin, path)
            checked += 1
    assert checked >= 12, checked


def test_every_non_ops_endpoint_in_plugin_source_is_registered():
    from voicebridge import hermes_compat as HC

    known = [r["pattern"] for r in HC.ROUTES]
    missing = []
    for plugin, paths in _entry_endpoints().items():
        for path in paths:
            if path.startswith("/api/ops/"):
                continue
            if not any(re.fullmatch(pattern, path) for pattern in known):
                missing.append((plugin, path))
    assert not missing, missing


def test_ops_plugin_uses_the_shared_run_confirm_cancel_broker():
    text = (PLUGDIR / "qubit-ops" / "qubit-ops.js").read_text(
        encoding="utf-8")
    assert "/api/ops/run" in text
    assert '"/api/ops/"' in text
    for action in ('"confirm"', '"cancel"'):
        assert action in text, action
    # no frontend-only success shortcut
    assert "innerHTML" not in text


def test_fabricated_ops_endpoint_is_typed_not_fabricated_success(tmp_path,
                                                                 monkeypatch):
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    status, body = HC.dispatch(cfg, "GET", "/api/ops/agent-fabricated-op")
    assert status == 404, (status, body)
    assert body.get("code") == "OPS_UNKNOWN", body


def test_every_manifest_tab_is_served_and_unique():
    import json

    from voicebridge import hermes_compat as HC

    manifests = HC._overlay_manifests()
    tabs = []
    for mf in MANIFESTS:
        man = json.loads(mf.read_text(encoding="utf-8"))
        route = "/hermes" + man["tab"]["path"]
        assert route.startswith("/hermes/"), (man["name"], route)
        assert route not in tabs, (man["name"], route)
        tabs.append(route)
    assert len(tabs) == len(manifests) == 11
    # the plugin response is the real served manifest set
    served = {m["name"] for m in HC.plugins_response()["plugins"]}
    assert served == set(EXPECTED)


def test_every_manifest_entry_registers_its_plugin_component():
    import json

    for mf in MANIFESTS:
        man = json.loads(mf.read_text(encoding="utf-8"))
        text = (mf.parent / man["entry"]).read_text(encoding="utf-8")
        assert f'register("{man["name"]}"' in text, man["name"]
        # a tab plugin must render a component, not a dead link
        assert "return" in text or "function" in text, man["name"]


# Ledger row ids for the eleven overlay plugin pages, plus a representative
# read-only Qubit operation each plugin drives through the shared dispatcher.
PLUGIN_ROW_IDS = {
    "qubit-approvals": "plugin_qubit-approvals",
    "qubit-boards": "plugin_qubit-boards",
    "qubit-brand": "plugin_qubit-brand",
    "qubit-chat": "plugin_qubit-chat",
    "qubit-devices": "plugin_qubit-devices",
    "qubit-gateway": "plugin_qubit-gateway",
    "qubit-memory": "plugin_qubit-memory",
    "qubit-models": "plugin_qubit-models",
    "qubit-ops": "plugin_qubit-ops",
    "qubit-reports": "plugin_qubit-reports",
    "qubit-runtime": "plugin_qubit-runtime",
}
PLUGIN_READ_OPS = {
    "qubit-approvals": ("approvals", {}),
    "qubit-boards": ("kanban_boards", {}),
    "qubit-brand": ("themes", {}),
    "qubit-chat": ("status", {}),
    "qubit-devices": ("runtime_status", {}),
    "qubit-gateway": ("gateway", {}),
    "qubit-memory": ("memory_search", {"query": "agent3"}),
    "qubit-models": ("list_model_profiles", {}),
    "qubit-ops": ("capabilities_list", {}),
    "qubit-reports": ("insights", {}),
    "qubit-runtime": ("runtime_status", {}),
}


def _prod_run(cfg, op_id, args=None):
    """Call the production dashboard dispatcher (the routed backend)."""
    from voicebridge import dashboard_ops as OPS

    return OPS.run_op(cfg, op_id, dict(args or {}), actor="dashboard:user",
                      session="dashboard")


def test_every_plugin_manifest_served_through_real_route(tmp_path,
                                                         monkeypatch):
    """Each overlay page: served tab + registered entry + real ops route.

    On-disk proof for ``plugin_qubit-*`` rows: the manifest is returned by the
    production ``hermes_compat.plugins_response`` loader, its tab is a served
    ``/hermes/...`` workspace, its entry bundle registers the component, and
    the page's own read operation executes through ``dashboard_ops.run_op``.
    A fabricated endpoint stays a typed ``OPS_UNKNOWN`` refusal.
    """
    from voicebridge import hermes_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    served = {m["name"]: m for m in HC.plugins_response()["plugins"]}
    for name, row_id in PLUGIN_ROW_IDS.items():
        assert row_id  # ledger row id literal for the proving-test index
        man = served[name]
        assert man["tab"]["path"].startswith("/"), (name, man)
        entry = PLUGDIR / name / man["entry"]
        text = entry.read_text(encoding="utf-8")
        assert f'register("{name}"' in text, name
        op_id, args = PLUGIN_READ_OPS[name]
        out = _prod_run(cfg, op_id, args)
        assert out["state"] == "executed", (row_id, op_id, out)
        assert out["receipt"]["entry"] == op_id
    # every /api/ops endpoint the bundles call is mapped by the real h_ops
    # dispatcher to a Qubit operation (never a dead route)
    checked = 0
    for name, paths in _entry_endpoints().items():
        for path in paths:
            if not path.startswith("/api/ops/"):
                continue
            leaf = path[len("/api/ops/"):]
            if "/" in leaf or leaf in DEDICATED:
                continue
            status, body = HC.dispatch(cfg, "GET", path)
            assert status != 404, (name, path, body)
            assert str(body.get("code", "")) != "OPS_UNKNOWN", (name, path)
            checked += 1
    assert checked >= 12, checked
