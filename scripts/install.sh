#!/usr/bin/env bash
# Install Qubit Agent (Qubit VoiceBridge) on a FRESH machine (or re-run to repair).
# Idempotent. Qubit-only: it never installs, launches, or requires a foreign assistant.
#
#   ./scripts/install.sh --help              # options, no changes
#   ./scripts/install.sh --dry-run           # preflight + layout, no writes
#   ./scripts/install.sh                     # venv, deps, verified models, config
#   ./scripts/install.sh --with-systemd      # also install/enable/start user units
#
# Requires: bash, curl, uv (https://astral.sh/uv). Optional: ollama (local
# brain), node/npm (dashboard build when frontend/qubit-dist is absent), and
# sha256sum/shasum/openssl + tar for model assets. Keys (optional, for
# complex/web answers): DEEPSEEK_API_KEY and PERPLEXITY_API_KEY in the
# config dir printed by --dry-run (new installs: $QUBIT_HOME/voice/config;
# existing XDG installs keep ~/.config/qubit-voicebridge read-old).
# See .env.example.
set -euo pipefail

WITH_SYSTEMD=0
WITH_LAUNCHD=0
DRY_RUN=0
WITHOUT_MODELS=0
WITHOUT_DASHBOARD=0
LAUNCHER_ONLY=0
ASSUME_YES=0
PREFIX=""

# Host platform is decided once, before any preflight or path is printed.
# Linux uses systemd user units; macOS uses launchd LaunchAgents for the
# gateway. Anything else is a typed refusal (Windows uses install.ps1).
HOST_SYSTEM="$(uname -s 2>/dev/null || /usr/bin/uname -s 2>/dev/null || echo unknown)"
case "$HOST_SYSTEM" in
  Linux) HOST_OS="linux" ;;
  Darwin) HOST_OS="macos" ;;
  *) HOST_OS="unknown" ;;
esac

usage() {
  cat <<'EOF'
Qubit installer

Usage:
  ./scripts/install.sh [options]

Core support: Linux and macOS. On macOS the core runtime, CLI, dashboard and
the messaging-gateway LaunchAgent are installed; the voice bridge, hotkey
capture, orb and voice-typing are NOT supported on macOS and are reported as
UNSUPPORTED (they are installed only on Linux).

Options:
  -h, --help            show this help without changing the machine
  -s, --with-systemd    install the user services and start them (Linux: systemd
                        user units; macOS: the gateway LaunchAgent only)
  --with-launchd        install the macOS gateway LaunchAgent (typed refusal on
                        Linux, which uses --with-systemd)
  --dry-run             run preflight and print the install layout; make no changes
  --without-models      skip model asset downloads and ollama model provisioning
  --without-dashboard   skip the dashboard bundle check/build (headless installs)
  --prefix DIR          install the new $DIR/voice tree and bin under DIR
                        (QUBIT_PREFIX is the env equivalent); a portable
                        install never adopts an old XDG tree
  -y, --yes             do not prompt before enabling the user services
  --launcher-only       (repair) re-render the `qubit` launcher and exit

Environment:
  QUBIT_HOME            runtime/agent home (default: ~/.qubit, or DIR with
                        --prefix). New installs place VoiceBridge under
                        $QUBIT_HOME/voice; existing XDG installs are kept
                        read-old and are never migrated or deleted here.
  QUBIT_PREFIX          same as --prefix when the flag is absent
  QUBIT_NO_LAUNCHER=1   do not install the `qubit` user launcher

Requires: bash, curl, uv. Optional: ollama (local brain), node/npm (dashboard
build when frontend/qubit-dist is absent), and sha256sum/shasum/openssl + tar
for Linux voice-model assets (unless --without-models). macOS never installs
the voice-model assets because the voice bridge is unsupported there.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -s|--with-systemd)
      WITH_SYSTEMD=1
      ;;
    --with-launchd)
      WITH_LAUNCHD=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --without-models)
      WITHOUT_MODELS=1
      ;;
    --without-dashboard)
      WITHOUT_DASHBOARD=1
      ;;
    -y|--yes)
      ASSUME_YES=1
      ;;
    --prefix)
      if [ "$#" -lt 2 ] || [ -z "${2:-}" ]; then
        echo "option --prefix requires a directory" >&2
        echo "run ./scripts/install.sh --help" >&2
        exit 2
      fi
      PREFIX="$2"
      shift
      ;;
    --prefix=*)
      PREFIX="${1#*=}"
      if [ -z "$PREFIX" ]; then
        echo "option --prefix requires a directory" >&2
        echo "run ./scripts/install.sh --help" >&2
        exit 2
      fi
      ;;
    --launcher-only|--install-launcher)
      LAUNCHER_ONLY=1
      ;;
    --)
      shift
      if [ "$#" -gt 0 ]; then
        echo "unexpected argument: $1" >&2
        echo "run ./scripts/install.sh --help" >&2
        exit 2
      fi
      ;;
    -*)
      echo "unknown option: $1" >&2
      echo "run ./scripts/install.sh --help" >&2
      exit 2
      ;;
    *)
      echo "unexpected argument: $1" >&2
      echo "run ./scripts/install.sh --help" >&2
      exit 2
      ;;
  esac
  shift
done

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --------------------------------------------------------------- layout
# VoiceBridge has ONE path resolver (``voicebridge.paths``): new installs live
# under ``$QUBIT_HOME/voice/{config,data,state,cache,models}`` and an existing
# XDG install keeps its paths in place, read-old. The installer never moves or
# deletes an existing tree; it only initializes the new one when this machine
# has no old data (or when --prefix asks for a portable install).
#
# --prefix wins, then QUBIT_PREFIX; without either, QUBIT_HOME is the runtime
# home (default ~/.qubit) the new tree is placed under.
QUBIT_HOME_EXPLICIT=0
if [ -n "${QUBIT_HOME:-}" ]; then
  QUBIT_HOME_EXPLICIT=1
fi
if [ -z "$PREFIX" ]; then
  PREFIX="${QUBIT_PREFIX:-}"
fi
if [ -n "$PREFIX" ]; then
  case "$PREFIX" in
    /*) ;;
    *) PREFIX="$(pwd)/$PREFIX" ;;
  esac
  case "$PREFIX" in
    /) ;;
    */) PREFIX="${PREFIX%/}" ;;
  esac
  LAUNCHER_DIR="$PREFIX/bin"
  # Child tooling (uv, the dashboard build, systemd unit install) keeps the
  # prefix-scoped XDG view. The VoiceBridge layout itself is resolved below.
  export XDG_DATA_HOME="$PREFIX/share"
  export XDG_CONFIG_HOME="$PREFIX/config"
  export XDG_STATE_HOME="$PREFIX/state"
  export XDG_CACHE_HOME="$PREFIX/cache"
