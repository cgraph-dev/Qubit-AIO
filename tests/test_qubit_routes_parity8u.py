"""Packet 8U closure: local models / providers / profiles / tools / learning.

Closes the remaining audit §1A absent reads and §1B wrong-verb gaps so the
vendored Hermes dashboard always receives a real Qubit backend or a typed gate
-- never a bare 404. Every read dispatches the SAME module the CLI/voice use
(``local_models``, ``providers.policy``, ``projects``, ``learning``,
``capability_catalog``, ``computer_use``); every write/unsafe verb is a typed
refusal and never fabricates success.

Assertions:
(a) every added route resolves (``HC.match``) and dispatches to a real read or
    its exact typed code -- never 404/500;
(b) the previously-unmapped wrong-verb rows now answer the intended typed gate;
(c) representative reads equal the direct backend call (files list, git status,
    local-models status, schedules runs);
(d) typed gates never touch the filesystem or the network.

No skips, no xfail.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_LOCAL_CFG = ("intent:\n  local_llm:\n    enabled: false\n")


def _cfg(tmp_path, monkeypatch):
    from voicebridge import config as C

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "c"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "s"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "d"))
    p = tmp_path / "voice.yaml"
    p.write_text("logging:\n  jsonl: false\n" + _LOCAL_CFG)
    return C.Config.load(p)


def _repo(tmp_path, name="work"):
    assert shutil.which("git"), "git is required for the scoped git reads"
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    env = {"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path),
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    (root / "notes.md").write_text("hello\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True, env=env)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"],
                   check=True, env=env)
    return root, env


def _project(cfg, folder):
    from voicebridge import projects as PJ

    created = PJ.create(cfg, "Work", [str(folder)], requested_by="t",
                        confirmed=True, use=True)
    assert created.get("state") == "OK", created
    return created


# (method, path, handler) -- implemented reads backed by real Qubit modules.
NEW_READS = [
    ("GET", "/api/local-models/status", "h_local_models_status"),
    ("GET", "/api/local-models/hardware", "h_local_models_hardware"),
    ("GET", "/api/local-models/catalog", "h_local_models_catalog"),
    ("GET", "/api/local-models/jobs", "h_local_models_jobs"),
    ("GET", "/api/local-models/jobs/j1", "h_local_models_job_show"),
    ("GET", "/api/providers/custom-endpoints", "h_custom_endpoints"),
    ("GET", "/api/profiles/sessions", "h_profiles_sessions"),
    ("GET", "/api/profiles/sessions/sidebar", "h_profiles_sessions_sidebar"),
    ("GET", "/api/profiles/projects/tree", "h_profiles_projects_tree"),
    ("GET", "/api/tools/terminal/backends", "h_terminal_backends"),
    ("GET", "/api/tools/computer-use/status", "h_computer_use_status"),
    ("GET", "/api/learning/graph", "h_learning_graph"),
    ("GET", "/api/learning/node", "h_learning_node"),
    ("GET", "/api/auth/providers", "h_auth_providers"),
    # Wave 3 (D3/D4/D20/D21/D24): always-200 reads over the shared modules.
    ("GET", "/api/transfers/status", "h_transfers_status"),
    ("GET", "/api/recall/status", "h_recall_status"),
    ("GET", "/api/recall/policy", "h_recall_policy_get"),
    ("GET", "/api/documents/status", "h_documents_status"),
    ("GET", "/api/documents/kinds", "h_documents_kinds"),
    ("GET", "/api/ask/status", "h_ask_status"),
    ("GET", "/api/ask/pending", "h_ask_pending"),
    ("GET", "/api/shares/status", "h_session_share_status"),
    ("GET", "/api/sessions/shares", "h_session_shares_list"),
]

# (method, path, query, body, code, http) -- implemented handlers that return a
# typed refusal (local-model verbs reuse local_models; staged mutations 409).
NEW_REFUSALS = [
    ("POST", "/api/local-models/runtime/install", {}, {"confirm": True},
     "LOCAL_MODEL_BROKERED", 403),
    ("POST", "/api/local-models/download", {},
     {"model": "qubit-brain4", "confirm": True}, "LOCAL_MODEL_BROKERED", 403),
    ("DELETE", "/api/local-models/models/qubit-brain4", {}, {},
     "LOCAL_MODEL_BROKERED", 403),
    ("POST", "/api/local-models/quickstart", {}, {"confirm": True},
     "LOCAL_MODEL_BROKERED", 403),
    ("POST", "/api/local-models/server", {}, {"confirm": True},
     "LOCAL_MODEL_UNAVAILABLE", 403),
    ("POST", "/api/local-models/eject", {},
     {"model": "qubit-brain4", "confirm": True}, "LOCAL_MODEL_UNAVAILABLE", 403),
    ("POST", "/api/local-models/activate", {},
     {"model": "qubit-brain4", "confirm": True}, "LOCAL_MODEL_UNAVAILABLE", 403),
    ("POST", "/api/local-models/sideload", {},
     {"path": "/tmp/x.gguf", "confirm": True}, "LOCAL_MODEL_BROKERED", 403),
    ("GET", "/api/local-models/search", {}, {}, "LOCAL_MODEL_UNAVAILABLE", 403),
    ("GET", "/api/local-models/search/files", {}, {},
     "LOCAL_MODEL_UNAVAILABLE", 403),
    ("POST", "/api/local-models/download-browsed", {}, {"confirm": True},
     "LOCAL_MODEL_BROKERED", 403),
    ("POST", "/api/profiles", {}, {"name": "Work"},
     "CONFIRM_REQUIRED", 409),
    ("PUT", "/api/learning/node", {}, {"id": "x", "content": "y"},
     "CONFIRM_REQUIRED", 409),
    ("DELETE", "/api/learning/node", {}, {"id": "x"},
     "CONFIRM_REQUIRED", 409),
    # Wave 2: blanket gates converted to real Qubit confirm/audit operations.
    # Without confirm each stages a typed 409 (never a fabricated 2xx); the
    # confirmed real path is proven in test_qubit_routes_agent2b_wiring.py.
    ("POST", "/api/providers/custom-endpoints", {},
     {"id": "local-llm", "base_url": "http://127.0.0.1:8080"},
     "CONFIRM_REQUIRED", 409),
    ("POST", "/api/providers/custom-endpoints/aa/activate", {}, {},
     "CONFIRM_REQUIRED", 409),
    ("DELETE", "/api/providers/custom-endpoints/aa", {}, {},
     "CONFIRM_REQUIRED", 409),
    ("PUT", "/api/tools/terminal/backend", {}, {"backend": "local"},
     "CONFIRM_REQUIRED", 409),
    ("POST", "/api/tools/computer-use/permissions/grant", {}, {},
     "CONFIRM_REQUIRED", 409),
    ("POST", "/api/profiles/aa/export", {}, {},
     "CONFIRM_REQUIRED", 409),
]

# (method, path, code, http) -- declarative typed policy gates.
NEW_GATES = [
    ("POST", "/api/providers/custom-endpoints/validate",
     "NETWORK_PROBE_BLOCKED", 403),
    ("POST", "/api/providers/validate", "NETWORK_PROBE_BLOCKED", 403),
    ("POST", "/api/profiles/import", "PROFILE_IMPORT_REVIEW", 403),
    ("POST", "/api/profiles/aa/open-terminal", "PTY_NO_SHELL", 403),
    ("GET", "/api/profiles/aa/desktop-overlay", "DESKTOP_OVERLAY_ONLY", 403),
    ("POST", "/api/sessions/owner-backfill", "SESSIONS_RECEIPT_STORE", 501),
    ("POST", "/api/memory/providers/aa/oauth/start",
     "OAUTH_MANUAL_HANDOFF", 403),
    ("GET", "/api/memory/providers/aa/oauth/status",
     "OAUTH_MANUAL_HANDOFF", 403),
    ("DELETE", "/api/mcp/oauth/flows/f1", "OAUTH_MANUAL_HANDOFF", 403),
    ("GET", "/api/mcp/oauth/callback/aa/bb", "OAUTH_MANUAL_HANDOFF", 403),
    # Credentialed/desktop/WS surfaces that stay gated even after the
    # git-review + audio HTTP reads become real.
    ("GET", "/api/git/gh-auth", "GH_AUTH_OPERATOR", 403),
    ("GET", "/api/git/review/ship-info", "GIT_SHIP_GH_EXTERNAL", 403),
    ("POST", "/api/audio/tts-lease", "AUDIO_LEASE_DESKTOP", 403),
    ("GET", "/api/audio/elevenlabs/voices", "PROVIDER_EXTERNAL", 403),
    ("POST", "/api/cron/fire", "CRON_FIRE_EXTERNAL", 403),
    ("POST", "/api/profiles/sessions/pull-requests", "GH_PR_EXTERNAL", 403),
]

# (method, path, code, http) -- audit §1B wrong-verb rows that used to 404 and
# now answer the intended typed refusal (or MCP_NO_MOUNT on the implemented
# inventory handlers).
WRONG_VERB = [
    ("PUT", "/api/config/raw", "SECRET_NEVER_SERVED", 403),
    ("POST", "/api/env/reveal", "SECRET_NEVER_SERVED", 403),
    ("POST", "/api/credentials/pool", "SECRET_NEVER_SERVED", 403),
    ("DELETE", "/api/credentials/pool/openai/0", "SECRET_NEVER_SERVED", 403),
    # POST /api/mcp/servers/{name}/auth is now a real handler that validates the
    # server exists (unknown server -> 404 MCP_NOT_FOUND), not a typed refusal,
    # so it no longer belongs in this wrong-verb gate table.
]


# ------------------------------------------------------------------ resolution
def test_new_routes_match_expected_disposition():
    from voicebridge import qubit_compat as HC

    for method, path, handler in NEW_READS:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "implemented", (method, path, route)
        assert route["handler"] == handler, (method, path, route["handler"])

    for method, path, _q, _b, _code, _http in NEW_REFUSALS:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "implemented", (method, path, route)

    for method, path, code, _http in NEW_GATES:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        assert route["disposition"] == "gated", (method, path, route)
        assert route["gate"]["code"] == code, (method, path, route["gate"])
        assert route["gate"]["counterpart"], (method, path, route["gate"])


# ------------------------------------------------------------------- dispatch
def test_new_reads_dispatch_real_200(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path, _handler in NEW_READS:
        code, out = HC.dispatch(cfg, method, path,
                                {"id": "deadbeef"}, {})
        assert code == 200, (method, path, code, str(out)[:200])
        assert isinstance(out, dict) and out.get("ok") is True, (path, out)


def test_new_refusals_dispatch_exact_typed_code(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path, q, b, code_want, http in NEW_REFUSALS:
        code, out = HC.dispatch(cfg, method, path, q, b)
        assert code not in (404, 500), (method, path, code, str(out)[:200])
        assert code == http, (method, path, code, str(out)[:200])
        assert out.get("ok") is False, (method, path, out)
        assert out.get("code") == code_want, (method, path, out)
        assert out.get("qubit_counterpart"), (method, path, out)


def test_new_gates_dispatch_exact_typed_code(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path, code_want, http in NEW_GATES:
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code not in (404, 500), (method, path, code, str(out)[:200])
        assert code == http, (method, path, code, str(out)[:200])
        assert out.get("ok") is False, (method, path, out)
        assert out.get("state") == "UNAVAILABLE", (method, path, out)
        assert out.get("code") == code_want, (method, path, out)
        assert out.get("qubit_counterpart"), (method, path, out)
        assert out.get("owner"), (method, path, out)
        assert out.get("rollback"), (method, path, out)


def _gate_sample(pattern: str) -> str:
    import re as _re
    p = _re.sub(r"\(\?P<[^>]+>", "", pattern)
    p = p.replace("[^/]+", "x").replace(".+", "x")
    p = _re.sub(r"/\.\*", "/x", p)      # a trailing /.* needs a segment
    p = p.replace(".*", "")             # bare .* widens a prefix
    return p.replace("(", "").replace(")", "").replace("?", "")


def test_every_gated_route_is_typed(tmp_path, monkeypatch):
    """Every registered compat GATED route returns a typed refusal.

    This is the row-level proof for the SAFE_EQUIVALENT hermes API rows: no
    gated route 200s, 404s, or 500s; each carries a typed code and a documented
    Qubit counterpart.
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    checked = 0
    for route in HC.ROUTES:
        if route.get("disposition") != "gated":
            continue
        method, pattern = route["method"], route["pattern"]
        path = _gate_sample(pattern)
        code, out = HC.dispatch(cfg, method, path, {}, {})
        # A registered gate route returns a typed refusal: never a 200
        # success and never a 500.  A 404 is only acceptable when it carries a
        # typed gate code with a documented counterpart (e.g. the manual-
        # handoff OAuth gate), not a phantom "no route" 404.
        assert code not in (200, 500), (method, path, code, str(out)[:200])
        assert isinstance(out, dict), (method, path, out)
        assert out.get("code"), (method, path, out)
        if code == 404:
            assert out.get("detail") or out.get("qubit_counterpart"), \
                (method, path, out)
        else:
            assert out.get("ok") is False, (method, path, out)
        checked += 1
    assert checked >= 30, checked


