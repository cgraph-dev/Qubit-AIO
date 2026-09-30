# Storage Planning for GGUF Models

## When to use this guide

- A user wants to download a GGUF model and you need to verify it fits
- The model is too large for the current disk and you need to assess upgrade options
- The user asks "can my machine run this model" and you need a full hardware inventory

## 1. Always check available disk first

Before suggesting a model download, check free space:

```bash
df -h /
df -h /home/<user>
```

If multiple drives exist, check each one. The model download may land on `/`, `/home`, or a dedicated data mount.

## 2. Enumerate quant sizes from the tree API

```python
import requests
r = requests.get('https://huggingface.co/api/models/<repo>/tree/main?recursive=true')
data = r.json()
for item in data:
    if item['type'] == 'file' and item['path'].endswith('.gguf'):
        size_gb = item['size'] / 1e9
        print(f'{item["path"]:75s} {size_gb:.1f} GB')
```

### Multi-shard quants

Large MoE models split GGUF files across multiple shards (e.g. `-00001-of-00009.gguf`). The total storage needed is the **sum of all shards** — not just the largest one. The first shard (`-00001-of-00009`) is often 0 bytes (an index file). The last shard may be smaller than the rest.

Before reporting sizes, calculate total per quant group:

```python
from collections import defaultdict
groups = defaultdict(float)
for item in data:
    if item['type'] == 'file' and item['path'].endswith('.gguf'):
        # Extract quant group from path (e.g. "UD-Q3_K_M" from "UD-Q3_K_M/GLM-5.2-UD-Q3_K_M-00002-of-00009.gguf")
        group = item['path'].split('/')[0] if '/' in item['path'] else 'root'
        groups[group] += item['size']
for group, total in sorted(groups.items(), key=lambda x: x[1]):
    print(f'{group:30s} {total/1e9:6.1f} GB')
```

### Single-file quants

Some repos provide a single-file quant (e.g. `DS4/` variants). These are the smallest footprint but still may be 200+ GB for large MoE models.

## 3. Hardware inventory when no quant fits

When even the smallest quant exceeds available disk, run a full inventory:

```bash
# Disk layout
lsblk -o NAME,SIZE,TYPE,MOUNTPOINT,MODEL

# Motherboard + slot info
sudo dmidecode -t baseboard | grep -A5 "Manufacturer\|Product Name"
sudo dmidecode -t slot | grep -B5 -A10 "M.2\|M2"

# NVMe presence
ls /dev/nvme* 2>/dev/null || echo "No NVMe devices"

# PCIe storage controllers (SATA vs NVMe)
lspci | grep -i "SATA\|NVMe\|Non-Volatile\|Storage"

# Memory (for RAM-based offloading decisions)
free -h

# CPU (for inference speed estimation)
cat /proc/cpuinfo | grep "model name" | head -1
```

## 4. Upgrade paths table

| Current situation | Recommended upgrade | Typical cost |
|---|---|---|
| SATA-only, M.2 slot empty | Add M.2 NVMe SSD | $50-130 (1-2 TB) |
| M.2 slot occupied, SATA ports free | Add SATA SSD | Similar cost, slower |
| No free slots/ports | Replace smallest existing drive or add external USB SSD | $50-150 |
| RAM < 32 GB for large models | RAM upgrade | $40-100 |

## 5. MoE model size warning

Some repos use model names like "GLM-5.2" or "Qwen3.6-35B" where the number is a **version number or active parameter count plus auxiliary parameters**, not total storage. Always check GGUF shard sizes to determine actual storage:

| Shard size | Estimated model class |
|---|---|
| ~2-6 GB per shard | 7-14B dense |
| ~10-25 GB per shard | 30-70B dense |
| ~40-50 GB per shard | 200B+ MoE or dense |
| Multiple shards × 48+ GB each | 500B-1T+ MoE |

## 6. Presenting the result

When no quant fits, present a clear table:

````
Available quants:
| Quant | Type | Total Size | Needs ~ |
|---|---|---|---|
| DS4/IQ2_XXS | Single file | 211 GB | 211 GB |
| UD-Q2_K_MXFP4 | 7 shards | 292 GB | 295 GB |

Free disk: 125 GB — **none fit**.

Hardware assessment:
- Board: MSI B450 GAMING PLUS MAX
- M.2 slot: EMPTY (PCIe 3.0 x4)
- RAM: 62 GB (plenty for CPU offloading)
→ Recommend: 2 TB NVMe SSD (~$100-130)
````