else
  LAUNCHER_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
fi
QUBIT_HOME="${QUBIT_HOME:-${PREFIX:-$HOME/.qubit}}"
export QUBIT_HOME
# A release bootstrap runs the installer from a temporary extraction. Keep the
# venv under the persistent QUBIT_HOME so the launcher survives that cleanup.
if [ "${QUBIT_BOOTSTRAP:-0}" = "1" ]; then
  VENV="$QUBIT_HOME/.venv"
else
  VENV="$PROJECT_DIR/.venv"
fi

# Resolver output (kept in this one function so the printed layout and the
# paths written below cannot disagree):
#   VOICE_LAYOUT_MODE   new | read-old | uninitialized
#   VOICE_INSTALL_MODE  new | read-old
#   VOICE_NEW_* / VOICE_OLD_*
resolve_voice_layout() {
  local py="" out=""
  if [ -x "$VENV/bin/python" ]; then
    py="$VENV/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    py="$(command -v python3)"
  elif command -v python >/dev/null 2>&1; then
    py="$(command -v python)"
  fi
  if [ -n "$py" ]; then
    if out="$(PYTHONPATH="$PROJECT_DIR${PYTHONPATH:+:$PYTHONPATH}" "$py" -c '
import shlex
from voicebridge.paths import voice_layout
lay = voice_layout()
print("VOICE_LAYOUT_MODE=" + shlex.quote(lay.mode))
print("VOICE_INSTALL_MODE=" + shlex.quote(lay.install_mode))
for kind, path in lay.new.items():
    print("VOICE_NEW_%s=%s" % (kind.upper(), shlex.quote(str(path))))
for kind, path in lay.read_old.items():
    print("VOICE_OLD_%s=%s" % (kind.upper(), shlex.quote(str(path))))
' 2>/dev/null)" && [ -n "$out" ]; then
      eval "$out"
      return 0
    fi
  fi

  # Mirror of voicebridge.paths.voice_layout() for hosts where no Python
  # exists yet (the installer may be the thing that creates the venv).
  local new_root="$QUBIT_HOME/voice"
  VOICE_NEW_CONFIG="$new_root/config"
  VOICE_NEW_DATA="$new_root/data"
  VOICE_NEW_STATE="$new_root/state"
  VOICE_NEW_CACHE="$new_root/cache"
  VOICE_NEW_MODELS="$new_root/models"
  VOICE_OLD_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/qubit-voicebridge"
  VOICE_OLD_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/qubit-voicebridge"
  VOICE_OLD_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/qubit-voicebridge"
  VOICE_OLD_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/qubit-voicebridge"
  VOICE_OLD_MODELS="$VOICE_OLD_DATA/models"
  if [ -d "$new_root" ]; then
    VOICE_LAYOUT_MODE="new"
    VOICE_INSTALL_MODE="new"
  elif [ -d "$VOICE_OLD_CONFIG" ] || [ -d "$VOICE_OLD_DATA" ] \
       || [ -d "$VOICE_OLD_STATE" ] || [ -d "$VOICE_OLD_CACHE" ] \
       || [ -d "$VOICE_OLD_MODELS" ]; then
    VOICE_LAYOUT_MODE="read-old"
    VOICE_INSTALL_MODE="read-old"
  else
    # No old data: this is a new install. Both the installer and the runtime
    # write ``$QUBIT_HOME/voice``; neither creates a fresh XDG tree that a
    # later run would misread as an existing install.
    VOICE_LAYOUT_MODE="uninitialized"
    VOICE_INSTALL_MODE="new"
  fi
}

resolve_voice_layout
# A portable --prefix install is always a new tree under its QUBIT_HOME; it
# never adopts an unrelated old XDG tree in the real HOME.
if [ -n "$PREFIX" ]; then
  VOICE_INSTALL_MODE="new"
fi
if [ "$VOICE_INSTALL_MODE" = "new" ]; then
  CONFIG_DIR="$VOICE_NEW_CONFIG"
  DATA_DIR="$VOICE_NEW_DATA"
  STATE_DIR="$VOICE_NEW_STATE"
  CACHE_DIR="$VOICE_NEW_CACHE"
  MODELS="$VOICE_NEW_MODELS"
else
  CONFIG_DIR="$VOICE_OLD_CONFIG"
  DATA_DIR="$VOICE_OLD_DATA"
  STATE_DIR="$VOICE_OLD_STATE"
  CACHE_DIR="$VOICE_OLD_CACHE"
  MODELS="$VOICE_OLD_MODELS"
fi
OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
DASHBOARD_STATE="present"
# macOS gateway supervision lives under the account LaunchAgents directory.
# The runtime owns the plist body and label (ai.qubit.gateway[-<profile>]);
# this path is printed so the install is predictable before it runs.
LAUNCH_AGENTS_DIR="${QUBIT_LAUNCH_AGENTS_DIR:-$HOME/Library/LaunchAgents}"

# The runtime owns the LaunchAgent label and path: a profile or other
# non-default QUBIT_HOME gets a home-scoped `ai.qubit.gateway-<suffix>.plist`
# under the ACCOUNT home. Ask the runtime once its venv exists (repair runs and
# post-install output); before that, the default-home path is the only
# predictable plan. Hardcoding the default path made `--prefix` installs claim a
# plist the runtime never writes.
launchd_plist_path() {
  if [ -x "$VENV/bin/python" ]; then
    "$VENV/bin/python" -c 'from voicebridge import qubit_runtime; qubit_runtime.ensure_path(); from qubit_cli import gateway; print(gateway.get_launchd_plist_path())' 2>/dev/null && return 0
  fi
  printf '%s\n' "$LAUNCH_AGENTS_DIR/ai.qubit.gateway.plist"
}

launchd_plist_display() {
  if [ -x "$VENV/bin/python" ] || [ "$QUBIT_HOME" = "$HOME/.qubit" ]; then
    launchd_plist_path
  else
    printf '%s/ai.qubit.gateway-<home-scoped>.plist\n' "$LAUNCH_AGENTS_DIR"
  fi
}

# Child processes (uv, the runtime gateway installer, the bridge) must resolve
# the same home the layout prints; without this the launchd plist could pin
# ~/.qubit while the installer claims another QUBIT_HOME.
export QUBIT_HOME

if [ "$WITH_LAUNCHD" = "1" ] && [ "$HOST_OS" != "macos" ]; then
  echo "UNSUPPORTED_PLATFORM: --with-launchd is macOS-only." >&2
  echo "  Linux uses the systemd gateway unit: --with-systemd." >&2
  exit 2
fi
if [ "$HOST_OS" = "unknown" ]; then
  echo "UNSUPPORTED_PLATFORM: $HOST_SYSTEM is not a supported install target." >&2
  echo "  Supported: Linux (systemd) and macOS (LaunchAgent gateway). On Windows use install.ps1." >&2
  exit 2
