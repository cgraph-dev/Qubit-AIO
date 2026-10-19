"""Behavioural probe: _profile_default_cwd / _is_other_profiles_cwd are CALLED and resolve in
the SESSION's profile scope, not the launch profile's.

Run from qubit_runtime/:  PYTHONPATH=. ../.venv/bin/python -W ignore scripts/_probe_laneA_workdir_scope.py

Builds a disposable topology:
  <tmp>/runtime            the launch home (what the process is bound to)
  <tmp>/profiles/box       a real named secondary profile (lives under the launch profiles root)
and asserts:
  1. _resumable_stored_cwd CALLS _is_other_profiles_cwd: a box row holding the launch home / the
     root / the install tree is set aside (""), an ordinary folder is kept.
  2. _completion_cwd CALLS _profile_default_cwd, and a box session with no stored cwd lands in
     box's own home — never the launch home or the launch profile's TERMINAL_CWD.
  3. The resolution ran bound to box (QUBIT_HOME override inside the call), and os.environ is
     unchanged afterwards.
"""
import os
import tempfile
from pathlib import Path

base = Path(tempfile.mkdtemp(prefix="laneA-scope-"))
launch = base / "runtime"
profiles_root = launch / "profiles"
box = profiles_root / "box"
box.mkdir(parents=True)
(launch / "config.yaml").write_text("terminal:\n  cwd: /tmp\n")          # launch profile's workspace
(box / "config.yaml").write_text("terminal:\n  backend: local\n")        # box: no terminal.cwd
os.environ["QUBIT_HOME"] = str(launch)
os.environ["TERMINAL_CWD"] = "/tmp"                                       # the launch profile's cwd

from tui_gateway import server, session_workdir  # noqa: E402

server._qubit_home = server.get_process_qubit_home()                      # re-home the process
print("launch home :", server._launch_home())
print("box home    :", box)
print("box resolves to _profile_home('box') ->", server._profile_home("box"), "| launch profile for 'default' ->",
      server._profile_home("default"))

# Count the calls the code under test must make.
seen = {"other": 0, "default": 0}
real_other, real_default = server._is_other_profiles_cwd, server._profile_default_cwd


def spy_other(cwd, profile_home):
    seen["other"] += 1
    return real_other(cwd, profile_home)


def spy_default(profile_home):
    seen["default"] += 1
    return real_default(profile_home)


server._is_other_profiles_cwd, server._profile_default_cwd = spy_other, spy_default

print("\n-- 1. _resumable_stored_cwd -> _is_other_profiles_cwd (named local profile 'box') --")
for label, stored in (("box's own home        ", str(box)),
                      ("launch profile home   ", str(launch)),
                      ("sibling profile tree  ", str(profiles_root / "other" / "proj")),
                      ("ordinary user folder  ", "/tmp/proj")):
    kept = server._resumable_stored_cwd(stored, box)
    print(f"  {label} {stored:<60} -> kept={kept!r}")
print("  _is_other_profiles_cwd calls:", seen["other"])

print("\n-- 2. _completion_cwd -> _profile_default_cwd --")
scope_hits = []                       # profile_home bound by each _session_profile_runtime_scope entry
real_tokens = server._profile_runtime_scope_tokens


def spy_tokens(profile_home, **kw):
    scope_hits.append(str(profile_home))
    return real_tokens(profile_home, **kw)


server._profile_runtime_scope_tokens = spy_tokens
env_before = dict(os.environ)
landed = server._completion_cwd({"profile": "box"})
print("  _completion_cwd({'profile':'box'}) ->", landed)
print("  lands in box home      :", Path(landed) == box)
print("  never the launch home  :", Path(landed) != launch)
print("  _profile_default_cwd calls:", seen["default"], "| scope binds seen:", scope_hits)
print("  os.environ unchanged   :", dict(os.environ) == env_before)

print("\n-- 2b. the same pair called with a profile NAME binds that profile's scope --")
scope_hits.clear()
named = server._profile_default_cwd("box")
print("  _profile_default_cwd('box') ->", named)
print("  scope binds seen:", scope_hits)

print("\n-- 3. resume fallback: methods_session.session.resume uses _profile_default_cwd --")
import inspect  # noqa: E402
src = inspect.getsource(server._methods["session.resume"])
print("  resume body references _profile_default_cwd:", "_profile_default_cwd(ctx.profile_home)" in src)
print("  server._hydrate_session_cwd references _is_other_profiles_cwd:",
      "_is_other_profiles_cwd(row[\"cwd\"], profile_home)" in inspect.getsource(server._hydrate_session_cwd))
