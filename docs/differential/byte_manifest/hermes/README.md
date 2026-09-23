Placeholder so the preferred in-repo byte-manifest directory exists in git.
Drop the pinned HERMES_BYTE_MANIFEST.json here to keep the byte_parity gate
fully in-repo; when absent the runner falls back to the legacy
/tmp/qubit-differential/byte_manifest/hermes/HERMES_BYTE_MANIFEST.json.