fi

SR="https://github.com/k2-fsa/sherpa-onnx/releases/download"

# --------------------------------------------------------------- preflight
MISSING_REQUIRED=0

have() { command -v "$1" >/dev/null 2>&1; }

check_required() { # check_required <tool> <why>
  local name="$1" why="${2:-}"
  if have "$name"; then
    printf '    OK       %-10s %s\n' "$name" "$why"
  else
    printf '    MISSING  %-10s %s\n' "$name" "$why"
    MISSING_REQUIRED=1
  fi
}

check_optional() { # check_optional <tool> <why>
  local name="$1" why="${2:-}"
  if have "$name"; then
    printf '    OK       %-10s %s\n' "$name" "$why"
  else
    printf '    DEGRADED %-10s missing: %s\n' "$name" "$why"
  fi
}

preflight_platform() {
  case "$HOST_OS" in
    linux)
      echo "    OK       platform   Linux ($(uname -m 2>/dev/null || echo unknown))"
      echo "    OK       gateway    systemd user unit"
      ;;
    macos)
      local _ver="unknown" _arch="unknown"
      _arch="$(uname -m 2>/dev/null || echo unknown)"
      if have sw_vers; then
        _ver="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
      fi
      echo "    OK       platform   macOS $_ver ($_arch)"
      echo "    OK       gateway    launchd LaunchAgent ($(launchd_plist_display))"
      check_required launchctl "loads and supervises the gateway LaunchAgent"
      echo "    INFO     voice      UNSUPPORTED on macOS (voice bridge, hotkey, orb, voice-typing)"
      ;;
    *)
      echo "    MISSING  platform   $HOST_SYSTEM (supported: Linux, macOS)"
      MISSING_REQUIRED=1
      ;;
  esac
}

preflight() {
  echo "==> preflight"
  preflight_platform
  check_required bash "the installer is a bash script"
  check_required curl "downloads model assets (skip with --without-models)"
  check_required uv "creates the Python venv and installs the package"

  if [ "$HOST_OS" = "macos" ]; then
    echo "    SKIP     models     voice-model assets are Linux-only (voice bridge unsupported on macOS)"
    if [ "$WITHOUT_MODELS" = "1" ]; then
      echo "    SKIP     ollama     model provisioning skipped (--without-models)"
    else
      check_optional ollama "local brain; DeepSeek API still works without it"
    fi
  elif [ "$WITHOUT_MODELS" = "1" ]; then
    echo "    SKIP     ollama     model provisioning skipped (--without-models)"
    echo "    SKIP     models     model assets skipped (--without-models)"
  else
    check_optional ollama "local brain; DeepSeek API still works without it"
    if have sha256sum || have shasum || have openssl; then
      echo "    OK       checksum   sha256 tool present (model integrity)"
    else
      echo "    MISSING  checksum   need sha256sum, shasum, or openssl to verify model assets"
      MISSING_REQUIRED=1
    fi
    check_required tar "extracts the sherpa-onnx model archives"
  fi

  if [ "$WITHOUT_DASHBOARD" = "1" ]; then
    echo "    SKIP     dashboard  dashboard bundle skipped (--without-dashboard)"
  elif [ -f "$PROJECT_DIR/frontend/qubit-dist/index.html" ]; then
    echo "    OK       dashboard  frontend/qubit-dist present"
  else
    check_optional node "dashboard build (frontend/qubit-dist is missing)"
    check_optional npm "dashboard build (frontend/qubit-dist is missing)"
  fi

  if [ "$MISSING_REQUIRED" = "1" ]; then
    echo "preflight failed: install the MISSING tools above and re-run." >&2
    return 1
  fi
}

print_layout() {
  echo "==> Qubit Agent install layout"
  case "$HOST_OS" in
    linux) printf '    platform  : Linux (%s)\n' "$(uname -m 2>/dev/null || echo unknown)" ;;
    macos) printf '    platform  : macOS (%s)\n' "$(uname -m 2>/dev/null || echo unknown)" ;;
    *)     printf '    platform  : %s (unsupported)\n' "$HOST_SYSTEM" ;;
  esac
  printf '    project   : %s\n' "$PROJECT_DIR"
  printf '    venv      : %s\n' "$VENV"
  if [ -n "$PREFIX" ]; then
    printf '    prefix    : %s\n' "$PREFIX"
    echo "    note      : export QUBIT_HOME for the installed service (new layout)"
  else
    printf '    prefix    : (QUBIT_HOME layout)\n'
  fi
  printf '    QUBIT_HOME: %s\n' "$QUBIT_HOME"
  if [ "$VOICE_INSTALL_MODE" = "read-old" ]; then
    printf '    layout    : read-old (existing XDG install preserved; no migration)\n'
    printf '    new-tree  : %s/voice (not initialized)\n' "$QUBIT_HOME"
  else
    printf '    layout    : new (%s/voice)\n' "$QUBIT_HOME"
  fi
  printf '    data      : %s\n' "$DATA_DIR"
  if [ "$HOST_OS" = "macos" ]; then
    echo "    models    : not installed (voice unsupported on macOS)"
  else
    printf '    models    : %s\n' "$MODELS"
  fi
  printf '    config    : %s/voice.yaml\n' "$CONFIG_DIR"
  printf '    state     : %s\n' "$STATE_DIR"
  printf '    cache     : %s\n' "$CACHE_DIR"
  printf '    launcher  : %s/qubit\n' "$LAUNCHER_DIR"
  if [ "$HOST_OS" = "macos" ]; then
    printf '    gateway   : %s (LaunchAgent)\n' "$(launchd_plist_display)"
    if [ "$QUBIT_HOME" != "$HOME/.qubit" ] && [ ! -x "$VENV/bin/python" ]; then
      echo "    note      : the gateway label is home-scoped; --with-launchd reports the exact plist"
    fi
    echo "    voice     : UNSUPPORTED on macOS (no voicebridge/orb/typing service)"
  fi
  if [ "$QUBIT_HOME_EXPLICIT" = "1" ] && [ -n "$PREFIX" ] && [ "$QUBIT_HOME" != "$PREFIX" ]; then
    echo "    note      : QUBIT_HOME is explicit and differs from the prefix; runtime state stays at QUBIT_HOME"
  fi
  if [ "$WITHOUT_MODELS" = "1" ]; then
    echo "    models    : skipped (--without-models)"
  fi
  if [ "$WITHOUT_DASHBOARD" = "1" ]; then
    echo "    dashboard : skipped (--without-dashboard)"
  fi
  if [ "$DRY_RUN" = "1" ]; then
    echo "    mode      : dry-run (no changes)"
  else
    echo "    mode      : install"
  fi
}

