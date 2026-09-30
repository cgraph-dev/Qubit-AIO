# Voice config persistence & multi-PC portability — Aug 2026-08-23

Why: a `git pull` on another PC must restore the exact tuned voice. Otherwise `voice.yaml`/`task-templates.yaml`/`personal-knowledge.md` drift and the bridge behaves differently.

## What syncs and how

| Item | Live path | Repo template | `scripts/install.sh` behavior |
|---|---|---|---|
| `personal-knowledge.md` | `~/.config/openclaw-voice-bridge/personal-knowledge.md` | `config/personal-knowledge.md` (Qubit/Tricky identity) | `cp .../personal-knowledge.md "$CONFIG_DIR/personal-knowledge.md"` **only if missing** |
| `voice.yaml` | `~/.config/openclaw-voice-bridge/voice.yaml` | `config/voice.yaml` (7.7 KB, checked in) + `config/voice.example.yaml`/`config/voice.local.yaml` | same: copies `voice.example.yaml` only if missing; `voice.yaml` carried in repo so fresh clones get today's tuned values |
| `task-templates.yaml` | `~/.config/openclaw-voice-bridge/task-templates.yaml` | `config/task-templates.yaml` | copied only if missing; today: 16 templates (`what-day` removed in favor of `local:time_date`, `search-web` broad) |

`$QUBIT_HOME/scripts/auto-push.sh` now syncs these three before every push:

```bash
# Carry live voice config/personal-knowledge into the bridge repo so a fresh
# install on another PC gets today's tuned values (install.sh copies them to ~/.config).
for f in personal-knowledge.md voice.yaml task-templates.yaml; do
  src="$HOME/.config/openclaw-voice-bridge/$f"
  dst="$HOME/Projects/Repos/openclaw-voice-bridge/config/$f"
  if [ -f "$src" ]; then cp -f "$src" "$dst" 2>/dev/null || true; fi
done
```

## Four-repo nightly auto-push (was three)

`$QUBIT_HOME/scripts/auto-push.sh` pushes **four** repos nightly via `qubit cron` (`auto-push repos`, `30 23 * * *`, `no_agent: true`, `script: auto-push.sh`):

1. `qubit-voice-bridge`
2. `voice-dashboard` ← new (fixes were previously local-only and would be lost)
3. `openclaw-home`
4. `hermes-home` (skills/memory/cron/config — see `.hermes/.gitignore` whitelist `!/state.db` excepted below)

`openclaw-home` also pushed via the job; `voice-dashboard` remote uses an embedded `gho_` token and `git ls-remote` proves pushability.

## GitHub 100 MB file-size trap

`$QUBIT_HOME/state.db` grew to 170 MB → GitHub rejects any push containing it in the delta (`GH001 Large files`). Fix:

- `.hermes/.gitignore`: `!/state.db` whitelist disabled (commented) — intentionally not tracked.
- `.hermes/.git/info/exclude`: `state.db` (sidesteps the pre-receive hook's full-push scan; file stays on disk).
- For a 300 MB+ state DB, use `git lfs` — but for now local-only is correct; sessions/... dumps still preserve transcripts.

## On the other PC

```bash
gh repo clone cgraph-dev/openclaw-voice-bridge && ./scripts/install.sh --with-systemd
gh repo clone cgraph-dev/voice-dashboard       # clone next to qubit-voice-bridge (WebUI auto-discovers ../voice-dashboard/frontend)
# qubit/openclaw homes restore via their own installers pulling private repos
systemctl --user restart qubit-voice-bridge
curl -s http://127.0.0.1:8890/api/status | python -m json.tool
curl -s http://127.0.0.1:8890/api/logs?limit=3 | python -m json.tool
```

## Pitfall (2026-08-23)

Commit `b3a1b6e` included the 170 MB `state.db`; `git push` was rejected even after `git reset --soft` + `git restore` because the object was still in the pending pack. Had to `git rm --cached state.db` + `git commit --amend` + `.git/info/exclude`. If cron stuck at `next_run_at 2026-08-22T23:30` in the past, it recovers on next tick — no manual fix needed unless `last_status: failed`.

## Verification

```bash
git -C ~/Projects/Repos/openclaw-voice-bridge log --oneline -1   # 7d08472 non-blocking EN/RO isolation...
git -C ~/Projects/Repos/voice-dashboard log --oneline -1         # 46241cd instant interrupt while busy...
git -C $QUBIT_HOME log --oneline -1                                # fcbaedd chore: usage snapshot
git -C ~/.openclaw log --oneline -1                              # 7960e1b auto-push ...
ls -lh voice-dashboard/frontend/index.html voice-dashboard/frontend/activity.html  # 290K/66K external, not 40M inline
```
