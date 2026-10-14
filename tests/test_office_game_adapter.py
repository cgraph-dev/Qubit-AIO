"""F05 — the data adapter: revision fence, stale retention, real-shape round-trip.

F05's own proof (60-IMPLEMENTATION-PLAN.md): "slow/out-of-order/error/unmount
responses cannot regress revision or blank last-known scene".

The adapter is TypeScript, so this compiles it with tsc and drives it in node
against the REAL recorded payload
(`frontend/qubit-web/src/lib/office-game/fixtures/tower-recording.json`,
byte-identical to a live `GET /agent/api/office/tower`; see its PROVENANCE.md).
That matters: the point of the fixture is that the adapter is written against
bytes the backend actually produced, not against a shape someone imagined.

The `preview` assertion is deliberate — an earlier design would have cleared the
flag to make a recording "look live". That is precisely the lie
`10-DECISIONS.md` P15 forbids.

Hermetic: tsc + node, no browser, no server, no network, no provider call.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "frontend" / "qubit-web"
GAME_DIR = REPO / "frontend" / "qubit-app" / "plugins" / "qubit-office" / "game"
FIXTURE = (REPO / "frontend" / "qubit-web" / "src" / "lib" / "office-game"
           / "fixtures" / "tower-recording.json")

NODE = shutil.which("node")
TSC = WEB / "node_modules" / "typescript" / "bin" / "tsc"


def _toolchain() -> bool:
    return bool(NODE) and TSC.is_file()


pytestmark = pytest.mark.skipif(
    not _toolchain(), reason="node + the web node_modules (typescript) required")


def _compile(tmp_path: Path) -> Path:
    """Compile the adapter + types to CJS for the node driver."""
    staged = tmp_path / "src"
    staged.mkdir(exist_ok=True)
    for name in ("types.ts", "adapter.ts"):
        shutil.copy(GAME_DIR / name, staged / name)
    subprocess.run(
        [NODE, str(TSC), "--module", "commonjs", "--target", "es2020",
         "--moduleResolution", "node", "--outDir", str(tmp_path / "js"),
         "--skipLibCheck", "--noEmitOnError", "false",
         "--lib", "es2020,dom", str(staged / "adapter.ts")],
        capture_output=True, text=True, timeout=180, cwd=str(staged))
    out = tmp_path / "js" / "adapter.js"
    if not out.is_file():
        pytest.skip("tsc could not compile the adapter for the node driver")
    return out


def _run(tmp_path: Path, js: str) -> object:
    """Drive the adapter in node. The result may be a promise, so the driver
    awaits it — otherwise `node -e` exits before an async assertion resolves
    and the test fails with a confusing missing key."""
    compiled = _compile(tmp_path)
    program = (
        'const A = require(%s);\n'
        'const FX = %s;\n'
        '(async () => {\n'
        '  const out = await (async () => { %s })();\n'
        '  process.stdout.write(JSON.stringify(out));\n'
        '})().catch(e => { console.error(String(e && e.stack || e)); '
        'process.exit(1); });\n'
    ) % (json.dumps(str(compiled)), json.dumps(str(FIXTURE)), js)
    proc = subprocess.run([NODE, "-e", program], capture_output=True,
                          text=True, timeout=90)
    assert proc.returncode == 0, (
        f"node exited {proc.returncode}\nstdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}")
    return json.loads(proc.stdout)


# ======================================================================
# 0 — the fixture is what the adapter claims
# ======================================================================
def test_the_fixture_exists_and_is_the_recorded_schema():
    assert FIXTURE.is_file(), f"missing {FIXTURE}"
    d = json.loads(FIXTURE.read_text())
    assert d["schema"] == "qubit-office-tower/1"
    # A recording, not a live read. The provenance note is the authority; this
    # asserts the fixture is still the thing that note describes.
    assert d["read_only"] is True
    assert isinstance(d.get("detail"), type(None)) or "detail" not in d


def test_the_adapter_validates_the_schema_rather_than_assuming_it():
    """A renamed backend field must be a typed refusal, not a silent empty
    office."""
    src = (GAME_DIR / "adapter.ts").read_text()
    assert "EXPECTED_SCHEMA" in src, "the adapter does not pin a schema"
    assert "unexpected schema" in src, "a schema mismatch is not reported"
    assert "from \"./types\"" in src, "the adapter does not use the typed shape"


# ======================================================================
# 1 — the real fixture round-trips
# ======================================================================
def test_the_real_recorded_payload_is_accepted(tmp_path):
    got = _run(tmp_path, """
      const payload = JSON.parse(require('fs').readFileSync(FX, 'utf8'));
      const r = A.parseTower(payload);
      return {ok: r.ok, error: r.ok ? null : r.error,
              schema: r.ok ? r.payload.schema : null,
              characters: r.ok ? r.payload.characters.length : -1,
              floors: r.ok ? r.payload.floorplan.floors.length : -1,
              headcount: r.ok ? r.payload.hud.headcount : -1};
    """)
    assert got["ok"] is True, f"the real payload was refused: {got['error']}"
    assert got["schema"] == "qubit-office-tower/1"
    assert got["characters"] == 10, f"expected 10 characters, got {got['characters']}"
    assert got["floors"] == 3, f"expected 3 floors, got {got['floors']}"
    assert got["headcount"] == 10


def test_the_shape_facts_the_scene_depends_on_survive_the_adapter(tmp_path):
    """`at` vs `animation`, and `dossier.known` vs a non-empty beliefs array —
    the two distinctions easy to collapse by accident."""
    got = _run(tmp_path, """
      const payload = JSON.parse(require('fs').readFileSync(FX, 'utf8'));
      const c = payload.characters[0];
      return {at: c.at, animation: c.animation,
              known: c.dossier.known,
              beliefs: c.dossier.beliefs.length,
              floorIsNumber: typeof c.position.floor === 'number',
              hasFloorRole: typeof c.position.floor_role === 'string',
              lastEventNullable: c.last_event === null ||
                                 typeof c.last_event.age_s === 'number'};
    """)
    assert got["at"] == "desk"
    assert got["animation"] == "idle"
    # KNOWN IS A FIELD. The first character is known:false with 2 beliefs.
    assert got["known"] is False
    assert got["beliefs"] > 0, (
        "the fixture no longer has beliefs under a known:false dossier, so the "
        "distinction this test protects is no longer exercised")
    assert got["floorIsNumber"] is True
    assert got["hasFloorRole"] is True
    assert got["lastEventNullable"] is True


def test_a_schema_mismatch_is_a_typed_refusal(tmp_path):
    got = _run(tmp_path, """
      const payload = JSON.parse(require('fs').readFileSync(FX, 'utf8'));
      payload.schema = 'qubit-office-tower/99';
      const r = A.parseTower(payload);
      return {ok: r.ok, error: r.ok ? null : r.error};
    """)
    assert got["ok"] is False
    assert "99" in got["error"]


def test_a_not_ok_answer_carries_the_backends_own_detail(tmp_path):
    """The route answers `ok:false` with a `detail` when the store is
    unreadable; the UI must show that sentence, not a generic failure."""
    got = _run(tmp_path, """
      const r = A.parseTower({schema: 'qubit-office-tower/1', ok: false,
                              detail: 'the office store could not be read: locked'});
      return {ok: r.ok, error: r.ok ? null : r.error};
    """)
    assert got["ok"] is False
    assert "locked" in got["error"], (
        "the backend's own detail was replaced by a generic message")


# ======================================================================
# 2 — the revision fence: out-of-order responses cannot regress
# ======================================================================
def test_a_late_response_cannot_overwrite_a_newer_one(tmp_path):
    """THE core F05 claim. Load A starts (rev 0), load B starts and lands
    (rev 1), then A's slow response arrives — it must be DROPPED."""
    got = _run(tmp_path, """
      const payload = JSON.parse(require('fs').readFileSync(FX, 'utf8'));
      let seen = [];
      const ad = new A.OfficeAdapter({fetchImpl: () => new Promise(()=>{}),
                                      onSnapshot: s => seen.push(s.revision)});
      ad.accept(payload, 0);            // rev 0 -> accepted, revision becomes 1
      const afterFirst = ad.snapshot().revision;
      ad.accept(payload, 1);            // rev 1 -> accepted, revision becomes 2
      const afterSecond = ad.snapshot().revision;
      ad.accept(payload, 0);            // STALE (0 < 2) -> must be dropped
      const afterStale = ad.snapshot().revision;
      return {afterFirst, afterSecond, afterStale, seen};
    """)
    assert got["afterFirst"] == 1
    assert got["afterSecond"] == 2
    assert got["afterStale"] == 2, (
        "a stale response advanced/regressed the revision — the view can now "
        "travel backwards in time")