# --------------------------------------------------------------- launcher
# Install the user-facing `qubit` shim that delegates to THIS checkout's
# canonical console script ($VENV/bin/qubit, generated from pyproject
# [project.scripts] "qubit = voicebridge.cli:main"). The authoritative repo
# path is resolved and baked at install time (never a stale/foreign checkout),
# with a $QUBIT_REPO env override and a PATH-discovery fallback at runtime.
# Idempotent; a divergent existing shim is backed up, never silently clobbered.
install_launcher() {
  local target="$LAUNCHER_DIR/qubit"
  mkdir -p "$LAUNCHER_DIR"
  local tmp="$LAUNCHER_DIR/.qubit.launcher.$$"
  cat >"$tmp" <<'QUBIT_LAUNCHER'
#!/usr/bin/env bash
# Qubit launcher — generated by scripts/install.sh.
# Delegates to the installed console script baked in at install time (or a
# QUBIT_REPO override). It never points at a foreign/stale checkout and never
# opens a second dashboard/bridge.
set -euo pipefail

# Qubit home: pinned by the installer for a --prefix / explicit-home install.
__QUBIT_HOME_PIN__

repo_for() {
  # Each branch prints the directory CONTAINING the real `qubit` console script.
  # 1) explicit operator override
  if [ -n "${QUBIT_REPO:-}" ] && [ -x "$QUBIT_REPO/.venv/bin/qubit" ]; then
    printf '%s\n' "$QUBIT_REPO/.venv/bin"; return 0
  fi
  # 2) authoritative venv baked in at install time
  if [ -x "__QUBIT_VENV_BIN__/qubit" ]; then
    printf '%s\n' "__QUBIT_VENV_BIN__"; return 0
  fi
  # 3) a real console script elsewhere on PATH (skipping this shim's own dir)
  local self_dir d
  self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local IFS=:
  for d in ${PATH:-}; do
    [ -n "$d" ] || continue
    if [ "$d" = "$self_dir" ]; then
      continue
    fi
    if [ -x "$d/qubit" ] && ! [ "$d/qubit" -ef "$self_dir/qubit" ]; then
      printf '%s\n' "$d"; return 0
    fi
  done
  return 1
}

if _repo="$(repo_for)"; then
  exec "$_repo/qubit" "$@"
fi
echo "qubit: no Qubit installation found." >&2
echo "  Run scripts/install.sh in the authoritative checkout, or set QUBIT_REPO." >&2
exit 127
QUBIT_LAUNCHER
  # Pin the resolved home only for a portable/explicit install. A default
  # install keeps resolving ~/.qubit at run time (so moving HOME still works),
  # but a --prefix install must not silently read the real HOME's ~/.qubit:
  # INSTALL.md documents --prefix as equivalent to QUBIT_HOME=DIR. An explicit
  # QUBIT_HOME in the environment still wins over the baked default.
  local launcher_home=""
  if [ -n "$PREFIX" ] || [ "$QUBIT_HOME_EXPLICIT" = "1" ]; then
    launcher_home="$QUBIT_HOME"
    case "$launcher_home" in
      "~") launcher_home="$HOME" ;;
      "~/"*) launcher_home="$HOME/${launcher_home#\~/}" ;;
    esac
    case "$launcher_home" in
      /*) ;;
      *) launcher_home="$(pwd)/$launcher_home" ;;
    esac
  fi
  local venv_bin_sed home_sed
  venv_bin_sed="$(printf '%s' "$VENV/bin" | sed -e 's/[\\&|]/\\&/g')"
  if [ -n "$launcher_home" ]; then
    # `export`, not a bare assignment: the launcher execs the real console
    # script, which only sees an exported QUBIT_HOME.
    home_sed="$(printf '%s' "export QUBIT_HOME=\"\${QUBIT_HOME:-$launcher_home}\"" | sed -e 's/[\\&|]/\\&/g')"
  else
    home_sed="# QUBIT_HOME resolves from the environment, defaulting to ~/.qubit."
  fi
  # Render to a NEW file instead of `sed -i`: BSD/macOS sed requires an
  # explicit backup suffix, so GNU-style in-place editing breaks the supported
  # macOS install at this exact step.
  sed -e "s|__QUBIT_VENV_BIN__|$venv_bin_sed|g" \
      -e "s|__QUBIT_HOME_PIN__|$home_sed|g" "$tmp" > "$tmp.rendered"
  mv -f "$tmp.rendered" "$tmp"
  if grep -q '__QUBIT_VENV_BIN__' "$tmp" || grep -q '__QUBIT_HOME_PIN__' "$tmp"; then
    echo "ERROR: launcher render left an unexpanded placeholder" >&2
    rm -f "$tmp"; return 1
  fi
  if [ -e "$target" ] && ! cmp -s "$target" "$tmp"; then
    local backup="$target.bak.$(date +%Y%m%d-%H%M%S)"
    cp -p "$target" "$backup"
    echo "    backed up previous launcher -> $backup"
  fi
  chmod 0755 "$tmp"
  mv -f "$tmp" "$target"
  echo "==> launcher installed: $target -> $VENV/bin/qubit"
}

# ------------------------------------------------------------ venv/package
install_venv_and_package() {
  if [ "$DRY_RUN" = "1" ]; then
    if [ -x "$VENV/bin/python" ]; then
      echo "==> [dry-run] venv already present: $VENV"
    else
      echo "==> [dry-run] would create venv (uv) and install package + declared dependencies"
    fi
    if [ "${QUBIT_NO_LAUNCHER:-0}" != "1" ]; then
      echo "==> [dry-run] would install the user launcher: $LAUNCHER_DIR/qubit"
    fi
    return 0
  fi

  if [ ! -x "$VENV/bin/python" ]; then
    echo "==> creating venv (uv)"
    command -v uv >/dev/null || { echo "ERROR: uv not found — install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
    uv venv --python 3.12 "$VENV"
  fi

  echo "==> installing Qubit package + declared dependencies (pyproject.toml)"
  # Install the package ITSELF so the venv is a real isolated install whose
  # dependencies come from pyproject.toml (never a hand-maintained list that
  # drifts). hotkey/edge/test are the optional runtime surfaces.
  uv pip install --python "$VENV/bin/python" "$PROJECT_DIR[hotkey,edge,test]"
  # webrtcvad needs the legacy pkg_resources API (removed in setuptools 81+)
  uv pip install --python "$VENV/bin/python" 'setuptools<81'

  # Make the canonical `qubit` console script reachable from any shell/terminal
  # (and repair a stale operator shim). QUBIT_NO_LAUNCHER=1 opts out.
  if [ "${QUBIT_NO_LAUNCHER:-0}" != "1" ]; then
    install_launcher
  fi
}

# --------------------------------------------------------------- dashboard
# The dashboard bundle (frontend/qubit-dist) is a build artifact and is not
# tracked in git; a fresh checkout/sdist without it would install a package
# that cannot serve the dashboard. Build it deterministically when node/npm are
# available; otherwise record a typed DEGRADED state that fails loudly at the
# end of the install (never a silent skip, never a dashboard-less PASS).
install_dashboard() {
  DASHBOARD_STATE="present"
  if [ "$WITHOUT_DASHBOARD" = "1" ]; then
    DASHBOARD_STATE="skipped"
    echo "==> dashboard skipped (--without-dashboard)"
    return 0
  fi
  if [ -f "$PROJECT_DIR/frontend/qubit-dist/index.html" ]; then
    return 0
  fi
  if have node && have npm; then
    if [ "$DRY_RUN" = "1" ]; then
      DASHBOARD_STATE="would_build"
      echo "==> [dry-run] would build dashboard (frontend/qubit-dist missing)"
      return 0
    fi
    echo "==> building dashboard (frontend/qubit-dist missing)"
    if "$PROJECT_DIR/scripts/build_dashboard.sh"; then
      DASHBOARD_STATE="built"
    else
      DASHBOARD_STATE="build_failed"
      echo "    WARNING: frontend/qubit-dist missing — the dashboard build failed;"
      echo "             re-run scripts/build_dashboard.sh once the toolchain/network is fixed"
    fi
  else
    DASHBOARD_STATE="toolchain_missing"
    echo "    WARNING: frontend/qubit-dist missing and node/npm not found —"
    echo "             the dashboard will be unavailable until you run scripts/build_dashboard.sh"
  fi
}

# ---------------------------------------------------- download integrity
# Every network download is verified against a PINNED sha256 before it is
# installed. A mismatch (including a truncated download) is a TYPED failure
# (exit 9) and the partial file is removed — never a silent continue.
#
# The digests below are pinned constants per asset, NOT fetched at install time
# from the same host, so a compromised mirror cannot satisfy both the download
# and the check. Provenance per asset is named in the comments below:
#   * sherpa-onnx assets: the release `checksum.txt` (upstream-published);
#   * the piper `.onnx`: the HuggingFace LFS sha256 oid (upstream-published);
#   * piper `.onnx.json` and silero VAD v6: sha256 observed on the pinned
#     upstream URL at authoring time (upstream publishes no digest for them).
#
# Assets for which upstream publishes no checksum at all are still handled by
# `dl <dest> <url> -`: they download to a temp file, print the OBSERVED sha256
# as a loud WARNING, and are explicitly NOT claimed as verified. No current
# asset uses that fallback path.
VOICE_SHA256="b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18"
VOICE_JSON_SHA256="95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77"
MOON_SHA256="43232c1d13013d37317163baec3135bd771a186a4356f28c889bab453bb0e891"
PKT_SHA256="5793d0fd397c5778d2cf2126994d58e9d56b1be7c04d13c7a15bb1b4eafb16bf"
SILERO_V5_SHA256="6b99cbfd39246b6706f98ec13c7c50c6b299181f2474fa05cbc8046acc274396"
SILERO_V6_SHA256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
ERES2NET_SHA256="c59158379255ad66e161679cca6af8d52d51e389e3224ab7d7a7baae295c2db5"

_sha256_hex() { # _sha256_hex <file> -> lowercase sha256 hex on stdout
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | sed 's/.*= *//'
  else
    echo "ASSET_HASH_TOOL_MISSING: need sha256sum, shasum, or openssl" >&2
    return 1
  fi
}

