#!/usr/bin/env bash
# Qubit one-line/bootstrap installer.
#
# Downloads a release source archive plus the release installer and their
# SHA-256 checksums into a temporary directory, verifies BOTH, extracts the
# archive, verifies that the extracted tree carries the same installer, and
# executes ONLY that extracted copy. It never reads a source checkout:
# everything it runs comes from <release-base>.
#
#   ./scripts/bootstrap.sh --help
#   ./scripts/bootstrap.sh --dry-run file:///srv/qubit/releases/v0.0.1
#   ./scripts/bootstrap.sh --prefix "$HOME/qubit" https://host/releases/v0.0.1
#
# Release asset contract (see INSTALL.md):
#   install.sh              verified bootstrap copy of the installer
#   qubit-release.tar.gz    source tree containing scripts/install.sh
#   SHA256SUMS              sha256 of both assets, named
#
# Linux is supported now. macOS is refused with a typed message (use
# scripts/install.sh from a checkout). Windows uses scripts/bootstrap.ps1 with
# a ZIP release archive; this POSIX script refuses Windows.
set -euo pipefail

ASSET_NAME="install.sh"
ARCHIVE_NAME="${QUBIT_RELEASE_ARCHIVE:-qubit-release.tar.gz}"
DRY_RUN=0
PROFILE=""
PREFIX=""
CHECKSUM_FILE=""
WITHOUT_MODELS=0
WITHOUT_DASHBOARD=0
ASSUME_YES=0
WITH_SYSTEMD=0
BASE=""

usage() {
  cat <<'EOF'
Qubit bootstrap installer

Usage:
  ./scripts/bootstrap.sh [options] <release-base>

  <release-base>     HTTPS base URL, file:// URL, or a local directory that
                     contains install.sh, the release source archive, and a
                     SHA256SUMS file naming both.

Options:
  -h, --help         show this help without changing the machine
  --dry-run          print the plan; do not download, extract, execute, or
                     touch QUBIT_HOME
  --profile NAME     install profile, exported to the verified installer as
                     QUBIT_PROFILE
  --prefix DIR       portable install prefix, forwarded as --prefix DIR
  --without-models   skip model downloads (forwarded to the installer)
  --without-dashboard
                     skip the dashboard build (forwarded to the installer)
  --yes              non-interactive install (forwarded to the installer)
  --with-systemd     install/enable the user services (forwarded)
  --archive NAME     release source archive file name under <release-base>
                     (default: qubit-release.tar.gz)
  --checksum FILE    use FILE instead of <release-base>/SHA256SUMS

Behaviour:
  The release installer (install.sh) and the source archive are downloaded into
  a temporary directory. Their SHA-256 values are checked against the checksum
  file, the archive is extracted there, and the installer is run from the
  extracted tree (not from the downloaded copy). A missing download, missing or
  invalid checksum, archive mismatch, or extraction failure fails closed.
  This command never reads the local source checkout and makes no network calls
  when <release-base> is a local path or file:// URL.

Release assets:
  install.sh, <archive> (default qubit-release.tar.gz), SHA256SUMS.
  The archive must extract to a tree with scripts/install.sh (either at the
  archive root or under exactly one top-level directory).

Supported host: Linux. macOS is a typed refusal; Windows uses bootstrap.ps1.
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
    --archive)
      need_value "$1" "${2:-}"
      ARCHIVE_NAME="$2"
      shift
      ;;
    --archive=*)
      ARCHIVE_NAME="${1#*=}"
      [ -n "$ARCHIVE_NAME" ] || die 2 "option --archive requires a value"
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
    --without-models)
      WITHOUT_MODELS=1
      ;;
    --without-dashboard)
      WITHOUT_DASHBOARD=1
      ;;
    --yes|-y)
      ASSUME_YES=1
      ;;
    --with-systemd)
      WITH_SYSTEMD=1
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