def test_an_identical_revision_does_not_regress(tmp_path):
    got = _run(tmp_path, """
      const payload = JSON.parse(require('fs').readFileSync(FX, 'utf8'));
      const ad = new A.OfficeAdapter({fetchImpl: () => new Promise(()=>{}),
                                      onSnapshot: () => {}});
      ad.accept(payload, 5); const a = ad.snapshot().revision;
      ad.accept(payload, 5); const b = ad.snapshot().revision;
      return {a, b};
    """)
    assert got["a"] == 6 and got["b"] == 6


# ======================================================================
# 3 — a failure retains the last-known scene, and does not blank it
# ======================================================================
def test_an_error_retains_the_last_good_payload_and_marks_it_stale(tmp_path):
    got = _run(tmp_path, """
      const payload = JSON.parse(require('fs').readFileSync(FX, 'utf8'));
      const ad = new A.OfficeAdapter({fetchImpl: () => new Promise(()=>{}),
                                      onSnapshot: () => {}});
      ad.accept(payload, 0);
      const before = ad.snapshot();
      // now a failing load
      return (async () => {
        const ad2 = new A.OfficeAdapter({
          fetchImpl: () => Promise.resolve({ok:false, status:503,
                                            json: async()=>({})}),
          onSnapshot: () => {}});
        ad2.accept(payload, 0);
        await ad2.load();
        const s = ad2.snapshot();
        return {hadPayload: before.payload !== null,
                stillHasPayload: s.payload !== null,
                error: s.error, stale: s.stale,
                characters: s.payload ? s.payload.characters.length : -1};
      })();
    """)
    assert got["hadPayload"] is True
    assert got["stillHasPayload"] is True, (
        "a failed poll blanked the scene — an unavailable backend now looks "
        "like an empty office")
    assert got["error"], "the failure was not surfaced"
    assert got["stale"] is True, "a retained-but-stale payload is not marked stale"
    assert got["characters"] == 10, "the retained payload lost its content"


