# tests/test_rebrand_identifier_map.py
"""Focused contract tests for the rebrand engine's ``IDENTIFIER_TOKENS`` map.

The map is the systemic fix for the v0.21.5 token-map gap: identifiers such as
``get_hermes_home`` are Python *symbols* -- neither module paths (the
``MODULE_TOKENS`` pass) nor env names (the ``ENV_TOKENS`` pass) -- so the
structural passes cannot express them.  Without the map OURS calls
``get_hermes_home`` while the Qubit tree defines ``get_qubit_home``.

Two obligations are pinned here:
  (a) the listed Hermes-named home/const identifiers are rewritten to Qubit;
  (b) load-bearing wire/compat/env/legal strings survive verbatim.

The engine-level whole-tree sweep lives in ``test_rebrand_engine_tokens.py``;
this file is the focused, fixture-driven proof.
"""
from __future__ import annotations

import importlib.util
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]


def _engine():
    p = REPO / "scripts" / "qubit_rebrand.py"
    s = importlib.util.spec_from_file_location("qb_rebrand_ident_map", p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


#: The exact home/const identifiers the fork-patch matrix requires renamed.
#: Kept explicit (never derived from the engine) so a map regression fails loud.
EXPECTED_RENAMES = {
    "get_hermes_home": "get_qubit_home",
    "get_process_hermes_home": "get_process_qubit_home",
    "get_hermes_home_override": "get_qubit_home_override",
    "set_hermes_home_override": "set_qubit_home_override",
    "reset_hermes_home_override": "reset_qubit_home_override",
    "display_hermes_home": "display_qubit_home",
    "get_default_hermes_root": "get_default_qubit_root",
    "get_hermes_dir": "get_qubit_dir",
    "mkdir_under_hermes_home": "mkdir_under_qubit_home",
    "get_routing_process_hermes_home": "get_routing_process_qubit_home",
    "pin_process_hermes_home": "pin_process_qubit_home",
    "process_hermes_home_is_pinned": "process_qubit_home_is_pinned",
    "_same_hermes_home": "_same_qubit_home",
    "_hermes_metadata": "_qubit_metadata",
    "_find_hermes_md": "_find_qubit_md",
}

#: Strings that must never be rewritten, even when they share a prefix with a
#: mapped identifier.  These are wire/data/compat/legal surfaces, not symbols.
NEVER_RENAME = (
    '"hermes_home"',             # persisted DB/JSON key (quote-adjacent)
    "metadata.hermes",           # read-old frontmatter key
    "X-Qubit-Session-Token",    # wire header
    "HermesAgent/",              # User-Agent prefix value
    "window.__HERMES_PLUGINS__",  # frontend global
    "Copyright (c) 2025 Nous Research",  # legal attribution line
)


# ---------------------------------------------------------------------------
# (a) renames apply to a fixture
# ---------------------------------------------------------------------------
def test_identifier_map_has_expected_renames():
    it = _engine().IDENTIFIER_TOKENS
    wrong = {o: it.get(o) for o, n in EXPECTED_RENAMES.items() if it.get(o) != n}
    assert not wrong, f"IDENTIFIER_TOKENS missing/incorrect: {wrong}"


def test_identifier_rewrite_renames_fixture(tmp_path):
    m = _engine()
    p = tmp_path / "fixture.py"
    p.write_text(
        "from qubit_constants import get_hermes_home\n"
        "def f(hermes_home):\n"
        "    root = get_default_hermes_root()\n"
        "    pin_process_hermes_home(get_process_hermes_home())\n"
        "    mkdir_under_hermes_home(get_hermes_dir())\n"
        "    display_hermes_home()\n"
        "    return (_same_hermes_home(hermes_home),\n"
        "            _hermes_metadata, _find_hermes_md,\n"
        "            get_routing_process_hermes_home(),\n"
        "            process_hermes_home_is_pinned(),\n"
        "            get_hermes_home_override(),\n"
        "            set_hermes_home_override(hermes_home),\n"
        "            reset_hermes_home_override())\n",
        encoding="utf-8",
    )
    m._rewrite_text(p, "fixture.py")
    out = p.read_text(encoding="utf-8")

    for old, new in EXPECTED_RENAMES.items():
        assert not re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", out), old
        assert re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(new)}(?![A-Za-z0-9_])", out), new
    # the bare local/kwarg symbol moved too
    assert "hermes_home" not in out
    assert "def f(qubit_home)" in out


