#!/usr/bin/env python3
"""Find orphaned ollama blobs (not referenced by any manifest) — reclaimable space.

Usage: sudo python3 orphan_check.py
       (blob dir is root-owned under systemd ollama; run with sudo)

Why: interrupted `ollama create` imports leave two kinds of junk in
<models>/blobs/: (1) temp files named COPY<rand> or sha256-<number>, and
(2) COMPLETED blobs (sha256-<hex>) whose manifest was never written. Both are
safe to delete once confirmed unreferenced. On a DRAM-less SSD this is often
10-26 GB of dead weight that pushes disk back into freeze territory.

GOTCHA: manifest digests are "sha256:hex" but blob FILENAMES are "sha256-hex"
(or "sha256-hex-<size>" on some versions) — normalize both to plain hex before
comparing, or EVERY blob looks orphaned.

Verify before deleting: sudo rm <models>/blobs/COPY* and the unreferenced
sha256-* files listed below.
"""
import json, os, sys

root = os.environ.get('OLLAMA_MODELS', '/usr/share/ollama/.ollama/models')
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
                    referenced.add(d.split(':', 1)[-1])  # sha256:hex -> hex
            cfg = m.get('config', {})
            if cfg.get('digest'):
                referenced.add(cfg['digest'].split(':', 1)[-1])
        except Exception as e:
            print(f'WARN manifest {p}: {e}', file=sys.stderr)

blob_files = os.listdir(blobs_dir)
total_size = 0
orphan_size = 0
orphans = []
for b in blob_files:
    path = os.path.join(blobs_dir, b)
    if not os.path.isfile(path):
        continue
    sz = os.path.getsize(path)
    total_size += sz
    digest = None
    if b.startswith('sha256-'):
        digest = b.split('-', 1)[1].rsplit('-', 1)[0]  # plain hex
    if digest is None or digest not in referenced:
        orphan_size += sz
        orphans.append((b, sz))

print(f'blobs dir total: {total_size/2**30:.2f} GiB')
print(f'orphaned blobs:  {orphan_size/2**30:.2f} GiB ({len(orphans)} files)')
for b, sz in sorted(orphans, key=lambda x: -x[1]):
    print(f'  {b}  {sz/2**30:.2f} GiB')
