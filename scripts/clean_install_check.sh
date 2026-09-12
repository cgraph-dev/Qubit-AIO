#!/usr/bin/env bash
# Hermetic clean-install rehearsal for the Qubit wheel.
#
# Builds the wheel, installs it (with its declared dependencies) into a
# throwaway venv under /tmp with a throwaway HOME, then proves the INSTALLED
# package — never the checkout — serves the dashboard and imports the
# vendored Hermes runtime and every console entry point.
#
# Usage: scripts/clean_install_check.sh [--require-network]
#
# Typed exit codes (never a silent pass):
#   0 PASS     wheel built, installed, and every startup probe passed
#   2 FAIL     a real defect (probe/entry point/package-data failure)
#   4 DEGRADED toolchain or network unavailable for the rehearsal
#   8 BLOCKED  reserved (dirty tree); the wheel is built from the working tree
#
# With --require-network, a failed dependency install is a FAIL (2) instead of
# a DEGRADED (4) skip, so CI can demand a full rehearsal.
#
# Hermetic guarantee: everything lives under the throwaway /tmp work dir; the
# live service, the operator's real HOME/config/state, and any running unit are
# never touched, enabled, or restarted.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
REQUIRE_NETWORK=0
[ "${1:-}" = "--require-network" ] && REQUIRE_NETWORK=1

WORK="$(mktemp -d "${TMPDIR:-/tmp}/qubit-clean-install-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

HOME_DIR="$WORK/home"
VENV="$WORK/venv"
DIST="$WORK/dist"
mkdir -p "$HOME_DIR" "$DIST"

# A genuinely separate HOME/XDG root outside the checkout: venv creation,
# install, and the probes must not read the operator's real config/cache.
export HOME="$HOME_DIR"
export XDG_CONFIG_HOME="$HOME_DIR/.config"
export XDG_DATA_HOME="$HOME_DIR/.local/share"
export XDG_STATE_HOME="$HOME_DIR/.local/state"
export XDG_CACHE_HOME="$HOME_DIR/.cache"
unset PYTHONPATH || true

degrade() { echo "DEGRADED: $*" >&2; exit 4; }
fail() { echo "FAIL: $*" >&2; exit 2; }

# ---------------------------------------------------------------- toolchain
BUILD_CMD=()
VENV_CMD=()
PIP_CMD=()
if command -v uv >/dev/null 2>&1; then
  BUILD_CMD=(uv build --wheel --out-dir "$DIST")
  VENV_CMD=(uv venv --quiet "$VENV")
  PIP_CMD=(uv pip install --python "$VENV/bin/python")
  TOOLCHAIN="uv"
else
  BASE_PY="${PYTHON:-python3}"
  "$BASE_PY" -c 'import build' >/dev/null 2>&1 || \
    degrade "neither uv nor the 'build' module is available (pip install build)"
  BUILD_CMD=("$BASE_PY" -m build --wheel --out-dir "$DIST")
  VENV_CMD=("$BASE_PY" -m venv "$VENV")
  PIP_CMD=("$VENV/bin/python" -m pip install)
  TOOLCHAIN="$BASE_PY -m build"
fi
echo "== toolchain: $TOOLCHAIN =="

# ---------------------------------------------------------------- build wheel
echo "== build wheel =="
if ! "${BUILD_CMD[@]}" >/dev/null 2>"$WORK/build.err"; then
  degrade "wheel build failed: $(tail -c 300 "$WORK/build.err" | tr '\n' ' ')"
fi
WHEEL="$(ls "$DIST"/*.whl 2>/dev/null | head -1 || true)"
[ -n "$WHEEL" ] || fail "wheel not produced by the build"
echo "wheel: $(basename "$WHEEL")"

# ---------------------------------------------------------------- venv + install
echo "== throwaway venv (under $VENV) =="
"${VENV_CMD[@]}" >/dev/null 2>&1 || degrade "venv creation failed"
[ -x "$VENV/bin/python" ] || degrade "venv python missing"

