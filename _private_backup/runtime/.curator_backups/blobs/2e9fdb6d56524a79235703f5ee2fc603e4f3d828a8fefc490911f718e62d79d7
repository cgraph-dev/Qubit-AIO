---
name: linux-workstation-setup
description: "Set up a Linux workstation (Ubuntu/Debian) for productive AI agent and development work. Covers system tools, shell environment, Qubit optimization, voice mode setup/troubleshooting, and credential configuration."
version: 1.1.0
author: agent
platforms: [linux]
metadata:
  hermes:
    tags: [setup, workstation, ubuntu, debian, devops, environment, configuration, voice, audio]
---

# Linux Workstation Setup for AI Agent Work

Comprehensive setup of a fresh or existing Ubuntu/Debian workstation for software development, AI agent operations (Qubit), and general productivity.

## Trigger Conditions

- User says "set up my PC", "prepare my machine", "make this computer ready for work"
- User asks to "install missing tools", "check what's missing", "optimize my setup"
- New Qubit installation on Linux needing full environment configuration
- Post-install Ubuntu/Debian needing dev tools, shell, git, docker, node, python stack

## Prerequisites

- Ubuntu 22.04+ or Debian 12+ (tested on Ubuntu 24.04 Noble)
- Internet connection
- `sudo` access (store password in `$QUBIT_HOME/.env` as `SUDO_PASSWORD` for agent use)

## Setup Steps

### 1. System Package Installation

Install core tools via apt. **Do NOT run `apt-get upgrade` unless the user explicitly asks for it.**

```bash
sudo apt-get update -qq
sudo apt-get install -y \
  python3-pip python3-venv python3-full python3-dev \
  build-essential pkg-config libssl-dev \
  ripgrep tmux htop jq fzf tree neofetch \
  unzip p7zip-full ffmpeg imagemagick poppler-utils \
  zsh zsh-autosuggestions zsh-syntax-highlighting
```

**Without sudo workaround:**
If sudo is unavailable, extract individual `.deb` packages:
```bash
mkdir -p ~/.local/bin
cd /tmp
apt download ripgrep
dpkg-deb -x ripgrep_*.deb ripgrep-extract
cp ripgrep-extract/usr/bin/rg ~/.local/bin/
```

### 2. Shell Environment (Zsh)

Install Oh My Zsh + Powerlevel10k for a modern shell experience:

```bash
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
git clone --depth=1 https://github.com/romkatv/powerlevel10k.git ~/.oh-my-zsh/custom/themes/powerlevel10k
```

Configure `~/.zshrc`:
- `ZSH_THEME="powerlevel10k/powerlevel10k"`
- `plugins=(git zsh-autosuggestions zsh-syntax-highlighting)`

### 3. Python Environment

System Python on Ubuntu 24.04+ has PEP 668 restrictions. Use `--break-system-packages` for user installs only:

```bash
python3 -m pip install --user --break-system-packages pip uv
```

Or prefer installing into the Qubit venv directly:
```bash
$QUBIT_HOME/hermes-agent/venv/bin/pip install --upgrade pip uv
```

### 4. Node.js Tooling

Install global npm packages useful for web dev and tooling:
```bash
npm install -g yarn npm-check-updates @antfu/ni serve http-server pm2
```

### 5. Git Configuration

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
git config --global init.defaultBranch main
git config --global push.default simple
git config --global core.editor "code --wait"
git config --global core.autocrlf input
git config --global pull.rebase true
git config --global fetch.prune true
git config --global diff.colorMoved zebra
```

### 6. Qubit-Specific Optimization

#### Enable all productive toolsets:
```bash
qubit tools enable web browser terminal file code_execution vision video image_gen tts skills todo memory session_search clarify delegation cronjob spotify homeassistant
```

#### Core config optimizations:
```bash
qubit config set checkpoints.enabled true
qubit config set approvals.mode smart
qubit config set stt.enabled true
qubit config set stt.provider local
qubit config set display.show_cost true
qubit config set display.show_reasoning true
qubit config set display.timestamps true
qubit config set display.bell_on_complete true
qubit config set agent.max_turns 90
qubit config set terminal.timeout 300
qubit config set terminal.persistent_shell true
qubit config set memory.memory_enabled true
qubit config set memory.user_profile_enabled true
qubit config set delegation.orchestrator_enabled true
qubit config set security.redact_secrets true
qubit config set privacy.redact_pii true
```

#### Set all auxiliary providers to the user's primary provider (prevents silent failures):
```bash
qubit config set auxiliary.vision.provider <primary-provider>
qubit config set auxiliary.compression.provider <primary-provider>
qubit config set auxiliary.web_extract.provider <primary-provider>
qubit config set auxiliary.skills_hub.provider <primary-provider>
qubit config set auxiliary.title_generation.provider <primary-provider>
qubit config set auxiliary.mcp.provider <primary-provider>
qubit config set auxiliary.approval.provider <primary-provider>
qubit config set auxiliary.curator.provider <primary-provider>
qubit config set auxiliary.triage_specifier.provider <primary-provider>
qubit config set auxiliary.kanban_decomposer.provider <primary-provider>
```

#### Enable gateway usage for Qubit subscription tools:
```bash
qubit config set web.use_gateway true
qubit config set browser.use_gateway true
qubit config set tts.use_gateway true
qubit config set stt.use_gateway true
```

### 7. Credentials Setup

Export GitHub token for Skills Hub rate limits:
```bash
gh auth token | xargs -I {} qubit config env-path | xargs -I {} sed -i "s/^GITHUB_TOKEN=.*/GITHUB_TOKEN={}/" {}
```

Or use Python for reliable in-place editing when `xargs` and `sed` get tricky with special characters:
```python
import os
token = open('/tmp/gh_tok').read().strip()
env_path = os.path.expanduser('$QUBIT_HOME/.env')
lines = list(open(env_path))
for i, line in enumerate(lines):
    if line.startswith('GITHUB_TOKEN='):
        lines[i] = f'GITHUB_TOKEN={token}\n'
