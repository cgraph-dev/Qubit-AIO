"""Audit a set of commits against the §4 EXCLUSION LIST (the real one).

The danger in a rebrand wave is REMOVING a name that something outside the repo reads.
The authoritative list is docs/REBRAND_MODULE_MIGRATION_PLAN.md §4. A blunt grep for
'hermes-gateway' flags false positives -- e.g. a local smoke-test OTel service.name is
not a systemd unit identity -- so each hit is classified rather than just counted.

Usage: python3 tmp/audit/check_s4_diff.py <rev-range>
       python3 tmp/audit/check_s4_diff.py 7c8e19192..HEAD
"""
import re
import subprocess
import sys

REPO = "/home/trick/Projects/Repos/qubit-voicebridge"

# §4 tokens that are ALWAYS a violation if removed/renamed.
HARD = {
    "HERMES_HOME": "read by helper subprocesses spawned with HERMES_PYTHON; get_qubit_home falls back to it",
    "HERMES_UPDATE_PAUSED": "read by the update/recovery path; may be set by a previous build",
    ".hermes-update-": "an in-flight updater must recognise a marker the old build wrote",
    "ai.hermes.gateway": "launchd service label (live macOS service identity)",
    "_HERMES_USER_AGENT": "HTTP User-Agent an external endpoint may key on",
    "HERMES_OVERLAYS": "env var consumed across the boundary",
    "HermesCLI": "one object under two names; tui_gateway/slash_worker binds it at module scope",
    "requires_hermes": "plugin-manifest field read-old by plugin_validate.py; 202 catalog files declare it",
    "hermes_agent.plugins": "pip entry-point group; renaming orphans installed plugins",
    "get_hermes_home": "upstream call sites import by that name (one-directional compat)",
    "${HERMES_SKILL_DIR}": "loader regex matches the literal; skills are authored against it",
    "${HERMES_SESSION_ID}": "loader regex matches the literal; skills are authored against it",
    "__HERMES_": "server->browser injection contract",
    "X-Hermes-Session-Token": "server->browser injection contract",
    "hermes-locale": "localStorage key read-old for existing browser sessions",
    "hermes-dashboard-": "localStorage keys read-old for existing browser sessions",
    "hermes.tokenReloadAttempted": "wire-format metric name",
    "hermes-kanban": "wire-format string",
    "hermes-flow": "wire-format string",
    "hermes_config_overrides.json": "legacy on-disk data existing installs read",
    "hermes_persona.json": "legacy on-disk data existing installs read",
    "hermes_platform/resolver/app.py": "a DEPLOYED service path external processes resolve",
}

# Tokens whose removal is USUALLY fine but must be read in context.
SOFT = {
    "hermes-gateway": "systemd unit names are protected; a local smoke-test label is not",
    "hermes_service": "service identity -- check whether a live unit depends on it",
    # the metric namespace IS protected, but `hermes.` alone also matches `.hermes.md`
    # (a context filename that is NOT protected), so require a metric-shaped occurrence.
    "hermes.tokenReload": "wire-format metric name",
    "hermes.kanban": "wire-format metric/namespace string",
    "hermes.flow": "wire-format namespace string",
}

rng = sys.argv[1] if len(sys.argv) > 1 else "HEAD~10..HEAD"
diff = subprocess.run(["git", "-C", REPO, "log", rng, "-p"],
                      capture_output=True, text=True).stdout

removed = [l for l in diff.split("\n") if l.startswith("-") and not l.startswith("---")]

hard_hits, soft_hits = [], []
for line in removed:
    for tok, why in HARD.items():
        if tok in line:
            hard_hits.append((tok, why, line.strip()[:120]))
            break
    else:
        for tok, why in SOFT.items():
            if tok in line:
                soft_hits.append((tok, why, line.strip()[:120]))
                break

print(f"  range: {rng}")
print(f"  removed lines examined: {len(removed)}")
print()
print(f"  HARD §4 violations: {len(hard_hits)}")
for tok, why, line in hard_hits:
    print(f"    !! {tok}")
    print(f"       why: {why}")
    print(f"       line: {line}")
print()
print(f"  SOFT hits needing judgement: {len(soft_hits)}")
for tok, why, line in soft_hits:
    print(f"    ? {tok}  ({why})")
    print(f"       {line}")

# Confirm the compat layer is untouched.
files = subprocess.run(["git", "-C", REPO, "log", rng, "--name-only", "--format="],
                       capture_output=True, text=True).stdout.split("\n")
compat = sorted({f for f in files if "agent_compat" in f or f.startswith("qubit_runtime/adapters/")})
print()
print(f"  compat-layer / adapters files changed: {len(compat)}")
for f in compat:
    print(f"    !! {f}")
if not compat:
    print("    (untouched -- the foreign-content surface survived)")