verify_sha256() { # verify_sha256 <file> <expected_hex> -> 0 ok / non-zero mismatch
  local file="$1" expected="$2" actual=""
  if command -v sha256sum >/dev/null 2>&1; then
    if printf '%s  %s\n' "$expected" "$file" | sha256sum -c - >/dev/null 2>&1; then
      return 0
    fi
  else
    actual="$(_sha256_hex "$file" 2>/dev/null || true)"
    if [ -n "$actual" ] && [ "$actual" = "$expected" ]; then
      return 0
    fi
  fi
  [ -n "$actual" ] || actual="$(_sha256_hex "$file" 2>/dev/null || echo '<unavailable>')"
  echo "ASSET_CHECKSUM_MISMATCH: $(basename "$file")" >&2
  echo "  expected sha256: $expected" >&2
  echo "  actual   sha256: $actual" >&2
  return 1
}

dl() { # dl <dest> <url> <expected_sha256|-> [<source-label>]
  local dest="$1" url="$2" expected="$3" label="${4:-}"
  if [ -f "$dest" ]; then
    if [ "$expected" = "-" ]; then
      echo "    have   $(basename "$dest")"
      return 0
    fi
    if verify_sha256 "$dest" "$expected"; then
      echo "    have   $(basename "$dest") (sha256 verified)"
      return 0
    fi
    echo "    WARNING: existing $(basename "$dest") failed its pinned sha256 — re-downloading" >&2
  fi
  echo "==> downloading $(basename "$dest")${label:+ [$label]}"
  local tmp="$dest.tmp.$$"
  curl -sL -o "$tmp" "$url" || {
    rm -f "$tmp"
    echo "ASSET_DOWNLOAD_FAILED: $(basename "$dest") from $url" >&2
    exit 9
  }
  if [ "$expected" = "-" ]; then
    local observed
    observed="$(_sha256_hex "$tmp")" || { rm -f "$tmp"; exit 9; }
    echo "    WARNING: upstream publishes no checksum for $(basename "$dest");" >&2
    echo "             observed sha256=$observed (recorded, NOT verified)" >&2
  elif ! verify_sha256 "$tmp" "$expected"; then
    rm -f "$tmp"
    echo "ASSET_CHECKSUM_MISMATCH: refusing to install $(basename "$dest") (exit 9)" >&2
    exit 9
  fi
  mv -f "$tmp" "$dest"
}