open(env_path, 'w').writelines(lines)
```

Store sudo password for agent use:
```bash
echo "SUDO_PASSWORD=your_password" >> $QUBIT_HOME/.env
```

### 8. Shell Completions

```bash
mkdir -p ~/.bash_completion.d
qubit completion bash > ~/.bash_completion.d/qubit
echo 'source ~/.bash_completion.d/qubit' >> ~/.bashrc

mkdir -p ~/.zsh/completions
qubit completion zsh > ~/.zsh/completions/_hermes
echo 'fpath+=(~/.zsh/completions)' >> ~/.zshrc
```

### 9. Voice Mode Setup (Local STT + TTS)

Qubit voice mode requires Python audio libraries in the Qubit venv:

```bash
# Install PortAudio system library first
sudo apt-get install -y libportaudio2 libportaudiocpp0 portaudio19-dev

# Install Python audio deps in Qubit venv
$QUBIT_HOME/hermes-agent/venv/bin/pip install sounddevice pyaudio SpeechRecognition
```

Enable voice in config:
```bash
qubit config set stt.enabled true
qubit config set stt.provider local
qubit config set voice.beep_enabled true
```

**Usage in interactive session:**
```
qubit
> /voice on
# Press Ctrl+B to record, speak, press Ctrl+B to stop
```

### 10. Workspace Directories

```bash
mkdir -p ~/Projects ~/Work ~/Downloads/tools ~/Backups
```

## Qubit-Specific Fixes

### Fix npm vulnerability in ui-tui
```bash
cd $QUBIT_HOME/hermes-agent/ui-tui
npm audit fix
```

### Install Qubit venv packages
```bash
$QUBIT_HOME/hermes-agent/venv/bin/pip install \
  python-telegram-bot faster-whisper aiohttp
