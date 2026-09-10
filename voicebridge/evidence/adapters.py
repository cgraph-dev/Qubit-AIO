"""Packet 5A: concrete read-only adapters for the declared sources.

Each adapter is a thin, read-only wrapper over an existing capability:
  files     -> pathlib read (bounded, no writes)
  repos     -> read-only git inspection (no checkout/commit)
  browser   -> declaration contract only (real CDP adapter lands in 8B)
  inbox     -> declaration contract only (real Chromium-CDP lands in 8B)
  reports   -> read-only manifest store scan (no deletion)
  web       -> bounded HTTP fetch (no form submission; SSRF-bounded)
  worker    -> Qubit worker: reads result history only
  memory    -> declaration contract only (store lands in 5B)
"""
from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (EvidenceError, EvidencePartialError, EvidenceRecord,
                     EvidenceUnavailableError, Scope, Sensitivity, Trust)

MAX_EXCERPT_CHARS = 2000
MAX_FETCH_BYTES = 256 * 1024
WEB_ALLOWED_HOSTS = ("example.com", "*.example.com", "localhost")


def _hash_short(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


class ReadOnlyAdapter:
    """Mixin enforcing read-only by construction: write-like members are
    defined as raising EvidencePermissionError instead of existing."""

    def _deny(self, *a: Any, **k: Any) -> None:
        raise EvidencePermissionError(
            f"{type(self).__name__}: read-only adapter, mutation denied")


class FileAdapter(ReadOnlyAdapter):
    def __init__(self, base: Path) -> None:
        self._base = Path(base).resolve()

    def fetch(self, rel: str) -> EvidenceRecord:
        target = (self._base / rel).resolve()
        try:
            target.relative_to(self._base)
        except ValueError as exc:
            raise EvidenceError(f"path escape blocked: {rel}") from exc
        if not target.exists():
            raise EvidenceUnavailableError(f"file missing: {rel}")
        if target.is_dir():
            raise EvidenceError(f"directory requested (file adapter): {rel}")
        if target.stat().st_size > MAX_FETCH_BYTES:
            raise EvidenceError(f"file exceeds {MAX_FETCH_BYTES} bytes: {rel}")
        text = target.read_text(errors="replace")
        return EvidenceRecord.create(
            adapter="files", adapter_version="1.0.0", source_ref=str(target),
            scope=Scope.FILES, sensitivity=Sensitivity.PERSONAL,
            trust=Trust.HIGH, excerpt=text[:MAX_EXCERPT_CHARS],
            raw_text=text, confidence=1.0,
            meta={"size": target.stat().st_size})


class RepoAdapter(ReadOnlyAdapter):
    """Read-only git inspection (status/log/show of HEAD); no mutations."""

    def fetch(self, repo: Path, ref: str = "HEAD",
              max_commits: int = 20) -> EvidenceRecord:
        repo = Path(repo)
        log = _run_readonly(["git", "-C", str(repo), "log",
                             f"-{max_commits}", "--oneline", ref])
        if log is None:
            raise EvidenceUnavailableError(f"git log failed: {repo}")
        status = _run_readonly(["git", "-C", str(repo), "status",
                                "--porcelain"])
        excerpt = f"log:\n{log}\nstatus:\n{(status or '').strip()}"
        return EvidenceRecord.create(
            adapter="repos", adapter_version="1.0.0",
            source_ref=f"{repo}@{ref}", scope=Scope.REPOS,
            sensitivity=Sensitivity.INTERNAL, trust=Trust.MEDIUM,
            excerpt=excerpt[:MAX_EXCERPT_CHARS], raw_text=excerpt,
            confidence=0.9,
            meta={"max_commits": max_commits})


class WebAdapter(ReadOnlyAdapter):
    """Bounded read-only HTTP fetch; no forms, no private network."""

    def fetch(self, url: str, max_bytes: int = MAX_FETCH_BYTES,
              timeout_s: float = 10.0) -> EvidenceRecord:
        if not _host_allowed(url):
            raise EvidenceError(f"host not allowed by SSRF policy: {url}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent":
                                                       "voicebridge-evidence/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                body = resp.read(max_bytes + 1)
        except Exception as exc:
            raise EvidenceUnavailableError(str(exc)[:160]) from exc
        if len(body) > max_bytes:
            raise EvidencePartialError("response exceeded max_bytes; truncated")
        text = body.decode("utf-8", "replace")
        return EvidenceRecord.create(
            adapter="web", adapter_version="1.0.0", source_ref=url,
            scope=Scope.WEB, sensitivity=Sensitivity.INTERNAL,
            trust=Trust.LOW, excerpt=text[:MAX_EXCERPT_CHARS],
            raw_text=text, confidence=0.5, freshness_ttl_s=600,
            citations=[url])


class ReportManifestAdapter(ReadOnlyAdapter):
    """Read-only scan of saved report manifests; never deletes or edits."""

    def fetch(self, manifest_path: Path) -> EvidenceRecord:
        mp = Path(manifest_path)
        if not mp.exists():
            raise EvidenceUnavailableError(f"manifest missing: {mp}")
        try:
            data = json.loads(mp.read_text(errors="replace"))
        except json.JSONDecodeError as exc:
            raise EvidencePartialError(f"unparseable manifest: {exc}") from exc
        if not isinstance(data, dict):
            raise EvidencePartialError("manifest not an object")
        excerpt = json.dumps(data, ensure_ascii=False)[:MAX_EXCERPT_CHARS]
        return EvidenceRecord.create(
            adapter="reports", adapter_version="1.0.0", source_ref=str(mp),
            scope=Scope.REPORTS, sensitivity=Sensitivity.PERSONAL,
            trust=Trust.MEDIUM, excerpt=excerpt, raw_text=excerpt,
            confidence=0.95, citations=[str(mp)],
            meta={"report_id": data.get("report_id")})


class DeclarativeAdapter(ReadOnlyAdapter):
    """Placeholder contract adapter for sources whose real implementation
    lands later (browser CDP=8B, inbox=8B, worker history, memory=5B)."""

    def __init__(self, name: str, version: str, scope: Scope,
                 sensitivity: Sensitivity) -> None:
        self._name, self._version = name, version
        self._scope, self._sensitivity = scope, sensitivity

    def fetch(self, source_ref: str) -> EvidenceRecord:
        raise EvidenceUnavailableError(
            f"{self._name}: real adapter not yet wired (see Phase 8 / 5B)")


def _run_readonly(argv: List[str]) -> Optional[str]:
    import subprocess
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=15)
    except Exception:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _host_allowed(url: str) -> bool:
    m = re.match(r"^https?://([^/]+)", url)
    if not m:
        return False
    host = m.group(1).lower()
    if host in ("127.0.0.1", "localhost", "::1"):
        return False
    for allow in WEB_ALLOWED_HOSTS:
        if allow.startswith("*."):
            if host.endswith(allow[1:]) and host.count(".") >= 2:
                return True
        elif host == allow:
            return True
    return False