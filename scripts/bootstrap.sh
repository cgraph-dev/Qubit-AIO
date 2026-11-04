#!/usr/bin/env bash
# Qubit one-line/bootstrap installer.
#
# Downloads a release installer asset plus its SHA-256 checksum into a temporary
# directory, verifies the checksum, and executes ONLY the verified copy. It never
# reads a source checkout: everything it runs comes from <release-base>.
#
#   ./scripts/bootstrap.sh --help
#   ./scripts/bootstrap.sh --dry-run file:///srv/qubit/releases/v0.0.1
#   ./scripts/bootstrap.sh --prefix "$HOME/qubit" https://host/releases/v0.0.1
#
# Linux is supported now. macOS and Windows are refused with a typed message
# until their bootstrap paths exist (Windows uses scripts/install.ps1 from a
# checkout today).
set -euo pipefail

ASSET_NAME="install.sh"
DRY_RUN=0
PROFILE=""
PREFIX=""
CHECKSUM_FILE=""
BASE=""

usage() {
  cat <<'EOF'
Qubit bootstrap installer

Usage:
  ./scripts/bootstrap.sh [options] <release-base>

  <release-base>     HTTPS base URL, file:// URL, or a local directory that
                     contains the release assets (install.sh and a checksum).

Options:
  -h, --help         show this help without changing the machine
  --dry-run          print the plan; do not download, execute, or touch QUBIT_HOME
  --profile NAME     install profile, exported to the verified installer as
                     QUBIT_PROFILE
  --prefix DIR       portable install prefix, forwarded as --prefix DIR
  --checksum FILE    use FILE instead of <release-base>/SHA256SUMS

Behaviour:
  The release installer (install.sh) is downloaded into a temporary directory,
  its SHA-256 is checked against the checksum file, and only then is it run.
  A missing download, missing/invalid checksum, or mismatch fails closed.
  This command never reads the local source checkout and makes no network
  calls when <release-base> is a local path or file:// URL.

Supported host: Linux. macOS and Windows are typed refusals for now.
EOF
}

die() {
  local code="$1"
  shift
  printf '%s\n' "$*" >&2
  exit "$code"
}

need_value() {
  local option="$1"
  local value="${2:-}"
  [ -n "$value" ] || die 2 "option $option requires a value
run ./scripts/bootstrap.sh --help"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --profile)
      need_value "$1" "${2:-}"
      PROFILE="$2"
      shift
      ;;
    --profile=*)
      PROFILE="${1#*=}"
      [ -n "$PROFILE" ] || die 2 "option --profile requires a value"
      ;;
    --prefix)
      need_value "$1" "${2:-}"
      PREFIX="$2"
      shift
      ;;
    --prefix=*)
      PREFIX="${1#*=}"
      [ -n "$PREFIX" ] || die 2 "option --prefix requires a value"
      ;;
    --checksum)
      need_value "$1" "${2:-}"
      CHECKSUM_FILE="$2"
      shift
      ;;
    --checksum=*)
      CHECKSUM_FILE="${1#*=}"
      [ -n "$CHECKSUM_FILE" ] || die 2 "option --checksum requires a file"
      ;;
    --)
      shift
      [ "$#" -eq 0 ] || die 2 "unexpected argument: $1"
      break
      ;;
    -*)
      die 2 "unknown option: $1
run ./scripts/bootstrap.sh --help"
      ;;
    *)
      [ -z "$BASE" ] || die 2 "unexpected argument: $1
run ./scripts/bootstrap.sh --help"
      BASE="$1"
      ;;
  esac
  shift
done

[ -n "$BASE" ] || die 2 "missing release base
run ./scripts/bootstrap.sh --help"

HOST_SYSTEM="$(uname -s 2>/dev/null || echo unknown)"
case "$HOST_SYSTEM" in
  Linux)
    HOST_OS="linux"
    ;;
  Darwin)
    die 3 "BOOTSTRAP_UNSUPPORTED_HOST: macOS is not supported by the bootstrap installer yet.
Linux is supported now. On macOS, use ./scripts/install.sh from a checkout (core install only)."
    ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    die 3 "BOOTSTRAP_UNSUPPORTED_HOST: Windows is not supported by the bootstrap installer yet.