```

### Update all skills
```bash
qubit skills update
```

## Pitfalls

1. **NEVER run `apt-get upgrade` without explicit user consent.** It can break the system, change kernel modules, or break GPU drivers. Always ask first.

2. **PEP 668 on Ubuntu 24.04+**: System Python refuses `--user` installs without `--break-system-packages`. Prefer Qubit venv installs or use `uv` for isolated environments.

3. **Rich CLI dependency conflict**: Installing `rich-cli` downgrades `rich` and breaks Qubit. If accidentally installed, uninstall `rich-cli` and `textual`, then reinstall `rich==14.3.3`.

4. **GitHub token masking in `.env`**: The `.env` file has comment lines that look like `GITHUB_TOKEN=`. Be careful with sed replacements — use line-number aware edits or Python scripts.

5. **Sudo timeout**: `sudo` commands in terminal tool may timeout waiting for approval. Set `SUDO_PASSWORD` in `.env` and ensure the config has `terminal.timeout` ≥ 300.

6. **Skill updates vs memory**: When the user corrects workflow (e.g., "don't upgrade"), save the lesson to the SKILL.md, not just memory. Memory is for who/what; skills are for how.

7. **TUI crash with `Invalid flags supplied to RegExp constructor 'v'`**: If `qubit --tui` crashes at startup with this error in `string-width/index.js`, the system has multiple Node installs and the wrong one (v18-) is being picked up. The `v` RegExp flag (unicodeSets) requires Node 20+.

   **Check which Node the TUI would use:**
   ```bash
   # See all Node binaries on PATH (system v18 vs mise-managed v22+)
   which -a node
   node --version
   ```

   **Fix — pin the correct Node for Qubit TUI:**
   ```bash
   # Find your Node 20+ binary from mise/nvm:
   export HERMES_NODE="$HOME/.local/share/mise/installs/node/22/bin/node"
   ```
   Add to `~/.zshrc`/`~/.bashrc` so it persists across sessions.

   **Root cause**: `_launch_tui` in the Qubit CLI calls `shutil.which("node")`, which follows PATH. If the shell launching `qubit` doesn't have mise/nvm shims first in PATH (e.g. desktop launcher, cron, systemd), it picks up `/usr/bin/node` at v18.

8. **Professional audio interfaces (Focusrite, etc.) and voice mode**: Hardware audio interfaces often produce very low software-level signals because gain is controlled by the hardware knob, not the OS. Qubit' default `voice.silence_threshold` of **200** will discard all recordings as "too quiet."
   - **Symptom**: `Ctrl+B` appears to do nothing, or recordings return `None` / empty transcript.
   - **Root cause**: `AudioRecorder.stop()` checks `peak_rms < SILENCE_RMS_THRESHOLD` (default 200). Professional interfaces show RMS of 1–5 at normal gain.
   - **Fix**: Lower the threshold to match the hardware:
     ```bash
     qubit config set voice.silence_threshold 20
     ```
     If still too quiet, go lower (10–15). The user should also turn up the hardware gain knob on their interface.
   - **Verification**: Run a direct Python test to see actual RMS values:
     ```python
     from tools.voice_mode import AudioRecorder
     import time
     rec = AudioRecorder()
     rec.start()
     time.sleep(3)
     print("Peak RMS:", rec._peak_rms)
     path = rec.stop()
     print("Path:", path)
     rec.shutdown()
     ```
     If `Peak RMS` < 200 and `Path` is `None`, the threshold is the problem.
   - **Preferred input device**: Use PipeWire/Pulse `default` device (index 10 on most systems) rather than raw ALSA hardware devices. Raw ALSA devices may fail with `Invalid sample rate` errors at 16kHz.

## Voice Troubleshooting Reference

See `references/voice-troubleshooting.md` for a full debugging transcript and device-selection recipes.

## TUI Node.js Troubleshooting Reference

See `references/tui-node-version-debug.md` for the full reproduction recipe, diagnosis commands, and fix for the "Invalid flags supplied to RegExp constructor 'v'" crash caused by Node.js v18+ vs v20+ conflicts.

## Verification

After setup, run:
```bash
qubit doctor --fix
qubit status
```

Expected: Only "Run 'qubit setup' to configure missing API keys" should remain — this is expected if the user hasn't added backup providers.

## I/O Performance Diagnostics Reference

When the user reports programs crashing or freezing during write-heavy operations (Steam updates, file transfers, system upgrades), see `references/linux-io-performance-diagnostics.md` for the full diagnostic protocol and fix procedures. Key root cause to check first: **`vm.dirty_ratio` is too high for the system's RAM size**, causing multi-GB write-back backlogs.

### Provider/Model routing diagnosis

When a model works on one provider but fails on another (e.g.
`deepseek-v4-flash` rejected by `opencode-zen/go` while `ox-alpha-free` and
`laguna-s-2.1-free` succeed), the cause is almost always a **model catalog
mismatch**, not a credential problem. Each Qubit provider has a curated model
list — OpenCode Go/Zen does **not** serve DeepSeek-native model IDs. Diagnose
by inspecting the credential pool in `$QUBIT_HOME/auth.json`:

```python
import json, os
with open(os.path.expandar('$QUBIT_HOME/auth.json')) as f:
    auth = json.load(f)
pool = auth.get('credential_pool', {})
for p in ['opencode-go', 'deepseek', 'openrouter']:
    if p in pool:
        c = pool[p][0]
        print(p, c.get('last_status'), c.get('last_error_code'),
              c.get('last_error_message'), c.get('base_url'))
```

Key fields: `last_status` (healthy → exhausted), `last_error_code` (401 =
unsupported model, 402 = billing), `last_error_message` (server's own
text), `base_url` (the actual endpoint hit). If the model isn't in the
provider's catalog, the server 401s with "Model … is not supported". Fix:
switch to a provider whose catalog includes the model, or pick a model the
current provider actually serves. Full recipe: `references/opencode-model-routing-diagnosis.md`.

To switch providers/models in Qubit config (and apply with `/reset`),
see `references/hermes-model-provider-switching.md`.

## Related Skills

- `qubit-agent` — Qubit-specific CLI and configuration
- `github-auth` — Detailed GitHub authentication workflows
- `docker-management` — Docker containers and images