install_model_assets() {
  if [ "$HOST_OS" = "macos" ]; then
    echo "==> voice-model assets skipped on macOS (voice bridge is unsupported; core CLI/dashboard/gateway need no mic/TTS models)"
    return 0
  fi
  if [ "$WITHOUT_MODELS" = "1" ]; then
    echo "==> models skipped (--without-models)"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    echo "==> [dry-run] would verify/download sha256-pinned model assets under $MODELS"
    return 0
  fi

  mkdir -p "$MODELS"

  # piper voice (TTS)
  VOICE="en_US-amy-medium"
  # sha256: .onnx = HuggingFace LFS oid (v1.0.0); .onnx.json = observed on the
  # pinned v1.0.0 URL (HuggingFace publishes no sha256 for the small JSON).
  dl "$MODELS/$VOICE.onnx" \
     "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/$VOICE.onnx" \
     "$VOICE_SHA256" "piper-voices v1.0.0"
  dl "$MODELS/$VOICE.onnx.json" \
     "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/amy/medium/$VOICE.onnx.json" \
     "$VOICE_JSON_SHA256" "piper-voices v1.0.0"

  # sherpa-onnx ASR: Moonshine base (CPU, 0 VRAM) — fallback engine
  # sha256 from the sherpa-onnx `asr-models` release checksum.txt.
  MOON="sherpa-onnx-moonshine-base-en-quantized-2026-02-27"
  MOON_TAR="$MODELS/.$MOON.tar.bz2"
  if [ ! -d "$MODELS/$MOON" ]; then
    echo "==> downloading Moonshine ASR (~200 MB)"
    dl "$MOON_TAR" "$SR/asr-models/$MOON.tar.bz2" "$MOON_SHA256" "sherpa-onnx asr-models"
    (cd "$MODELS" && tar xjf "$MOON_TAR" && rm -f "$MOON_TAR")
  else
    echo "    have   $MOON"
  fi

  # sherpa-onnx ASR: Parakeet-TDT 0.6B v3 int8 (CPU, 0 VRAM) — DEFAULT engine
  # (asr.engine: parakeet). 25 European languages incl. Romanian, max accuracy.
  # sha256 from the sherpa-onnx `asr-models` release checksum.txt.
  PKT="sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8"
  PKT_TAR="$MODELS/.$PKT.tar.bz2"
  if [ ! -d "$MODELS/$PKT" ]; then
    echo "==> downloading Parakeet-TDT ASR (~640 MB)"
    dl "$PKT_TAR" "$SR/asr-models/$PKT.tar.bz2" "$PKT_SHA256" "sherpa-onnx asr-models"
    (cd "$MODELS" && tar xjf "$PKT_TAR" && rm -f "$PKT_TAR")
  else
    echo "    have   $PKT"
  fi
  # sherpa 1.13.6 hard-errors without vocab_size/context_size metadata on the
  # decoder/joiner ONNX — patch them in if missing (idempotent). Requires the
  # optional 'onnx' package; skipping the patch only risks a sherpa load error,
  # never a broken install.
  if ! "$VENV/bin/python" -c 'import onnx' 2>/dev/null; then
    echo "    [warn] onnx not installed — skipping Parakeet metadata patch"
  else
  "$VENV/bin/python" - "$MODELS/$PKT" <<'PYEOF'
import sys, onnx
from pathlib import Path
base = Path(sys.argv[1])
n_tokens = len((base / "tokens.txt").read_text().splitlines())
for fname in ("decoder.int8.onnx", "joiner.int8.onnx"):
    p = base / fname
    if not p.exists():
        continue
    m = onnx.load(str(p))
    names = {x.key for x in m.metadata_props}
    changed = False
    if "vocab_size" not in names:
        m.metadata_props.add().key = "vocab_size"; m.metadata_props[-1].value = str(n_tokens)
        changed = True
    if "context_size" not in names:
        m.metadata_props.add().key = "context_size"; m.metadata_props[-1].value = "1"
        changed = True
    if changed:
        onnx.save(m, str(p))
        print(f"    patched {fname} metadata (vocab_size={n_tokens}, context_size=1)")
PYEOF
  fi

  # Silero VAD v5 (sherpa-onnx) — sha256 from that release's checksum.txt.
  dl "$MODELS/silero_vad_v5.onnx" "$SR/asr-models/silero_vad_v5.onnx" \
     "$SILERO_V5_SHA256" "sherpa-onnx asr-models"
  # Silero VAD v6 (Aug 2025: ~16% fewer errors than v5 on noisy real data) —
  # the bridge prefers v6 and falls back to v5 automatically.
  # sha256 observed on the pinned v6.2.1 raw URL (upstream publishes no digest).
  dl "$MODELS/silero_vad_v6.onnx" \
     "https://raw.githubusercontent.com/snakers4/silero-vad/v6.2.1/src/silero_vad/data/silero_vad.onnx" \
     "$SILERO_V6_SHA256" "silero-vad v6.2.1"

  # Speaker verification (voice-ID): eres2net English
  # sha256 from the sherpa-onnx `speaker-recongition-models` checksum.txt.
  dl "$MODELS/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx" \
     "$SR/speaker-recongition-models/3dspeaker_speech_eres2net_sv_en_voxceleb_16k.onnx" \
     "$ERES2NET_SHA256" "sherpa-onnx speaker-recongition-models"

  # Optional: Parakeet-TDT (max ASR accuracy) and Kokoro (quality TTS) —
  # uncomment if you want them; config knobs: asr.engine / tts.provider.
  # curl -sL -o /tmp/p.tar.bz2 "$SR/asr-models/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2" && (cd "$MODELS" && tar xjf /tmp/p.tar.bz2 && rm /tmp/p.tar.bz2)
  # curl -sL -o /tmp/k.tar.bz2 "$SR/tts-models/kokoro-int8-en-v0_19.tar.bz2" && (cd "$MODELS" && tar xjf /tmp/k.tar.bz2 && rm /tmp/k.tar.bz2)
}

# ------------------------------------------------------------------ ollama
install_ollama_models() {
  if [ "$WITHOUT_MODELS" = "1" ]; then
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    if have ollama; then
      echo "==> [dry-run] would ensure ollama models: nomic-embed-text, qubit-brain"
    else
      echo "==> [dry-run] ollama missing: local brain/embeddings would be DEGRADED"
    fi
    return 0
  fi

  echo "==> checking ollama"
  if command -v ollama >/dev/null; then
    if ! ollama list >/dev/null 2>&1; then
      echo "    (starting ollama daemon…)"
      (ollama serve >/dev/null 2>&1 &) || true
      for i in $(seq 1 15); do ollama list >/dev/null 2>&1 && break; sleep 1; done
    fi
    # Optional EN-only smart mode. NOT the default brain (that is
    # ``qubit-brain``, built below); pull only when the operator opts in.
    if [ "${QUBIT_OPTIONAL_PHI4:-0}" = "1" ]; then
      if ! ollama list | grep -q "huihui_ai/phi4-mini-abliterated"; then
        echo "==> pulling huihui_ai/phi4-mini-abliterated (optional EN-only smart mode, 3.1GB)"
        ollama pull huihui_ai/phi4-mini-abliterated
      else
        echo "    have   huihui_ai/phi4-mini-abliterated"
      fi
    fi
    # NOTE: the 3B alias (huihui_ai/llama3.2-abliterate:3b) is retired and is
    # intentionally not installed; the configured default is qubit-brain below.
    # Qubit gateway memory embeddings need this tiny model (274 MB CPU) —
    # the gateway spins on 'memory embeddings retryable error' without it
    if ! ollama list | grep -q "nomic-embed-text"; then
      echo "==> pulling nomic-embed-text (gateway memory embeddings, 274 MB)"
      ollama pull nomic-embed-text
    else
      echo "    have   nomic-embed-text"
    fi
    # Qubit's configured default brain for chat/intent/summary. Build it from the
    # pinned Modelfile when absent so a fresh install has the model the config
    # actually names (no hand-baked step needed).
    # Prefer the machine-local bake artifact; fall back to the tracked, sanitized
    # persona fixture so a clean clone can still build the configured default.
    BRAIN_MODEFILE="$PROJECT_DIR/Modelfile.qubit4"
    if [ ! -f "$BRAIN_MODEFILE" ]; then
      BRAIN_MODEFILE="$PROJECT_DIR/tests/fixtures/modelfiles/qubit4.sanitized"
    fi
    if ! ollama list | grep -q "^qubit-brain"; then
      if [ -f "$BRAIN_MODEFILE" ]; then
        echo "==> creating qubit-brain (Qwen3-4B default brain)"
        ollama create qubit-brain -f "$BRAIN_MODEFILE" \
          || echo "    WARNING: could not create qubit-brain; run 'ollama create qubit-brain -f $BRAIN_MODEFILE'"
      else
        echo "    WARNING: no Modelfile found for qubit-brain; run 'ollama create qubit-brain -f Modelfile.qubit4'"
      fi
    else
      echo "    have   qubit-brain"
    fi
  else
    echo "    WARNING: ollama not found — install https://ollama.com then re-run"
    echo "    (local short replies need qubit-brain; DeepSeek API still works)"
  fi
}

