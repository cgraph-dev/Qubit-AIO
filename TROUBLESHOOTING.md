# Troubleshooting

Start with the diagnostics before changing configuration:

```bash
qubit doctor run
qubit status
```

## Dashboard

The dashboard is served by the bridge at <http://127.0.0.1:8890/> and binds to
loopback by default.

Check the service and its log:

```bash
systemctl --user status qubit-voicebridge.service
journalctl --user -u qubit-voicebridge.service -n 100
```

If the dashboard bundle is missing, build it or re-run the installer without
`--without-dashboard`:

```bash
./scripts/build_dashboard.sh
```

To reach the dashboard from another device, opt in explicitly in
`~/.config/qubit-voicebridge/voice.yaml`: set `webui.allow_lan: true`, use a
non-loopback `webui.host`, and set a non-empty `webui.token`. Without
`allow_lan`, the listener stays on loopback even if a LAN host is configured.

## Models

The agent can use a local model or a configured cloud provider. Run
`qubit doctor run` and `qubit status` to see what is available.

- Local model assets are optional. `--without-models` skips them during install.
- Cloud provider keys are optional. See `.env.example` and place keys in
  `~/.config/qubit-voicebridge/.env` when you want those routes.
- If a provider fails, check the key, the network, and the provider status, then
  re-run `qubit doctor run`.

## Audio

Audio is optional; text and dashboard use work without a microphone.

```bash
qubit audio list
qubit audio status
qubit mic-test
```

If the microphone is silent:

1. Check the OS input device, mute state, and gain.
2. Select the correct device with `qubit audio list`.
3. Re-run `qubit mic-test`.

For voice identity, enroll once with `qubit enroll`. If barge-in is too eager or
too strict, re-enroll with a clean recording and adjust the voice-identity
threshold in the voice configuration.

## Missing dependencies

Run the installer preflight to see what is missing:

```bash
./scripts/install.sh --dry-run
```

Common fixes:

- Install `uv`:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- Install Node.js 22+ and `npm` when the dashboard must be built.
- Install `tar` and a SHA-256 tool (`sha256sum`, `shasum`, or `openssl`) when
  model assets are downloaded.
- For development dependencies:
  ```bash
  python -m pip install -e ".[hotkey,edge,test]"
  ```

## Uninstall

Preview the uninstall plan (read-only; state and configuration are preserved by
default):

```bash
qubit package uninstall
```

To remove the installation manually:

```bash
systemctl --user disable --now \
  qubit-voicebridge.service qubit-gateway.service \
  qubit-voice-typing.service qubit-voice-orb.service
rm -f ~/.local/bin/qubit
rm -rf .venv
```

Then remove the source checkout. User data is preserved unless you delete it
yourself:

- config: `~/.config/qubit-voicebridge/`
- data and models: `~/.local/share/qubit-voicebridge/`
- state: `~/.local/state/qubit-voicebridge/`
- runtime home: `~/.qubit`
