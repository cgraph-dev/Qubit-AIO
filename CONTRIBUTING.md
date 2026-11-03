# Contributing

## Development Setup

Requirements:

- Linux with Python 3.14
- Node.js 22+ for the frontend and desktop tooling
- `git`, `make`, and the usual build toolchain

Create a virtual environment and install the project in editable mode:

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

Run the focused test suite for the area you changed:

```bash
python -m pytest tests -q
```

The full matrix runner is available through the project’s documented test tooling. Do not
run release-wide matrices for a small change.

## Pull Requests

- Keep each pull request focused on one behavior or refactor.
- Add or update tests for every behavior change.
- Do not commit secrets, local runtime state, model caches, or generated build output.
- Update the public documentation when a user-facing surface changes.
- Describe the evidence you ran, not just the change.

## Commit Style

Use a concise conventional prefix, for example:

```text
fix(voice): stop wake capture during playback
feat(office): expose room snapshot endpoint
docs(readme): clarify local-only setup
```

## Security

Report vulnerabilities through `SECURITY.md`, not a public issue.
