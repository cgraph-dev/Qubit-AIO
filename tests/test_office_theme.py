"""The office theme contract is data-only, fail-closed, and local.

WHAT THIS PINS
--------------
Task 1 of the office UI theme upgrade builds the one module every later task
depends on: the contract, the loader, the validator, and the token -> CSS
generator. The rules below are the ones that make a theme safe to load at all:

  * a full local pack validates and loads; a shareable recipe validates without
    any art or file map;
  * credit/licence are mandatory, and ``source`` is a description, never an
    operator path;
  * file paths are relative, allowlisted, traversal-free, and unique both
    after NFC normalisation and case folding;
  * every declared file exists with the declared bytes and sha256;
  * token values are typed (colour or length) and cannot carry a CSS
    exfiltration/injection vector;
  * contrast is checked on the pairs the office actually renders;
  * ``token_css`` emits only the allowlisted ``.ui-root`` variables;
  * an unknown or invalid theme falls back to ``classic`` with a reason.

Hermetic: temporary directories only, no network, no Pillow, no live service.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from voicebridge.office import theme as T

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------- helpers
def _tokens():
    return {
        "color-surface": "#0d0d1a",
        "color-surface-raised": "#161a23",
        "color-surface-hover": "#1d2230",
        "color-text-primary": "#e9edf7",
        "color-text-muted": "#8a93a8",
        "color-border": "#334155",
        "color-accent": "#7cc8ff",
        "radius-control": "4px",
    }


def _manifest(**overrides):
    manifest = {
        "format": T.FORMAT,
        "schema": T.SCHEMA,
        "id": "demo",
        "label": "Demo",
        "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "owner purchase, 2026-11-09",
        "template_version": 1,
        "tokens": _tokens(),
        "slices": {},
        "files": {},
        "limits": {"max_total_bytes": 10_000_000, "max_files": 20},
    }
    manifest.update(overrides)
    return manifest


def _write(root, relpath, data: bytes):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _pack(root, source="owner purchase, 2026-11-09", tokens=None):
    """Write a complete, valid local theme pack and return its root."""
    root = root / "demo"
    root.mkdir(parents=True, exist_ok=True)
    files = {}
    files["portraits/atlas32.png"] = _write(root, "portraits/atlas32.png", b"PNG-PORTRAITS")
    files["bodies/atlas32.png"] = _write(root, "bodies/atlas32.png", b"PNG-BODIES")
    portrait_index = {"schema": 1, "size": 32, "cols": 20, "rows": 15,
                      "cells": {"ceo": [0, 0], "lead-quality": [1, 0]}}
    body_index = {"schema": 1, "size": 32, "frame": {"w": 32, "h": 64},
                  "cols": 20, "rows": 15, "cells": {"ceo": [0, 0]}}
    files["portraits/index.json"] = _write(
        root, "portraits/index.json", json.dumps(portrait_index).encode())
    files["bodies/index.json"] = _write(
        root, "bodies/index.json", json.dumps(body_index).encode())
    agents = {"schema": 1, "templateVersion": 1,
              "recipes": {"ceo": {"skin": 2, "eyes": 1, "hair": 1,
                                   "outfit": 1, "accessory": 1}}}
    files["agents.json"] = _write(
        root, "agents.json", json.dumps(agents).encode())

    manifest = _manifest(
        source=source,
        tokens=tokens or _tokens(),
        files=files,
        portraits={"atlas": "portraits/atlas32.png", "index": "portraits/index.json",
                   "size": 32, "cols": 20, "rows": 15},
        bodies={"atlas": "bodies/atlas32.png", "index": "bodies/index.json",
                "size": 32, "frame": {"w": 32, "h": 64}, "cols": 20, "rows": 15},
        parts={"skin": 9, "eyes": 7, "hair": 200, "outfit": 42, "accessory": 85},
    )
    (root / "theme.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _recipe(**overrides):
    recipe = {
        "format": T.FORMAT,
        "schema": T.SCHEMA,
        "id": "demo",
        "label": "Demo",
        "version": "1.0.0",
        "credit": "LimeZu — https://limezu.itch.io",
        "license": "LimeZu licence; redistribution prohibited",
        "source": "owner purchase, 2026-11-09",
        "template_version": 1,
        "tokens": _tokens(),
        "slices": {},
        "parts": {"skin": 9, "eyes": 7, "hair": 200, "outfit": 42, "accessory": 85},
        "agents": {"ceo": {"skin": 2, "eyes": 1, "hair": 1,
                           "outfit": 1, "accessory": 1}},
    }
    recipe.update(overrides)
    return recipe


class _Cfg:
    """A config object with the same ``.get(*path, default=)`` shape as Config."""

    def __init__(self, data):
        self.data = data

    def get(self, *path, default=None):
        node = self.data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node


# ------------------------------------------------------------ valid inputs
def test_valid_pack_validates_and_loads(tmp_path):
    pack = _pack(tmp_path)
    assert T.validate_theme(pack) == []
    theme = T.load_theme(pack)
    assert theme.id == "demo"
    assert theme.tokens["color-accent"] == "#7cc8ff"
    assert theme.portraits["cols"] == 20
    assert theme.bodies["frame"] == {"w": 32, "h": 64}
    assert theme.agents["ceo"]["hair"] == 1


def test_valid_recipe_validates_without_art(tmp_path):
    assert T.validate_recipe(_recipe()) == []


# -------------------------------------------------------- required fields
@pytest.mark.parametrize("field", ["credit", "license"])
def test_missing_credit_or_license_is_refused(field):
    recipe = _recipe()
    recipe.pop(field)
    problems = T.validate_recipe(recipe)
    assert any(field in p for p in problems), problems


@pytest.mark.parametrize("source", [
    "/home/alice/Desktop/UI Office",
    "~/Desktop/UI Office",
    r"C:\Users\alice\Desktop",
    r"\\server\share\art",
])
def test_source_must_not_be_a_local_path(source, tmp_path):
    pack = _pack(tmp_path, source=source)
    assert any("path" in p for p in T.validate_theme(pack))


def test_recipe_with_files_is_refused():
    problems = T.validate_recipe(_recipe(files={"a.png": {"sha256": "0" * 64, "bytes": 1}}))
    assert any("files" in p for p in problems), problems


# -------------------------------------------------------------- file paths
@pytest.mark.parametrize(("bad", "reason"), [
    ("../escape.png", "segment"),
    ("/absolute.png", "relative"),
    ("a//b.png", "segment"),
    ("./same.png", "segment"),
    ("portraits/CON.png", "reserved"),
    ("portraits/aux.txt", "reserved"),
    ("portraits/x.svg", "suffix"),
    ("portraits/x.css", "suffix"),
    ("portraits/x.js", "suffix"),
    ("portraits/x.html", "suffix"),
])
def test_traversal_reserved_and_unsupported_suffixes_are_refused(bad, reason):
    manifest = _manifest(files={bad: {"sha256": "0" * 64, "bytes": 1}})
    problems = T.validate_manifest(manifest)
    assert any(reason in p for p in problems), (bad, problems)


def test_a_valid_manifest_has_no_path_problems(tmp_path):
    """The control the parametrised path test needs: a good path is silent."""
    pack = _pack(tmp_path)
    manifest = json.loads((pack / "theme.json").read_text())
    assert T.validate_manifest(manifest) == []


def test_case_insensitive_duplicate_is_refused():
    manifest = _manifest(files={
        "portraits/A.png": {"sha256": "0" * 64, "bytes": 1},
        "portraits/a.png": {"sha256": "0" * 64, "bytes": 1},
    })
    assert any("duplicate" in p for p in T.validate_manifest(manifest))


def test_duplicate_after_unicode_normalisation_is_refused():
    manifest = _manifest()
    # 'e' + combining acute vs precomposed 'é'
    manifest["files"] = {"portraits/cafe\u0301.png": {"sha256": "0" * 64, "bytes": 1},
                         "portraits/caf\u00e9.png": {"sha256": "0" * 64, "bytes": 1}}
    assert any("duplicate" in p for p in T.validate_manifest(manifest))


# --------------------------------------------------------- hashes and sizes
def test_hash_mismatch_is_refused(tmp_path):
    pack = _pack(tmp_path)
    (pack / "portraits/atlas32.png").write_bytes(b"TAMPERED")
    problems = T.validate_theme(pack)
    assert any("sha256" in p or "bytes" in p for p in problems), problems


def test_symlinked_file_is_refused(tmp_path):
    pack = _pack(tmp_path)
    target = pack / "portraits/real.png"
    target.write_bytes((pack / "portraits/atlas32.png").read_bytes())
    atlas = pack / "portraits/atlas32.png"
    atlas.unlink()
    atlas.symlink_to(target)
    problems = T.validate_theme(pack)
    assert any("symlink" in p for p in problems), problems


def test_declared_size_mismatch_is_refused(tmp_path):
    pack = _pack(tmp_path)
    manifest = json.loads((pack / "theme.json").read_text())
    manifest["files"]["portraits/atlas32.png"]["bytes"] += 1
    (pack / "theme.json").write_text(json.dumps(manifest))
    problems = T.validate_theme(pack)
    assert any("bytes" in p for p in problems), problems


def test_missing_file_is_refused(tmp_path):
    pack = _pack(tmp_path)
    (pack / "bodies/atlas32.png").unlink()
    problems = T.validate_theme(pack)
    assert any("missing" in p or "exist" in p for p in problems), problems


def test_totals_must_fit_limits(tmp_path):
    pack = _pack(tmp_path)
    manifest = json.loads((pack / "theme.json").read_text())
    manifest["limits"] = {"max_total_bytes": 1, "max_files": 1}
    (pack / "theme.json").write_text(json.dumps(manifest))
    problems = T.validate_theme(pack)
    assert any("max_files" in p or "max_total_bytes" in p for p in problems), problems


# ------------------------------------------------------------------ tokens
@pytest.mark.parametrize("value", [
    "url(https://evil.test/x)", "image-set('x')", "var(--ui-text)",
    "attr(data-x)", "red; } html { display:none", "\\75 rl(",
])
def test_dangerous_token_values_are_refused(value):
    assert T.validate_tokens({"color-accent": value}), value


@pytest.mark.parametrize("value", [
    "expression(alert(1))", "javascript:alert(1)", "@import 'x'",
    "url", "red", "#12", "#12345", "4", "4 pt", "-4px", "01px",
])
def test_untyped_token_values_are_refused(value):
    assert T.validate_tokens({"color-accent": value}), value


def test_non_string_token_value_is_refused_without_crashing():
    problems = T.validate_tokens({"color-surface": 123})
    assert any("token" in p for p in problems), problems


def test_low_contrast_is_reported():
    tokens = {
        "color-surface": "#000000",
        "color-surface-raised": "#000000",
        "color-surface-hover": "#000000",
        "color-text-primary": "#555555",
        "color-text-muted": "#444444",
    }
    problems = T.validate_tokens(tokens)
    assert any("contrast" in p for p in problems), problems


def test_contrast_gate_cannot_be_bypassed_through_the_surface_alias():
    """``color-surface-solid`` emits ``--ui-bg-solid`` just like the raised key."""
    tokens = {
        "color-surface": "#000000",
        "color-surface-solid": "#ffffff",
        "color-text-primary": "#808080",
    }
    problems = T.validate_tokens(tokens)
    assert any("contrast" in p for p in problems), problems
    with pytest.raises(ValueError):
        T.token_css(tokens, {}, "/plugins/x", "1.0.0")


def test_status_colours_must_reach_three_to_one():
    tokens = {"color-surface": "#000000", "color-status-working": "#333333"}
    assert any("contrast" in p for p in T.validate_tokens(tokens))


# ------------------------------------------------------------------- slices
def test_slice_descriptor_requires_valid_shape():
    manifest = _manifest(slices={
        "dock": {"src": "ui/dock.png", "slice": [8, 8, 8, 8],
                 "width": "4px", "repeat": "nope"},
    })
    assert any("repeat" in p for p in T.validate_manifest(manifest))


def test_slice_src_must_be_a_declared_file(tmp_path):
    pack = _pack(tmp_path)
    manifest = json.loads((pack / "theme.json").read_text())
    manifest["slices"] = {"dock": {"src": "ui/absent.png", "slice": [8, 8, 8, 8],
                                   "width": "4px", "repeat": "round"}}
    (pack / "theme.json").write_text(json.dumps(manifest))
    assert any("declared" in p or "missing" in p for p in T.validate_theme(pack))


# ------------------------------------------------------------------ indexes
def test_index_cell_out_of_bounds_is_refused(tmp_path):
    pack = _pack(tmp_path)
    index = json.loads((pack / "portraits/index.json").read_text())
    index["cells"]["ceo"] = [20, 0]  # cols == 20, valid x is 0..19
    (pack / "portraits/index.json").write_bytes(json.dumps(index).encode())
    manifest = json.loads((pack / "theme.json").read_text())
    manifest["files"]["portraits/index.json"] = _write(
        pack, "portraits/index.json", json.dumps(index).encode())
    (pack / "theme.json").write_text(json.dumps(manifest))
    problems = T.validate_theme(pack)
    assert any("out of bounds" in p or "index" in p for p in problems), problems


def test_agent_recipe_index_out_of_bounds_is_refused():
    recipe = _recipe(agents={"ceo": {"skin": 99, "eyes": 0, "hair": 0,
                                     "outfit": 0, "accessory": 0}})
    problems = T.validate_recipe(recipe)
    assert any("index" in p or "out of bounds" in p for p in problems), problems


# ---------------------------------------------------------------- token CSS
def test_token_css_only_allowlisted_vars():
    css = T.token_css({"color-accent": "#7cc8ff", "radius-control": "4px"}, {}, "/plugins/x", "1.0.0")
    assert "--ui-accent:" in css and "--radius-sm:" in css
    for banned in ("display:", "position:", "pointer-events", "z-index", "@layer", "!important", "url("):
        assert banned not in css


def test_token_css_emits_slice_border_image():
    css = T.token_css({}, {"dock": {"src": "ui/dock.png", "slice": [8, 8, 8, 8],
                                    "width": "4px", "repeat": "round"}},
                      "/plugins/x", "1.0.0")
    assert "border-image" in css
    assert 'url("/plugins/x/ui/dock.png?v=1.0.0")' in css
    assert "border-image-width: 4px" in css


def test_token_css_requires_slice_src_to_be_declared_when_files_are_given():
    slices = {"dock": {"src": "ui/dock.png", "slice": [8, 8, 8, 8],
                       "width": "4px", "repeat": "round"}}
    with pytest.raises(ValueError):
        T.token_css({}, slices, "/plugins/x", "1.0.0", files={"other.png"})
    assert "border-image" in T.token_css(
        {}, slices, "/plugins/x", "1.0.0", files={"ui/dock.png"})


def test_token_css_refuses_invalid_tokens():
    with pytest.raises(ValueError):
        T.token_css({"color-accent": "url(https://evil.test/x)"}, {}, "/plugins/x", "1.0.0")


def test_token_css_can_emit_every_allowlisted_variable():
    tokens = {
        "color-surface": "#000000",
        "color-surface-soft": "#000000",
        "color-surface-raised": "#000000",
        "color-surface-solid": "#000000",
        "color-surface-hover": "#000000",
        "color-border": "#ffffff",
        "color-border-strong": "#ffffff",
        "color-text-primary": "#ffffff",
        "color-text-muted": "#999999",
        "color-text-faint": "#999999",
        "color-accent": "#ffffff",
        "color-accent-soft": "#ffffff",
        "radius-panel": "8px",
        "radius-control": "4px",
        "color-status-working": "#ffffff",
        "color-status-waiting": "#ffffff",
        "color-status-done": "#ffffff",
        "color-status-idle": "#ffffff",
        "color-status-offline": "#ffffff",
        "color-status-shell": "#ffffff",
        "color-level-ok": "#ffffff",
        "color-level-warn": "#ffffff",
        "color-level-crit": "#ffffff",
    }
    css = T.token_css(tokens, {}, "/plugins/x", "1.0.0")
    for variable in T.ALLOWED_THEME_VARS:
        assert f"{variable}:" in css, variable
    emitted = {line.split(":", 1)[0].strip()
               for line in css.splitlines() if line.strip().startswith("--")}
    assert emitted <= set(T.ALLOWED_THEME_VARS)


def test_recipe_must_not_declare_atlases():
    problems = T.validate_recipe(_recipe(portraits={"atlas": "p.png"}))
    assert any("atlas" in p for p in problems), problems


# --------------------------------------------------------------- resolution
def test_unknown_theme_falls_back_to_classic_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path])
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    payload = T.theme_payload({}, requested="nope")
    assert payload["id"] == "classic"
    assert "not found" in payload["reason"]


def test_invalid_theme_id_falls_back_to_classic(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path])
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    payload = T.theme_payload({}, requested="../../etc")
    assert payload["id"] == "classic"
    assert "invalid" in payload["reason"]


def test_active_theme_id_is_classic_when_pack_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path])
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    assert T.active_theme_id({}, requested="nope") == "classic"


def test_active_theme_id_returns_an_existing_pack(tmp_path, monkeypatch):
    _pack(tmp_path)
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path])
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    assert T.active_theme_id({}, requested="demo") == "demo"


def test_active_theme_id_is_classic_when_the_pack_is_invalid(tmp_path, monkeypatch):
    pack = _pack(tmp_path)
    manifest = json.loads((pack / "theme.json").read_text())
    manifest.pop("credit")
    (pack / "theme.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path])
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    assert T.active_theme_id({}, requested="demo") == "classic"
    assert T.theme_payload({}, requested="demo")["id"] == "classic"


def test_env_wins_over_requested_and_config(tmp_path, monkeypatch):
    _pack(tmp_path / "a")
    _pack(tmp_path / "b")
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path / "a", tmp_path / "b"])
    monkeypatch.setenv("QUBIT_OFFICE_THEME", "demo")
    payload = T.theme_payload({"office": {"theme": "other"}}, requested="other")
    assert payload["id"] == "demo"


def test_requested_wins_over_config(tmp_path, monkeypatch):
    _pack(tmp_path)
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path])
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    payload = T.theme_payload({"office": {"theme": "other"}}, requested="demo")
    assert payload["id"] == "demo"
    assert payload["active"] is True


def test_config_object_is_supported(tmp_path, monkeypatch):
    _pack(tmp_path)
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path])
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    payload = T.theme_payload(_Cfg({"office": {"theme": "demo"}}))
    assert payload["id"] == "demo"


def test_payload_is_trimmed_and_versioned(tmp_path, monkeypatch):
    _pack(tmp_path)
    monkeypatch.setattr(T, "theme_roots", lambda: [tmp_path])
    monkeypatch.delenv("QUBIT_OFFICE_THEME", raising=False)
    payload = T.theme_payload({}, requested="demo")
    assert "files" not in payload
    assert payload["atlas"].endswith("portraits/atlas32.png?v=1.0.0")
    assert payload["index"].endswith("portraits/index.json?v=1.0.0")
    assert payload["bodies"]["atlas"].endswith("bodies/atlas32.png?v=1.0.0")
    assert payload["credit"].startswith("LimeZu")


def test_theme_roots_order(tmp_path, monkeypatch):
    monkeypatch.setenv("QUBIT_HOME", str(tmp_path / "home"))
    roots = T.theme_roots()
    assert roots[0] == tmp_path / "home" / "office" / "themes"
    assert len(roots) == 3


def test_loader_cache_is_keyed_by_manifest_mtime(tmp_path):
    pack = _pack(tmp_path)
    first = T.load_theme(pack)
    second = T.load_theme(pack)
    assert first is second
    path = pack / "theme.json"
    manifest = json.loads(path.read_text())
    manifest["label"] = "Changed"
    path.write_text(json.dumps(manifest))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    third = T.load_theme(pack)
    assert third.label == "Changed"
    assert third is not first


def test_invalid_pack_raises_typed_error(tmp_path):
    pack = _pack(tmp_path)
    manifest = json.loads((pack / "theme.json").read_text())
    manifest.pop("credit")
    (pack / "theme.json").write_text(json.dumps(manifest))
    with pytest.raises(T.ThemeError):
        T.load_theme(pack)


# ----------------------------------------------------- host chrome (Task 7)
def test_host_token_css_scopes_tokens_and_slices_to_the_host():
    css = T.host_token_css(
        _tokens(), "demo", "1.0.0",
        {"dock": {"src": "ui/dock.png", "slice": [8, 8, 8, 8],
                  "width": "8px", "repeat": "stretch"}})
    assert ':root[data-office-theme="demo"] {' in css
    assert ':root[data-office-theme="demo"] [data-office-slice="dock"] {' in css
    assert 'border-image: url("/plugins/qubit-office/themes/ui/dock.png?v=1.0.0")' in css
    # The painted slice and the reserved CSS border are the SAME width.
    assert "border-width: 8px" in css
    assert "border-image-width: 8px" in css
    # Only our own elements are targeted; no pack CSS reaches Habblaud.
    assert ".ui-root" not in css


def test_host_token_css_without_slices_emits_no_border_image():
    css = T.host_token_css(_tokens(), "demo", "1.0.0")
    assert "--ui-bg:" in css
    assert "border-image" not in css
    assert "[data-office-slice" not in css


def test_host_token_css_refuses_an_invalid_slice():
    with pytest.raises(ValueError):
        T.host_token_css(_tokens(), "demo", "1.0.0",
                         {"dock": {"src": "../escape.png", "slice": [8, 8, 8, 8],
                                   "width": "8px", "repeat": "stretch"}})


def test_host_token_css_requires_a_valid_theme_id():
    with pytest.raises(ValueError):
        T.host_token_css(_tokens(), 'bad"id', "1.0.0")