case "$ARCHIVE_NAME" in
  ""|*/*|.|..)
    die 2 "option --archive must be a bare file name, got: $ARCHIVE_NAME"
    ;;
esac

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
    die 3 "BOOTSTRAP_UNSUPPORTED_HOST: this POSIX bootstrap does not run on Windows.
On Windows, use scripts\\bootstrap.ps1 with the ZIP release assets."
    ;;
  *)
    die 3 "BOOTSTRAP_UNSUPPORTED_HOST: $HOST_SYSTEM is not supported by the bootstrap installer.
Linux is supported now; macOS is a typed refusal. Windows uses scripts/bootstrap.ps1."
    ;;
esac

if command -v sha256sum >/dev/null 2>&1; then
  SHA256_TOOL="sha256sum"
elif command -v shasum >/dev/null 2>&1; then
  SHA256_TOOL="shasum"
elif command -v openssl >/dev/null 2>&1; then
  SHA256_TOOL="openssl"
else
  die 4 "BOOTSTRAP_MISSING_TOOL: need sha256sum, shasum, or openssl to verify release assets"
fi

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

# Print the expected sha256 for <want> from a SHA256SUMS file. A bare hash is
# accepted for install.sh only (legacy install.sh.sha256); the archive must be
# named so two assets cannot be confused.
checksum_for() {
  local want="$1"
  local file="$2"
  local allow_bare="$3"
  awk -v want="$want" -v allow_bare="$allow_bare" '
    { gsub(/\r/, "") }
    $1 ~ /^[0-9A-Fa-f]{64}$/ {
      name = (NF >= 2 ? $2 : "")
      sub(/^\*/, "", name)
      sub(/^.*\//, "", name)
      if (name == want || (allow_bare == "1" && name == "")) {
        print tolower($1)
        exit
      }
    }
  ' "$file"
}

sha256_of() {
  local path="$1"
  local value=""
  case "$SHA256_TOOL" in
    sha256sum)
      value="$(sha256sum -- "$path")"
      value="${value%% *}"
      ;;
    shasum)
      value="$(shasum -a 256 -- "$path")"
      value="${value%% *}"
      ;;
    openssl)
      value="$(openssl dgst -sha256 -- "$path")"
      value="$(printf '%s' "$value" | sed 's/.*= *//')"
      ;;
  esac
  printf '%s' "$value" | tr 'A-F' 'a-f'
}

# Verify <path> against <expected>; dies on mismatch/null and prints the hash.
verify_asset() {
  local label="$1"
  local path="$2"
  local expected="$3"
  local actual
  actual="$(sha256_of "$path")"
  if [ "$actual" != "$expected" ]; then
    die 6 "BOOTSTRAP_CHECKSUM_MISMATCH: refusing to use $label
  expected sha256: $expected
  actual   sha256: $actual"
  fi
  VERIFIED_SHA="$actual"
  printf '==> verified %s sha256=%s\n' "$label" "$actual"
}

