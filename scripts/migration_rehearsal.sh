#!/usr/bin/env bash
# Hermetic migration -> upgrade -> rollback -> uninstall rehearsal (Wave 4).
#
# Drives the REAL product lifecycle commands against a synthetic legacy state
# bundle inside a THROWAWAY /tmp tree with a throwaway HOME/XDG root:
#
#   * migration  — `qubit product migrate --apply --backup-dir`
#   * upgrade    — `qubit product upgrade` + `qubit package upgrade`
#   * rollback   — `qubit product migrate-rollback --apply`
#   * uninstall  — `qubit package uninstall`
#
# It proves the migration moves the legacy config/state/data trees to the
# canonical Qubit paths and preserves every byte (including credential files
# that the safety backup intentionally excludes), that the upgrade plan mutates
# nothing, that rollback reverses the migration (canonical removed, legacy
# trees whole), and that the uninstall plan requires confirmation and preserves
# user state. No network, no live service, and no operator
# HOME/config/state/browser/audio is touched.
#
# Usage: scripts/migration_rehearsal.sh
#        PYTHON=/path/to/python scripts/migration_rehearsal.sh
#
# Typed exit codes (never a silent pass):
#   0 PASS     every lifecycle stage passed and state was preserved
#   2 FAIL     a real defect (stage failed / state not preserved)
#   4 DEGRADED tooling unavailable (python + importable voicebridge)
#   8 BLOCKED  cannot create the throwaway workspace under /tmp
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/qubit-lifecycle-XXXXXX")" || {
  echo "BLOCKED: cannot create a throwaway workspace under ${TMPDIR:-/tmp}" >&2
  exit 8
}
trap 'rm -rf "$WORK"' EXIT

HOME_DIR="$WORK/home"
export HOME="$HOME_DIR"
export XDG_CONFIG_HOME="$HOME_DIR/.config"
export XDG_DATA_HOME="$HOME_DIR/.local/share"
export XDG_STATE_HOME="$HOME_DIR/.local/state"
export XDG_CACHE_HOME="$HOME_DIR/.cache"
unset PYTHONPATH || true
mkdir -p "$HOME_DIR"

fail() { echo "FAIL: $*" >&2; exit 2; }
degrade() { echo "DEGRADED: $*" >&2; exit 4; }

# ---------------------------------------------------------------- tooling
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
    PYTHON="$REPO_ROOT/.venv/bin/python"
  else
    PYTHON="python3"
  fi
fi
command -v "$PYTHON" >/dev/null 2>&1 || degrade "no python interpreter: $PYTHON"
(cd "$REPO_ROOT" && "$PYTHON" -c 'import voicebridge') >/dev/null 2>&1 \
  || degrade "$PYTHON cannot import voicebridge; install the package or run scripts/install.sh first"

echo "== migration rehearsal (hermetic) =="
echo "   workspace : $WORK"
echo "   python    : $PYTHON"

# The harness shells out to the real CLI so the evidence is the product's
# actual lifecycle commands, never a reimplementation of them.
cd "$REPO_ROOT"
PY="$PYTHON" WORK="$WORK" REPO="$REPO_ROOT" \
  "$PYTHON" - <<'PYEOF'
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

PY = os.environ["PY"]
REPO = Path(os.environ["REPO"])
WORK = Path(os.environ["WORK"])

CFG_BASE = Path(os.environ["XDG_CONFIG_HOME"])
STATE_BASE = Path(os.environ["XDG_STATE_HOME"])
DATA_BASE = Path(os.environ["XDG_DATA_HOME"])
LEGACY = "openclaw-voice-bridge"
CANON = "qubit-voicebridge"

CATEGORIES = {"config": CFG_BASE, "state": STATE_BASE, "data": DATA_BASE}

BACKUP = WORK / "migration-backup"


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(2)


def run(cmd, *, expect_rc=0):
    r = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
    if r.returncode != expect_rc:
        tail = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")
        fail(f"{' '.join(cmd)} rc={r.returncode} (wanted {expect_rc}): {tail[-400:]}")
    return (r.stdout or "") + (r.stderr or "")


def run_json(cmd):
    text = run(cmd)
    start = text.find("{")
    if start < 0:
        fail(f"no JSON in output of {' '.join(cmd)}: {text[-300:]}")
    obj, _ = json.JSONDecoder().raw_decode(text[start:])
    return obj


def cli(*args):
    return [PY, "-m", "voicebridge", *args]


def _hash_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(category, dirname):
    base = CATEGORIES[category] / dirname
    out = {}
    if not base.exists() or base.is_symlink():
        return out
    for p in sorted(base.rglob("*")):
        rel = str(p.relative_to(base))
        if p.is_symlink():
            out[rel] = "symlink:" + os.readlink(p)
        elif p.is_file():
            out[rel] = _hash_file(p)
    return out


def logical_tree(dirname):
    return {cat: snapshot(cat, dirname) for cat in CATEGORIES}


def assert_preserved(expected, actual, stage):
    for cat, files in expected.items():
        for rel, digest in files.items():
            if rel not in actual[cat]:
                fail(f"{stage}: {cat}/{rel} lost (state not preserved)")
            if actual[cat][rel] != digest:
                fail(f"{stage}: {cat}/{rel} changed content (state corrupted)")
    total = sum(len(f) for f in expected.values())
    print(f"   {stage}: {total} synthetic state file(s) preserved byte-for-byte")