# ------------------------------------------------------------------ layout
# Initialize the new ``$QUBIT_HOME/voice`` tree once. A read-old install is
# never initialized, migrated, or touched by this step.
install_voice_layout() {
  if [ "$VOICE_INSTALL_MODE" = "read-old" ]; then
    echo "==> existing VoiceBridge install detected (read-old XDG paths)"
    echo "    preserved in place: $CONFIG_DIR"
    echo "    new layout not initialized: $QUBIT_HOME/voice (migration deferred)"
    return 0
  fi
  if [ "$DRY_RUN" = "1" ]; then
    echo "==> [dry-run] would initialize $QUBIT_HOME/voice/{config,data,state,cache,models}"
    return 0
  fi
  mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$STATE_DIR" "$CACHE_DIR" "$MODELS"
  echo "==> VoiceBridge layout initialized: $QUBIT_HOME/voice"
}

# ------------------------------------------------------------------ config
install_config_files() {
  if [ "$DRY_RUN" = "1" ]; then
    echo "==> [dry-run] would create/update $CONFIG_DIR/{voice.yaml,personal-knowledge.md,task-templates.yaml}"
    return 0
  fi

  mkdir -p "$CONFIG_DIR"
  if [ ! -f "$CONFIG_DIR/voice.yaml" ]; then
    cp "$PROJECT_DIR/config/voice.example.yaml" "$CONFIG_DIR/voice.yaml"
    echo "==> config created: $CONFIG_DIR/voice.yaml"
  else
    echo "==> config exists:  $CONFIG_DIR/voice.yaml (leave as-is; merge new keys from"
    echo "    config/voice.example.yaml manually if you want them)"
  fi
  # personal knowledge for the local chat model (user-editable). The sdist
  # intentionally excludes the developer's own file, so a release install must
  # not fail on its absence — create an empty starter instead.
  if [ ! -f "$CONFIG_DIR/personal-knowledge.md" ]; then
    if [ -f "$PROJECT_DIR/config/personal-knowledge.md" ]; then
      cp "$PROJECT_DIR/config/personal-knowledge.md" "$CONFIG_DIR/personal-knowledge.md"
    else
      cat > "$CONFIG_DIR/personal-knowledge.md" <<'EOF'
# Personal knowledge (optional)
# Add stable facts the assistant may use when answering. This starter ships
# with the release; the developer's own file is never distributed.
EOF
    fi
    echo "==> personal-knowledge.md created: edit it, then restart the bridge"
  else
    echo "==> personal-knowledge.md exists (leave as-is)"
  fi
  # task templates (user-editable). The developer's own file is excluded from the
  # sdist, so fall back to the portable example as a starter.
  if [ ! -f "$CONFIG_DIR/task-templates.yaml" ]; then
    if [ -f "$PROJECT_DIR/config/task-templates.example.yaml" ]; then
      cp "$PROJECT_DIR/config/task-templates.example.yaml" "$CONFIG_DIR/task-templates.yaml"
    fi
    echo "==> task-templates.yaml created from the example: edit it, then restart the bridge"
  else
    echo "==> task-templates.yaml exists (leave as-is)"
  fi
}

# ------------------------------------------------------------- systemd (opt)
# install_systemd_unit.sh only renders files; this installer must not pretend
# that is the same as "the service is installed". After rendering, enable and
# start the units explicitly (with confirmation when interactive).
install_systemd_units() {
  # Units started here; the layout drop-ins cover every shipped voice unit that
  # pins a runtime home, including the hotkey helper (installed separately by
  # the voice setup) so a new-layout host never falls back to the read-old home.
  local units=(qubit-voicebridge.service qubit-gateway.service)
  local layout_units=(qubit-voicebridge.service qubit-gateway.service qubit-voice-typing.service)
  local unit_dir="${SYSTEMD_UNIT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
  if [ "$DRY_RUN" = "1" ]; then
    echo "==> [dry-run] would install user units: ${units[*]}"
    if [ "$VOICE_INSTALL_MODE" = "new" ]; then
      echo "==> [dry-run] would install layout drop-ins:"
      local unit
      for unit in "${layout_units[@]}"; do
        echo "    $unit_dir/$unit.d/10-qubit-home.conf"
      done
    fi
    echo "==> [dry-run] would run: systemctl --user enable --now ${units[*]}"
    return 0
  fi

  echo "==> installing systemd user service"
  "$PROJECT_DIR/scripts/install_systemd_unit.sh" "$PROJECT_DIR"
  echo "==> installing systemd messaging-gateway unit (qubit-gateway.service)"
  "$PROJECT_DIR/scripts/install_gateway_unit.sh" "$PROJECT_DIR"

  # The shipped unit templates keep the historical XDG paths for existing
  # installs. A new-layout install drops its QUBIT_HOME/config override in a
  # drop-in so the templates (and every existing tuned unit) stay untouched.
  if [ "$VOICE_INSTALL_MODE" = "new" ]; then
    local unit drop
    for unit in "${layout_units[@]}"; do
      drop="$unit_dir/$unit.d"
      mkdir -p "$drop"
      cat > "$drop/10-qubit-home.conf" <<EOF
[Service]
Environment=QUBIT_HOME=$QUBIT_HOME
Environment=VOICE_BRIDGE_CONFIG=$CONFIG_DIR/voice.yaml
EOF
      echo "    layout drop-in: $drop/10-qubit-home.conf"
    done
  fi

  if ! command -v systemctl >/dev/null 2>&1; then
    echo "DEGRADED: systemctl not found; unit files are installed but not enabled." >&2
    echo "  enable later: systemctl --user enable --now ${units[*]}" >&2
    return 0
  fi

  local do_enable=0
  if [ "$ASSUME_YES" = "1" ] || [ ! -t 0 ]; then
    do_enable=1
  else
    local reply=""
    printf 'Enable and start the Qubit user services now? [y/N] '
    read -r reply || reply=""
    case "$reply" in
      y|Y|yes|YES) do_enable=1 ;;
      *) do_enable=0 ;;
    esac
  fi

  if [ "$do_enable" != "1" ]; then
    echo "    services not enabled (no confirmation)."
    echo "    enable later: systemctl --user enable --now ${units[*]}"
    return 0
  fi

  if systemctl --user enable --now "${units[@]}"; then
    echo "==> enabled and started: ${units[*]}"
  else
    echo "DEGRADED: systemctl --user enable --now failed; unit files are installed." >&2
    echo "  run manually: systemctl --user enable --now ${units[*]}" >&2
  fi
}

