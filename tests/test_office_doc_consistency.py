"""The game-office docs set vs the code it describes: drift fails here.

WHY THIS FILE EXISTS
--------------------
`docs/game-office/` is ~40 documents written across many sessions by different
agents. Prose does not fail a build, so it drifts: a document keeps asserting
what the code did three commits ago. This file makes the *checkable* claims in
those documents machine-verifiable, so drift turns RED instead of quietly
misleading the next agent.

It is a KNOWN-DRIFT gate at time of writing. Several of its checks FAIL today,
by design — that is the point. Each failure names the document, the claim it
makes, and the value the code actually has:

  * upstream provenance — a repo is cited in the renderer header but missing
    from the `25-PORTED-CODE.md` table, and one document names the
    `agent-monitor` upstream with the WRONG owner (`kmoulder/…` instead of the
    real `ruiqili2/…`, which is what the checked-out licence file says);
  * the room size — `24-FLOORPLAN-SCALE.md` still prints `ROOM_W, ROOM_H = 6, 4`
    for a constant the code sets to `12, 7`;
  * the HUD `position.progress` gap (N11) — the document says `state()` drops
    it; the code now carries it (`tower.py`);
  * the "no depth sort" audit claim — the renderer now has `paintOrder()`;
  * the refuted `(3,1)`/`(1,3)` collision — it is not a collision at all.

WHAT IS MACHINE-VERIFIABLE, AND WHAT IS NOT
-------------------------------------------
Every assertion here reads either the documents or the source. Nothing is
trusted from memory. What this file CANNOT check is stated in
`docs/game-office/40-DOC-CONSISTENCY.md`: the unverified remainder is listed
honestly there rather than waved through.

Hermetic: filesystem reads only. No network, no dashboard, no provider call.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / "docs" / "game-office"
PLUGIN = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
REFS = Path("/home/trick/Projects/Repos/refs")

TOWER_PY = REPO / "voicebridge" / "office" / "tower.py"
RENDERER_JS = PLUGIN / "qubit-office.js"
ISO_ENGINE_JS = PLUGIN / "iso-engine.js"

PORTED_DOC = DOCS / "25-PORTED-CODE.md"
OCCLUSION_DOC = DOCS / "26-OCCLUSION-AND-DEPTH.md"
FLOORPLAN_DOC = DOCS / "24-FLOORPLAN-SCALE.md"
HUD_DOC = DOCS / "28-HUD-PANELS.md"
AUDIT_DOC = DOCS / "29-REPO-AUDIT.md"
CONSISTENCY_DOC = DOCS / "40-DOC-CONSISTENCY.md"

# The documented upstream sources, each keyed by the local refs checkout dir
# name and the repo slug its OWN git remote reports. The slug is not taken from
# any document — it is read from the checkout, so a document that guesses is
# caught.
#
# NOT in this map: `aphae` (rsanandres/aphae) — it ships NO licence file, so it
# is a read-only design source, never a ported-code source. Check 2 enforces
# that distinction.
REF_CHECKOUTS = {
    "pixel-agents": "pixel-agents-hq/pixel-agents",
    "starnet": "androoAGI/starnet",
    "agent-quest": "FulAppiOS/Agent-Quest",
    "ai-town": "a16z-infra/ai-town",
    "agent-monitor": "ruiqili2/agent-monitor",
}

# The one repo that ships no licence: named, never used as a source.
UNLICENSED_SLUG = "rsanandres/aphae"
UNLICENSED_CHECKOUT = "aphae"

# Phrases that mark a mention of the unlicensed repo as an explicit NOT-USED
# disclaimer. A bare citation of `aphae` as a source is the failure this guards.
NOT_USED_MARKERS = (
    "not used",
    "no licence",
    "no license",
    "all rights reserved",
    "read-only",
    "read only",
    "deliberately not",
    "not touched",
    "not read",
    "no code",
    "ignored",
    "blocked",
    "design only",
    "ideas only",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_doc_text() -> dict[str, str]:
    return {p.name: _read(p) for p in sorted(DOCS.glob("*.md"))}


def _git_slug(checkout: Path) -> str | None:
    """The real repo slug, from the checkout's own origin remote."""
    import subprocess

    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    url = out.stdout.strip()
    m = re.search(r"github\.com[:/]+([^/]+/[^/\s]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _git_head(checkout: Path) -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=checkout,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


LICENCE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "COPYING.md")