# ---------------------------------------------------------------- seed state
def write(rel, data):
    p = Path(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data, encoding="utf-8")


config_dir = CFG_BASE / LEGACY
state_dir = STATE_BASE / LEGACY
data_dir = DATA_BASE / LEGACY

write(config_dir / "voice.yaml", "audio: {}\n")
write(config_dir / "personal-knowledge.md",
      "# Synthetic personal knowledge\nDemo fact for the rehearsal.\n")
# A credential-shaped name the migration backup intentionally EXCLUDES while the
# live move still preserves every byte. The value is an obviously synthetic
# placeholder, never a real secret.
write(config_dir / "credentials.json",
      json.dumps({"synthetic": "placeholder-not-a-real-credential"}) + "\n")
write(state_dir / "events.jsonl", '{"event": "synthetic", "n": 1}\n')
write(state_dir / "memory" / "memory.db", b"SQLite format 3\x00synthetic\n")
write(state_dir / "reports" / "report.json",
      json.dumps({"report": "synthetic", "ok": True}) + "\n")
write(data_dir / "models" / "weights.bin", b"synthetic-model-bytes\n")

before = logical_tree(LEGACY)
if sum(len(v) for v in before.values()) == 0:
    fail("synthetic legacy state was not seeded")

# ---------------------------------------------------------------- 1. migration
print("== 1. migration ==")
dry = run_json(cli("product", "migrate", "--json"))
if dry.get("state") != "READY":
    fail(f"migrate dry-run expected READY, got {dry.get('state')}: {dry}")

mig = run_json(cli("product", "migrate", "--apply",
                   "--backup-dir", str(BACKUP), "--json"))
if mig.get("state") != "OK":
    fail(f"migrate --apply expected OK, got {mig.get('state')}: {mig}")

after_migrate = logical_tree(CANON)
assert_preserved(before, after_migrate, "migration")

for cat in CATEGORIES:
    legacy = CATEGORIES[cat] / LEGACY
    canonical = CATEGORIES[cat] / CANON
    if not canonical.is_dir() or canonical.is_symlink():
        fail(f"migration: canonical {cat} tree missing at {canonical}")
    if not legacy.is_symlink() or os.path.realpath(legacy) != str(canonical):
        fail(f"migration: legacy {cat} alias missing/mismatched ({legacy})")
print("   canonical trees present; legacy compat aliases point at them")

# The safety backup must exist, be validated, and exclude credentials.
manifest = BACKUP / "manifest.json"
if not manifest.is_file():
    fail("migration backup manifest missing")
backup_creds = [str(p.relative_to(BACKUP)) for p in BACKUP.rglob("*")
                if "credential" in p.name.lower()]
if backup_creds:
    fail(f"credentials leaked into the migration backup: {backup_creds}")
print("   validated backup present; credentials excluded from the backup")

# ---------------------------------------------------------------- 2. upgrade
print("== 2. upgrade ==")
up = run_json(cli("product", "upgrade", "--json"))
if up.get("state") != "OK":
    fail(f"product upgrade expected OK, got {up.get('state')}: {up}")
for key in ("user state", "memory/reports/cursors", "user config"):
    if key not in up.get("preserves", []):
        fail(f"upgrade contract does not preserve {key!r}: {up.get('preserves')}")
pkg_up = run_json(cli("package", "upgrade"))
if pkg_up.get("action") != "upgrade":
    fail(f"package upgrade plan unexpected: {pkg_up}")
assert_preserved(before, logical_tree(CANON), "upgrade")
print(f"   upgrade preserves: {', '.join(up.get('preserves', []))}")

# ---------------------------------------------------------------- 3. rollback
print("== 3. rollback ==")
rb_text = run(cli("product", "migrate-rollback", "--apply"))
start = rb_text.find("{")
rb, _ = json.JSONDecoder().raw_decode(rb_text[start:])
if rb.get("state") != "OK":
    fail(f"migrate-rollback expected OK, got {rb.get('state')}: {rb}")

after_rollback = logical_tree(LEGACY)
assert_preserved(before, after_rollback, "rollback")
for cat in CATEGORIES:
    canonical = CATEGORIES[cat] / CANON
    if canonical.exists() or canonical.is_symlink():
        fail(f"rollback: canonical {cat} tree still present at {canonical}")
print("   canonical trees removed; legacy trees restored whole")

# ---------------------------------------------------------------- 4. uninstall
print("== 4. uninstall ==")
un = run_json(cli("package", "uninstall"))
if un.get("requires_confirmation") is not True:
    fail(f"uninstall plan does not require confirmation: {un}")
preserves = " ".join(un.get("preserves_by_default", [])).lower()
if "state dir" not in preserves or "user config" not in preserves:
    fail(f"uninstall plan does not preserve state/config: {un}")
assert_preserved(before, logical_tree(LEGACY), "uninstall")
print("   uninstall requires confirmation and preserves state/config")

print("migration rehearsal: PASS (migration -> upgrade -> rollback -> "
      "uninstall; state preserved)")
PYEOF

echo "migration rehearsal: PASS"