# Extract <archive> into <dest> after rejecting absolute or parent-escaping
# entries. Symlink escapes are caught again when the installer is resolved.
extract_archive() {
  local archive="$1"
  local dest="$2"
  command -v tar >/dev/null 2>&1 || die 4 \
    "BOOTSTRAP_MISSING_TOOL: tar is required to extract $ARCHIVE_NAME"
  mkdir -p "$dest" || die 4 \
    "BOOTSTRAP_MISSING_TOOL: cannot create the extraction directory"

  local listing entry
  listing="$(tar -tf "$archive")" || die 6 \
    "BOOTSTRAP_ARCHIVE_INVALID: cannot read $ARCHIVE_NAME"
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    case "$entry" in
      /*|..|../*|*/..|*/../*)
        die 6 "BOOTSTRAP_ARCHIVE_INVALID: unsafe path in $ARCHIVE_NAME: $entry"
        ;;
    esac
  done <<< "$listing"

  tar -xf "$archive" -C "$dest" || die 6 \
    "BOOTSTRAP_ARCHIVE_INVALID: cannot extract $ARCHIVE_NAME"
}

# Locate scripts/install.sh at the archive root or under exactly one top-level
# directory (a git-archive style prefix such as Qubit-0.0.1/).
find_install_root() {
  local extract="$1"
  local candidate
  if [ -f "$extract/scripts/install.sh" ]; then
    printf '%s' "$extract"
    return 0
  fi
  local -a dirs=()
  for candidate in "$extract"/*; do
    [ -d "$candidate" ] && dirs+=("$candidate")
  done
  if [ "${#dirs[@]}" -eq 1 ] && [ -f "${dirs[0]}/scripts/install.sh" ]; then
    printf '%s' "${dirs[0]}"
    return 0
  fi
  return 1
}

print_plan() {
  local checksum_label="$1"
  printf 'Qubit bootstrap plan\n'
  printf '  host       : linux (%s)\n' "$HOST_SYSTEM"
  printf '  base       : %s\n' "$BASE"
  printf '  asset      : %s\n' "$ASSET_NAME"
  printf '  archive    : %s\n' "$ARCHIVE_NAME"
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
  [ "$WITHOUT_MODELS" = "1" ] && printf '  models     : skipped (--without-models)\n'
  [ "$WITHOUT_DASHBOARD" = "1" ] && printf '  dashboard  : skipped (--without-dashboard)\n'
  printf '  QUBIT_HOME : untouched (dry-run)\n'
  printf '  steps      : download %s + %s -> verify sha256 -> extract -> run the extracted scripts/install.sh\n' \
    "$ASSET_NAME" "$ARCHIVE_NAME"
  printf 'dry-run complete; nothing downloaded, extracted, executed, or written\n'
}

if [ "$DRY_RUN" = "1" ]; then
  checksum_path=""
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
    checksum_label="<base>/SHA256SUMS (must name $ASSET_NAME and $ARCHIVE_NAME)"
    if ! is_remote "$BASE"; then
      found=""
      for candidate in "SHA256SUMS" "install.sh.sha256" "install.sh.sha256sum"; do
        if local_asset_exists "$candidate"; then
          checksum_path="$(join_base "$candidate")"
          checksum_label="$checksum_path"
          found=1
          break
        fi
      done
      [ -n "$found" ] || die 7 \
        "BOOTSTRAP_CHECKSUM_UNAVAILABLE: no SHA256SUMS/install.sh.sha256 under $BASE"
      [ -n "$(checksum_for "$ASSET_NAME" "$checksum_path" 1)" ] || die 7 \
        "BOOTSTRAP_CHECKSUM_INVALID: $ASSET_NAME is not named in $checksum_path"
      [ -n "$(checksum_for "$ARCHIVE_NAME" "$checksum_path" 0)" ] || die 7 \
        "BOOTSTRAP_CHECKSUM_INVALID: $ARCHIVE_NAME is not named in $checksum_path"
    fi
  fi

  if ! is_remote "$BASE"; then
    local_asset_exists "$ASSET_NAME" || die 5 \
      "BOOTSTRAP_DOWNLOAD_FAILED: release asset not found: $(join_base "$ASSET_NAME")"
    local_asset_exists "$ARCHIVE_NAME" || die 5 \
      "BOOTSTRAP_ARCHIVE_MISSING: release archive not found: $(join_base "$ARCHIVE_NAME")"
  fi

  print_plan "$checksum_label"
  exit 0
fi

if is_remote "$BASE" || is_remote "$CHECKSUM_FILE"; then
  command -v curl >/dev/null 2>&1 || die 4 \
    "BOOTSTRAP_MISSING_TOOL: curl is required to download release assets"
fi

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/qubit-bootstrap.XXXXXX")" || die 4 \
  "BOOTSTRAP_MISSING_TOOL: cannot create a temporary directory"
cleanup() {
  rm -rf -- "$TMP_DIR"
}
trap cleanup EXIT HUP INT TERM

ASSET_PATH="$TMP_DIR/$ASSET_NAME"
ARCHIVE_PATH="$TMP_DIR/$ARCHIVE_NAME"
CHECKSUM_PATH="$TMP_DIR/checksum"
EXTRACT_DIR="$TMP_DIR/release"

CHECKSUM_SOURCE=""
printf '==> downloading checksum\n'
fetch_checksum "$CHECKSUM_PATH" || die 7 \
  "BOOTSTRAP_CHECKSUM_UNAVAILABLE: no checksum found (use --checksum FILE or publish SHA256SUMS beside $ASSET_NAME and $ARCHIVE_NAME)"

EXPECTED_INSTALL="$(checksum_for "$ASSET_NAME" "$CHECKSUM_PATH" 1)"
[ -n "$EXPECTED_INSTALL" ] || die 7 \
  "BOOTSTRAP_CHECKSUM_INVALID: $ASSET_NAME is not named in the checksum file"

EXPECTED_ARCHIVE="$(checksum_for "$ARCHIVE_NAME" "$CHECKSUM_PATH" 0)"
[ -n "$EXPECTED_ARCHIVE" ] || die 7 \
  "BOOTSTRAP_CHECKSUM_INVALID: $ARCHIVE_NAME is not named in the checksum file"

printf '==> downloading %s from %s\n' "$ASSET_NAME" "$BASE"
fetch_asset "$ASSET_NAME" "$ASSET_PATH" || die 5 \
  "BOOTSTRAP_DOWNLOAD_FAILED: could not download $(join_base "$ASSET_NAME")"

verify_asset "$ASSET_NAME" "$ASSET_PATH" "$EXPECTED_INSTALL"
ACTUAL_INSTALL="$VERIFIED_SHA"

printf '==> downloading %s from %s\n' "$ARCHIVE_NAME" "$BASE"
fetch_asset "$ARCHIVE_NAME" "$ARCHIVE_PATH" || die 5 \
  "BOOTSTRAP_ARCHIVE_MISSING: could not download $(join_base "$ARCHIVE_NAME")"

verify_asset "$ARCHIVE_NAME" "$ARCHIVE_PATH" "$EXPECTED_ARCHIVE"

printf '==> extracting %s\n' "$ARCHIVE_NAME"
extract_archive "$ARCHIVE_PATH" "$EXTRACT_DIR"

INSTALL_ROOT="$(find_install_root "$EXTRACT_DIR")" || die 6 \
  "BOOTSTRAP_ARCHIVE_INVALID: $ARCHIVE_NAME does not contain scripts/install.sh"

EXTRACT_DIR_REAL="$(cd "$EXTRACT_DIR" && pwd -P)"
INSTALL_SCRIPT_DIR_REAL="$(cd "$INSTALL_ROOT/scripts" && pwd -P)"
case "$INSTALL_SCRIPT_DIR_REAL/" in
  "$EXTRACT_DIR_REAL"/*) ;;
  *)
    die 6 "BOOTSTRAP_ARCHIVE_INVALID: extracted scripts/install.sh escapes the extraction directory"
    ;;
esac

EXTRACTED_INSTALL="$INSTALL_ROOT/scripts/install.sh"
EXTRACTED_SHA="$(sha256_of "$EXTRACTED_INSTALL")"
if [ "$EXTRACTED_SHA" != "$ACTUAL_INSTALL" ]; then
  die 6 "BOOTSTRAP_CHECKSUM_MISMATCH: extracted scripts/install.sh does not match the verified $ASSET_NAME
  $ASSET_NAME sha256: $ACTUAL_INSTALL
  archive scripts/install.sh sha256: $EXTRACTED_SHA"
fi
printf '==> verified extracted scripts/install.sh sha256=%s\n' "$EXTRACTED_SHA"
printf '==> running verified installer from the extracted release tree\n'

INSTALL_ARGS=()
if [ -n "$PREFIX" ]; then
  INSTALL_ARGS+=("--prefix" "$PREFIX")
fi
[ "$WITHOUT_MODELS" = "1" ] && INSTALL_ARGS+=("--without-models")
[ "$WITHOUT_DASHBOARD" = "1" ] && INSTALL_ARGS+=("--without-dashboard")
[ "$ASSUME_YES" = "1" ] && INSTALL_ARGS+=("--yes")
[ "$WITH_SYSTEMD" = "1" ] && INSTALL_ARGS+=("--with-systemd")
if [ -n "$PROFILE" ]; then
  export QUBIT_PROFILE="$PROFILE"
fi
# The installer runs from a temporary extraction; keep its venv and launcher
# target under the persistent QUBIT_HOME.
export QUBIT_BOOTSTRAP=1

set +e
bash "$EXTRACTED_INSTALL" "${INSTALL_ARGS[@]}"
STATUS=$?
set -e
exit "$STATUS"
