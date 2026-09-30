# tests/test_rebrand_engine_tokens.py
"""Rebrand-engine token-map completeness + Step A identifier map.

Task 1 pinned the two missing *module* tokens (``hermes_state_health``,
``hermes_yaml``).  Step A of the corrected Task-6 redo adds an *identifier* map
for the home/const accessors the module token map cannot express: without it,
OURS = engine(v0.21.5) calls ``get_hermes_home`` while the Qubit tree defines
``get_qubit_home`` -- a dynamic-lookup break across ~64 files.

Coverage:
  (a) the map is present *and* behaviourally renames the listed identifiers;
  (b) the load-bearing wire/serialized strings survive;
  (c) a scratch rebrand of the *pinned* source contains none of the renamed
      identifiers (and still carries the never-rename literals).
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Read-only pinned upstream source (v0.21.5 @ 69948c0057).
PINNED_SOURCE = pathlib.Path(os.environ.get(
    "QUBIT_PINNED_SOURCE", "/tmp/qubit-1to1-20260925-082150/ref"))

#: Engine-owned trees the engine deliberately never rewrites (provenance).
PROTECTED_TREES = ("website", "contributors", "native/docs")


def _engine():
    p = REPO / "scripts" / "qubit_rebrand.py"
    s = importlib.util.spec_from_file_location("qb_rebrand", p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# Task 1 -- module token map
# ---------------------------------------------------------------------------
def test_missing_modules_are_mapped():
    mt = _engine().MODULE_TOKENS
    assert mt.get("hermes_state_health") == "qubit_state_health"
    assert mt.get("hermes_yaml") == "qubit_yaml"


def test_private_underscore_module_basename_is_renamed():
    """``_hermes_home.py`` must rename: the module-basename regex only matches
    ``^hermes_``, so a leading-underscore private helper needs the curated map.
    Otherwise the content pass rewrites its import to ``_qubit_home`` while the
    file stays ``_hermes_home.py`` (``No module named '_qubit_home'``)."""
    curated = _engine().CURATED_BASENAME_RENAMES
    assert curated.get("_hermes_home.py") == "_qubit_home.py"


# ---------------------------------------------------------------------------
# Step A -- home/const identifier map
# ---------------------------------------------------------------------------
#: The accessors/helpers the correction matrix requires renamed.  Kept explicit
#: (not derived from the engine) so a token-map regression fails loudly.
EXPECTED_IDENTIFIER_RENAMES = {
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
    "hermes_meta": "qubit_meta",
}

#: Literals that must survive verbatim (wire/serialized/compat).  The frozen
#: deep-rebrand amendment (2026-09-19 §2) maps ``HERMES_HOME`` -> ``QUBIT_HOME``
#: for the runtime home, so that one env token is asserted separately; every
#: *other* ``HERMES_*`` env name is wire and survives.
NEVER_RENAME = (
    '"hermes_home"',             # persisted DB/JSON key (quoted)
    "metadata.hermes",           # read-old frontmatter key
    "X-Qubit-Session-Token",
    "HermesAgent/",
    "window.__HERMES_PLUGINS__",
    "HERMES_QWEN_",
    "hermes_flow",
)


def test_identifier_map_covers_listed_accessors():
    it = _engine().IDENTIFIER_TOKENS
    missing = {o: n for o, n in EXPECTED_IDENTIFIER_RENAMES.items()
               if it.get(o) != n}
    assert not missing, f"IDENTIFIER_TOKENS missing/incorrect: {missing}"


def test_identifier_rewrite_renames_fixture(tmp_path):
    m = _engine()
    p = tmp_path / "sample.py"
    p.write_text(
        "from qubit_constants import get_hermes_home\n"
        "def f(hermes_home):\n"
        "    return (get_hermes_home(), get_process_hermes_home(),\n"
        "            get_default_hermes_root(), mkdir_under_hermes_home(),\n"
        "            _same_hermes_home(hermes_home), _hermes_metadata)\n"
    )
    m._rewrite_text(p, "sample.py")
    out = p.read_text()
    for old in EXPECTED_IDENTIFIER_RENAMES:
        assert not re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", out), old
    assert "get_qubit_home" in out
    assert "hermes_home" not in out
    assert "def f(qubit_home)" in out


def test_never_rename_literals_survive(tmp_path):
    m = _engine()
    p = tmp_path / "wire.py"
    p.write_text(
        "def f(hermes_home):\n"
        '    rec = {"hermes_home": str(hermes_home), "x": hermes_home}\n'
        '    fm = {"metadata": {"hermes": {"tags": []}}}\n'
        '    dot = "metadata.hermes"\n'
        '    hdr = "X-Qubit-Session-Token"\n'
        '    ua = "HermesAgent/1.0"\n'
        '    js = "window.__HERMES_PLUGINS__"\n'
        '    tok = os.environ.get("HERMES_QWEN_TOKEN")\n'
        '    wire = "hermes_flow"\n'
        '    return rec.get("hermes_home"), fm, hdr, ua, js, tok\n'
    )
    m._rewrite_text(p, "wire.py")
    out = p.read_text()
    for literal in NEVER_RENAME:
        assert literal in out, f"never-rename literal lost: {literal!r}"
    # persisted key stays; the local kwarg/variable renames
    assert '{"hermes_home": str(qubit_home)' in out
    assert 'def f(qubit_home)' in out


def test_home_env_token_follows_frozen_deep_rebrand_contract(tmp_path):
    """Frozen owner amendment 2026-09-19 §2: ``HERMES_HOME`` -> ``QUBIT_HOME``.

    The new identifier maps are not allowed to touch the env token; the
    pre-existing env-token pass owns it, and every *other* ``HERMES_*`` name is
    wire-compat and survives.
    """
    m = _engine()
    assert "HERMES_HOME" not in m.IDENTIFIER_TOKENS
    p = tmp_path / "home.py"
    p.write_text('h = os.environ.get("HERMES_HOME")\n'
                 's = os.environ.get("HERMES_QWEN_TOKEN")\n')
    m._rewrite_text(p, "home.py")
    out = p.read_text()
    assert 'os.environ.get("QUBIT_HOME")' in out
    assert 'os.environ.get("HERMES_QWEN_TOKEN")' in out


# ---------------------------------------------------------------------------
# Step A -- whole-tree residue on a scratch rebrand of the pinned source
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def rebranded_pin(tmp_path_factory):
    if not PINNED_SOURCE.is_dir():
        pytest.skip(f"pinned source unavailable: {PINNED_SOURCE}")
    root = tmp_path_factory.mktemp("stepA-pin")
    shutil.copytree(PINNED_SOURCE, root / "qubit_hermes")
    (root / "scripts").mkdir()
    shutil.copy2(REPO / "scripts" / "qubit_rebrand.py",
                 root / "scripts" / "qubit_rebrand.py")
    subprocess.run(
        [sys.executable, "scripts/qubit_rebrand.py", "--apply"],
        cwd=root, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    pkg = root / "qubit_runtime"
    assert (pkg / "qubit_state_health.py").is_file(), "rebrand did not run on the pin"
    return pkg


def _text_files(root: pathlib.Path):
    """Every file the engine would rewrite (its own TEXT_SUFFIXES), minus the
    provenance trees it protects."""
    suffixes = _engine().TEXT_SUFFIXES
    for p in root.rglob("*"):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        if p.suffix not in suffixes:
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel == t or rel.startswith(t + "/") for t in PROTECTED_TREES):
            continue
        yield p


def test_pinned_rebrand_has_no_renamed_identifiers(rebranded_pin):
    patterns = {
        old: re.compile(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])")
        for old in EXPECTED_IDENTIFIER_RENAMES
    }
    patterns["_hermes_ (private helper)"] = re.compile(r"(?<!_)_hermes_")
    patterns["hermes_home (bare symbol)"] = re.compile(
        r"""(?<![A-Za-z0-9_'"])hermes_home(?![A-Za-z0-9_'"])""")
    hits: list[str] = []
    for p in _text_files(rebranded_pin):
        text = p.read_text(encoding="utf-8", errors="ignore")
        for label, rx in patterns.items():
            if rx.search(text):
                hits.append(f"{p.relative_to(rebranded_pin)}: {label}")
    assert not hits, "renamed identifiers remain in OURS:\n" + "\n".join(hits[:40])


def test_pinned_rebrand_preserves_never_rename_wire_strings(rebranded_pin):
    blob = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                     for p in _text_files(rebranded_pin))
    missing = [lit for lit in NEVER_RENAME if lit not in blob]
    assert not missing, f"never-rename literals lost from OURS: {missing}"
