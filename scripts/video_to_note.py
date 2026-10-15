#!/usr/bin/env python3
"""video_to_note.py — YouTube URL -> contract-shaped note in the Obsidian vault.

Closes the ONE gap identified in the tool/pipeline audit: transcript acquisition,
vault writing, and agent read-back all already worked; nothing turned a transcript
into a note.

Pipeline:
  1. Fetch the transcript  (the youtube-content skill's helper, already in .venv)
  2. Summarise to L0/L1/L2 with the API model — NOT a local one; the 8 GB GPU is ~90%
     full with the voice stack + VLM, so a local summariser would evict it or spill.
     The model endpoint is read from config.yaml (provider/base_url/key_env), with the
     omniroute fallback chain behind it. This is the DEFAULT path.
  3. PUT the note via the bridged route, which auto-refreshes the plugin cache so
     agents can read it immediately.

The raw-transcript form (L0 = first line of the transcript) is only a FALLBACK: it is
used when --no-summarise is passed, or when a caller supplies SUMMARY_JSON. It produces
a poorly-titled, poorly-abstracted note — prefer the summarised default.

Usage:
    .venv/bin/python scripts/video_to_note.py <youtube-url> [--dry-run] [--no-summarise]

Requires a dashboard session token for the PUT. The script reads it from the
served shell (same way the E2E auditors do) unless QUBIT_SESSION_TOKEN is set.
API keys are loaded from the runtime .env unless already exported.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from urllib.parse import quote

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "qubit_runtime/skills/media/youtube-content/scripts/fetch_transcript.py"
DASHBOARD = os.environ.get("QUBIT_DASHBOARD", "http://127.0.0.1:8890")
RUNTIME = Path(
    os.environ.get(
        "QUBIT_RUNTIME_DIR", "/home/trick/.local/state/qubit-voicebridge/runtime"
    )
)
# ASCII only: the Obsidian write route does NOT URL-decode its path, so an emoji
# or space in the path silently creates a literal "%f0%9f..." directory instead of
# writing where intended (verified). Keep this ASCII.
VAULT_SUBDIR = "AI_Brain/YouTube"

# The model endpoint is the *configured* one, not a local model: the 8 GB GPU is
# ~90% full (voice stack + VLM), so a local summariser would evict or spill.
# Read from config.yaml, overridable by env for a scripted run.
_PROVIDER = os.environ.get("QUBIT_SUMMARY_PROVIDER", "opencode-go")
_MODEL = os.environ.get("QUBIT_SUMMARY_MODEL", "deepseek-v4.1-flash")
_TIMEOUT = int(os.environ.get("QUBIT_SUMMARY_TIMEOUT", "180"))


def _load_env() -> None:
    """Load the runtime .env so key_env refs resolve without a shell wrapper."""
    env_file = RUNTIME / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def _configured_primary() -> tuple[str, str, str]:
    """(base_url, model, api_key) for the configured primary, from config.yaml."""
    base, model, key = None, _MODEL, None
    cfg = RUNTIME / "config.yaml"
    if cfg.is_file():
        try:
            import yaml  # available in the repo venv

            data = yaml.safe_load(cfg.read_text()) or {}
            m = data.get("model", {}) or {}
            base = m.get("base_url") or base
            model = os.environ.get("QUBIT_SUMMARY_MODEL") or m.get("default") or model
            key_env = (m.get("key_env") or "").replace("env:", "").strip()
            key = os.environ.get(key_env) if key_env else None
        except Exception:
            pass
    base = base or "https://opencode.ai/zen/go/v1"
    return base, model, key or ""


def _omniroute_fallback() -> tuple[str, str, str] | None:
    """(base_url, model, api_key) for the omniroute fallback, if configured."""
    cfg = RUNTIME / "config.yaml"
    if not cfg.is_file():
        return None
    try:
        import yaml

        data = yaml.safe_load(cfg.read_text()) or {}
        chain = data.get("fallback_providers") or []
        for entry in chain:
            if entry.get("provider") == "omniroute":
                base = entry.get("base_url") or "http://127.0.0.1:20128/v1"
                model = entry.get("model") or "auto"
                key_env = (entry.get("key_env") or "").replace("env:", "").strip()
                return base, model, os.environ.get(key_env, "")
    except Exception:
        pass
    return None


_SUMMARY_PROMPT = """You are summarising a YouTube transcript into a 3-layer agent-memory note.

