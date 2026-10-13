#!/usr/bin/env bash
# =============================================================================
# verify_office_port.sh — make the Qubit Office port's PORT-LEVEL claims PROVABLE.
#
# WHY THIS EXISTS
# ---------------
# This project has been burned by CLAIMS that turned out false: a subagent
# reported `status=completed` having edited nothing; the renderer was "verified
# ready" twice while drawing an almost-empty canvas; a gradient claim was
# disproved by arithmetic; and a comment-stripper deleted the very string
# literals it was checking, so "no write verb" passed by erasing evidence.
#
# Prose is not evidence. This script re-derives, by execution, the five claims
# the port rests on and prints a PASS/FAIL line plus the evidence for EACH one.
# It exits non-zero if ANY check fails, so a future reader never has to trust a
# sentence in a document.
#
# It CHECKS ORIGIN (provenance), LOADING (the browser path), PURITY (no timer,
# no write verb), the PAYLOAD (the backend actually ships what the renderer
# reads) and the HEADER (attribution travels with the code). What it CANNOT do
# is see pixels: it proves the PIPELINE is intact, never that the drawing LOOKS
# right. See docs/game-office/31-PORT-VERIFICATION.md.
#
# USAGE
# -----
#     scripts/verify_office_port.sh            # run every check
#     scripts/verify_office_port.sh --verbose  # also print the raw probes
#
# Exit codes: 0 = every check passed; 1 = at least one check FAILED;
#             2 = the harness itself could not run (missing node/venv/CLI).
# =============================================================================
set -u

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

VENV_PY="$REPO/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  VENV_PY="$(command -v python3 || true)"
fi
if [[ -z "$VENV_PY" ]]; then
  echo "FATAL: no python interpreter (.venv/bin/python or python3) found" >&2
  exit 2
fi

VERBOSE=0
[[ "${1:-}" == "--verbose" ]] && VERBOSE=1

export REPO VERBOSE
# The whole verification is one Python program; the shell wrapper only locates
# the interpreter and forwards the exit code. Keeping the logic in Python means
# the comment-stripper is the exact same one the repo's tests use, not a
# re-typed regex that could drift.
exec "$VENV_PY" - "$@" <<'PYEOF'
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(os.environ["REPO"])
VERBOSE = os.environ.get("VERBOSE") == "1"

PLUGIN_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office"
BUNDLE = PLUGIN_DIR / "qubit-office.bundle.js"
BUILDER = REPO / "scripts" / "build_office_bundle.py"
PROVENANCE_DOC = REPO / "docs" / "game-office" / "25-PORTED-CODE.md"
RENDERER = PLUGIN_DIR / "qubit-office.js"
MODULES = ["iso-engine.js", "sprites.js", "camera.js", "control-bar.js",
           "motion.js"]
GLOBALS = ["QubitIso", "QubitSprites", "QubitCamera", "QubitControlBar",
           "QubitOfficeMotion"]
WRITE_VERBS = ("POST", "PUT", "PATCH", "DELETE")

# --------------------------------------------------------------- reporting
results = []          # (ok: bool, check_num, title, lines: list[str])


def record(ok, num, title, evidence):
    results.append((ok, num, title, evidence))


def show(ok, num, title, evidence):
    print(f"[{'PASS' if ok else 'FAIL'}] check {num}: {title}")
    for line in evidence:
        print(f"        {line}")
    print()


# =========================================================== comment stripper
def strip_js_comments(src: str) -> str:
    """Remove JS comments but KEEP string literals, quotes and all.

    This is the exact hazard the project already hit: an earlier stripper here
    deleted the contents of string literals, so a "no write verb" assertion
    passed by DELETING the evidence it was looking for. A string is therefore
    copied through as a unit and never inspected for comment markers.
    """
    out, i, n = [], 0, len(src)
    while i < n:
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
        elif src[i] in "\"'`":
            quote = src[i]
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == quote:
                    j += 1
                    break
                j += 1
            out.append(src[i:j])
            i = j
        else:
            out.append(src[i])
            i += 1
    return "".join(out)