# ------------------------------------------------------------- launchd (opt)
# macOS core support is the runtime/CLI/dashboard plus ONE gateway LaunchAgent
# (ai.qubit.gateway[-<profile>].plist). The voice bridge/orb/typing units do
# not exist on macOS and are deliberately never attempted.
install_launchd_services() {
  if [ "$HOST_OS" != "macos" ]; then
    echo "UNSUPPORTED_PLATFORM: LaunchAgent services are macOS-only." >&2
    return 2
  fi
  if [ "$DRY_RUN" = "1" ]; then
    echo "==> [dry-run] would install the gateway LaunchAgent: $(launchd_plist_display)"
    if [ -n "$PREFIX" ]; then
      echo "    note      : the gateway label is home-scoped"
    fi
    echo "==> [dry-run] would load/start it through the runtime's launchd installer"
    echo "==> [dry-run] voice bridge/orb/typing: UNSUPPORTED on macOS (not installed)"
    return 0
  fi

  echo "==> installing the macOS gateway LaunchAgent (ai.qubit.gateway)"
  if "$PROJECT_DIR/scripts/install_launchd_unit.sh" "$PROJECT_DIR"; then
    echo "==> gateway LaunchAgent ready: $(launchd_plist_path)"
  else
    echo "DEGRADED: the gateway LaunchAgent was not installed/started." >&2
    echo "  retry: $PROJECT_DIR/scripts/install_launchd_unit.sh $PROJECT_DIR" >&2
  fi
  echo "==> voice bridge/orb/typing: UNSUPPORTED on macOS (not installed)"
  echo "    legacy ai.hermes.gateway* plists stay read-old; they are never deleted here."
}

print_next_steps() {
  echo
  if [ "$HOST_OS" = "macos" ]; then
    echo "==> done (macOS core install). Run:"
    echo "    $VENV/bin/qubit agent --help          # core runtime/CLI"
    echo "    $VENV/bin/python -m voicebridge webui --open   # dashboard"
    echo "    launchctl print gui/$(id -u)/ai.qubit.gateway # gateway status"
    echo
    echo "    Voice bridge, hotkey, orb and voice-typing are UNSUPPORTED on macOS."
    echo "    Auto-start the gateway instead?  $0 --with-launchd"
    return 0
  fi
  echo "==> done. Run:"
  echo "    $VENV/bin/python -m voicebridge status"
  echo "    $VENV/bin/python -m voicebridge run        # hotkey PTT loop"
  echo "    $VENV/bin/python -m voicebridge enroll     # enroll YOUR voice (voice-ID)"
  echo
  if [ "${QUBIT_BOOTSTRAP:-0}" = "1" ]; then
    echo "    Auto-start instead?  re-run the bootstrap with --with-systemd"
  else
    echo "    Auto-start instead?  $0 --with-systemd"
  fi
}

# ---------------------------------------------------------------- main flow
print_layout

if [ "$LAUNCHER_ONLY" = "1" ]; then
  echo "==> Qubit launcher install"
  if [ "$DRY_RUN" = "1" ]; then
    echo "==> [dry-run] would render $LAUNCHER_DIR/qubit -> $VENV/bin/qubit"
    exit 0
  fi
  if [ ! -x "$VENV/bin/qubit" ]; then
    echo "ERROR: $VENV/bin/qubit missing — run the full install first" >&2
    exit 1
  fi
  install_launcher
  exit 0
fi

if ! preflight; then
  exit 1
fi

# Build the dashboard BEFORE installing the package. The package is installed
# non-editable, and voicebridge/resources.py resolves the bundle from package
# data (site-packages/voicebridge/_data/frontend/qubit-dist) first; a wheel
# built before the bundle exists can never see a later checkout build. The
# previous order installed a dashboard-less wheel and then built the bundle in
# the checkout, so a one-pass clean source install served a STALE/BROKEN
# dashboard until the installer was run a second time.
install_dashboard
install_venv_and_package

install_voice_layout
install_model_assets
install_ollama_models
install_config_files

if [ "$DASHBOARD_STATE" = "toolchain_missing" ] || \
   [ "$DASHBOARD_STATE" = "build_failed" ]; then
  echo
  if [ "$DRY_RUN" = "1" ]; then
    echo "DASHBOARD_UNAVAILABLE (dry-run): the dashboard bundle is missing ($DASHBOARD_STATE)."
    echo "  A real install would exit 4 until node/npm are available."
  else
    echo "DASHBOARD_UNAVAILABLE: the dashboard bundle is missing ($DASHBOARD_STATE)."
    echo "  The Qubit CLI and runtime are installed, but the dashboard cannot be served."
    echo "  Action: install node/npm and run:"
    echo "      $PROJECT_DIR/scripts/build_dashboard.sh"
    echo "  or install from a release archive that already contains frontend/qubit-dist."
    echo "  Typed DEGRADED install (exit 4)."
    exit 4
  fi
fi

if [ "$HOST_OS" = "macos" ]; then
  if [ "$WITH_SYSTEMD" = "1" ]; then
    echo "    note      : systemd is Linux-only; installing the macOS gateway LaunchAgent instead"
  fi
  if [ "$WITH_SYSTEMD" = "1" ] || [ "$WITH_LAUNCHD" = "1" ]; then
    install_launchd_services
  elif [ "$DRY_RUN" != "1" ]; then
    print_next_steps
  fi
elif [ "$WITH_SYSTEMD" = "1" ]; then
  install_systemd_units
elif [ "$DRY_RUN" != "1" ]; then
  print_next_steps
fi

if [ "$DRY_RUN" = "1" ]; then
  echo
  echo "==> dry-run complete: no changes were made."
  exit 0
fi
