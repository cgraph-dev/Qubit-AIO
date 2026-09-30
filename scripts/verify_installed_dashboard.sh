#!/usr/bin/env bash
# Isolated-install proof: build the wheel, install it into a throwaway venv
# with --no-deps, and confirm (a) the REAL Qubit dashboard is
# served from package data and (b) the Qubit-owned upstream runtime
# (voicebridge/_data/qubit_runtime) ships and imports from the install. No
# checkout on sys.path, no untracked files. This is the packaging acceptance
# gate for the active dashboard + runtime.
#
# Usage:  scripts/verify_installed_dashboard.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/qubit-install-XXXXXX")"
# pyproject force-includes the whole frontend/ tree (a frozen mapping) and
# hatchling force-include does NOT honor .gitignore, so a leftover
# frontend/qubit-web/node_modules would ship ~21.6k build-only members into
# the wheel. Stash it for the build and restore it on exit.
NODE_MODULES="$REPO_ROOT/frontend/qubit-web/node_modules"
NODE_MODULES_STASH="$WORK/node_modules.stash"
cleanup() {
  if [ -d "$NODE_MODULES_STASH" ]; then mv "$NODE_MODULES_STASH" "$NODE_MODULES"; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

PY="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
command -v uv >/dev/null 2>&1 || {
  echo "ERROR: uv is required for the isolated install gate" >&2; exit 1; }

# frontend/qubit-dist is a git-ignored build artifact; without it the wheel
# would silently install with no dashboard. Build it deterministically when
# node/npm are available, otherwise stop with a typed DEGRADED (exit 4).
if [ ! -f "$REPO_ROOT/frontend/qubit-dist/index.html" ]; then
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    echo "== dashboard bundle missing — building deterministically =="
    "$REPO_ROOT/scripts/build_dashboard.sh" || {
      echo "DEGRADED: dashboard build failed (node/npm present)" >&2; exit 4; }
  else
    echo "DEGRADED: frontend/qubit-dist missing and node/npm not found —" >&2
    echo "          install node/npm or use a release sdist that ships it" >&2
    exit 4
  fi
fi

echo "== build wheel =="
if [ -d "$NODE_MODULES" ]; then
  mv "$NODE_MODULES" "$NODE_MODULES_STASH"
fi
uv build --wheel --out-dir "$WORK/dist" >/dev/null
WHEEL="$(ls "$WORK"/dist/*.whl)"
echo "wheel: $(basename "$WHEEL")"

echo "== isolated venv =="
uv venv --quiet "$WORK/venv"
uv pip install --quiet --no-deps --python "$WORK/venv/bin/python" "$WHEEL"
# only the light runtime deps needed to boot the WebUI handler with a stub ASR
uv pip install --quiet --python "$WORK/venv/bin/python" pyyaml

echo "== served dashboard probe =="
cat > "$WORK/probe.py" <<'PY'
import http.client
import json
import re
import tempfile
import time
from pathlib import Path

from voicebridge.config import Config
from voicebridge.webui import WebUI
import yaml

state = Path(tempfile.mkdtemp())
p = state / "voice.yaml"
# build the config via yaml.safe_dump so this gate script itself carries no
# secret-shaped assignment (the release secret scan treats `key: value` in
# shell/config files as a potential secret).
p.write_text(yaml.safe_dump({
    "logging": {"jsonl_path": str(state)},
    "webui": {"token": "gate-token"},
}))
cfg = Config.load(p)


class _F:
    _route = "local"
    _web_active = 0
    task = type("T", (), {"active": False})()

    def __init__(self):
        self.cfg = cfg
        self._t0 = time.time()


ui = WebUI(_F(), host="127.0.0.1", port=0, state_dir=state,
           asr_factory=lambda: None)
ui.start()
port = ui._server.server_address[1]


def raw(path, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    c.request("GET", path, headers=headers or {})
    r = c.getresponse()
    return r.status, r.getheader("Location"), r.read()


try:
    st, loc, _ = raw("/")
    assert (st, loc) == (302, "/chat"), (st, loc)
    # /activity is a public alias for the Qubit /logs route
    # (PUBLIC_ROUTE_ALIASES); a legacy upstream path must redirect to the real
    # Qubit page, never serve the stale activity.html shell with a 200.
    st, loc, _ = raw("/activity")
    assert (st, loc) == (302, "/logs"), (st, loc)
    st, _, _ = raw("/logs")
    assert st == 200, st
    st, _, body = raw("/chat")
    assert st == 200 and b"__HERMES_BASE_PATH__" in body, st
    assert b'__HERMES_BASE_PATH__=""' in body, "base path not root"
    assert b"__HERMES_SESSION_TOKEN__" in body, "token not injected"
    asset = re.search(rb"/(assets/[^\"]+\.js)", body).group(1).decode()
    st, _, ab = raw("/" + asset)
    assert st == 200 and len(ab) > 1000, (asset, st, len(ab))
    st, _, _ = raw("/agent/api/dashboard/plugins")
    assert st == 401, st
    st, _, pb = raw("/agent/api/dashboard/plugins",
                    {"X-Hermes-Session-Token": "gate-token"})
    names = [m["name"] for m in json.loads(pb)]
    for want in ("qubit-ops", "qubit-devices", "qubit-memory",
                 "qubit-reports", "qubit-approvals"):
        assert want in names, (want, names)
    st, _, jb = raw("/dashboard-plugins/qubit-ops/qubit-ops.js")
    assert st == 200 and b"__HERMES_PLUGINS__" in jb, st
    st, _, ob = raw("/agent/api/ops",
                    {"X-Hermes-Session-Token": "gate-token"})
    ops = json.loads(ob)
    assert st == 200 and ops["count"] > 300, (st, ops.get("count"))
    print(f"OK root->/chat  asset={asset}  plugins={len(names)}  "
          f"ops={ops['count']}")
finally:
    ui.stop()
PY
"$WORK/venv/bin/python" "$WORK/probe.py"

echo "== installed upstream runtime probe =="
# The vendored fork's env_loader imports python-dotenv at module load; that and
# pyyaml are the only third-party imports needed for qubit_cli.main to import.
uv pip install --quiet --python "$WORK/venv/bin/python" python-dotenv
cat > "$WORK/runtime_probe.py" <<'PY'
from pathlib import Path

import voicebridge
from voicebridge import qubit_runtime as hr

PIN = "2237be355906fbe6065ce1815711eee52b2d646e"

site = Path(voicebridge.__file__).resolve()
assert "site-packages" in str(site), f"voicebridge not imported from install: {site}"

st = hr.status()
assert st["present"] is True, st
assert st["importable"] is True, st
assert st["upstream_revision"] == PIN, st

root = Path(hr.runtime_root()).resolve()
assert "site-packages" in str(root), f"runtime not under install: {root}"
assert (root / "qubit_cli" / "main.py").is_file(), root

hr.ensure_path()
import qubit_cli.main as m  # noqa: E402  (forced by ensure_path above)

mod = Path(m.__file__).resolve()
assert mod.is_relative_to(root), (mod, root)
assert "site-packages" in str(mod), mod
print(f"OK runtime={root} module={mod}")
PY
# run from a neutral cwd with no PYTHONPATH so the checkout cannot leak onto
# sys.path and satisfy the import from package data only.
( cd "$WORK" && env -u PYTHONPATH "$WORK/venv/bin/python" "$WORK/runtime_probe.py" )

echo "installed dashboard gate: PASS"
echo "installed upstream runtime gate: PASS"