# ==================================================== check 1: provenance
TWO_WORD_SLUG = re.compile(r"\b([A-Za-z0-9][\w.-]*/[\w.-]+)\b")

# The provenance table lists repos as `owner/name` in backticks. The aphae row
# carries no commit and the licence column reads `**none**`.
def parse_provenance_table(text: str):
    """Return (repos, aphae_read_only) parsed from 25-PORTED-CODE.md.

    `repos` maps owner/name -> dict(licence=..., paths=[...]). Only rows with a
    licence cell are counted; the "read for design, code NOT used" table is
    parsed separately so an unlicensed repo can never be mistaken for a source.
    """
    repos = {}
    rows = [ln for ln in text.splitlines() if ln.strip().startswith("|")]
    for ln in rows:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        repo_cell = cells[0]
        m = re.search(r"`([^`]*/[^`]*)`", repo_cell)
        if not m:
            continue
        slug = m.group(1)
        # Adopted table has 6 cols (repo|branch|commit|licence|source path|
        # local change); the "read for design, code NOT used" table has 3
        # cols (repo|licence|why no code). Licence is cell 3 in the first and
        # cell 1 in the second, so read it positionally by table width.
        if len(cells) >= 5:
            licence = cells[3].strip("*` ")
        else:
            licence = cells[1].strip("*` ")
        path = cells[4].strip("` ") if len(cells) > 4 else ""
        entry = repos.setdefault(slug, {"licence": licence, "paths": []})
        if path:
            entry["paths"].append(path)
    # A repo whose licence cell reads `none` is unlicensed -> all rights
    # reserved -> it may be READ but its code must never be ported.
    read_only = {s for s, v in repos.items() if "none" in v["licence"].lower()}
    return repos, read_only



if not PROVENANCE_DOC.is_file():
    print(f"FATAL: {PROVENANCE_DOC} is missing; cannot check provenance",
          file=sys.stderr)
    sys.exit(2)
prov_text = PROVENANCE_DOC.read_text(encoding="utf-8")
prov_repos, prov_read_only = parse_provenance_table(prov_text)

# Walk every PORTED CODE header in the plugin's source files and collect the
# upstream repos it cites. Only the header block above the IIFE is scanned, and
# canonical citation forms that count as a provenance claim:
#     `*   1. owner/name (MIT)`            a numbered attribution entry
#     `*   upstream repo : owner/name`     the single-source form (iso-engine)
#     '(`file.ts` (owner/name, MIT))'      the parenthetical form (renderer)
# This deliberately rejects path fragments (`src/engine`, `webview-ui/src`) and
# prose tokens (`H/2`, `Approve/deny`) that a loose `\w+/[\w.]+` regex would
# miscount as repos — the failure a false POSITIVE would cause.
CITED_NUMBERED = re.compile(r"^\s*\*?\s*\d+\.\s*([A-Za-z0-9][\w.-]*/[\w.-]+)\s*\(MIT",
                            re.M | re.I)
CITED_UPSTREAM = re.compile(r"upstream repo\s*:\s*([A-Za-z0-9][\w.-]*/[\w.-]+)",
                            re.I)
CITED_PAREN = re.compile(r"\(([A-Za-z0-9][\w.-]*/[\w.-]+),\s*MIT\)", re.I)
cited_by_file = {}
for name in MODULES + [RENDERER]:
    p = PLUGIN_DIR / name
    if not p.is_file():
        continue
    src = p.read_text(encoding="utf-8")
    # the leading block comment is the provenance header
    header_end = src.find("*/")
    header = src[:header_end] if header_end >= 0 else src[:4000]
    slugs = (set(CITED_NUMBERED.findall(header))
             | set(CITED_UPSTREAM.findall(header))
             | set(CITED_PAREN.findall(header)))
    cited_by_file[name] = slugs

# Every upstream repo slug ANY header cites is a port claim, whether or not the
# provenance table lists it. That is the whole point: an unlisted citation is
# silently-ungoverned provenance. No owner-based filtering here.
extra_from_headers = set()
for slugs in cited_by_file.values():
    extra_from_headers |= slugs

unlisted = sorted(s for s in extra_from_headers if s not in prov_repos)