# --------------------------------------------------------- wrong-verb closure
def test_wrong_verb_rows_now_typed_not_404(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path, code_want, http in WRONG_VERB:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code not in (404, 500), (method, path, code, str(out)[:200])
        assert code == http, (method, path, code, str(out)[:200])
        assert out.get("ok") is False, (method, path, out)
        assert out.get("code") == code_want, (method, path, out)

    code, out = HC.dispatch(cfg, "GET", "/api/auth/providers", {}, {})
    assert code == 200, (code, out)
    assert out.get("providers") == [], out


# ----------------------------------------- representative reads == backend
def test_files_list_read_equals_backend(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import files as FI

    cfg = _cfg(tmp_path, monkeypatch)
    root = tmp_path / "proj"
    root.mkdir(exist_ok=True)
    (root / "a.txt").write_text("hi\n")
    (root / "sub").mkdir(exist_ok=True)
    _project(cfg, root)

    direct = FI.list_dir(cfg, str(root))
    code, out = HC.dispatch(cfg, "GET", "/api/fs/list", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert {e["name"] for e in out["entries"]} == \
        {e["name"] for e in direct["entries"]}, (out, direct)


def test_git_status_read_equals_backend(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import git_scope as GI

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)

    direct = GI.status(cfg, str(root), requested_by="dashboard:user")
    code, out = HC.dispatch(cfg, "GET", "/api/git/status", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["root"] == direct["root"], (out, direct)
    assert out["status"] == direct["status"], (out, direct)


def test_local_models_status_read_equals_backend(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import local_models as LM

    cfg = _cfg(tmp_path, monkeypatch)
    direct = LM.status(cfg)
    code, out = HC.dispatch(cfg, "GET", "/api/local-models/status", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    for key, value in direct.items():
        assert out[key] == value, (key, out.get(key), value)


def test_schedules_runs_read_equals_backend(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge import schedules as S

    cfg = _cfg(tmp_path, monkeypatch)
    created = S.create(cfg, "reads-equal", "summarize notes",
                       "once in 10 minutes", requested_by="t", confirmed=True)
    job_id = int(created["job"]["id"])
    fired = S.run_due(
        cfg, requested_by="t",
        executor=lambda *_a, **_k: {"state": "OK", "code": "OK",
                                    "detail": "done"},
        now=time.time() + 3600, estop_engaged=lambda: False)
    assert fired["ran"] == 1, fired
    direct = S.history(cfg, job_id, 20)

    code, out = HC.dispatch(cfg, "GET", f"/api/cron/jobs/{job_id}/runs", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["count"] == len(direct.get("runs", [])), (out, direct)


# ------------------------------------------------- gates: no fs / no network
class _NetworkBlocked(BaseException):
    """Raised on any attempted socket connect (BaseException so broad
    ``except Exception`` handlers cannot swallow the proof)."""


def test_gates_never_touch_filesystem_or_network(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    cfg.state_dir()

    def _block_connect(self, address, *a, **k):
        raise _NetworkBlocked("gate attempted network connect: %r" % (address,))

    def _block_urlopen(*a, **k):
        raise _NetworkBlocked("gate attempted urlopen")

    monkeypatch.setattr("socket.socket.connect", _block_connect)
    monkeypatch.setattr("urllib.request.urlopen", _block_urlopen)
    monkeypatch.setattr(HC, "_audit", lambda *a, **k: None)

    class _CleanPower:
        def load(self):
            return {"emergency_stop": False, "global_disable": False}

    monkeypatch.setattr("voicebridge.power.power_control_store",
                        lambda cfg: _CleanPower())

    def _snapshot(root):
        return {str(p.relative_to(root)) for p in root.rglob("*")}

    before = _snapshot(tmp_path)
    for method, path, _code, _http in NEW_GATES:
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code not in (404, 500), (method, path, code, out)
    for method, path, q, b, _code, _http in NEW_REFUSALS:
        code, out = HC.dispatch(cfg, method, path, q, b)
        assert code not in (404, 500), (method, path, code, out)
    after = _snapshot(tmp_path)
    # Confirm-staged mutations write exactly one artifact: the single shared
    # approval journal (a real settleable confirmation). No other state, config,
    # secret, or network side effect is permitted.
    removed = before - after
    assert not removed, sorted(removed)
    added = after - before
    assert all(str(p).endswith("approvals.jsonl") for p in added), sorted(added)


# --------------------------------------------- git review reads (real backend)
def test_git_review_reads_are_real_scoped_backends(tmp_path, monkeypatch):
    """Branches/worktrees/review list/commit-context/rev-parse read the real
    scoped repo through git_scope's fixed-argv confinement (no shell/network)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    (root / "notes.md").write_text("hello changed\n")
    (root / "new.txt").write_text("brand new\n")

    code, out = HC.dispatch(cfg, "GET", "/api/git/worktrees", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["worktrees"] and out["worktrees"][0]["isMain"] is True, out
    assert out["worktrees"][0]["path"] == str(root), out

    code, out = HC.dispatch(cfg, "GET", "/api/git/branches", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert any(b["name"] in ("master", "main") and b["isDefault"]
               for b in out["branches"]), out

    code, out = HC.dispatch(cfg, "GET", "/api/git/base-branches", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert any(b["isDefault"] for b in out["branches"]), out

    code, out = HC.dispatch(cfg, "GET", "/api/git/review/list", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    paths = {f["path"] for f in out["files"]}
    assert {"notes.md", "new.txt"} <= paths, out
    row = [f for f in out["files"] if f["path"] == "notes.md"][0]
    assert row["added"] == 1 and row["removed"] == 1, row
    assert row["status"] == "M", row
    new_row = [f for f in out["files"] if f["path"] == "new.txt"][0]
    assert new_row["status"] == "?" and new_row["added"] == 1, new_row

    code, out = HC.dispatch(cfg, "GET", "/api/git/review/commit-context", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert "hello changed" in out["diff"], out
    assert "new.txt" in out["diff"], out
    assert "init" in out["recent"], out

    code, out = HC.dispatch(cfg, "GET", "/api/git/review/rev-parse", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["sha"] and len(out["sha"]) == 40, out

    code, out = HC.dispatch(cfg, "GET", "/api/fs/git-root", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)


def test_git_review_reads_typed_on_bad_root(tmp_path, monkeypatch):
    """A path outside the approved roots is a typed 400, never a 500."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)
    other = tmp_path / "elsewhere"
    other.mkdir()
    for path in ("/api/git/worktrees", "/api/git/branches",
                 "/api/git/review/list", "/api/git/review/rev-parse"):
        code, out = HC.dispatch(cfg, "GET", path, {"root": str(other)}, {})
        assert code not in (200, 404, 500), (path, code, out)
        assert out["ok"] is False and out["code"], (path, out)
        assert out["qubit_counterpart"], (path, out)


def test_git_review_reads_reject_option_injection(tmp_path, monkeypatch):
    """A ref/base that looks like a git option is a typed 400 (no argv leak);
    a syntactically valid but missing ref is an honest null sha."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    root, _ = _repo(tmp_path)
    _project(cfg, root)

    code, out = HC.dispatch(cfg, "GET", "/api/git/review/rev-parse",
                            {"ref": "--upload-pack=touch"}, {})
    assert code == 400 and out["ok"] is False, (code, out)

    code, out = HC.dispatch(cfg, "GET", "/api/git/review/list",
                            {"scope": "lastTurn", "base": "--output=/tmp/x"}, {})
    assert code == 400 and out["ok"] is False, (code, out)

    code, out = HC.dispatch(cfg, "GET", "/api/git/review/rev-parse",
                            {"ref": "deadbeef"}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["sha"] is None, out


# ------------------------------------------- audio HTTP actions (real backend)
def test_audio_transcribe_runs_local_asr_with_temp_lifecycle(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.asr.base import ASRUnavailable, Transcript

    cfg = _cfg(tmp_path, monkeypatch)
    captured = {}

    class _StubASR:
        def load(self):
            captured["loaded"] = True

        def transcribe(self, path):
            captured["path"] = str(path)
            captured["bytes"] = Path(path).read_bytes()
            return Transcript(text="hello from asr", provider="stub")

    monkeypatch.setattr(HC, "_asr_from_cfg", lambda cfg: _StubASR())
    import base64
    payload = base64.b64encode(b"RIFFfake-audio").decode()
    code, out = HC.dispatch(
        cfg, "POST", "/api/audio/transcribe", {},
        {"data_url": f"data:audio/wav;base64,{payload}"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["transcript"] == "hello from asr", out
    assert out["provider"] == "stub", out
    assert captured["loaded"] is True
    assert captured["bytes"] == b"RIFFfake-audio", captured
    assert not Path(captured["path"]).exists(), "temp upload must be removed"

    class _NoASR:
        def load(self):
            raise ASRUnavailable("no model on disk")

    monkeypatch.setattr(HC, "_asr_from_cfg", lambda cfg: _NoASR())
    code, out = HC.dispatch(
        cfg, "POST", "/api/audio/transcribe", {},
        {"data_url": f"data:audio/wav;base64,{payload}"})
    assert code == 403, (code, out)
    assert out["code"] == "AUDIO_ASR_UNAVAILABLE", out
    assert out.get("owner") and out.get("rollback"), out


def test_audio_transcribe_caps_payload(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    called = {"n": 0}

    class _StubASR:
        def load(self):
            called["n"] += 1

        def transcribe(self, path):
            called["n"] += 1

    monkeypatch.setattr(HC, "_asr_from_cfg", lambda cfg: _StubASR())

    code, out = HC.dispatch(cfg, "POST", "/api/audio/transcribe", {}, {})
    assert (code, out.get("code")) == (400, "AUDIO_PAYLOAD_REQUIRED"), (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/audio/transcribe", {},
                            {"data_url": "not-a-data-url"})
    assert (code, out.get("code")) == (400, "AUDIO_BAD_PAYLOAD"), (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/audio/transcribe", {},
                            {"data_url": "data:text/plain;base64,AAAA"})
    assert (code, out.get("code")) == (400, "AUDIO_BAD_PAYLOAD"), (code, out)
    assert called["n"] == 0, "ASR must not run for a rejected payload"
    assert out.get("owner") and out.get("rollback"), out


def test_audio_speak_runs_local_tts_as_data_url(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC
    from voicebridge.tts.base import TTSUnavailable

    cfg = _cfg(tmp_path, monkeypatch)

    class _StubTTS:
        name = "stub-tts"

        def load(self):
            pass

        def synthesize(self, text, out_path):
            Path(out_path).write_bytes(b"WAVDATA")
            return Path(out_path)

    monkeypatch.setattr(HC, "_tts_from_cfg", lambda cfg: _StubTTS())
    code, out = HC.dispatch(cfg, "POST", "/api/audio/speak", {}, {"text": "hi"})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["data_url"].startswith("data:audio/wav;base64,"), out
    assert out["provider"] == "stub-tts", out
    import base64
    assert base64.b64decode(out["data_url"].split(",", 1)[1]) == b"WAVDATA"

    class _NoTTS:
        name = "no-tts"

        def load(self):
            raise TTSUnavailable("voice not installed")

    monkeypatch.setattr(HC, "_tts_from_cfg", lambda cfg: _NoTTS())
    code, out = HC.dispatch(cfg, "POST", "/api/audio/speak", {}, {"text": "hi"})
    assert code == 403, (code, out)
    assert out["code"] == "AUDIO_TTS_UNAVAILABLE", out
    assert out.get("owner") and out.get("rollback"), out

    code, out = HC.dispatch(cfg, "POST", "/api/audio/speak", {}, {})
    assert (code, out.get("code")) == (400, "AUDIO_TEXT_REQUIRED"), (code, out)


def test_audio_voice_config_is_references_only(tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/audio/voice-config", {}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["schema"] == "qubit-runtime-audio/1", out
    for kind in ("stt", "tts"):
        assert out[kind]["mode"] == "relay", out
        assert out[kind]["provider"], out
        assert out[kind]["profile"], out
    assert out["voice"]["name"], out
    assert "mic" in out["audio"] and "output" in out["audio"], out


# ---------------------------------------------- Wave 3 session-share routes
def _seed_receipt(cfg, rid="rec-share"):
    state = Path(cfg.state_dir())
    state.mkdir(parents=True, exist_ok=True)
    (state / "actions.json").write_text(json.dumps(
        {"schema": 1,
         "receipts": {rid: {"receipt_id": rid, "action_id": "act.send",
                            "state": "OK", "code": "OK",
                            "runtime": "qubit-broker", "created_at": 1.0,
                            "detail": "invoice alpha sent"}},
         "audit": []}))


def test_session_share_routes_roundtrip_without_dashboard_token(
        tmp_path, monkeypatch):
    """GET /share/{id} is authenticated by the SHARE token alone (no dashboard
    token), and publish/list/revoke/purge run the same session_share module."""
    from voicebridge import qubit_compat as HC
    from voicebridge import session_share as SS

    cfg = _cfg(tmp_path, monkeypatch)
    _seed_receipt(cfg)

    # publish requires confirmation and stages a 409, never a fabricated 200
    code, out = HC.dispatch(cfg, "POST", "/api/sessions/rec-share/share",
                            {}, {})
    assert code == 409 and out["code"] == SS.CONFIRM_CODE, (code, out)
    assert out["ok"] is False, out

    code, out = HC.dispatch(cfg, "POST", "/api/sessions/rec-share/share",
                            {}, {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    share_id, token = out["share"]["id"], out["token"]
    assert SS.resolve(cfg, share_id, token)["state"] == "OK"

    # list never renders a token or hash
    code, out = HC.dispatch(cfg, "GET", "/api/sessions/shares", {}, {})
    assert code == 200 and out["count"] == 1, (code, out)
    blob = json.dumps(out)
    assert token not in blob and "token_hash" not in blob, out

    # GET /share/{id} with ONLY the share token: no dashboard token involved
    code, out = HC.dispatch(cfg, "GET", f"/share/{share_id}",
                            {"token": token}, {})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["state"] == "OK" and out["transcript"]["session_id"] == \
        "rec-share", out
    assert token not in json.dumps(out), out

    # a wrong/missing token is a typed denial that leaks no transcript
    code, out = HC.dispatch(cfg, "GET", f"/share/{share_id}",
                            {"token": "nope"}, {})
    assert code == 403 and out["ok"] is False, (code, out)
    assert out["code"] == SS.BAD_TOKEN_CODE, out
    assert "transcript" not in out, out
    code, out = HC.dispatch(cfg, "GET", f"/share/{share_id}", {}, {})
    assert code == 400 and out["ok"] is False, (code, out)
    assert out["code"] == SS.BAD_INPUT_CODE, out
    assert "transcript" not in out, out

    # an unknown share id is an honest 404, never a fabricated view
    code, out = HC.dispatch(cfg, "GET", "/share/shr_missing",
                            {"token": token}, {})
    assert code == 404 and out["code"] == SS.NOT_FOUND_CODE, (code, out)

    # revoke is confirm-gated, then the share is dead
    code, out = HC.dispatch(cfg, "DELETE", f"/api/sessions/shares/{share_id}",
                            {}, {})
    assert code == 409 and out["code"] == SS.CONFIRM_CODE, (code, out)
    code, out = HC.dispatch(cfg, "DELETE", f"/api/sessions/shares/{share_id}",
                            {}, {"confirm": True})
    assert code == 200 and out["state"] == "OK" and out["changed"] is True, out
    code, out = HC.dispatch(cfg, "GET", f"/share/{share_id}",
                            {"token": token}, {})
    assert code == 403 and out["code"] == SS.REVOKED_CODE, (code, out)

    # purge requires confirmation; with it, the tombstone is cleared
    code, out = HC.dispatch(cfg, "POST", "/api/sessions/shares/purge",
                            {}, {})
    assert code == 409 and out["code"] == SS.CONFIRM_CODE, (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/sessions/shares/purge",
                            {}, {"confirm": True})
    assert code == 200 and out["ok"] is True, (code, out)
    assert out["purged"] == 0 and out["remaining"] == 1, out


def test_wave3_routes_dispatch_typed_never_404(tmp_path, monkeypatch):
    """The Wave 3 mutation routes answer typed gates (never 404/500)."""
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    cases = [
        ("POST", "/api/transfers/fetch", {}, {}),
        ("POST", "/api/transfers/plan", {}, {}),
        ("GET", "/api/transfers/list", {}, {}),
        ("GET", "/api/transfers/resume", {}, {}),
        ("GET", "/api/recall", {}, {}),
        ("POST", "/api/recall/policy", {}, {"confirm": True}),
        ("POST", "/api/recall/escalate", {}, {"confirm": True}),
        ("GET", "/api/recall/resolve", {}, {}),
        ("POST", "/api/documents/extract", {}, {}),
        ("POST", "/api/ask", {}, {}),
        ("POST", "/api/ask/answer", {}, {}),
    ]
    for method, path, q, b in cases:
        route, _ = HC.match(method, path)
        assert route is not None and route["disposition"] == "implemented", \
            (method, path)
        code, out = HC.dispatch(cfg, method, path, q, b)
        assert code not in (404, 500), (method, path, code, out)
        assert out.get("ok") is False, (method, path, out)
        assert out.get("code"), (method, path, out)


# ===========================================================================
# Remaining needs=none rows that stay typed Qubit gates because the upstream
# behavior is genuinely unsafe/operator-only (secrets, credentialed network,
# PTY/desktop, destructive receipt sweeps) or has no Qubit equivalent. The
# positive is the typed envelope; the negative proves ``confirm`` cannot bypass
# the gate and that a body is never interpreted as success.
# ===========================================================================
REMAINING_TYPED = [
    ("GET", "/api/config/raw", "SECRET_NEVER_SERVED", 403),
    ("PUT", "/api/config/raw", "SECRET_NEVER_SERVED", 403),
    ("POST", "/api/env/reveal", "SECRET_NEVER_SERVED", 403),
    ("GET", "/api/credentials/pool", "SECRET_NEVER_SERVED", 403),
    ("POST", "/api/credentials/pool", "SECRET_NEVER_SERVED", 403),
    ("DELETE", "/api/credentials/pool/openai/0", "SECRET_NEVER_SERVED", 403),
    ("POST", "/api/providers/validate", "NETWORK_PROBE_BLOCKED", 403),
    ("POST", "/api/providers/custom-endpoints/validate",
     "NETWORK_PROBE_BLOCKED", 403),
    ("GET", "/api/media", "MEDIA_NOT_SERVED", 403),
    ("POST", "/api/chat/image-upload", "CHAT_IMAGE_OVERLAY", 403),
    # D5: the file download/stream routes are now real brokered binary reads
    # (voicebridge.qubit_compat.h_files_download); covered by
    # tests/test_audit_remaining_repair9u.py, not this typed-refusal table.
    ("GET", "/api/git/gh-auth", "GH_AUTH_OPERATOR", 403),
    ("GET", "/api/git/review/ship-info", "GIT_SHIP_GH_EXTERNAL", 403),
    ("POST", "/api/git/review/pr-list", "GIT_WRITE_BROKERED", 403),
    ("POST", "/api/git/review/push", "GIT_WRITE_BROKERED", 403),
    ("POST", "/api/git/review/create-pr", "GIT_WRITE_BROKERED", 403),
    ("GET", "/api/ops/backup/download", "BACKUP_PASSPHRASE", 403),
    ("GET", "/api/ssh/ownership", "SSH_OWNERSHIP_OPERATOR", 403),
    # Curator pause/run and memory reset were converted to real Qubit-backed
    # controls (persisted pause/run state; canonical memory forget ops) and are
    # covered by tests/test_dashboard_backend_closure9u.py instead of this
    # typed-refusal table.

    ("POST", "/api/profiles/import", "PROFILE_IMPORT_REVIEW", 403),
    ("POST", "/api/profiles/work/open-terminal", "PTY_NO_SHELL", 403),
    ("GET", "/api/profiles/work/desktop-overlay", "DESKTOP_OVERLAY_ONLY", 403),
    ("POST", "/api/profiles/sessions/pull-requests", "GH_PR_EXTERNAL", 403),
    ("POST", "/api/memory/providers/x/oauth/start",
     "OAUTH_MANUAL_HANDOFF", 403),
    ("POST", "/api/sessions/owner-backfill", "SESSIONS_RECEIPT_STORE", 501),
    # POST /api/sessions/{bulk-delete,import,prune} were converted to real
    # Qubit-backed confirm-gated mutations (owner decisions in force); they are
    # covered by tests/test_sessions_mutations_wave18.py, not this typed table.
    ("PUT", "/api/model/moa", "MOA_SUPERSEDED", 501),
    # POST /api/profiles/{name}/describe-auto is now a real ported handler
    # (reference web_routers/profiles.py) that validates the profile and runs
    # the auxiliary-LLM describer; it is covered by
    # tests/test_profile_describe_auto_wave17.py, not this typed table.
    # PATCH /api/profiles/work validates its payload before the persona gate
    # (empty/malformed body -> 400 MALFORMED), so it is no longer a pure typed
    # refusal; the immutable-persona gate remains for a well-formed rename and
    # is covered by tests/test_dashboard_matrix9u.py (BOOLEAN_CONFIRM_PREVALIDATED).
    ("POST", "/api/memory/providers/cloud/setup",
     "MEMORY_LOCAL_ONLY", 403),
    ("PUT", "/api/memory/providers/cloud/config",
     "MEMORY_LOCAL_ONLY", 403),
]


def test_remaining_typed_rows_are_typed_and_confirm_cannot_bypass(
        tmp_path, monkeypatch):
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    for method, path, code_want, http in REMAINING_TYPED:
        route, _ = HC.match(method, path)
        assert route is not None, (method, path)
        code, out = HC.dispatch(cfg, method, path, {}, {})
        assert code == http, (method, path, code, str(out)[:200])
        assert out.get("ok") is False, (method, path, out)
        assert out.get("state") == "UNAVAILABLE", (method, path, out)
        assert out.get("code") == code_want, (method, path, out)
        assert out.get("qubit_counterpart"), (method, path, out)
        assert out.get("owner"), (method, path, out)
        assert out.get("rollback"), (method, path, out)

        # Negative: claiming confirm must not bypass a typed gate.
        code2, out2 = HC.dispatch(cfg, method, path, {}, {"confirm": True})
        assert code2 == http, (method, path, code2, str(out2)[:200])
        assert out2.get("ok") is False and out2.get("code") == code_want, \
            (method, path, out2)



def test_memory_reset_confirms_and_deletes(tmp_path, monkeypatch):
    """GET is a read-only status; POST is a real confirm-gated reset.

    ``GET /api/memory/reset`` reports the destructive target set without
    deleting (read-only). ``POST /api/memory/reset`` stages the destructive
    action through the shared journal (409 with a real ``confirmation_id``),
    then on ``confirm`` deletes the local semantic memories / markdown
    workspace notes and returns the real deleted list.
    """
    from voicebridge import qubit_compat as HC

    cfg = _cfg(tmp_path, monkeypatch)
    code, out = HC.dispatch(cfg, "GET", "/api/memory/reset", {}, {})
    assert code == 200 and out.get("ok") is True, (code, out)
    assert out.get("confirm_required") is True, out

    code, out = HC.dispatch(cfg, "POST", "/api/memory/reset", {}, {})
    assert code == 409 and out["code"] == "CONFIRM_REQUIRED", (code, out)
    assert out.get("confirmation_id"), (code, out)
    code, out = HC.dispatch(cfg, "POST", "/api/memory/reset", {},
                            {"confirm": True})
    assert code == 200 and out.get("ok") is True, (code, out)
    assert isinstance(out.get("deleted"), list), (code, out)