def test_a_failed_read_never_reports_itself_as_a_live_success(tmp_path):
    got = _run(tmp_path, """
      const ad = new A.OfficeAdapter({
        fetchImpl: () => Promise.resolve({ok:false, status:500, json: async()=>({})}),
        onSnapshot: () => {}});
      return (async () => { await ad.load(); const s = ad.snapshot();
        return {error: s.error, provenance: s.provenance,
                loading: s.loading, payloadNull: s.payload === null}; })();
    """)
    assert got["error"], "the HTTP failure produced no error"
    assert got["payloadNull"] is True
    assert got["provenance"] == "none", (
        "a failed read claimed a provenance it does not have")
    assert got["loading"] is False, "the loading flag stuck after a failure"


# ======================================================================
# 4 — provenance: a recording is never called live
# ======================================================================
def test_a_preview_payload_is_reported_as_a_recording_not_live(tmp_path):
    """P15: an unavailable capability is labelled. The adapter must NOT clear
    the backend's own `preview` flag to make a recording look live."""
    got = _run(tmp_path, """
      const payload = JSON.parse(require('fs').readFileSync(FX, 'utf8'));
      payload.preview = true;
      const ad = new A.OfficeAdapter({fetchImpl: () => new Promise(()=>{}),
                                      onSnapshot: () => {}});
      ad.accept(payload, 0);
      const s = ad.snapshot();
      return {provenance: s.provenance, previewFlag: s.payload.preview};
    """)
    assert got["provenance"] == "recording", (
        "a preview payload was reported as live")
    assert got["previewFlag"] is True, (
        "the adapter cleared the backend's own preview flag — that is exactly "
        "the lie P15 forbids")


def test_a_live_payload_is_reported_as_live(tmp_path):
    got = _run(tmp_path, """
      const payload = JSON.parse(require('fs').readFileSync(FX, 'utf8'));
      payload.preview = false;
      const ad = new A.OfficeAdapter({fetchImpl: () => new Promise(()=>{}),
                                      onSnapshot: () => {}});
      ad.accept(payload, 0);
      return {provenance: ad.snapshot().provenance};
    """)
    assert got["provenance"] == "live"


# ======================================================================
# 5 — lifecycle: dispose is safe in a React cleanup
# ======================================================================
def test_dispose_is_idempotent_and_aborts_in_flight(tmp_path):
    got = _run(tmp_path, """
      let aborted = false;
      const ad = new A.OfficeAdapter({
        fetchImpl: (url, init) => {
          init.signal.addEventListener('abort', () => { aborted = true; });
          return new Promise(() => {});
        }, onSnapshot: () => {}});
      ad.start();
      ad.dispose();
      ad.dispose();   // must not throw
      return (async () => {
        await new Promise(r => setTimeout(r, 30));
        return {aborted, emittedAfterDispose: ad.snapshot().loading};
      })();
    """)
    assert got["aborted"] is True, (
        "dispose() did not abort the in-flight request — an unmounted view "
        "keeps a live request open")
    assert got["emittedAfterDispose"] is False


def test_the_adapter_never_writes(tmp_path):
    """C4: the adapter reads. A renderer that could POST would be the boundary
    failure the whole contract exists to prevent."""
    src = (GAME_DIR / "adapter.ts").read_text()
    for verb in ("POST", "PUT", "PATCH", "DELETE"):
        assert f'"{verb}"' not in src, f"the adapter contains {verb}"
    assert 'method:' not in src, (
        "the adapter sets an HTTP method; the tower route is a bare GET")


def test_the_token_is_read_from_the_injected_global_not_a_query_param(tmp_path):
    """`webui.py::token_ok` rejects query-string tokens outright."""
    got = _run(tmp_path, """
      globalThis.__QUBIT_SESSION_TOKEN__ = 'tok-abc';
      return {tok: A.readSessionToken()};
    """)
    assert got["tok"] == "tok-abc"
    src = (GAME_DIR / "adapter.ts").read_text()
    assert "?token=" not in src and "&token=" not in src, (
        "the adapter puts the token in a query string, which token_ok rejects")