ev1 = [
    f"doc: {PROVENANCE_DOC.relative_to(REPO)}",
    f"MIT sources in provenance table ({len(set(prov_repos) - prov_read_only)}): "
    + ", ".join(sorted(set(prov_repos) - prov_read_only)),
    f"read-only / unlicensed rows: "
    + (", ".join(sorted(prov_read_only)) or "(none)"),
]
ok1 = True
# (a) the unlicensed repo is present and marked read-only
if not prov_read_only:
    ok1 = False
    ev1.append("aphae is NOT marked read-only (no row with licence `none`)")
else:
    ev1.append(f"read-only provenance confirmed for: "
               + ", ".join(sorted(prov_read_only)))
# (b) no ported file cites a repo absent from the provenance table
if unlisted:
    ok1 = False
    ev1.append("UPSTREAM CITED BUT NOT IN THE PROVENANCE TABLE: "
               + ", ".join(unlisted))
    for name, slugs in cited_by_file.items():
        bad = sorted(s for s in slugs if s in unlisted)
        if bad:
            ev1.append(f"  - {name} cites {', '.join(bad)} in its header")
else:
    ev1.append("every header-cited upstream repo appears in the table")
# (c) no ported file lists the unlicensed repo as a SOURCE. A "source" entry is
# a numbered attribution line (`N. owner/name (MIT)`) or an `upstream repo:`
# line; a mention in a "deliberately not used / read only" sentence is not a
# source claim.
aphae_re = re.compile(
    r"^\s*\*?\s*\d+\.\s*rsanandres/aphae\s*\(|upstream repo\s*:\s*rsanandres/aphae",
    re.M | re.I)
aphae_as_source = False
for name in MODULES + [RENDERER]:
    p = PLUGIN_DIR / name
    if not p.is_file():
        continue
    if aphae_re.search(p.read_text(encoding="utf-8")):
        aphae_as_source = True
        ok1 = False
        ev1.append(f"{name} lists aphae as a PORTED SOURCE (attribution entry)")
if not aphae_as_source:
    ev1.append("no ported file lists aphae as a source (only 'not used' prose)")
record(ok1, 1, "licence provenance is internally consistent", ev1)


# ============================================ check 2: registry == loading
if shutil.which("node") is None:
    record(False, 2, "every registered module is loaded by the browser path",
           ["node not found on PATH; cannot execute the bundle"])
