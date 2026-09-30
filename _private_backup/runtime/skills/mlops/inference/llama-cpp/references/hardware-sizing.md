# Hardware Sizing & Deployment Planning

When a user finds a GGUF model that doesn't fit their local machine, use this workflow to assess options and recommend the cheapest private path forward.

## 1. Get model size

Use the tree API to find the smallest quant:

```
https://huggingface.co/api/models/<repo>/tree/main?recursive=true
```

Sum shard sizes for multi-file quants (skip the first shard — it's usually 0-byte index). Prefer single-file quants when available — no merging needed.

**Rule of thumb for RAM sizing:** a model needs ~1.2× its file size in RAM to load with `--mlock`. If RAM < 1.2× model size, inference will thrash swap and be unusably slow.

## 2. Check local hardware

```bash
# Disk
df -h /

# RAM
free -h

# GPU (if any)
nvidia-smi  # VRAM

# CPU
lscpu | grep "Model name"
nproc

# Check if M.2 NVMe slot is free
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL
lspci | grep -i "Non-Volatile\|NVMe"
# vs SATA (slower):
lspci | grep -i SATA

# Motherboard for upgrade compatibility
sudo dmidecode -t baseboard 2>/dev/null | grep -A5 "Manufacturer\|Product Name"
sudo dmidecode -t slot 2>/dev/null | grep -B2 -A10 "M.2"
```

## 3. Decision matrix

| Scenario | Action |
|---|---|
| Model fits in RAM + VRAM | Run locally with `--mlock`, GPU offload if available |
| Model fits on disk but not RAM | Needs cloud rental or RAM upgrade |
| Model fits nowhere | Cloud rental, smaller model, or cheaper quant |
| User shows interest in renting | → Section 4 below |

## 4. Cheapest private cloud options for running LLMs

For models that need 150 GB+ RAM/VRAM, these are the best private (dedicated) options:

### 🥇 Hetzner Dedicated Servers — ~$0.15/hr

Fully private, dedicated hardware. Best price/RAM ratio in the market. You get a root server — nothing shared.

| Model | CPU | Max RAM | Est. Price | Good for |
|---|---|---|---|---|
| AX102 | Ryzen 9 7950X3D (16C/32T) | 512 GB DDR5 | ~€106-189/mo ($0.15-0.26/hr) | 200-400 GB models |
| AX52 | i7-12700 (12C/20T) | 256 GB DDR4 | ~€109/mo ($0.15/hr) | Up to ~200 GB models |
| AX161-R | EPYC 9254 (24C/48T) | 256 GB DDR5 ECC | ~€133/mo ($0.18/hr) | Compute-heavy |
| AX162-R | EPYC 9454 (48C/96T) | 256 GB DDR5 ECC | ~€219/mo ($0.30/hr) | Fastest CPU inference |

Set up with llama.cpp server:

```bash
# Install
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp && cmake -B build && cmake --build build --config Release -j$(nproc)

# Download
huggingface-cli download <repo> <quant-file> --local-dir ./models/

# Run with mlock so it stays in RAM
llama-server \
  --model ./models/<model.gguf> \
  --host 0.0.0.0 --port 8080 \
  -c 8192 --mlock
```

### 🥈 Vast.ai — ~$0.10-0.30/hr

Cheapest raw rates. Filter for **verified/exclusive** instances (some are shared). Search for CPU-only high-RAM bundles.

### 🥉 TensorDock — ~$0.20-0.50/hr

Dedicated instances, mid-range pricing.

### ❌ RunPod / Lambda Labs

Mostly GPU-focused. A model this size would need multi-GPU (4× A100 80GB etc.), which costs $2-5+/hr — generally not worth it vs. a dedicated CPU server.

## 5. Expected performance on CPU

For an 800B+ MoE model on a high-end CPU with enough RAM:

| Quant | Approx size | Speed (tok/s) |
|---|---|---|
| IQ2_XXS | ~210 GB | 1-3 tok/s |
| IQ1_M | ~230 GB | 2-4 tok/s |
| Q2_K | ~290 GB | 0.5-2 tok/s |

Active-parameter MoE models (like GLM-5.2) benefit because only a subset of experts activate per token — this makes CPU inference more viable than on dense models of equivalent total size.

## 6. Alternative: drop to a smaller model

If cloud rental is out of scope, recommend a model that fits locally:

| RAM Available | Max Model Size (Q4_K_M) | Good options |
|---|---|---|
| 64 GB | ~50 GB model | Llama 3.1 70B, Qwen 2.5 72B, Command R+ 104B |
| 32 GB | ~25 GB model | Mistral Small 3.1 24B, Qwen 2.5 32B Q8 |
| 16 GB | ~12 GB model | Llama 3.1 8B, Qwen 2.5 7B, Mistral 7B |

For abliterated/uncensored variants, search huggingface with: `<model-family> abliterated GGUF`
