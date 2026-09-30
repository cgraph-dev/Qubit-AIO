#!/usr/bin/env python3
"""
Fix GITHUB_TOKEN line in ~/.hermes/.env reliably.
Handles cases where sed/xargs fail due to token characters or line numbering.

Usage:
    python3 fix_github_token.py
"""
import os

def fix_github_token(token_path="/tmp/gh_tok", env_path=None):
    if env_path is None:
        env_path = os.path.expanduser("~/.hermes/.env")

    with open(token_path, "r") as f:
        token = f.read().strip()

    with open(env_path, "r") as f:
        lines = f.readlines()

    updated = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("GITHUB_TOKEN=") or stripped == "GITHUB_TOKEN=***             lines[i] = f"GITHUB_TOKEN={token...= True

    if updated:
        with open(env_path, "w") as f:
            f.writelines(lines)
        print(f"Updated GITHUB_TOKEN in {env_path}")
    else:
        print(f"No GITHUB_TOKEN line found in {env_path}")

if __name__ == "__main__":
    fix_github_token()