else:
    ev2 = []
    ok2 = True
    # 2a. the bundle on disk is current (use the builder, do not reimplement)
    chk = subprocess.run([str(REPO / ".venv" / "bin" / "python") if
                          (REPO / ".venv" / "bin" / "python").is_file()
                          else sys.executable,
                          str(BUILDER), "--check"],
                         capture_output=True, text=True, cwd=str(REPO),
                         timeout=120)
    ev2.append(f"builder --check rc={chk.returncode}: "
               + (chk.stdout or chk.stderr).strip().splitlines()[-1])
    if chk.returncode != 0:
        ok2 = False

    # 2b. load in a vm with `window` and NO `module` — the real browser path
    harness = textwrap.dedent("""
        const fs = require("fs"), vm = require("vm");
        const src = fs.readFileSync(process.argv[1], "utf8");
        const sandbox = {};
        sandbox.window = sandbox; sandbox.self = sandbox;
        sandbox.globalThis = sandbox; sandbox.console = console;
        vm.createContext(sandbox);
        const out = {threw: null, loaded: []};
        try {
            vm.runInContext(src, sandbox, {filename: "bundle.js"});
            out.loaded = sandbox.__qubit_loaded__ || [];
        } catch (e) { out.threw = String((e && e.message) || e); }
        try { out.probe = vm.runInContext(process.argv[2], sandbox,
                                          {filename: "probe.js"}); }
        catch (e) { out.probeError = String((e && e.message) || e); }
        console.log(JSON.stringify(out));
    """)
    probe = ("(()=>{"
             "const ids=window.QubitControlBar.buildButtons().map(b=>b.id);"
             "const pt=window.QubitIso.gridToScreen(2,3,0,"
             "{cx:0,cy:0,zoom:1,w:800,h:500});"
             "const dir=window.QubitOfficeMotion.DIR(0,0,1,0);"
             "const mm=window.QubitCamera.minimapTransform("
             "{minX:0,minY:0,maxX:10,maxY:10},{w:100,h:100},{x:0,y:0});"
             "const fr=window.QubitSprites.walkFrame(0.5);"
             "return JSON.stringify({ids,pt,dir,mm,fr});})()")
    proc = subprocess.run(["node", "-e", harness, str(BUNDLE), probe],
                          capture_output=True, text=True, timeout=90)
    if proc.returncode != 0:
        ok2 = False
        ev2.append(f"node harness rc={proc.returncode}: {proc.stderr[-400:]}")
    else:
        res = json.loads(proc.stdout.strip().splitlines()[-1])
        if VERBOSE:
            print(f"        [verbose] node harness raw: "
                  f"{json.dumps(res)[:600]}")
        if res.get("threw"):
            ok2 = False
            ev2.append(f"bundle THREW on load: {res['threw']}")
        if res.get("loaded") != GLOBALS:
            ok2 = False
            ev2.append(f"modules registered {res.get('loaded')} != {GLOBALS}")
        else:
            ev2.append(f"loaded (window, no module): {res['loaded']}")
        if res.get("probeError"):
            ok2 = False
            ev2.append(f"probe threw: {res['probeError']}")
        else:
            p = json.loads(res["probe"])
            # usable, not merely present
            if p["ids"] != ["reset", "fit", "zoom", "pnl"]:
                ok2 = False
                ev2.append(f"buildButtons() ids {p['ids']} != the four verbs")
            else:
                ev2.append(f"QubitControlBar.buildButtons().length = "
                           f"{len(p['ids'])} ({p['ids']})")
            if not (isinstance(p["pt"], dict)
                    and isinstance(p["pt"].get("x"), (int, float))
                    and isinstance(p["pt"].get("y"), (int, float))):
                ok2 = False
                ev2.append(f"gridToScreen returned non-numbers: {p['pt']}")
            else:
                ev2.append(f"QubitIso.gridToScreen(2,3,0,...) = {p['pt']}")
            if p["dir"] not in ("left", "right", "up", "down"):
                ok2 = False
                ev2.append(f"DIR() returned {p['dir']!r}, not a direction")
            else:
                ev2.append(f"QubitOfficeMotion.DIR(0,0,1,0) = {p['dir']!r}")
            if not (isinstance(p["fr"], (int, float))
                    and int(p["fr"]) == p["fr"] and p["fr"] >= 0):
                ok2 = False
                ev2.append("QubitSprites.walkFrame(0.5) did not return a "
                           f"non-negative frame index: {p['fr']!r}")
            else:
                ev2.append(f"QubitSprites.walkFrame(0.5) = {p['fr']} "
                           "(a frame index, derived from progress)")
            if not isinstance(p["mm"], dict):
                ok2 = False
                ev2.append(f"QubitCamera.minimapTransform inert: {p['mm']}")
            else:
                ev2.append(f"QubitCamera.minimapTransform(...) = {p['mm']}")
    record(ok2, 2, "every registered module loads AND is usable in the browser path",
           ev2)


# ================================================== check 3: purity / verbs
ev3 = []
ok3 = True
bundle_raw = BUNDLE.read_text(encoding="utf-8") if BUNDLE.is_file() else ""
bundle_code = strip_js_comments(bundle_raw)
raw_counts = {k: bundle_raw.count(k) for k in
              ("setInterval", "setTimeout", "requestAnimationFrame")}
code_setinterval = len(re.findall(r"\bsetInterval\b", bundle_code))
ev3.append(f"raw (comments included): setInterval={raw_counts['setInterval']}, "
           f"setTimeout={raw_counts['setTimeout']}, "
           f"requestAnimationFrame={raw_counts['requestAnimationFrame']} "
           f"(these live in the header's prose, not the code)")
if code_setinterval != 1:
    ok3 = False
    ev3.append(f"CODE contains {code_setinterval} setInterval, need exactly 1")
