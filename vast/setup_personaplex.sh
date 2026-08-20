#!/usr/bin/env bash
# PersonaPlex on Vast.ai — one-shot instance setup (Ubuntu 22.04/24.04, root).
#
# Usage:
#   HF_TOKEN=hf_xxx TS_AUTHKEY=tskey-xxx ./setup_personaplex.sh
#
# - TS_AUTHKEY optional (recommended): joins this box to your tailnet so the
#   bridge reaches PersonaPlex over WireGuard, never over the public internet.
# - HF_TOKEN required: read token for nvidia/personaplex-7b-v1. Accept the
#   model license first: https://huggingface.co/nvidia/personaplex-7b-v1
# - Installs: deps, Tailscale, the PersonaPlex server, self-signed certs for
#   the tailnet hostname, and a systemd unit bound to the tailnet IP only.
set -euo pipefail

HF_TOKEN="${HF_TOKEN:-dummy}"   # placeholder until a real read token is provided
                                # (real token: accept the license + hf_... read token)
TS_AUTHKEY="${TS_AUTHKEY:-}"
REPO="${PERSONAPLEX_REPO:-https://github.com/NVIDIA/personaplex.git}"
INSTALL_DIR=/opt/personaplex
SSL_DIR=/etc/personaplex/ssl
PORT=8998

log() { echo -e "\033[1;34m[setup]\033[0m $*"; }

log "1/8 system deps"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git curl ca-certificates python3-venv python3-pip \
    libopus-dev ffmpeg openssl >/dev/null

log "2/8 Tailscale (private transport)"
if ! command -v tailscale >/dev/null; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi
if [ -n "$TS_AUTHKEY" ]; then
  # container-friendly: start tailscaled manually when systemd isn't running
  # (userspace networking: containers usually have no /dev/net/tun)
  if ! pgrep -x tailscaled >/dev/null 2>&1; then
    mkdir -p /var/lib/tailscale /var/run/tailscale
    nohup tailscaled --tun=userspace-networking --netfilter-mode=off \
      --state=/var/lib/tailscale/tailscaled.state \
      --socket=/var/run/tailscale/tailscaled.sock >/var/log/tailscaled.log 2>&1 &
    for _ in $(seq 1 20); do
      tailscale --socket=/var/run/tailscale/tailscaled.sock status >/dev/null 2>&1 && break
      sleep 1
    done
  fi
  tailscale --socket=/var/run/tailscale/tailscaled.sock up \
    --authkey="$TS_AUTHKEY" --hostname=personaplex --ssh=false --netfilter-mode=off \
    || tailscale --socket=/var/run/tailscale/tailscaled.sock up \
       --authkey="$TS_AUTHKEY" --hostname=personaplex --netfilter-mode=off
fi
TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
TS_HOST="$(tailscale status --json 2>/dev/null | jq -r '.Self.DNSName' 2>/dev/null | sed 's/\.$//' || true)"
: "${TS_IP:=$(hostname -I | awk '{print $1}')}"   # fallback: LAN IP (not recommended)
log "listening on ${TS_IP}${TS_HOST:+ (${TS_HOST})}"

log "3/8 clone + venv"
git clone --depth 1 "$REPO" "$INSTALL_DIR" 2>/dev/null || git -C "$INSTALL_DIR" pull
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet -U pip wheel

log "4/8 GPU detection + torch (Ada=cu121, Blackwell=cu130)"
GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -1 || echo unknown)"
log "GPU: $GPU_NAME"
BLACKWELL=0
case "$GPU_NAME" in
  *RTX\ 50*|*B100*|*B200*|*GB200*|*RTX\ PRO\ 50*) BLACKWELL=1 ;;
esac
if [ "$BLACKWELL" = "1" ]; then
  log "Blackwell GPU -> torch cu130"
  "$INSTALL_DIR/.venv/bin/pip" install --quiet torch torchvision torchaudio \
      --index-url https://download.pytorch.org/whl/cu130
else
  log "Ada/Ampere GPU -> torch cu121 (2.4.x, matches moshi pins)"
  "$INSTALL_DIR/.venv/bin/pip" install --quiet torch==2.4.1 torchvision torchaudio \
      --index-url https://download.pytorch.org/whl/cu121
fi

log "5/8 moshi package (no-deps so torch stays as installed)"
cd "$INSTALL_DIR"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --no-deps moshi/.
grep -v '^torch' moshi/requirements.txt > /tmp/pp-reqs.txt
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r /tmp/pp-reqs.txt
"$INSTALL_DIR/.venv/bin/pip" install --quiet accelerate websockets   # cpu-offload + check script

log "6/8 pre-download model + voices (~16 GB, one time)"
if [[ "$HF_TOKEN" == dummy* ]]; then
  log "skipping model download: HF_TOKEN is a placeholder (set a real read token, then run:"
  log "  HF_TOKEN=hf_xxx .venv/bin/python -c \"from huggingface_hub import snapshot_download; snapshot_download('nvidia/personaplex-7b-v1')\""
  log "  systemctl restart personaplex"
else
  HF_TOKEN="$HF_TOKEN" "$INSTALL_DIR/.venv/bin/python" - <<'EOF'
from huggingface_hub import snapshot_download
snapshot_download("nvidia/personaplex-7b-v1")
print("model + voices cached")
EOF
fi

log "7/8 self-signed certs (SAN: DNS + IP) for wss://"
mkdir -p "$SSL_DIR"
openssl req -x509 -newkey rsa:2048 -keyout "$SSL_DIR/key.pem" -out "$SSL_DIR/cert.pem" \
  -days 365 -nodes -subj "/CN=${TS_HOST:-personaplex}" \
  -addext "subjectAltName=DNS:${TS_HOST:-personaplex},IP:${TS_IP}" >/dev/null 2>&1

log "7b/8 patch NVIDIA moshi server bug (KeyError: 'seed' on any session)"
SP="$("$INSTALL_DIR/.venv/bin/python" -c "import moshi, os; print(os.path.join(os.path.dirname(moshi.__file__), 'server.py'))")"
if grep -q 'int(request\["seed"\]) if "seed" in request.query' "$SP"; then
  python3 - "$SP" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()
s = s.replace('int(request["seed"]) if "seed" in request.query',
              'int(request.query["seed"]) if "seed" in request.query')
open(p, "w").write(s)
print("patched server.py seed bug")
PYEOF
fi

log "8/8 systemd service (bound to $TS_IP:$PORT only)"
sed -e "s|__HOST__|$TS_IP|" -e "s|__HF_TOKEN__|$HF_TOKEN|" \
    /root/personaplex.service > /etc/systemd/system/personaplex.service 2>/dev/null \
  || sed -e "s|__HOST__|$TS_IP|" -e "s|__HF_TOKEN__|$HF_TOKEN|" \
    "$INSTALL_DIR/../personaplex.service" > /etc/systemd/system/personaplex.service
systemctl daemon-reload
systemctl enable --now personaplex

log "done. verify with:"
log "  tailscale ip -4   -> $TS_IP"
log "  curl -sk https://$TS_IP:$PORT/  (web UI; ignore cert warning)"
log "  python3 check_personaplex.py wss://$TS_IP:$PORT/api/chat"
log "  journalctl -u personaplex -f"
