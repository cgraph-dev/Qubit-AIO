# Contributing

## Development setup

Requirements:

- Linux (the supported release target is Ubuntu x86_64).
- Python **3.12 or newer, below 3.15**.
- Node.js **22.12 or newer** for the dashboard and desktop tooling (Vite 8
  requires `^20.19` or `>=22.12`).
- `git`, `bash`, and the usual build toolchain.

Create a virtual environment and install the project with its development
extras:

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[hotkey,edge,test]"
```

Run the focused tests for the area you changed:

```bash
python -m pytest tests -q
```

Keep test runs focused; run the wider suite only when the change needs it.

## Pull requests

- Keep each pull request focused on one behavior or refactor.
- Add or update tests for every behavior change.
- Do not commit secrets, local runtime state, model caches, or generated build
  output.
- Update the public documentation when a user-facing surface changes.
- Describe the evidence you ran, not just the change.

## Commit style

Use a concise conventional prefix, for example:

```text
fix(voice): stop wake capture during playback
feat(office): expose room snapshot endpoint
docs(readme): clarify local-only setup
```

## Security

Report vulnerabilities through `SECURITY.md`, not a public issue.