def test_identifier_map_does_not_break_existing_maps():
    m = _engine()
    # pre-existing structural maps remain intact
    assert m.MODULE_TOKENS.get("hermes_state_health") == "qubit_state_health"
    assert m.MODULE_TOKENS.get("hermes_yaml") == "qubit_yaml"
    assert m.ENV_TOKENS.get("HERMES_HOME") == "QUBIT_HOME"
    # the identifier map is additive: no module/env name is captured by it
    for key in m.IDENTIFIER_TOKENS:
        assert key not in m.MODULE_TOKENS, key
        assert key not in m.ENV_TOKENS, key


# ---------------------------------------------------------------------------
# (b) never-rename strings are untouched
# ---------------------------------------------------------------------------
def test_never_rename_strings_survive(tmp_path):
    m = _engine()
    p = tmp_path / "wire.py"
    p.write_text(
        "def f(hermes_home):\n"
        '    rec = {"hermes_home": str(hermes_home)}\n'
        '    fm = {"metadata": {"hermes": {"tags": []}}}\n'
        '    dot = "metadata.hermes"\n'
        '    hdr = "X-Qubit-Session-Token"\n'
        '    ua = "HermesAgent/1.2.3"\n'
        '    js = "window.__HERMES_PLUGINS__"\n'
        '    legal = "Copyright (c) 2025 Nous Research"\n'
        "    return rec.get(\"hermes_home\"), fm, dot, hdr, ua, js, legal\n",
        encoding="utf-8",
    )
    m._rewrite_text(p, "wire.py")
    out = p.read_text(encoding="utf-8")

    for literal in NEVER_RENAME:
        assert literal in out, f"never-rename literal lost: {literal!r}"
    # the serialized key is preserved, the local kwarg/variable is renamed
    assert '{"hermes_home": str(qubit_home)}' in out
    assert "def f(qubit_home)" in out


def test_other_hermes_env_names_are_not_identifier_tokens(tmp_path):
    """The identifier pass must not own the load-bearing ``HERMES_*`` env names.

    ``HERMES_HOME`` is deliberately owned by the pre-existing ``ENV_TOKENS``
    pass (frozen deep-rebrand amendment 2026-09-19 §2 maps it to ``QUBIT_HOME``);
    every *other* ``HERMES_*`` env name is wire/compat and survives.  (Uppercase
    *code constants* such as ``HERMES_INDEX_TTL`` are intentionally renamed by
    the matrix, so this asserts the env-name literals, not an ``HERMES_`` prefix.)
    """
    m = _engine()
    env_names = ("HERMES_HOME", "HERMES_QWEN_TOKEN", "HERMES_AGENT_PORT",
                 "HERMES_SESSION_TOKEN")
    for name in env_names:
        assert name not in m.IDENTIFIER_TOKENS, name
    p = tmp_path / "env.py"
    p.write_text(
        'a = os.environ.get("HERMES_QWEN_TOKEN")\n'
        'b = os.environ.get("HERMES_AGENT_PORT")\n'
        'c = os.environ.get("HERMES_HOME")\n',
        encoding="utf-8",
    )
    m._rewrite_text(p, "env.py")
    out = p.read_text(encoding="utf-8")
    assert 'os.environ.get("HERMES_QWEN_TOKEN")' in out
    assert 'os.environ.get("HERMES_AGENT_PORT")' in out
    # HERMES_HOME keeps its separate, committed env-token contract
    assert 'os.environ.get("QUBIT_HOME")' in out


def test_legal_files_are_path_protected():
    m = _engine()
    assert m._protected("LICENSE")
    assert m._protected("THIRD_PARTY_NOTICES.md")
