# PersonaPlex on Vast.ai — deployment guide

The Vast.ai box runs **only** the PersonaPlex speech service. All state,
memory, and tooling stays on your PC.

## 1. Rent an instance

With the `vastai` CLI (already on your PC, key configured):

```bash
# 24 GB+ VRAM, 1 GPU, Ubuntu, 100+ GB disk, no public ports
vastai search offers "gpu_ram>=24 num_gpus=1 cuda_vers>=12.4 rented=false disk_space>=100 \
  verified=true" --order "dph-"

# pick an offer id, then:
vastai create instance <offer_id> \
  --image pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime \
  --disk 120 --direct --env '-e HF_HOME=/root/.cache/huggingface' \
  --onstart-cmd "true"
```

Notes:
- **GPU**: RTX 4090 / 3090 / A6000 / A10G are fine. RTX 5090 (Blackwell)
  works too — the setup script detects it and installs cu130 torch.
- **VRAM**: weights are 15.6 GB bf16 → 24 GB is the comfortable minimum.
  `--cpu-offload` exists but is slow; don't cheap out below 24 GB.
- **Public ports**: none needed. Everything goes over Tailscale. Vast's
  firewall can stay closed — if you must expose a port (Web UI debugging),
  restrict it to your IP and remove it after.

## 2. On the instance

```bash
# copy the kit to the box (from your PC)
scp -r vast/ root@<vast-host>:/root/
ssh root@<vast-host>

# run the one-shot setup (license + token first, see step 3)
export HF_TOKEN=hf_xxxxxxxx
export TS_AUTHKEY=tskey-auth-xxxxxxxx   # optional but strongly recommended
cd /root/vast && ./setup_personaplex.sh
```

The script: installs deps + Tailscale, clones NVIDIA/personaplex, installs the
`moshi` package with the right torch build, pre-downloads the 16 GB model +
voice prompts, generates self-signed certs for your tailnet hostname, and
starts a systemd service bound to the **tailnet IP only** (`:8998`).

## 3. One-time license acceptance (you, in a browser)

1. Log in to https://huggingface.co
2. Open https://huggingface.co/nvidia/personaplex-7b-v1 and click **Agree** on
   the NVIDIA Open Model license.
3. Create a read token: https://huggingface.co/settings/tokens → `hf_...`

## 4. Verify from the box

```bash
curl -sk https://$(tailscale ip -4):8998/ | head            # web UI (cert warning is fine)
python3 /root/vast/check_personaplex.py wss://$(tailscale ip -4):8998/api/chat
journalctl -u personaplex -f                                  # startup logs
```

## 5. Point the bridge at it (your PC)

In `~/.config/openclaw-voice-bridge/voice.yaml`:

```yaml
personaplex:
  enabled: true
  url: wss://personaplex:8998/api/chat   # MagicDNS name, or the tailnet IP
  ssl_insecure: true
  voice_prompt: NATF2.pt
```

Then `voicebridge personaplex-check` (from your PC, on the tailnet) should
print `OK handshake in ~NN ms`.

## 6. Rotating instances (no memory loss)

Memory/session state lives in the OpenClaw workspace **on your PC**. To swap
the GPU box:

1. `vastai destroy instance <id>` (or stop it).
2. Rent a new one and rerun `setup_personaplex.sh` (steps 2–4). Reuse the same
   Tailscale auth key and hostname (`personaplex`) so the MagicDNS name is stable.
3. Nothing else changes — the bridge reconnects to `wss://personaplex:8998`.

If you use a different hostname/IP, update `personaplex.url` in voice.yaml and
restart the bridge (`systemctl --user restart openclaw-voice-bridge`).

## 7. Security model

- Tailscale (WireGuard) between PC ⇄ Vast box; server bound to the tailnet IP.
- Self-signed TLS on `:8998` — fine because the transport is already encrypted;
  `ssl_insecure: true` only skips cert *verification*, not encryption.
- OpenClaw gateway stays loopback-only (`bind: loopback`, token auth).
- The Vast box is disposable: no irreplaceable state lives there.