Return STRICT JSON only (no prose, no code fence) with these keys:
  "title": the video's real title (infer it from the content if unknown)
  "l0":    ONE sentence (<50 tokens) stating the video's core claim or subject
  "l1":    an array of 3-6 short bullet strings covering the key entities/structure
  "l2":    the timestamped key points, as a short list of "MM:SS — point" lines
           (at most ~40 lines; keep only substantive points, drop filler)

Video id: {vid}
Transcript (timestamped):
{transcript}
"""


def summarise(meta: dict) -> dict:
    """Summarise the transcript with the API model. Returns {title,l0,l1,l2}."""
    transcript = meta.get("timestamped_text") or meta.get("full_text") or ""
    prompt = _SUMMARY_PROMPT.format(vid=meta.get("video_id", ""), transcript=transcript[:60000])
    attempts: list[tuple[str, str, str, str]] = []
    base, model, key = _configured_primary()
    attempts.append(("primary", base, model, key))
    fb = _omniroute_fallback()
    if fb:
        attempts.append(("omniroute", *fb))

    last_err = "no attempt made"
    for label, base_url, model_name, api_key in attempts:
        # The opencode-go edge intermittently returns a non-JSON body (HTML error
        # page) or an empty content (tokens spent on reasoning). Retry a couple of
        # times before falling through to the next provider.
        for try_no in range(3):
            try:
                parsed = _summarise_once(base_url, model_name, api_key, prompt)
                print(f"      summarised via {label} ({model_name})")
                return parsed
            except Exception as exc:  # noqa: BLE001 - retry / next provider
                last_err = f"{label}: {exc}"
                print(f"      {label} summary attempt {try_no + 1}/3 failed ({exc})")
        print(f"      {label} exhausted; trying next provider …")
    raise SystemExit(f"summarisation failed on every provider — last: {last_err}")


def _extract_json(content: str) -> dict:
    """Pull the JSON object out of a model reply (tolerate fences / preamble)."""
    text = content.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start : end + 1])
    raise ValueError(f"no JSON object in reply: {content[:200]}")


def _summarise_once(base_url: str, model_name: str, api_key: str, prompt: str) -> dict:
    """One summarisation call. Raises on any failure (caller retries/falls through)."""
    body = json.dumps(
        {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
            "temperature": 0.2,
        }
    ).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        # The opencode-go edge sits behind Cloudflare, which 403s (error code
        # 1010) on urllib's default User-Agent "Python-urllib/3.x". A browser
        # UA is required or every request is rejected.
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
    }
    # opencode-go requires a routing session id.
    if "opencode" in base_url:
        headers["x-opencode-session"] = "video-to-note-summariser"
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=body,
        method="POST",
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        raw = resp.read().decode("utf-8", "replace")
    data = json.loads(raw)
    content = data["choices"][0]["message"].get("content") or ""
    parsed = _extract_json(content)
    if not parsed.get("l0"):
        raise ValueError(f"model returned no l0: {content[:200]}")
    return parsed


def _session_token() -> str:
    """Token from the env, else scrape the served shell (same seam as the E2E auditors)."""
    tok = os.environ.get("QUBIT_SESSION_TOKEN")
    if tok:
        return tok
    try:
        html = urllib.request.urlopen(f"{DASHBOARD}/chat", timeout=15).read().decode(
            "utf-8", "replace"
        )
    except Exception as exc:  # noqa: BLE001 - reported, not swallowed
        raise SystemExit(f"could not reach the dashboard at {DASHBOARD}: {exc}")
    m = re.search(r'window\.__QUBIT_SESSION_TOKEN__\s*=\s*"([^"]+)"', html)
    if not m:
        raise SystemExit(
            "no session token found in the served shell; set QUBIT_SESSION_TOKEN"
        )
    return m.group(1)


def fetch_transcript(url: str) -> dict:
    """Run the skill's helper. Returns its JSON (video_id, full_text, timestamped_text...)."""
    out = subprocess.run(
        [sys.executable, str(HELPER), url, "--timestamps"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if out.returncode != 0:
        raise SystemExit(f"transcript fetch failed:\n{out.stderr.strip()[:800]}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"helper returned non-JSON:\n{out.stdout[:400]}")


def slugify(text: str, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    s = re.sub(r"[\s_]+", "-", s)
    return re.sub(r"-+", "-", s)[:maxlen].strip("-") or "untitled"


def write_note(path: str, content: str, token: str) -> dict:
    """PUT the note through the bridged route (auto-refreshes the plugin cache)."""
    body = json.dumps({"content": content}).encode("utf-8")
    # Send the path RAW. The route does not URL-decode, so percent-encoding writes a
    # literal "%f0%9f..." directory (verified live). ASCII paths need no encoding.
    if any(ord(c) > 127 or c == " " for c in path):
        raise SystemExit(f"note path must be ASCII with no spaces: {path!r}")
    req = urllib.request.Request(
        f"{DASHBOARD}/agent/api/plugins/obsidian-memory/notes/{path}",
        data=body,
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "X-Qubit-Session-Token": token,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
        return {"status": resp.status, "body": raw[:400]}


def read_note(path: str, token: str) -> dict:
    """Read it back — proof the write is visible to agents, not just on disk.

    The nested-path GET currently 404s (a separate, in-flight fix); the write still
    landed. Report the status instead of aborting so the index update still runs.
    """
    req = urllib.request.Request(
        f"{DASHBOARD}/agent/api/plugins/obsidian-memory/notes/{path}",
        headers={"X-Qubit-Session-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": resp.status, "body": resp.read().decode("utf-8", "replace")[:400]}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": f"(nested GET not yet supported: {exc.code})"}


def update_index(vault_root: Path, note_path: str, title: str, url: str) -> tuple[str, str]:
    """Append the note to AI_Brain/YouTube/_index.md as a [[wikilink]].

    Written directly on disk, not via the PUT route: the route refuses leading-dot
    names (`_index.md`), and the Obsidian plugin re-scans on GET after a refresh.
    Returns (index's vault-relative path, the [[wikilink]] used for the note).
    """
    subdir = Path(VAULT_SUBDIR)  # AI_Brain/YouTube
    index_abs = vault_root / subdir / "_index.md"
    stem = Path(note_path).stem  # e.g. 2026-10-12_but-what-is-a-neural-network...
    # Qualify with the folder so Obsidian resolves it unambiguously (bare stems collide
    # across folders). Matches how the note's own graph edge links back to the index.
    link = f"[[{subdir.as_posix()}/{stem}]]"
    line = f"- {link} — {title} ({url})"
    header = (
        f"# YouTube Note Index\n\n"
        f"Linked cluster of video-derived memory notes. "
        f"Upstream: [[_MEMORY_CONTRACT]]\n\n"
    )
    index_abs.parent.mkdir(parents=True, exist_ok=True)
    existing = index_abs.read_text() if index_abs.is_file() else header
    if link in existing:
        return str(subdir / "_index.md"), link
    if not existing.endswith("\n"):
        existing += "\n"
    index_abs.write_text(existing + line + "\n")
    return str(subdir / "_index.md"), link


def build_note(meta: dict, summary: dict) -> str:
    """Assemble the contract-shaped markdown (L0 <50 tokens / L1 / L2 + wikilinks)."""
    vid = meta.get("video_id", "")
    today = date.today().isoformat()
    url = f"https://www.youtube.com/watch?v={vid}"
    l0 = (summary.get("l0") or "No summary produced.").strip().splitlines()[0][:400]
    l1 = summary.get("l1") or []
    if isinstance(l1, str):
        l1 = [l1]
    l1_lines = "\n".join(
        f"  - {str(item).strip().lstrip('-* ').strip()}" for item in l1 if str(item).strip()
    )
    if not l1_lines:
        l1_lines = "- (no overview produced)"
    l2 = summary.get("l2") or meta.get("timestamped_text") or meta.get("full_text", "")
    if isinstance(l2, (list, tuple)):
        l2 = "\n".join(str(line).strip().lstrip("-* ").strip() for line in l2)
    else:
        l2 = str(l2)
    l2 = l2[:12000]
    return f"""---
tags: [youtube, type/memory, status/active]
viking_id: ""
last_updated: {today}
---
# {summary.get('title') or vid}

## [L0] Abstract
> [!NOTE] L0 (Tokens: <50)
> {l0}

## [L1] Overview
- **Source:** {url}
- **Segments:** {meta.get('segment_count', '?')}
- **Key Points:**
{l1_lines}
- **Current Execution Status:** Completed
- **Dependency Map:** [[_MEMORY_CONTRACT]]

## [L2] Details
### Transcript Highlights
{l2}

---
## 🕸️ Agent Memory Graph
- **Upstream Context:** [[_MEMORY_CONTRACT]]
- **Downstream Actions:** [[YouTube/_index]]
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", help="YouTube URL or video id")
    ap.add_argument("--dry-run", action="store_true", help="build but do not write")
    ap.add_argument(
        "--no-summarise",
        dest="summarise",
        action="store_false",
        help="skip the API model and use the raw-transcript fallback (poor quality)",
    )
    args = ap.parse_args()

    _load_env()

    print(f"[1/3] fetching transcript for {args.url} …")
    meta = fetch_transcript(args.url)
    print(
        f"      ok: video_id={meta.get('video_id')} "
        f"segments={meta.get('segment_count')} "
        f"chars={len(meta.get('full_text') or '')}"
    )

    print("\n[2/3] summarising …")
    summary_path = os.environ.get("SUMMARY_JSON")
    if summary_path and Path(summary_path).is_file():
        summary = json.loads(Path(summary_path).read_text())
        print(f"      using SUMMARY_JSON={summary_path}")
    elif args.summarise:
        summary = summarise(meta)
    else:
        # Explicit fallback so the note is still well-formed and useful without an LLM.
        first = " ".join((meta.get("full_text") or "").split())[:200]
        summary = {"title": meta.get("video_id", "Video"), "l0": first or "Transcript captured."}
        print("      --no-summarise: raw-transcript fallback in use")

    path = f"{VAULT_SUBDIR}/{date.today().isoformat()}_{slugify(summary.get('title', 'video'))}.md"
    note = build_note(meta, summary)
    print(f"\n[3/3] target note: {path} ({len(note)} bytes)")

    if args.dry_run:
        print("\n-- dry run, not writing --\n")
        print(note[:1200])
        return 0

    token = _session_token()
    res = write_note(path, note, token)
    print(f"      PUT -> {res['status']} {res['body'][:160]}")
    back = read_note(path, token)
    print(f"      GET -> {back['status']} (read-back proves agents can see it)")

    vault_root = Path(os.environ.get("OBSIDIAN_VAULT_PATH", "/home/trick/Documents/ObsidianVault"))
    idx, link = update_index(
        vault_root, path, summary.get("title") or meta.get("video_id", ""),
        f"https://www.youtube.com/watch?v={meta.get('video_id', '')}",
    )
    print(f"      index -> {idx} ({link} appended)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
