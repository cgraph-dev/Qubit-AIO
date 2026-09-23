#!/usr/bin/env bash
# Hermetic clean-install rehearsal for the Qubit wheel.
#
# Builds the wheel, installs it (with its declared dependencies) into a
# throwaway venv under /tmp with a throwaway HOME, then proves the INSTALLED
# package — never the checkout — serves the dashboard and imports the
# vendored upstream runtime and every console entry point.
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
# pyproject force-includes the whole frontend/ tree (a frozen mapping) and
# hatchling force-include does NOT honor .gitignore, so a leftover
# frontend/qubit-web/node_modules would ship ~21.6k build-only members
# (~405 MB) into the wheel. Stash it for the duration of the build and restore
# it on exit — never delete a developer's node_modules in a gate.
NODE_MODULES="$REPO_ROOT/frontend/qubit-web/node_modules"
NODE_MODULES_STASH="$WORK/node_modules.stash"
cleanup() {
  if [ -d "$NODE_MODULES_STASH" ]; then mv "$NODE_MODULES_STASH" "$NODE_MODULES"; fi
  rm -rf "$WORK"
}
trap cleanup EXIT

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

SYS_PY="${PYTHON:-python3}"

# ------------------------------------------------- dashboard reproducibility
# frontend/qubit-dist is a git-ignored build artifact: a clean clone cannot
# ship it, so the wheel would silently install a package with no dashboard.
# Build it deterministically here when node/npm are available; otherwise stop
# with a typed DEGRADED (never build a dashboard-less wheel and report PASS).
if [ ! -f "$REPO_ROOT/frontend/qubit-dist/index.html" ]; then
  if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
    echo "== dashboard bundle missing — building deterministically =="
    if ! "$REPO_ROOT/scripts/build_dashboard.sh" >"$WORK/dashboard-build.log" 2>&1; then
      degrade "dashboard build failed (node/npm present but build/network failed); see $WORK/dashboard-build.log"
    fi
  else
    degrade "dashboard bundle frontend/qubit-dist missing and node/npm not found — cannot build from a clean clone; install node/npm or use a release sdist that ships the built dashboard (docs/archive/2026-09-21/CLEAN_CLONE_AUDIT_WAVE5.md)"
  fi
fi

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
if [ -d "$NODE_MODULES" ]; then
  mv "$NODE_MODULES" "$NODE_MODULES_STASH"
fi
if ! "${BUILD_CMD[@]}" >/dev/null 2>"$WORK/build.err"; then
  degrade "wheel build failed: $(tail -c 300 "$WORK/build.err" | tr '\n' ' ')"
fi
WHEEL="$(ls "$DIST"/*.whl 2>/dev/null | head -1 || true)"
[ -n "$WHEEL" ] || fail "wheel not produced by the build"
echo "wheel: $(basename "$WHEEL")"

# The wheel must actually carry the built dashboard; a dashboard-less wheel is
# a typed FAIL, never a PASS.
if ! "$SYS_PY" - "$WHEEL" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as z:
    names = z.namelist()
assert any(n.endswith("_data/frontend/qubit-dist/index.html") for n in names), \
    "wheel is missing voicebridge/_data/frontend/qubit-dist/index.html"
# Force-include ignores .gitignore, so prove the build-only node_modules tree
# never leaked into the wheel (the stash above must have worked).
leaked = [n for n in names if "node_modules/" in n]
assert not leaked, f"wheel swept {len(leaked)} node_modules members"
PY
then
  fail "wheel built without the dashboard bundle or swept build-only node_modules"
fi
echo "  dashboard bundle shipped in wheel; node_modules members: 0"

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
for exe in qubit voicebridge qubit-dev qubit-agent; do
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
    "frontend_dist": resources.resource_path("frontend/qubit-dist/index.html"),
    "qubit_runtime": resources.resource_path("qubit_runtime/qubit_cli/main.py"),
    "vendor_manifest": resources.resource_path(
        "docs/archive/2026-09-21/QUBIT_HERMES_VENDOR_MANIFEST.json"),
    "config_example": resources.resource_path("config/voice.example.yaml"),
    "systemd": resources.resource_path("systemd"),
    "migration_doc": resources.resource_path("docs/archive/2026-09-21/QUBIT_MIGRATION.md"),
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
    assert (st, loc) == (302, "/sessions"), (st, loc)
    st, _, body = raw("/sessions")
    assert st == 200 and b"__HERMES_BASE_PATH__" in body, st
    assert b'__HERMES_BASE_PATH__=""' in body, "base path not root"
    asset = re.search(rb"/(assets/[^\"]+\.js)", body).group(1).decode()
    st, _, ab = raw("/" + asset)
    assert st == 200 and len(ab) > 1000, (asset, st, len(ab))
    st, _, pb = raw("/agent/api/dashboard/plugins",
                    {"X-Hermes-Session-Token": "probe-token"})
    names = [m["name"] for m in json.loads(pb)]
    for want in ("qubit-ops", "qubit-devices", "qubit-memory",
                 "qubit-reports", "qubit-approvals"):
        assert want in names, (want, names)
finally:
    ui.stop()

# Vendored runtime resolves and imports from the install.
from voicebridge import qubit_runtime as hr

st = hr.status()
assert st["present"] is True and st["importable"] is True, st
root = pathlib.Path(hr.runtime_root()).resolve()
assert venv in root.parents, f"runtime not under install: {root}"
hr.ensure_path()
import qubit_cli.main as m  # noqa: E402  (forced by ensure_path above)
mod = pathlib.Path(m.__file__).resolve()
assert mod.is_relative_to(root), (mod, root)

# Real runtime import path: the default service start imports the orchestrator
# (and therefore local_llm) and builds the VAD gate. On a stock install the
# silero VAD model is absent, so voicebridge/audio/vad.py falls back to
# webrtcvad, which imports the legacy pkg_resources API at import time. This
# probe catches both an undeclared setuptools dependency and a Python-floor
# syntax incompatibility that a webui-only import misses.
import voicebridge.orchestrator as _orch  # noqa: E402,F401
from voicebridge.audio.vad import _make_vad_gate  # noqa: E402

_gate = _make_vad_gate("silero", 1, None)
assert callable(_gate), "default VAD gate did not resolve (silero->webrtc fallback)"

print(f"OK dashboard plugins={len(names)} runtime={root} vads=webrtc-fallback")
PY
VB_INSTALL_VENV="$VENV" "$VENV/bin/python" -I "$WORK/probe.py" \
  || fail "startup probe failed"

echo "clean install check: PASS (wheel=$(basename "$WHEEL") venv=$VENV)"
