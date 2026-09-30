"""Wave 22 — onboarding routes, theme parity, bundled plugins, scope helper.

Behavioral invariants (no source-text reads) for the 1:1 repairs:

* the 8 channel-onboarding routes resolve to implemented handlers (not gates),
* ``themes_response`` serves the vendored built-ins + the Qubit overlay and
  ``h_theme_set`` persists any listed theme while refusing unknown names,
* ``list_plugins`` includes the vendored bundled set (59 rows Hermes shows),
* ``normalize_scope`` preserves the None/global contract of the reference.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        (tmp_path / var.lower()).mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(var, str(tmp_path / var.lower()))
    qhome = tmp_path / "qhome"
    (qhome / "skills").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("QUBIT_HOME", str(qhome))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n")
    return C.Config.load(p)


def test_onboarding_routes_are_implemented_not_gated():
    from voicebridge import qubit_compat as HC

    expected = {
        ("POST", "/api/messaging/whatsapp/onboarding/start"): "h_wa_ob_start",
        ("GET", "/api/messaging/whatsapp/onboarding/abc"): "h_wa_ob_status",
        ("POST", "/api/messaging/whatsapp/onboarding/abc/apply"): "h_wa_ob_apply",
        ("DELETE", "/api/messaging/whatsapp/onboarding/abc"): "h_wa_ob_cancel",
        ("POST", "/api/messaging/telegram/onboarding/start"): "h_tg_ob_start",
        ("GET", "/api/messaging/telegram/onboarding/abc"): "h_tg_ob_status",
        ("POST", "/api/messaging/telegram/onboarding/abc/apply"): "h_tg_ob_apply",
        ("DELETE", "/api/messaging/telegram/onboarding/abc"): "h_tg_ob_cancel",
    }
    for (method, path), handler in expected.items():
        route, params = HC.match(method, path)
        assert route is not None, f"no route for {method} {path}"
        assert route["disposition"] == "implemented", (method, path, route["disposition"])
        assert route["handler"] == handler, (method, path, route["handler"])
    # the blanket refusal rows are gone: no gated onboarding wildcard remains
    for row in HC.ROUTES:
        if row.get("disposition") == "gated" and "onboarding" in str(row.get("pattern", "")):
            raise AssertionError(f"stale gate still present: {row}")


def test_themes_serve_vendored_builtins_plus_overlay(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    out = HC.themes_response(cfg)
    names = [t["name"] for t in out["themes"]]
    for expected in ("default", "midnight", "ember", "mono", "cyberpunk",
                     "rose", "nous-blue", "default-large", "qubit"):
        assert expected in names, names
    code, res = HC.dispatch(cfg, "PUT", "/api/dashboard/theme",
                            {}, {"theme": "midnight"})
    assert code == 200 and res.get("persisted") is True, res
    code, res = HC.dispatch(cfg, "GET", "/api/dashboard/theme", {}, {})
    assert res.get("theme") == "midnight", res
    code, res = HC.dispatch(cfg, "PUT", "/api/dashboard/theme",
                            {}, {"theme": "nope"})
    assert code == 400 and res.get("code") == "NOT_FOUND", res


def test_list_plugins_includes_vendored_bundled_set(tmp_path, monkeypatch):
    from voicebridge import plugin_runtime as PR

    cfg = _cfg(tmp_path, monkeypatch)
    out = PR.list_plugins(cfg)
    assert out.get("state") == "OK", out
    rows = out["plugins"]
    bundled = [r for r in rows if r.get("source_kind") == "directory"]
    assert len(bundled) >= 50, len(bundled)
    ids = {r["id"] for r in rows}
    assert "browser/browser_use" in ids, sorted(ids)[:5]
    assert len(ids) == len(rows), "duplicate plugin ids"


def test_normalize_scope_contract():
    from voicebridge import qubit_runtime as QR

    QR.ensure_path()
    from qubit_constants import normalize_scope, qubit_home_key

    assert normalize_scope(None) is None
    assert normalize_scope("/tmp") == qubit_home_key("/tmp")
