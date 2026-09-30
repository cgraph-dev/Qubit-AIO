#!/usr/bin/env python3
"""Find orphaned ollama blobs (not referenced by any manifest).

Why: interrupted `ollama create` runs leave COPY<rand> temps AND completed-but-
unreferenced blobs (manifest never written), and imports whose requantize did
NOT dedupe leave a second full-size source blob. `ollama list`/`ollama rm`
never see them; they silently eat 10-30GB.

Usage: sudo python3 orphan_check.py   (blob store is root-owned on this box)
Env: OLLAMA_MODELS_DIR to override the store path (default:
     /usr/share/ollama/.ollama/models for the systemd 'ollama' service).

Gotcha handled here: manifests reference digests as "sha256:hex" (colon) while
blob FILENAMES are "sha256-hex" (dash). Normalize both sides to plain hex or
EVERYTHING looks orphaned.
"""
import json, os, sys

root = os.environ.get('OLLAMA_MODELS_DIR', '/usr/share/ollama/.ollama/models')
manifests_dir = os.path.join(root, 'manifests')
blobs_dir = os.path.join(root, 'blobs')

referenced = set()
for dirpath, _, files in os.walk(manifests_dir):
    for f in files:
        p = os.path.join(dirpath, f)
        try:
            with open(p) as fh:
                m = json.load(fh)
            for layer in m.get('layers', []):
                d = layer.get('digest', '')
                if d:
                    referenced.add(d.split(':', 1)[-1])
            cfg = m.get('config', {})
            if cfg.get('digest'):
                referenced.add(cfg['digest'].split(':', 1)[-1])
        except Exception as e:
            print(f'WARN manifest {p}: {e}', file=sys.stderr)

orphans = []
total = 0
for b in os.listdir(blobs_dir):
    path = os.path.join(blobs_dir, b)
    if not os.path.isfile(path):
        continue
    sz = os.path.getsize(path)
    total += sz
    digest = None
    if b.startswith('sha256-'):
        digest = b.split('-', 1)[1].rsplit('-', 1)[0]  # plain hex
    if digest is None or digest not in referenced:
        orphans.append((b, sz))

print(f'blobs total: {total/2**30:.2f} GiB')
print(f'orphaned:    {sum(s for _, s in orphans)/2**30:.2f} GiB ({len(orphans)} files)')
for b, sz in sorted(orphans, key=lambda x: -x[1])[:20]:
    print(f'  {b}  {sz/2**30:.2f} GiB')
if orphans:
    print('\ndelete with: sudo rm -f ' + ' '.join(
        os.path.join(blobs_dir, b) for b, _ in orphans))