echo "== install wheel + declared deps (network) =="
if ! "${PIP_CMD[@]}" "$WHEEL" >"$WORK/install.log" 2>&1; then
  detail="$(tail -c 300 "$WORK/install.log" | tr '\n' ' ')"
  if [ "$REQUIRE_NETWORK" = 1 ]; then
    fail "wheel install failed (network denied or build backend unavailable): $detail"
  fi
  degrade "wheel install failed (network/build backend?): $detail"
fi

# ---------------------------------------------------------------- entry points
echo "== console entry points =="
for exe in qubit voicebridge qubit-dev qubit-hermes; do
  if ! "$VENV/bin/$exe" --help >"$WORK/$exe.out" 2>&1; then
    fail "entry point '$exe --help' failed: $(tail -c 300 "$WORK/$exe.out" | tr '\n' ' ')"
  fi
  echo "  ok: $exe --help"
done

# ---------------------------------------------------------------- probe
echo "== installed dashboard + runtime probe =="
cat > "$WORK/probe.py" <<'PY'
import http.client
import json
import os
import pathlib
import re
import sys
import tempfile
import time

import yaml

venv = pathlib.Path(os.environ["VB_INSTALL_VENV"]).resolve()
state = pathlib.Path(tempfile.mkdtemp())

import voicebridge
from voicebridge import resources

pkg = pathlib.Path(voicebridge.__file__).resolve()
assert venv in pkg.parents, f"voicebridge not imported from install: {pkg}"

# Package-data completeness: every runtime resource must resolve from the
# installed package data, not a checkout.
need = {
    "frontend_dist": resources.resource_path("frontend/hermes-dist/index.html"),
    "hermes_runtime": resources.resource_path("qubit_hermes/hermes_cli/main.py"),
    "vendor_manifest": resources.resource_path(
        "docs/QUBIT_HERMES_VENDOR_MANIFEST.json"),
    "config_example": resources.resource_path("config/voice.example.yaml"),
    "systemd": resources.resource_path("systemd"),
    "migration_doc": resources.resource_path("docs/QUBIT_MIGRATION.md"),
}
missing = [k for k, v in need.items() if not v.exists()]
assert not missing, f"packaged resources missing: {missing}"

from voicebridge.config import Config
from voicebridge.webui import WebUI

p = state / "voice.yaml"
p.write_text(yaml.safe_dump({
    "logging": {"jsonl_path": str(state)},
    "webui": {"token": "probe-token"},
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
    st, _, body = raw("/hermes/")
    assert st == 200 and b"__HERMES_BASE_PATH__" in body, st
    asset = re.search(rb"/hermes/(assets/[^\"]+\.js)", body).group(1).decode()
    st, _, ab = raw("/hermes/" + asset)
    assert st == 200 and len(ab) > 1000, (asset, st, len(ab))
    st, _, pb = raw("/hermes/api/dashboard/plugins",
                    {"X-Hermes-Session-Token": "probe-token"})
    names = [m["name"] for m in json.loads(pb)]
    for want in ("qubit-ops", "qubit-devices", "qubit-memory",
                 "qubit-reports", "qubit-approvals"):
        assert want in names, (want, names)
finally:
    ui.stop()

# Vendored runtime resolves and imports from the install.
from voicebridge import hermes_runtime as hr

st = hr.status()
assert st["present"] is True and st["importable"] is True, st
root = pathlib.Path(hr.runtime_root()).resolve()
assert venv in root.parents, f"runtime not under install: {root}"
hr.ensure_path()
import hermes_cli.main as m  # noqa: E402  (forced by ensure_path above)
mod = pathlib.Path(m.__file__).resolve()
assert mod.is_relative_to(root), (mod, root)

print(f"OK dashboard plugins={len(names)} runtime={root}")
PY
VB_INSTALL_VENV="$VENV" "$VENV/bin/python" -I "$WORK/probe.py" \
  || fail "startup probe failed"

echo "clean install check: PASS (wheel=$(basename "$WHEEL") venv=$VENV)"