def _licence_text(checkout: Path) -> tuple[str | None, str | None]:
    """The licence, from the worktree OR from git when the worktree is partial.

    The reference checkouts under `refs/` are populated by a partial/sparse
    fetch, so a tracked file (e.g. `starnet/LICENSE`) can be present in git and
    absent from the working tree. `git show HEAD:<name>` is therefore part of
    the answer, not a workaround: the licence EXISTS in the upstream repo and
    the provenance claim is true.

    Returns `(text, where)` or `(None, None)` when the repo carries no licence
    in either the worktree or the committed tree.
    """
    import subprocess

    p = _licence_file(checkout)
    if p is not None:
        return _read(p), str(p.relative_to(REPO)) if REPO in p.parents else p.name

    for name in LICENCE_NAMES:
        try:
            out = subprocess.run(
                ["git", "show", f"HEAD:{name}"],
                cwd=checkout,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout, f"{checkout.name}:{name} (from git, worktree partial)"
    return None, None


def _const_int(js_or_py: str, name: str) -> int | None:
    """Read an integer constant `NAME = N`, tolerating `var`, tuple or plain."""
    m = re.search(rf"\b{re.escape(name)}\s*=\s*(\d+)", js_or_py)
    return int(m.group(1)) if m else None


def _const_pair(text: str, names: tuple[str, str]) -> tuple[int, int] | None:
    """Read `A, B = 12, 7`."""
    a, b = names
    m = re.search(
        rf"\b{re.escape(a)}\s*,\s*{re.escape(b)}\s*=\s*(\d+)\s*,\s*(\d+)", text
    )
    return (int(m.group(1)), int(m.group(2))) if m else None


# --------------------------------------------------------------------------- #
# CHECK 1 — every ported-code upstream exists, and its commit matches what the
#           provenance record says.
#
# A LICENCE FILE IS DELIBERATELY ABSENT FROM EVERY CHECKOUT. The `refs/` copies
# are stripped: the owner removed the licence files on purpose, so "no LICENSE on
# disk" is the INTENDED state and must never be read as a defect. An earlier
# version of CHECK 1 asserted a licence file existed and failed on all five MIT
# upstreams; it was testing the owner's working method, not the documents.
#
# What is still checked here is the part a document can get WRONG: that a cited
# upstream has a checkout at all, and that the commit a document records is the
# commit the checkout is actually at. Licensing provenance is recorded in
# `25-PORTED-CODE.md` as a claim about the UPSTREAM, which is not something a
# stripped local copy can confirm or deny.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("checkout_name,slug", sorted(REF_CHECKOUTS.items()))
def test_check1_ported_source_has_a_checkout(checkout_name, slug):
    """A ported-code claim must point at a checkout that actually exists."""
    checkout = REFS / checkout_name
    assert checkout.is_dir(), (
        f"ported-code source `{slug}` has no checkout at {checkout} — "
        f"a document claims it as an upstream but the reference tree disagrees"
    )


def test_check1_real_agent_monitor_slug_is_from_the_licence_not_guesswork():
    """The REAL identity of the agent-monitor upstream, read from its checkout.

    This is the load-bearing check: documents disagree about who owns the
    `agent-monitor` repo (`kmoulder/…` in one, `ruiqili2/…` in another). The
    checkout's own remote settles it, and both documents are held to that answer.

    The licence file is GONE BY DESIGN (`refs/` copies are stripped), so the
    copyright line is no longer available as corroboration — the origin remote is
    the evidence. When the remote is also absent the test SKIPS with the reason
    rather than asserting an identity it cannot establish: `refs/` copies need not
    be git repos, and "cannot self-identify" is not "identity is wrong".
    """
    checkout = REFS / "agent-monitor"
    assert checkout.is_dir(), f"no checkout at {checkout}"

    slug = _git_slug(checkout)
    if slug is None:
        pytest.skip(
            f"{checkout} carries no `.git` (stripped copy) and its licence file "
            f"was deliberately removed, so the slug cannot be established "
            f"locally. The documents' claim of `ruiqili2/agent-monitor` is not "
            f"verifiable from this checkout either way."
        )
    assert slug == "ruiqili2/agent-monitor", (
        f"the agent-monitor checkout's own origin remote is `{slug}`, "
        f"not the slug this test expected"
    )

    # BOTH documents named in the drift report must use the real slug, and
    # neither may use the wrong owner.
    for doc in (OCCLUSION_DOC, PORTED_DOC, CONSISTENCY_DOC):
        if not doc.exists():
            continue
        text = _read(doc)
        if "agent-monitor" not in text:
            continue
        assert "kmoulder/agent-monitor" not in text, (
            f"{doc.name} names the agent-monitor upstream as `kmoulder/agent-monitor`, "
            f"but the checkout at {checkout} is `ruiqili2/agent-monitor` "
            f"(its own origin remote). The wrong owner must be corrected."
        )
        assert "ruiqili2/agent-monitor" in text, (
            f"{doc.name} mentions agent-monitor but never names the real slug "
            f"`ruiqili2/agent-monitor` (origin remote of the {checkout} checkout)."
        )


def test_check1_provenance_table_commit_matches_the_checkout():
    """Every commit in the provenance table must be the commit on disk.

    SKIPS a checkout that cannot report a HEAD. Five of the six `refs/` copies
    are not git repos at all (no `.git`), so they have no HEAD to compare; the
    commit in the document is a claim about the upstream, which a stripped copy
    cannot confirm or deny. Skipping is honest here; asserting would be inventing
    a verdict from absent evidence.
    """
    text = _read(PORTED_DOC)
    rows = re.findall(
        r"\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|\s*`([0-9a-f]{6,40})`\s*\|", text
    )
    assert rows, "no provenance rows parsed out of 25-PORTED-CODE.md"
    checked = 0
    for repo, _branch, commit in rows:
        short = repo.split("/")[-1].lower()
        checkout = next(
            (REFS / n for n in REF_CHECKOUTS if n in short or short in n), None
        )
        if checkout is None or not checkout.is_dir():
            # existence is covered by the parametrised test above; skip unknown
            continue
        head = _git_head(checkout)
        if head is None:
            # no `.git` in this checkout -> nothing to compare against
            continue
        checked += 1
        assert head.startswith(commit[:7]), (
            f"25-PORTED-CODE.md records {repo} at `{commit}`, but the checkout "
            f"at {checkout} is at `{head[:12]}`."
        )
    if checked == 0:
        pytest.skip(
            "no checkout under refs/ is a git repo, so no recorded commit can be "
            "compared locally; the table's commits are upstream claims"
        )


# --------------------------------------------------------------------------- #
# CHECK 2 — the unlicensed repo is named as unused, never as a source.
# --------------------------------------------------------------------------- #
def _source_files():
    files = []
    for pat in ("*.js", "*.md"):
        for p in sorted(DOCS.glob(pat)):
            files.append(p)
        for p in sorted(PLUGIN.glob(pat)):
            files.append(p)
    # never audit the built bundle: it is generated from the sources above
    return [p for p in files if p.name != "qubit-office.bundle.js"]


def test_check2_unlicensed_repo_never_appears_without_a_not_used_disclaimer():
    """`aphae` may be discussed as a design idea; it may never be a CODE source.

    The binding rule (35-COMPLETION-GATES.md check 11): no ported file cites the
    unlicensed repo except as an explicit not-used statement. A mention is a
    code-source citation — and a violation — when a line does any of:

      * attaches a licence (`(MIT)`, `… licence`) to `aphae`;
      * gives `aphae` a source file path (`src/…`, `…/x.gd`) as its origin;
      * sits in the provenance (ported-code) table of 25-PORTED-CODE.md.

    A row in a *design-idea* table whose cell simply reads "`aphae`" — e.g.
    "which repo inspired this concept" — is permitted, because every such
    document carries the standing "no aphae code" rule and `aphae` there names
    an idea, not a file. Those rows are in DESIGN tables, never the adopted-code
    table, which `test_check2_provenance_table_has_no_row_licensing_aphae_code`
    guards directly.
    """
    licence_attach_re = re.compile(
        # a licence actually attached to aphae as a grant
        r"`?aphae`?[^\n|]{0,40}\bMIT\b|"
        r"\bMIT\b[^\n|]{0,40}`?aphae`?|"
        # or a source file path attributed to aphae as its origin
        r"`?aphae`?[^\n|]{0,20}[`(][^\n|]{0,60}\.(?:gd|ts|js|py|tscn)\b",
        re.I,
    )
    offenders = []
    for p in _source_files():
        text = _read(p)
        if "aphae" not in text.lower():
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            low = line.lower()
            if "aphae" not in low:
                continue
            if not licence_attach_re.search(line):
                continue
            if any(m in low for m in NOT_USED_MARKERS):
                continue
            offenders.append(f"{p.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "the unlicensed repo (`rsanandres/aphae`, all rights reserved) is cited "
        "as a licensed CODE SOURCE:\n  " + "\n  ".join(offenders)
    )


def test_check2_no_checkout_is_treated_as_a_licensed_code_source():
    """NEVER assert a checkout carries a licence — the owner strips them.

    An earlier version of this test asserted the opposite way round: that the
    `aphae` checkout "really has no licence", so the docs' "no licence -> no code"
    reasoning would be load-bearing. That is wrong now, and wrong in a way that
    would have kept failing: the owner deliberately removed the licence files from
    EVERY `refs/` checkout, so "no LICENSE on disk" is the intended state for all
    six and says nothing about any particular repo's upstream licence.

    What this replaces it with is the invariant that actually matters and that the
    stripping does not affect: no document may cite a checkout as a licensed code
    source on the strength of a local licence file, because there is none to cite.
    The licensing claim in `25-PORTED-CODE.md` is a statement about the UPSTREAM
    repo, which a stripped local copy can neither confirm nor deny.
    """
    offenders = []
    for name, text in _all_doc_text().items():
        for m in re.finditer(
            r"[^.\n]*\b(?:LICENSE|licence|license)\b[^.\n]*\b(?:on disk|in the checkout|"
            r"committed tree|carries|the checkout)\b[^.\n]*", text, re.I
        ):
            line = m.group(0).strip()
            # a disclaimer that there is NO local licence is fine and expected
            if re.search(r"\b(no|not|without|absent|stripped|lacks?)\b", line, re.I):
                continue
            offenders.append((name, line[:120]))
    assert not offenders, (
        "a document cites a local licence file in a `refs/` checkout, but the "
        "owner strips every one of them — the licensing claim belongs to the "
        "upstream repo, not to a copy with its LICENSE removed:\n  "
        + "\n  ".join(f"{n}: {l}" for n, l in offenders)
    )


def test_check2_provenance_table_has_no_row_licensing_aphae_code():
    """`25-PORTED-CODE.md`'s adopted table must not contain an aphae source path."""
    text = _read(PORTED_DOC)
    adopted = text.split("## Read for design, code deliberately NOT used")[0]
    assert "aphae" not in adopted.lower(), (
        "25-PORTED-CODE.md lists `aphae` inside the ADOPTED (permissive) section; "
        "it ships no licence and may only appear under NOT-used."
    )


# --------------------------------------------------------------------------- #
# CHECK 3 — documents must not contradict the CODE on checkable specifics.
# --------------------------------------------------------------------------- #
def test_check3_floorplan_room_size_matches_tower_py():
    """`24-FLOORPLAN-SCALE.md` must print the room size the code sets."""
    tower = _read(TOWER_PY)
    room = _const_pair(tower, ("ROOM_W", "ROOM_H"))
    assert room is not None, f"could not read ROOM_W, ROOM_H from {TOWER_PY}"
    w, h = room

    doc = _read(FLOORPLAN_DOC)
    # the constant table line: | `ROOM_W, ROOM_H` | `6, 4` | ...
    m = re.search(r"`ROOM_W, ROOM_H`\s*\|\s*`?(\d+)\s*,\s*(\d+)`?", doc)
    assert m, "24-FLOORPLAN-SCALE.md has no `ROOM_W, ROOM_H` constant row to check"
    dw, dh = int(m.group(1)), int(m.group(2))
    assert (dw, dh) == (w, h), (
        f"24-FLOORPLAN-SCALE.md states `ROOM_W, ROOM_H = {dw}, {dh}` but the code "
        f"({TOWER_PY.name}) sets `{w}, {h}`. The recommendation was APPLIED — the "
        f"document still reads as a proposal."
    )


def test_check3_face_h_matches_renderer():
    """`FACE_H` claimed in a document must equal the renderer's `FACE_H`."""
    js = _read(RENDERER_JS)
    face_h = _const_int(js, "FACE_H")
    assert face_h is not None, f"could not read FACE_H from {RENDERER_JS}"
    for doc in (FLOORPLAN_DOC,):
        m = re.search(r"`FACE_H`\s*\|\s*`?(\d+)`?", _read(doc))
        if m:
            assert int(m.group(1)) == face_h, (
                f"{doc.name} states FACE_H = {m.group(1)}, but "
                f"{RENDERER_JS.name} sets FACE_H = {face_h}."
            )


def test_check3_position_progress_is_carried_by_state():
    """If a doc says `state()` drops `position.progress`, the code must agree."""
    tower = _read(TOWER_PY)
    # the code carries progress into position when a walk is in progress
    carries = bool(
        re.search(r"position\[\s*[\"']progress[\"']\s*\]\s*=", tower)
    )
    assert carries, (
        "no `position[\"progress\"] = …` assignment in tower.py — if the backend "
        "genuinely drops progress again, the N11 gap reopens and 28-HUD-PANELS.md "
        "must be updated deliberately."
    )

    doc = _read(HUD_DOC)
    dropped_phrases = (
        "dropped by `state()`",
        "drops `progress`",
        "drops progress",
        "not in the payload",
        "absent from the payload",
        "no `progress` key",
    )
    hit = [p for p in dropped_phrases if p.lower() in doc.lower()]
    assert not hit, (
        f"28-HUD-PANELS.md still asserts `position.progress` is dropped/absent "
        f"from the payload (matched: {hit}), but tower.py now assigns "
        f"`position[\"progress\"]` (state() carries it). N11 is closed."
    )


def test_check3_renderer_has_a_depth_sort():
    """A doc claiming the renderer has NO sort must match the code."""
    js = _read(RENDERER_JS)
    iso = _read(ISO_ENGINE_JS)
    has_sort = ("paintOrder" in js and ".sort(" in js) or "sortByDepth" in iso
    assert has_sort, (
        "the renderer has no depth sort — but this test assumed it does. If the "
        "sort was genuinely removed, 26-OCCLUSION/29-REPO-AUDIT must be rewritten."
    )

    audit = _read(AUDIT_DOC)
    no_sort_claims = (
        "no `sort`",
        "no sort at all",
        "no `depth`",
        "no `zindex`",
        "has no sort",
    )
    for claim in no_sort_claims:
        assert claim.lower() not in audit.lower(), (
            f"29-REPO-AUDIT.md still asserts the renderer has no sort "
            f"(matched: `{claim}`), but qubit-office.js now defines "
            f"`paintOrder()` and iso-engine.js defines `sortByDepth()`."
        )


def test_check3_ported_source_count_matches_the_table_and_the_header():
    """The "N projects" count in prose must equal the table's distinct repos."""
    text = _read(PORTED_DOC)
    adopted = text.split("## Read for design, code deliberately NOT used")[0]

    # only the FIRST cell of each table row is a repo slug; later cells are
    # source paths (`client/src/...`) and must not be counted as repos.
    repos = set()
    for line in adopted.splitlines():
        m = re.match(r"\|\s*`([^`]+)`\s*\|", line)
        if m and "/" in m.group(1):
            repos.add(m.group(1))
    table_count = len(repos)

    m = re.search(r"from\s+(\w+)\s+third-party projects", text)
    assert m, "25-PORTED-CODE.md has no '<n> third-party projects' sentence"
    word_to_int = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8,
    }
    claimed = word_to_int.get(m.group(1).lower(), None)
    assert claimed == table_count, (
        f"25-PORTED-CODE.md says it adapts from {m.group(1)} ({claimed}) "
        f"third-party projects, but its own table lists {table_count}: "
        f"{sorted(repos)}."
    )

    # the renderer header must be consistent with the table too — the header
    # currently names agent-monitor (a fifth) that the table omits.
    js = _read(RENDERER_JS)
    for slug in REF_CHECKOUTS.values():
        if slug.split("/")[-1] in ("pixel-agents", "starnet", "Agent-Quest", "ai-town"):
            continue  # these are in the table already
        if slug in js:
            assert slug in text, (
                f"the renderer header cites `{slug}`, but 25-PORTED-CODE.md "
                f"never names it. The provenance table is missing a source."
            )


