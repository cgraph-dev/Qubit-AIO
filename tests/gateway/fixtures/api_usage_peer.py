"""Loopback peer with explicit SSE usage for per-admission accounting probes."""

# laneA bootstrap: Qubit repo root + fork runtime root on sys.path.
# The Qubit repo root must be ahead of the runtime root so ``tests`` resolves
# to this checkout's tests package, not the vendored ``qubit_runtime/tests``.
import os as _os, sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve()
while _ROOT.name and not (_ROOT / 'voicebridge').is_dir():
    _ROOT = _ROOT.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))
try:
    from voicebridge import qubit_runtime as _qbh
    _qbh.ensure_path()
except Exception:  # pragma: no cover - standalone/off-repo invocation
    pass
import json

from tests.gateway.fixtures.shared_authority_peer import ModelPeer


class UsageModelPeer(ModelPeer):
    def do_POST(self):
        output = self.wfile

        class StreamUsage:
            def write(self, data):
                if data == b'data: [DONE]\n\n':
                    chunk = {'id': 'chatcmpl-local', 'object': 'chat.completion.chunk',
                             'model': 'local-wire-stub', 'created': 1, 'choices': [],
                             'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}
                    output.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
                return output.write(data)

            def flush(self):
                output.flush()

        self.wfile = StreamUsage()
        try:
            super().do_POST()
        finally:
            self.wfile = output