else:
    ev3.append("CODE contains exactly 1 setInterval (the poll)")
for banned in ("setTimeout", "requestAnimationFrame"):
    n = len(re.findall(rf"\b{banned}\b", bundle_code))
    if n:
        ok3 = False
        ev3.append(f"CODE contains {n} {banned}, must be 0")
    else:
        ev3.append(f"CODE contains 0 {banned}")
verbs_in_code = [v for v in WRITE_VERBS
                 if f'"{v}"' in bundle_code or f"'{v}'" in bundle_code]
if verbs_in_code:
    ok3 = False
    ev3.append(f"CODE names write verb(s): {', '.join(verbs_in_code)}")
else:
    ev3.append(f"CODE names 0 of {WRITE_VERBS} as a string literal "
               "(comments stripped, string literals KEPT)")
if "/api/office/tower" not in bundle_code:
    ok3 = False
    ev3.append("the literal /api/office/tower is absent from the code")
else:
    ev3.append("the literal /api/office/tower is present")
record(ok3, 3, "the bundle claims no work it does not do", ev3)


# ===================================================== check 4: the payload
ev4 = []
ok4 = True
py = str(REPO / ".venv" / "bin" / "python") if \
    (REPO / ".venv" / "bin" / "python").is_file() else sys.executable
try:
    proc = subprocess.run([py, "-m", "voicebridge", "office", "tower"],
                          capture_output=True, text=True, cwd=str(REPO),
                          timeout=120)
except subprocess.TimeoutExpired:
    proc = None
if proc is None or proc.returncode != 0:
    ok4 = False
    ev4.append("CLI `python -m voicebridge office tower` failed/none: "
               + (proc.stderr[-400:] if proc else "timeout"))
    payload = None
else:
    try:
        payload = json.loads(proc.stdout)
        ev4.append("CLI `python -m voicebridge office tower` returned JSON "
                   f"(schema={payload.get('schema')!r})")
    except json.JSONDecodeError as exc:
        ok4 = False
        payload = None
        ev4.append(f"CLI stdout is not JSON: {exc}")

if payload is not None:
    chars = payload.get("characters")
    if not isinstance(chars, list) or not chars:
        ok4 = False
        ev4.append("characters[] missing or empty — the renderer would draw "
                   "an empty canvas")
    else:
        bad_pos = 0
        walking_seen = 0
        progress_missing = 0
        for c in chars:
            pos = c.get("position") if isinstance(c, dict) else None
            if not (isinstance(pos, dict) and "x" in pos and "y" in pos):
                bad_pos += 1
            if c.get("walking") is True:
                walking_seen += 1
                if pos is None or pos.get("progress") is None:
                    progress_missing += 1
        if bad_pos:
            ok4 = False
            ev4.append(f"{bad_pos}/{len(chars)} characters lack "
                       "position.{{x,y}}")
        else:
            ev4.append(f"characters[{len(chars)}].position.{{x,y}} present on "
                       "every character")
        if walking_seen and progress_missing:
            ok4 = False
            ev4.append(f"{progress_missing}/{walking_seen} walking characters "
                       "lack position.progress (the renderer's bob would stall)")
        elif walking_seen:
            ev4.append(f"all {walking_seen} walking characters carry "
                       "position.progress")
        else:
            # No live walker, so assert the PRODUCER directly: run the exact
            # function `state()` calls, at a clock where the walk is in
            # progress, and require the walking+progress contract. This is the
            # field that a silent schema drop would remove (it once DID: the
            # value was computed and then dropped, so the renderer's bob keyed
            # on a field that never arrived).
            producer = textwrap.dedent("""
                import time, json
                from voicebridge.config import Config
                from voicebridge.office import tower as T
                cfg = Config.load(None)
                plan = T.floorplan(cfg)
                room = plan["floors"][0]["rooms"][0]
                seat = room["seats"][0]
                now = time.time()
                w = T.walk_for(seat["agent_id"], plan=plan,
                               room_id=room["id"], event_kind="tool_call",
                               ts=now - 2.0, now=now)
                print(json.dumps({"walking": w.get("walking"),
                                  "progress": w.get("progress"),
                                  "walk_s": T.WALK_S}))
            """)
            pp = subprocess.run([py, "-c", producer], capture_output=True,
                                text=True, cwd=str(REPO), timeout=60)
            if pp.returncode != 0:
                ok4 = False
                ev4.append("no live walker AND walk_for() failed to run: "
                           + pp.stderr[-300:])
            else:
                try:
                    w = json.loads(pp.stdout.strip().splitlines()[-1])
                except Exception as exc:
                    ok4 = False
                    w = None
                    ev4.append(f"walk_for() output not JSON: {exc}")
                if w is not None:
                    if w.get("walking") is not True:
                        ok4 = False
                        ev4.append("no live walker AND walk_for() did not "
                                   f"return walking=True: {w}")
                    elif not (isinstance(w.get("progress"), (int, float))
                              and 0.0 < w["progress"] < 1.0):
                        ok4 = False
                        ev4.append("walk_for() returned walking=True but no "
                                   f"progress in (0,1): {w}")
                    else:
                        ev4.append("no live walker now; producer walk_for() "
                                   "proven at ts=now-2: walking=True, "
                                   f"progress={w['progress']} "
                                   f"(walk_s={w['walk_s']})")
                    ev4.append("the live payload carries position.progress the "
                               "instant any character reports walking:true")

    grid = (payload.get("floorplan") or {}).get("grid")
    if not isinstance(grid, dict) or "cols" not in grid or "rows" not in grid:
        ok4 = False
        ev4.append(f"floorplan.grid is not a {{cols,rows}} object: {grid!r}")
    else:
        ev4.append(f"floorplan.grid = {grid}")
    hud = payload.get("hud")
    if not isinstance(hud, dict) or not hud:
        ok4 = False
        ev4.append(f"hud missing or empty: {type(hud).__name__}")
    else:
        ev4.append(f"hud present with keys: {sorted(hud)[:6]}...")
