"""Independent §4 verification, run by me rather than trusting the agent's report."""
import subprocess

REPO = "/home/trick/Projects/Repos/qubit-voicebridge"
BASE = "7c8e19192"


def sh(args):
    return subprocess.run(args, cwd=REPO, capture_output=True, text=True).stdout


print("=== §4 tokens still present (grep of tracked .py) ===")
for tok in ("HERMES_HOME", "hermes_agent.plugins", "requires_hermes", "HermesCLI",
            "hermes-locale", "X-Hermes-Session-Token", "HERMES_OVERLAYS", "_HERMES_USER_AGENT"):
    out = sh(["git", "grep", "-lI", "--", tok, "--", "*.py"])
    n = len([l for l in out.split("\n") if l])
    flag = "OK" if n else "!! MISSING"
    print(f"  {tok:28} {n:4d} files  {flag}")

print()
print("=== compat surface changed by this wave? ===")
for f in ("voicebridge/agent_compat/framework.py",
          "voicebridge/agent_compat/adapters/hermes.py",
          "voicebridge/agent_compat/adapters/openclaw.py",
          "voicebridge/agent_compat/__init__.py"):
    d = sh(["git", "diff", f"{BASE}..HEAD", "--stat", "--", f])
    status = "CHANGED" if d.strip() else "untouched"
    print(f"  {f:52} {status}")

print()
print("=== commits this wave ===")
print(" ", sh(["git", "rev-list", "--count", f"{BASE}..HEAD"]).strip(), "commits")
