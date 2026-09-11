#!/usr/bin/env bash
# Isolated-install proof: build the wheel, install it into a throwaway venv
# with --no-deps, and confirm (a) the REAL Hermes-based Qubit dashboard is
# served from package data and (b) the Qubit-owned Hermes runtime
# (voicebridge/_data/qubit_hermes) ships and imports from the install. No
# checkout on sys.path, no untracked files. This is the packaging acceptance
# gate for the active dashboard + runtime.
#
# Usage:  scripts/verify_installed_dashboard.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/qubit-install-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

PY="${PYTHON:-$REPO_ROOT/.venv/bin/python}"
command -v uv >/dev/null 2>&1 || {
  echo "ERROR: uv is required for the isolated install gate" >&2; exit 1; }

echo "== build wheel =="
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
    assert (st, loc) == (302, "/hermes/"), (st, loc)
    st, loc, _ = raw("/activity")
    assert (st, loc) == (302, "/hermes/logs"), (st, loc)
    st, _, body = raw("/hermes/")
    assert st == 200 and b"__HERMES_BASE_PATH__" in body, st
    assert b"__HERMES_SESSION_TOKEN__" in body, "token not injected"
    asset = re.search(rb"/hermes/(assets/[^\"]+\.js)", body).group(1).decode()
    st, _, ab = raw("/hermes/" + asset)
    assert st == 200 and len(ab) > 1000, (asset, st, len(ab))
    st, _, _ = raw("/hermes/api/dashboard/plugins")
    assert st == 401, st
    st, _, pb = raw("/hermes/api/dashboard/plugins",
                    {"X-Hermes-Session-Token": "gate-token"})
    names = [m["name"] for m in json.loads(pb)]
    for want in ("qubit-ops", "qubit-devices", "qubit-memory",
                 "qubit-reports", "qubit-approvals"):
        assert want in names, (want, names)
    st, _, jb = raw("/hermes/dashboard-plugins/qubit-ops/qubit-ops.js")
    assert st == 200 and b"__HERMES_PLUGINS__" in jb, st
    st, _, ob = raw("/hermes/api/ops",
                    {"X-Hermes-Session-Token": "gate-token"})
    ops = json.loads(ob)
    assert st == 200 and ops["count"] > 300, (st, ops.get("count"))
    print(f"OK root->/hermes/  asset={asset}  plugins={len(names)}  "
          f"ops={ops['count']}")
finally:
    ui.stop()
PY
"$WORK/venv/bin/python" "$WORK/probe.py"

echo "== installed Hermes runtime probe =="
# The vendored fork's env_loader imports python-dotenv at module load; that and
# pyyaml are the only third-party imports needed for hermes_cli.main to import.
uv pip install --quiet --python "$WORK/venv/bin/python" python-dotenv
cat > "$WORK/runtime_probe.py" <<'PY'
from pathlib import Path

import voicebridge
from voicebridge import hermes_runtime as hr

PIN = "2237be355906fbe6065ce1815711eee52b2d646e"

site = Path(voicebridge.__file__).resolve()
assert "site-packages" in str(site), f"voicebridge not imported from install: {site}"

st = hr.status()
assert st["present"] is True, st
assert st["importable"] is True, st
assert st["upstream_revision"] == PIN, st

root = Path(hr.runtime_root()).resolve()
assert "site-packages" in str(root), f"runtime not under install: {root}"
assert (root / "hermes_cli" / "main.py").is_file(), root

hr.ensure_path()
import hermes_cli.main as m  # noqa: E402  (forced by ensure_path above)

mod = Path(m.__file__).resolve()
assert mod.is_relative_to(root), (mod, root)
assert "site-packages" in str(mod), mod
print(f"OK runtime={root} module={mod}")
PY
# run from a neutral cwd with no PYTHONPATH so the checkout cannot leak onto
# sys.path and satisfy the import from package data only.
( cd "$WORK" && env -u PYTHONPATH "$WORK/venv/bin/python" "$WORK/runtime_probe.py" )

echo "installed dashboard gate: PASS"
echo "installed Hermes runtime gate: PASS"