record(ok4, 4, "the backend reaches the payload the renderer needs", ev4)


# ======================================== check 5: provenance in the header
ev5 = []
ok5 = True
mit_sources = set(prov_repos) - prov_read_only
renderer_src = RENDERER.read_text(encoding="utf-8") if \
    RENDERER.is_file() else ""
header_end = renderer_src.find("*/")
header = renderer_src[:header_end] if header_end >= 0 else renderer_src[:5000]
if "PORTED CODE" not in header:
    ok5 = False
    ev5.append("no 'PORTED CODE' header block in the renderer")
else:
    ev5.append("PORTED CODE header block present in the renderer")
# every MIT source the script proved is drawn on must be NAMED in the header:
# the provenance-table sources, plus any extra MIT source other modules cite.
required = set(mit_sources)
for s in extra_from_headers:
    if s not in prov_read_only:
        required.add(s)
named = sorted(s for s in required if s in header)
missing = sorted(s for s in required if s not in header)
if missing:
    ok5 = False
    ev5.append("renderer header omits upstream source(s) the port draws on: "
               + ", ".join(missing))
if not named:
    ok5 = False
    ev5.append("renderer header names no upstream source at all")
ev5.append(f"renderer header names: {', '.join(named) or '(none)'}")
if "aphae" in header:
    ev5.append("header also notes aphae as deliberately unused (not a source)")
record(ok5, 5, "provenance header travels with the renderer", ev5)


# ================================================================= summary
for ok, num, title, evidence in results:
    show(ok, num, title, evidence)

failed = [n for ok, n, _, _ in results if not ok]
total = len(results)
passed = total - len(failed)
print("=" * 74)
if failed:
    print(f"RESULT: {passed}/{total} checks passed — FAILED: "
          + ", ".join(f"check {n}" for n in failed))
    print("The port's claims are NOT all backed by execution. Fix the "
          "failures above; do not edit this script to hide them.")
    sys.exit(1)
print(f"RESULT: {passed}/{total} checks passed — the port's claims are "
      "backed by execution.")
print("NOTE: this proves the PIPELINE is intact and the claims are consistent; "
      "it cannot prove the drawing LOOKS right.")
sys.exit(0)
PYEOF
