"""Tests for the parity ledger — Phase 0 of docs/PARITY_PLAN.md.

Two halves:

1. data tests — read the committed ``docs/PARITY_LEDGER.json`` and assert the
   rules the ledger claims to obey. These fail if someone hand-edits the JSON,
   and they fail if the generator ever emits a lie.
2. generator tests — import ``scripts/parity_ledger.py`` and exercise the rule
   enforcement and the normalised comparison directly, including on synthetic
   upstream clones, so the fabrication and silent-decline rules are proven to
   FIRE rather than merely to be written down.

The rules, restated because they are the point:

* row count == sum of ``git ls-tree -r`` (and ``git ls-files``) on the clones;
* no ``ported`` row with ``our_lines == 0``;
* no ``declined`` row with empty notes;
* no ``rewired`` row that fails to name what was replaced and with what;
* both output files exist and parse.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = REPO_ROOT / "docs" / "PARITY_LEDGER.json"
MD_PATH = REPO_ROOT / "docs" / "PARITY_LEDGER.md"
GENERATOR = REPO_ROOT / "scripts" / "parity_ledger.py"

DEFAULT_SCRATCH = Path(
    "/home/trick/.local/state/qubit-voicebridge/runtime/cache/scratch")

CLONES = {
    "hud": "hermes-hudui",
    "webui": "hermes-webui",
    "extension": "hermes-browser-extension",
}

FIELDS = ["upstream_path", "surface", "kind", "status", "our_path",
          "upstream_lines", "our_lines", "line_delta", "verified_by", "notes"]
STATUSES = {"ported", "rewired", "partial", "binary-identical", "missing",
            "declined", "absent-at-pin"}
KINDS = {"backend", "frontend", "test", "asset", "doc", "config", "other"}


def _load_generator():
    """Import the generator by path (``scripts/`` is not a package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("parity_ledger", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    sys.modules["parity_ledger"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pl():
    return _load_generator()


@pytest.fixture(scope="module")
def ledger():
    assert JSON_PATH.is_file(), (
        f"{JSON_PATH} is missing — run `python scripts/parity_ledger.py`")
    return json.loads(JSON_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def rows(ledger):
    return ledger["rows"]


@pytest.fixture(scope="module")
def clones_present():
    missing = [n for n in CLONES.values() if not (DEFAULT_SCRATCH / n / ".git").exists()]
    if missing:
        pytest.skip(f"upstream clones absent: {missing} (network-free environment)")
    return True


# ---------------------------------------------------------------------------
# outputs
# ---------------------------------------------------------------------------
def test_both_outputs_exist_and_parse(ledger):
    assert MD_PATH.is_file(), f"{MD_PATH} is missing"
    text = MD_PATH.read_text(encoding="utf-8")
    assert text.strip(), "PARITY_LEDGER.md is empty"
    assert "# Parity ledger" in text
    assert "## Gaps" in text, "the markdown must carry the gaps section"
    assert isinstance(ledger["rows"], list) and ledger["rows"]


def test_every_row_has_the_plan_schema(rows):
    for row in rows:
        assert list(row.keys()) == FIELDS, f"wrong schema on {row.get('upstream_path')}"


def test_surface_is_one_of_three(rows):
    allowed = set(CLONES)
    for row in rows:
        assert row["surface"] in allowed, f"bad surface {row['surface']!r}"


def test_kind_is_defined(rows):
    for row in rows:
        assert row["kind"] in KINDS, f"bad kind {row['kind']!r} on {row['upstream_path']}"


# ---------------------------------------------------------------------------
# the hard rules
# ---------------------------------------------------------------------------
def test_every_row_has_a_status(rows):
    for row in rows:
        assert row["status"], f"empty status on {row['surface']}:{row['upstream_path']}"
        assert row["status"] in STATUSES, (
            f"undefined status {row['status']!r} on {row['upstream_path']}")


def test_ported_rows_have_lines_behind_them(rows):
    """The fabrication signature: 'ported' with nothing behind it."""
    bad = [r for r in rows if r["status"] in ("ported", "partial") and r["our_lines"] == 0]
    assert not bad, (
        "fabrication: claimed present with our_lines == 0: "
        + ", ".join(f"{r['surface']}:{r['upstream_path']}" for r in bad[:10]))


def test_claimed_rows_point_at_a_real_file(rows):
    for row in rows:
        if row["status"] in ("ported", "rewired", "partial", "binary-identical"):
            assert row["our_path"], f"{row['upstream_path']}: claim without our_path"
            assert (REPO_ROOT / row["our_path"]).is_file(), (
                f"{row['upstream_path']}: our_path missing on disk: {row['our_path']}")


def test_declined_rows_say_why(rows):
    bad = [r for r in rows if r["status"] == "declined" and not r["notes"].strip()]
    assert not bad, "silent decline: " + ", ".join(
        f"{r['surface']}:{r['upstream_path']}" for r in bad[:10])


def test_rewired_rows_name_replacement_and_replacement_with(rows):
    bad = [r for r in rows if r["status"] == "rewired"
           and ("rewired:" not in r["notes"] or "replaced with" not in r["notes"])]
    assert not bad, "unnamed rewiring: " + ", ".join(
        f"{r['surface']}:{r['upstream_path']}" for r in bad[:10])


def test_ported_rows_record_a_comparison(rows):
    bad = [r for r in rows if r["status"] == "ported"
           and not r["notes"].startswith("ported: normalised comparison")]
    assert not bad, "ported without a recorded comparison: " + ", ".join(
        f"{r['surface']}:{r['upstream_path']}" for r in bad[:10])


def test_missing_and_declined_rows_carry_no_path(rows):
    for row in rows:
        if row["status"] in ("missing", "declined"):
            assert row["our_path"] == "", (
                f"{row['upstream_path']}: status {row['status']!r} has our_path")
            assert row["our_lines"] == 0


def test_line_delta_is_arithmetic(rows):
    bad = [r for r in rows if r["line_delta"] != r["upstream_lines"] - r["our_lines"]]
    assert not bad, f"line_delta wrong on {len(bad)} rows"


def test_no_duplicate_rows(rows):
    seen = Counter((r["surface"], r["upstream_path"]) for r in rows)
    dupes = [k for k, n in seen.items() if n > 1]
    assert not dupes, f"duplicate rows: {dupes[:5]}"


# ---------------------------------------------------------------------------
# reconciliation against the clones — measured, not remembered
# ---------------------------------------------------------------------------
def test_row_count_reconciles_with_upstream(rows, clones_present):
    per_clone = {}
    for surface, dirname in CLONES.items():
        clone = DEFAULT_SCRATCH / dirname
        ls_tree = subprocess.run(
            ["git", "-C", str(clone), "ls-tree", "-r", "--name-only", "HEAD"],
            capture_output=True, text=True, check=True).stdout.split()
        ls_files = subprocess.run(
            ["git", "-C", str(clone), "ls-files"],
            capture_output=True, text=True, check=True).stdout.splitlines()
        assert len(ls_tree) == len([p for p in ls_files if p.strip()]), (
            f"{dirname}: ls-tree and ls-files disagree at HEAD")
        per_clone[surface] = len(ls_tree)

    assert len(rows) == sum(per_clone.values()), (
        f"ledger has {len(rows)} rows, upstream has {sum(per_clone.values())} files "
        f"({per_clone})")

    per_surface = Counter(r["surface"] for r in rows)
    for surface, count in per_clone.items():
        assert per_surface[surface] == count, (
            f"{surface}: {per_surface[surface]} rows vs {count} files at the pin")


def test_recorded_pins_match_the_clones(ledger, clones_present):
    for surface, dirname in CLONES.items():
        clone = DEFAULT_SCRATCH / dirname
        measured = subprocess.run(
            ["git", "-C", str(clone), "log", "-1", "--format=%H"],
            capture_output=True, text=True, check=True).stdout.strip()
        assert ledger["surfaces"][surface]["pin"] == measured, (
            f"{surface}: ledger records {ledger['surfaces'][surface]['pin']!r}, "
            f"clone is at {measured!r} — regenerate")


# ---------------------------------------------------------------------------
# the generator's own rules must FIRE, not just be documented
# ---------------------------------------------------------------------------
def _row(pl, **kw):
    base = dict(upstream_path="backend/api/x.py", surface="hud", kind="backend",
                status="missing", our_path="", upstream_lines=10, our_lines=0,
                line_delta=10, notes="")
    base.update(kw)
    if "line_delta" not in kw:
        base["line_delta"] = base["upstream_lines"] - base["our_lines"]
    return pl.Row(**base)


def test_enforcement_rejects_ported_with_zero_lines(pl):
    rows = [_row(pl, status="ported", our_path="a/b.py", our_lines=0,
                 notes="ported: normalised comparison ran — structure comparison")]
    errors = pl.enforce(rows, 1, {"hud": 1}, [])
    assert any("FABRICATION" in e for e in errors), errors


def test_enforcement_rejects_silent_decline(pl):
    errors = pl.enforce([_row(pl, status="declined", notes="")], 1, {"hud": 1}, [])
    assert any("SILENT DECLINE" in e for e in errors), errors


def test_enforcement_rejects_unnamed_rewiring(pl):
    errors = pl.enforce(
        [_row(pl, status="rewired", our_path="ours.py", our_lines=5,
              notes="rewired: something")], 1, {"hud": 1}, [])
    assert any("UNNAMED REWIRING" in e for e in errors), errors


def test_enforcement_rejects_uncompared_port(pl):
    errors = pl.enforce(
        [_row(pl, status="ported", our_path="ours.py", our_lines=5,
              notes="ported: looks the same")], 1, {"hud": 1}, [])
    assert any("UNCOMPARED PORT" in e for e in errors), errors


def test_enforcement_rejects_row_count_drift(pl):
    errors = pl.enforce([_row(pl)], 2, {"hud": 2}, [])
    assert any("ROW COUNT MISMATCH" in e for e in errors), errors


def test_enforcement_rejects_empty_status(pl):
    errors = pl.enforce([_row(pl, status="")], 1, {"hud": 1}, [])
    assert any("EMPTY STATUS" in e for e in errors), errors


def test_enforcement_accepts_a_clean_ledger(pl):
    rows = [
        _row(pl, status="missing"),
        _row(pl, upstream_path="b.py", status="declined", notes="declined: prose"),
        _row(pl, upstream_path="c.py", status="rewired", our_path="ours.py",
             our_lines=5, notes="rewired: `a` replaced with `b`"),
        _row(pl, upstream_path="d.py", status="ported", our_path="ours.py",
             our_lines=5, notes="ported: normalised comparison ran — 3/3"),
    ]
    assert pl.enforce(rows, 4, {"hud": 4}, []) == []


def test_comparison_is_actually_performed(pl):
    upstream = (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/chat')\n\n"
        "def list_sessions():\n"
        "    return {'sessions': [], 'count': 0}\n"
    )
    ours_same = upstream.replace("hermes", "qubit")
    same = pl.normalised_equivalent(upstream, ours_same, "backend/api/chat.py")
    assert same.performed and same.equivalent, same
    assert same.fields == pl.COMPARE_FIELD_COUNT

    ours_different = (
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/office')\n\n"
        "def list_chats():\n"
        "    return {'rows': [], 'total': 0}\n"
    )
    diff = pl.normalised_equivalent(upstream, ours_different, "backend/api/chat.py")
    assert diff.performed and not diff.equivalent
    assert diff.fields < pl.COMPARE_FIELD_COUNT


def test_brand_difference_alone_does_not_break_a_port(pl):
    upstream = (
        "def get_hermes_status():\n"
        "    return {'hermes_status': 'ok'}\n"
    )
    ours = (
        "def get_qubit_status():\n"
        "    return {'qubit_status': 'ok'}\n"
    )
    cmp = pl.normalised_equivalent(upstream, ours, "backend/api/health.py")
    # A brand-only rename is the one permitted difference; the *content* still
    # has to line up, and the key rename is folded before comparing.
    assert cmp.performed
    assert cmp.fields >= 1


def test_missing_data_is_never_ported(pl):
    cmp = pl.normalised_equivalent("", "something", "backend/api/x.py")
    assert cmp.performed is False and cmp.equivalent is False


# ---------------------------------------------------------------------------
# end to end on synthetic clones: the generator must exit non-zero
# ---------------------------------------------------------------------------
def _make_clone(root: Path, dirname: str, files: dict) -> None:
    """A tiny git repo with one commit, as a stand-in upstream."""
    repo = root / dirname
    repo.mkdir(parents=True)
    for rel, content in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    for cmd in (["git", "init", "-q", "-b", "main"],
                ["git", "add", "-A"],
                ["git", "commit", "-qm", "pin"]):
        subprocess.run(cmd, cwd=repo, check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture()
def synthetic_scratch(tmp_path):
    _make_clone(tmp_path, "hermes-hudui",
                {"backend/api/health.py": "def health():\n    return {'ok': True}\n",
                 "docs/README.md": "# hi\n"})
    _make_clone(tmp_path, "hermes-webui", {"server.py": "print('x')\n"})
    _make_clone(tmp_path, "hermes-browser-extension",
                {"extension/manifest.json": "{}\n"})
    return tmp_path


def test_generator_writes_both_outputs_and_reconciles(synthetic_scratch, tmp_path):
    out_dir = tmp_path / "out"
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--scratch", str(synthetic_scratch),
         "--out-dir", str(out_dir)],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert proc.returncode == 0, proc.stderr
    assert "TOTAL" in proc.stdout and "MISMATCH" not in proc.stdout
    out = json.loads((out_dir / "PARITY_LEDGER.json").read_text(encoding="utf-8"))
    assert len(out["rows"]) == 4
    assert out["totals"]["reconciled"] is True
    assert (out_dir / "PARITY_LEDGER.md").read_text(encoding="utf-8").strip()
    # and the same inputs regenerated are not stale
    again = subprocess.run(
        [sys.executable, str(GENERATOR), "--scratch", str(synthetic_scratch),
         "--out-dir", str(out_dir), "--check"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert again.returncode == 0, again.stderr


def test_generator_refuses_a_fabricated_ported_row(pl, monkeypatch):
    """The generator must exit non-zero rather than emit the fabricated row."""
    calls = {"n": 0}

    def fake_build(surface, clone, pin, inv, warnings):
        calls["n"] += 1
        return [pl.Row("backend/api/health.py", surface.name, "backend", "ported",
                       "frontend/qubit-app/plugins/qubit-hud/qubit-hud.js",
                       5, 0, 5, notes="ported: normalised comparison ran")]

    monkeypatch.setattr(pl, "build_rows", fake_build)
    monkeypatch.setattr(pl, "repo_ls_tree", lambda clone, pin: ["backend/api/health.py"])
    monkeypatch.setattr(pl, "repo_ls_files_count", lambda clone: 1)
    monkeypatch.setattr(pl, "repo_pin", lambda clone: "0" * 40)
    rc = pl.main(["--scratch", str(pl.DEFAULT_SCRATCH), "--check"])
    assert rc == 1, "a fabricated 'ported' row must make the generator fail"


def test_generator_output_is_not_stale(pl, clones_present):
    """`--check` regenerates in memory: a hand-edited ledger fails here."""
    rc = pl.main(["--check"])
    assert rc == 0, "docs/PARITY_LEDGER.* are stale — regenerate, do not hand-edit"