Linux is supported now. On Windows, use scripts\\install.ps1 from a checkout."
    ;;
  *)
    die 3 "BOOTSTRAP_UNSUPPORTED_HOST: $HOST_SYSTEM is not supported by the bootstrap installer.
Linux is supported now; macOS and Windows are typed refusals until their bootstrap paths exist."
    ;;
esac

# Normalise a trailing slash but keep scheme information intact.
BASE="${BASE%/}"

is_remote() {
  case "$1" in
    http://*|https://*) return 0 ;;
    *) return 1 ;;
  esac
}

# Turn a release-relative path into either a URL or a local path.
join_base() {
  local relative="$1"
  case "$BASE" in
    file://*)
      printf '%s/%s' "${BASE#file://}" "$relative"
      ;;
    *)
      printf '%s/%s' "$BASE" "$relative"
      ;;
  esac
}

# Fetch <relative> from the release base into <dest>. Local paths are copied;
# remote paths require curl. Return non-zero when the asset is absent.
fetch_asset() {
  local relative="$1"
  local dest="$2"
  local source
  source="$(join_base "$relative")"

  if is_remote "$BASE"; then
    command -v curl >/dev/null 2>&1 || die 4 \
      "BOOTSTRAP_MISSING_TOOL: curl is required to download release assets"
    curl -fsSL --retry 2 --connect-timeout 15 --max-time 300 "$source" -o "$dest"
    return $?
  fi

  [ -f "$source" ] || return 1
  cp -- "$source" "$dest"
}

# Resolve the checksum file into <dest>. --checksum wins over the release base.
fetch_checksum() {
  local dest="$1"
  if [ -n "$CHECKSUM_FILE" ]; then
    case "$CHECKSUM_FILE" in
      file://*)
        cp -- "${CHECKSUM_FILE#file://}" "$dest"
        ;;
      http://*|https://*)
        command -v curl >/dev/null 2>&1 || die 4 \
          "BOOTSTRAP_MISSING_TOOL: curl is required to download the checksum"
        curl -fsSL --retry 2 --connect-timeout 15 --max-time 60 "$CHECKSUM_FILE" -o "$dest"
        ;;
      *)
        cp -- "$CHECKSUM_FILE" "$dest"
        ;;
    esac
    return $?
  fi

  local candidate
  for candidate in "SHA256SUMS" "install.sh.sha256" "install.sh.sha256sum"; do
    if fetch_asset "$candidate" "$dest" 2>/dev/null; then
      CHECKSUM_SOURCE="$(join_base "$candidate")"
      return 0
    fi
  done
  return 1
}

# Check local assets without writing anything (used by --dry-run).
local_asset_exists() {
  local relative="$1"
  local source
  source="$(join_base "$relative")"
  [ -f "$source" ]
}

print_plan() {
  local checksum_label="$1"
  printf 'Qubit bootstrap plan\n'
  printf '  host       : linux (%s)\n' "$HOST_SYSTEM"
  printf '  base       : %s\n' "$BASE"
  printf '  asset      : %s\n' "$ASSET_NAME"
  printf '  checksum   : %s\n' "$checksum_label"
  if [ -n "$PROFILE" ]; then
    printf '  profile    : %s (QUBIT_PROFILE)\n' "$PROFILE"
  else
    printf '  profile    : (default)\n'
  fi
  if [ -n "$PREFIX" ]; then
    printf '  prefix     : %s\n' "$PREFIX"
  else
    printf '  prefix     : (default)\n'
  fi
  printf '  QUBIT_HOME : untouched (dry-run)\n'
  printf '  steps      : download %s -> verify sha256 -> run the verified copy\n' "$ASSET_NAME"
  printf 'dry-run complete; nothing downloaded, executed, or written\n'
}