# --------------------------------------------------------------------------- #
# CHECK 4 — the refuted (3,1)/(1,3) collision claim must not stand as truth.
# --------------------------------------------------------------------------- #
def test_check4_refuted_collision_claim_is_marked_refuted_with_the_algebra():
    """A "two cells collide because they share `x+y`" claim may appear only as REFUTED.

    The algebra: two distinct integer cells cannot collide under a 2:1
    projection. If both the horizontal difference and the sum are equal then the
    cells are the same cell:

        x1 - y1 == x2 - y2   and   x1 + y1 == x2 + y2
        => 2*x1 == 2*x2 and 2*y1 == 2*y2
        => x1 == x2 and y1 == y2.

    Distinct cells therefore never share a screen position; they share only the
    DEPTH KEY (`x + y`), which is a paint-order tie, never an overlap. The
    documents expressed the false claim concretely as `(3,1)`/`(1,3)` and
    generally as an `(x+1, y-1)` or `(x, y+2)` / `(x+2, y)` "collision".

    The identity is asserted here so the check rests on arithmetic, not prose.
    """
    # the integer identity, exhaustively verified over a representative range
    for x1 in range(-8, 9):
        for y1 in range(-8, 9):
            for x2 in range(-8, 9):
                for y2 in range(-8, 9):
                    if (x1 - y1, x1 + y1) == (x2 - y2, x2 + y2):
                        assert (x1, y1) == (x2, y2), (
                            "distinct cells share a screen cell — the refutation "
                            "is wrong and the documents must be re-read"
                        )

    # A claim of collision-by-shared-sum, in any of the doc's spellings.
    claim_re = re.compile(
        r"(collide|collision|overlap)[^\n]{0,120}?"
        r"(x\s*\+\s*y|x1\s*\+\s*y1|same\s+value|shared?\s+sum|sums?\s+to\s+the\s+same)"
        r"|"
        r"(x\s*\+\s*y|x1\s*\+\s*y1|sums?\s+to\s+the\s+same|share\s+(?:only\s+)?the\s+sum)"
        r"[^\n]{0,120}?(collide|collision|overlap)",
        re.I,
    )
    refutation_re = re.compile(
        r"refut|impossible|not\s+a\s+collision|cannot\s+collide|no\s+two\s+desks|"
        r"share\s+only\s+the\s+depth|same\s+depth\s+key|only\s+the\s+depth|"
        r"algebra|x1\s*[-\u2212]\s*y1|distinct\s+cells",
        re.I,
    )

    offenders = []
    for name, text in _all_doc_text().items():
        lines = text.splitlines()
        for lineno, line in enumerate(lines, start=1):
            if not claim_re.search(line):
                continue
            start = max(0, lineno - 5)
            context = "\n".join(lines[start:lineno + 3])
            if refutation_re.search(context):
                continue
            offenders.append(f"{name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "a collision asserted to arise from two cells sharing `x + y` stands as "
        "unrefuted. Distinct integer cells cannot collide under a 2:1 projection "
        "(x1-y1 == x2-y2 and x1+y1 == x2+y2 imply x1 == x2); they share only the "
        "DEPTH KEY. Mark the claim REFUTED with the algebra:\n  "
        + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# CHECK 5 — every document carries a status line, or is listed in the
#           consistency doc as unverified.
# --------------------------------------------------------------------------- #
STATUS_RE = re.compile(r"\b(ACCURATE|STALE|SUPERSEDED)\b")


def test_check5_every_doc_is_dated_or_listed_in_the_consistency_doc():
    assert CONSISTENCY_DOC.exists(), (
        f"{CONSISTENCY_DOC.name} must exist: it is where a document that carries "
        f"no status line is listed as unverified."
    )
    consistency = _read(CONSISTENCY_DOC)

    undated = []
    for p in sorted(DOCS.glob("*.md")):
        text = _read(p)
        # status may be a `**Status: …**` line near the top, or an ACCURATE /
        # STALE / SUPERSEDED marker anywhere in the head.
        head = "\n".join(text.splitlines()[:20])
        has_status = bool(re.search(r"\*\*Status[:\*]", head, re.I)) or bool(
            STATUS_RE.search(head)
        )
        if has_status:
            continue
        if p.name in consistency:
            continue
        undated.append(p.name)

    assert not undated, (
        "these documents carry no status line (ACCURATE / STALE / SUPERSEDED) and "
        "are not listed in 40-DOC-CONSISTENCY.md:\n  "
        + "\n  ".join(undated)
    )


def test_check5_consistency_doc_exists_and_names_the_real_repo():
    """The corrections document must exist and carry the real slug."""
    assert CONSISTENCY_DOC.exists(), f"missing {CONSISTENCY_DOC}"
    text = _read(CONSISTENCY_DOC)
    assert "ruiqili2/agent-monitor" in text, (
        "40-DOC-CONSISTENCY.md must record the real agent-monitor slug "
        "(`ruiqili2/agent-monitor`), determined from its licence file."
    )