if [ "$DRY_RUN" = "1" ]; then
  if [ -n "$CHECKSUM_FILE" ]; then
    if is_remote "$CHECKSUM_FILE"; then
      checksum_label="$CHECKSUM_FILE (not fetched in dry-run)"
    else
      checksum_path="${CHECKSUM_FILE#file://}"
      [ -f "$checksum_path" ] || die 7 \
        "BOOTSTRAP_CHECKSUM_UNAVAILABLE: checksum file not found: $checksum_path"
      checksum_label="$CHECKSUM_FILE"
    fi
  else
    checksum_label="<base>/SHA256SUMS (or install.sh.sha256)"
    if ! is_remote "$BASE"; then
      found=""
      for candidate in "SHA256SUMS" "install.sh.sha256" "install.sh.sha256sum"; do
        if local_asset_exists "$candidate"; then
          checksum_label="$(join_base "$candidate")"
          found=1
          break
        fi
      done
      [ -n "$found" ] || die 7 \
        "BOOTSTRAP_CHECKSUM_UNAVAILABLE: no SHA256SUMS/install.sh.sha256 under $BASE"
    fi
  fi

  if ! is_remote "$BASE"; then
    local_asset_exists "$ASSET_NAME" || die 5 \
      "BOOTSTRAP_DOWNLOAD_FAILED: release asset not found: $(join_base "$ASSET_NAME")"
  fi

  print_plan "$checksum_label"
  exit 0
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/qubit-bootstrap.XXXXXX")" || die 4 \
  "BOOTSTRAP_MISSING_TOOL: cannot create a temporary directory"
cleanup() {
  rm -rf -- "$TMP_DIR"
}
trap cleanup EXIT HUP INT TERM

ASSET_PATH="$TMP_DIR/$ASSET_NAME"
CHECKSUM_PATH="$TMP_DIR/checksum"

printf '==> downloading %s from %s\n' "$ASSET_NAME" "$BASE"
fetch_asset "$ASSET_NAME" "$ASSET_PATH" || die 5 \
  "BOOTSTRAP_DOWNLOAD_FAILED: could not download $(join_base "$ASSET_NAME")"

CHECKSUM_SOURCE=""
printf '==> downloading checksum\n'
fetch_checksum "$CHECKSUM_PATH" || die 7 \
  "BOOTSTRAP_CHECKSUM_UNAVAILABLE: no checksum found (use --checksum FILE or publish SHA256SUMS beside $ASSET_NAME)"

# Accept a standard "hash  filename" line (SHA256SUMS) or a bare hash. Compare
# only on the basename so paths in a checksum file cannot smuggle another asset.
EXPECTED="$(awk -v want="$ASSET_NAME" '
  { gsub(/\r/, "") }
  $1 ~ /^[0-9A-Fa-f]{64}$/ {
    name = (NF >= 2 ? $2 : "")
    sub(/^\*/, "", name)
    sub(/^.*\//, "", name)
    if (name == want || name == "") {
      print tolower($1)
      exit
    }
  }
' "$CHECKSUM_PATH")"

[ -n "$EXPECTED" ] || die 7 \
  "BOOTSTRAP_CHECKSUM_INVALID: $ASSET_NAME is not named in the checksum file"

if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL="$(sha256sum "$ASSET_PATH" | cut -d' ' -f1)"
elif command -v shasum >/dev/null 2>&1; then
  ACTUAL="$(shasum -a 256 "$ASSET_PATH" | cut -d' ' -f1)"
elif command -v openssl >/dev/null 2>&1; then
  ACTUAL="$(openssl dgst -sha256 "$ASSET_PATH" | sed 's/.*= *//')"
else
  die 4 "BOOTSTRAP_MISSING_TOOL: need sha256sum, shasum, or openssl to verify the release asset"
fi
ACTUAL="$(printf '%s' "$ACTUAL" | tr 'A-F' 'a-f')"

if [ "$ACTUAL" != "$EXPECTED" ]; then
  die 6 "BOOTSTRAP_CHECKSUM_MISMATCH: refusing to execute $ASSET_NAME
  expected sha256: $EXPECTED
  actual   sha256: $ACTUAL"
fi

printf '==> verified %s sha256=%s\n' "$ASSET_NAME" "$ACTUAL"
printf '==> running verified installer\n'

INSTALL_ARGS=()
if [ -n "$PREFIX" ]; then
  INSTALL_ARGS+=("--prefix" "$PREFIX")
fi
if [ -n "$PROFILE" ]; then
  export QUBIT_PROFILE="$PROFILE"
fi

set +e
bash "$ASSET_PATH" "${INSTALL_ARGS[@]}"
STATUS=$?
set -e
exit "$STATUS"
