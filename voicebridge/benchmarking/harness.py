"""Packet 4A benchmark harness: reproducible cold/warm transition measurements.

Wraps the existing production model callers unchanged (R5: WRAP over the
Ollama HTTP boundary). Measures; never tunes, never mutates defaults.

Output honesty rules:
- raw per-step rows are kept unaveraged in transitions.jsonl;
- warm and cold samples are reported separately, never merged;
- every unavailable resource becomes a typed null/degraded marker.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..intent.llm import classify_with_ollama
from ..local_llm import chat_local_stream
from ..summarizer import summarize_ollama

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
PS_POLL_TIMEOUT_S = 15.0
RESTORE_TIMEOUT_S = 45.0

SUMMARY_INPUT = (
    "Task finished. Renamed 14 files to lowercase in demo-folder. "
    "Two files were skipped because names contained only symbols. "
    "No errors were written to the log."
)


class BenchmarkError(RuntimeError):
    """Typed harness failure (never a silent pass)."""


# ---------------------------------------------------------------- percentiles
def percentile(values: List[float], q: float) -> Optional[float]:
    """Linear-interpolated percentile on a sorted copy; None when empty."""
    if not values:
        return None
    if not 0.0 <= q <= 100.0:
        raise ValueError(f"percentile q out of range: {q}")
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    pos = (len(s) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return float(s[lo] + (s[hi] - s[lo]) * frac)


# ------------------------------------------------------------ environment
def read_proc_stat() -> List[int]:
    with open("/proc/stat", "r", encoding="ascii") as fh:
        first = fh.readline()
    parts = first.split()[1:]
    return [int(x) for x in parts[:8]]


def cpu_busy_fraction(start: List[int], end: List[int]) -> Optional[float]:
    deltas = [e - s for s, e in zip(start, end)]
    if len(deltas) < 4:
        return None
    idle = deltas[3] + (deltas[4] if len(deltas) > 4 else 0)
    total = sum(deltas[:5])
    if total <= 0:
        return 0.0
    return round(max(0.0, 1.0 - idle / total), 4)


def gpu_sample(timeout_s: float = 3.0) -> Dict[str, Any]:
    """Bounded nvidia-smi sample; typed nulls when unavailable."""
    out: Dict[str, Any] = {"available": False}
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=timeout_s, check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            out["error"] = (proc.stderr.strip() or f"rc={proc.returncode}")[:160]
            return out
        fields = [f.strip() for f in proc.stdout.strip().splitlines()[0].split(",")]
        if len(fields) >= 5:
            out.update(available=True, name=fields[0], driver=fields[1],
                       vram_used_mib=_int_or_none(fields[2]),
                       vram_total_mib=_int_or_none(fields[3]),
                       util_pct=_int_or_none(fields[4]))
        return out
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        out["error"] = str(exc)[:160]
        return out


def _int_or_none(v: str) -> Optional[int]:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def mem_available_mib() -> Optional[int]:
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return None


def modelfile_hash(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def ollama_get_json(url: str, path: str, timeout_s: float = 5.0,
                    payload: Optional[Dict[str, Any]] = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{url.rstrip('/')}{path}", data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode())


def resident_state(url: str, model: str) -> Dict[str, Any]:
    """Residency facts from GET /api/ps — never inferred from keep_alive.

    Ollama /api/ps reports ``size``/``size_vram`` in BYTES. Raw byte values are
    preserved under ``*_bytes``; ``*_mib`` fields are the correctly converted
    MiB (bytes / 1048576), never a byte value under a MiB name.
    """
    try:
        body = ollama_get_json(url, "/api/ps")
    except Exception as exc:
        return {"reachable": False, "resident": False, "error": str(exc)[:160]}
    models = {m.get("name", "").split(":")[0]: m for m in body.get("models", [])}
    entry = models.get(model.split(":")[0])
    if entry is None:
        for key, m in models.items():
            if model.startswith(key):
                entry = m
                break
    if entry is None:
        return {"reachable": True, "resident": False}
    size_bytes = entry.get("size")
    vram_bytes = entry.get("size_vram")
    return {"reachable": True, "resident": True,
            "digest": entry.get("digest"),
            "size_bytes": size_bytes,
            "size_vram_bytes": vram_bytes,
            "size_mib": _bytes_to_mib(size_bytes),
            "size_vram_mib": _bytes_to_mib(vram_bytes),
            "ctx": entry.get("context_length"),
            "until_epoch": entry.get("expires_at")}


def _bytes_to_mib(value: Any) -> Optional[float]:
    """Convert a byte count to MiB (1 MiB = 1048576 B); None stays None."""
    if value is None:
        return None
    try:
        return round(float(value) / 1048576.0, 1)
    except (TypeError, ValueError):
        return None


def environment_snapshot(url: str, model: str) -> Dict[str, Any]:
    env: Dict[str, Any] = {
        "captured_at_epoch": round(time.time(), 3),
        "model_alias": model,
        "ollama_url": url,
        "hardware": {},
    }
    try:
        env["ollama_version"] = ollama_get_json(url, "/api/version").get("version")
    except Exception as exc:
        env["ollama_version"] = None
        env["ollama_version_error"] = str(exc)[:160]
    ps = resident_state(url, model)
    env["residency_before"] = ps
    try:
        show = ollama_get_json(url, "/api/show", payload={"model": model})
        details = show.get("details", {})
        env["model_details"] = {
            "family": details.get("family"),
            "parameter_size": details.get("parameter_size"),
            "quantization_level": details.get("quantization_level"),
        }
        served = show.get("modelfile") or ""
        env["served_modelfile_sha256"] = (
            hashlib.sha256(served.encode()).hexdigest() if served else None)
    except Exception as exc:
        env["model_details_error"] = str(exc)[:160]
    # The bake artifact Modelfile.qubit4 is gitignored/generated (bake_brain.py);
    # the tracked sanitized fixture is the canonical repo artifact a clean
    # checkout can hash for comparison.
    repo_modelfile = REPO_ROOT / "tests" / "fixtures" / "modelfiles" / "qubit4.sanitized"
    env["repo_modelfile_qubit4_sha256"] = modelfile_hash(repo_modelfile)
    env["gpu_before"] = gpu_sample()
    env["mem_available_mib"] = mem_available_mib()
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("model name"):
                    env["hardware"]["cpu_model"] = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    env["hardware"]["platform"] = platform_identity()
    return env


def platform_identity() -> str:
    try:
        with open("/etc/os-release", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "unknown"


# ------------------------------------------------------- transport recorder
@dataclass
class CapturedCall:
    url_path: str
    payload: Dict[str, Any]
    body_text: str
    t_start: float
    t_first_byte: Optional[float]
    t_end: float
    stream_lines: List[tuple] = field(default_factory=list)

    def parsed_body(self) -> Dict[str, Any]:
        try:
            obj = json.loads(self.body_text)
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}

    def gen_metrics(self) -> Dict[str, Any]:
        body = self.parsed_body()
        if not body and self.stream_lines:
            for _, text in reversed(self.stream_lines):
                try:
                    obj = json.loads(text)
                    if isinstance(obj, dict) and obj.get("done"):
                        body = obj
                        break
                except json.JSONDecodeError:
                    continue
        keys = ("total_duration", "load_duration", "prompt_eval_count",
                "prompt_eval_duration", "eval_count", "eval_duration")
        return {k: body.get(k) for k in keys}


# R5 semantics: the reload key is model identity + full runner options
# (num_ctx inside it); keep_alive affects expiry only.
def material_options(payload: Dict[str, Any]) -> Dict[str, Any]:
    opts = payload.get("options") or {}
    return {"num_ctx": opts.get("num_ctx"),
            "keep_alive_present": "keep_alive" in payload}


class _TeeResponse:
    def __init__(self, inner: Any, call: CapturedCall) -> None:
        self._inner = inner
        self._call = call
        self._it = iter(inner)

    def __enter__(self) -> "_TeeResponse":
        enter = getattr(self._inner, "__enter__", None)
        if enter is not None:
            # Forward the CM protocol: production code relies on resources
            # being live after `with urlopen(...) as resp`.
            enter()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __iter__(self) -> "_TeeResponse":
        return self

    def __next__(self) -> bytes:
        raw = next(self._it)
        now = time.monotonic()
        if self._call.t_first_byte is None:
            self._call.t_first_byte = now
        self._call.stream_lines.append((now, raw.decode("utf-8", "replace")))
        return raw

    def read(self, n: int = -1) -> bytes:
        # http.client treats amt=-1 as a bounded read of raw stream bytes
        # (bypassing chunked decoding); only None means "read it all".
        data = self._inner.read(None if n is None or n < 0 else n)
        if self._call.t_first_byte is None and data:
            self._call.t_first_byte = time.monotonic()
        self._call.body_text += data.decode("utf-8", "replace")
        return data

    def close(self) -> None:
        self._call.t_end = time.monotonic()
        self._inner.close()


class TransportRecorder:
    """Tees production urlopen traffic while leaving behavior identical."""

    def __init__(self) -> None:
        self.calls: List[CapturedCall] = []
        self._orig: Optional[Callable] = None

    def __enter__(self) -> "TransportRecorder":
        self._orig = urllib.request.urlopen

        def recorded(req, timeout=None, **kwargs):
            payload: Dict[str, Any] = {}
            try:
                data = getattr(req, "data", None)
                if data:
                    payload = json.loads(bytes(data).decode())
            except Exception:
                payload = {"_unparsed": True}
            path = getattr(req, "full_url", "")
            path = re.sub(r"^https?://[^/]+", "", path)
            call = CapturedCall(url_path=path, payload=payload, body_text="",
                                t_start=time.monotonic(), t_first_byte=None,
                                t_end=time.monotonic())
            inner = self._orig(req, timeout=timeout, **kwargs)
            self.calls.append(call)
            return _TeeResponse(inner, call)

        urllib.request.urlopen = recorded
        return self

    def __exit__(self, *exc) -> None:
        assert self._orig is not None
        urllib.request.urlopen = self._orig
        self._orig = None

    def option_drift_events(self) -> List[Dict[str, Any]]:
        """R5: the scheduler compares options across calls to the SAME model.
        Only generation endpoints carry runner options; probes (/api/ps,
        /api/show, /api/version) must never anchor a drift comparison."""
        model_calls = [c for c in self.calls
                       if c.url_path.endswith(("/api/generate", "/api/chat"))]
        events: List[Dict[str, Any]] = []
        prev: Optional[CapturedCall] = None
        for call in model_calls:
            if prev is not None:
                a = material_options(prev.payload)
                b = material_options(call.payload)
                if a != b:
                    events.append({
                        "between_paths": [prev.url_path, call.url_path],
                        "before_options": a, "after_options": b,
                        "num_ctx_conflict":
                            a["num_ctx"] is not None and b["num_ctx"] is not None
                            and a["num_ctx"] != b["num_ctx"],
                        "keep_alive_conflict":
                            a["keep_alive_present"] != b["keep_alive_present"],
                    })
            prev = call
        return events


# --------------------------------------------------------- lifecycle helpers
def unload_model(url: str, model: str) -> None:
    """Documented expireRunner path: empty prompt + keep_alive=0 (R5)."""
    ollama_get_json(url, "/api/generate",
                    payload={"model": model, "keep_alive": 0}, timeout_s=10.0)


def wait_until_unloaded(url: str, model: str,
                        deadline_s: float = PS_POLL_TIMEOUT_S) -> bool:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if not resident_state(url, model).get("resident"):
            return True
        time.sleep(0.5)
    return False


def restore_residency(url: str, model: str, prior_ctx: Optional[int],
                      timeout_s: float = RESTORE_TIMEOUT_S) -> Dict[str, Any]:
    """Restore prior residency at canonical keep_alive=-1; verify via /api/ps."""
    report: Dict[str, Any] = {"attempted": True, "restored": False,
                              "prior_ctx": prior_ctx}
    payload = {"model": model, "stream": False,
               "messages": [{"role": "user", "content": "ping"}],
               "keep_alive": -1,
               "options": {"num_ctx": prior_ctx or 4096}}
    try:
        ollama_get_json(url, "/api/chat", payload=payload, timeout_s=60.0)
    except Exception as exc:
        report["error"] = str(exc)[:160]
        return report
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        ps = resident_state(url, model)
        if ps.get("resident"):
            report["restored"] = True
            report["verified_ctx"] = ps.get("ctx")
            report["state_changed_from_prior_context"] = (
                prior_ctx is not None and ps.get("ctx") != prior_ctx)
            return report
        time.sleep(1.0)
    report["error"] = "restore verification timed out"
    return report


# ------------------------------------------------------------ config resolve
def resolve_caller_config(cfg: Any) -> Dict[str, Any]:
    # Packet 4B: num_ctx/alias come from the canonical runner contract so
    # benchmarks measure exactly what production sends.
    from ..runner_contract import resolve as _rc_resolve
    _rc = _rc_resolve(cfg)
    llm = cfg.get("intent", "local_llm", default={}) or {}
    chat_model = llm.get("model") or cfg.get("intent", "ollama_model",
                                             default="qubit-brain4")
    return {
        "url": _rc.url,
        "chat_model": chat_model,
        "classifier_model": _rc.alias,
        "summary_model": (cfg.get("summarize", "ollama_model", default=None)
                          or chat_model),
        "timeout_s": float(llm.get("timeout_s", 4.0)),
        "max_tokens": int(llm.get("max_tokens", 100)),
        "num_ctx": int(_rc.num_ctx),
        "classify_timeout_s": float(cfg.get("intent", "classify_timeout_s",
                                            default=8.0)),
        "summary_timeout_s": float(cfg.get("summarize", "timeout_s",
                                           default=5.0)),
    }


# ------------------------------------------------------------ step execution
STEP_CLASSIFIER = "classifier"
STEP_CHAT = "chat"
STEP_SUMMARY = "summary"


def run_transition_iterations(recorder: TransportRecorder,
                              ccfg: Dict[str, Any],
                              cases: List[dict], iterations: int,
                              deadline_monotonic: Optional[float],
                              tts_fn: Optional[Callable[[str], float]],
                              start_iteration: int = 0) -> List[Dict[str, Any]]:
    """classifier→chat→classifier→summary→chat per iteration.

    cases: list of dicts with keys classifier_a/b (text), chat_a/b, summary.
    Each timed step wraps ONLY the production call; /api/ps probes stay
    outside the timed window.
    """
    rows: List[Dict[str, Any]] = []
    url = ccfg["url"]
    calls_before = len(recorder.calls)

    def _ps_before(model: str) -> Dict[str, Any]:
        return resident_state(url, model)

    def _finish_row(row: Dict[str, Any], t0: float, call_idx: int,
                    first_sentence_at: Optional[float]) -> None:
        wall = (time.monotonic() - t0) * 1000.0
        row["wall_ms"] = round(wall, 2)
        new_calls = recorder.calls[call_idx:]
        if new_calls:
            cap = new_calls[-1]
            metrics = cap.gen_metrics()
            row["gen_metrics"] = metrics
            ec, ed = metrics.get("eval_count"), metrics.get("eval_duration")
            if isinstance(ec, (int, float)) and isinstance(ed, (int, float)) \
                    and ed > 0 and ec > 0:
                row["tokens_per_s"] = round(ec / (ed / 1e9), 2)
            if cap.stream_lines:
                for ts, text in cap.stream_lines:
                    try:
                        chunk = json.loads(text).get("message", {}) or {}
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("content"):
                        row["first_content_ms"] = round((ts - t0) * 1000.0, 2)
                        break
            row["full_response_ms"] = row["wall_ms"]
        if first_sentence_at is not None:
            row["first_sentence_ms"] = round((first_sentence_at - t0) * 1000.0, 2)

    for it in range(iterations):
        case = cases[(start_iteration + it) % len(cases)]
        plan = [
            (STEP_CLASSIFIER, case["classifier_a"]),
            (STEP_CHAT, case["chat_a"]),
            (STEP_CLASSIFIER, case["classifier_b"]),
            (STEP_SUMMARY, SUMMARY_INPUT),
            (STEP_CHAT, case["chat_b"]),
        ]
        for step_no, (step, text) in enumerate(plan):
            if deadline_monotonic is not None \
                    and time.monotonic() > deadline_monotonic:
                rows.append({"iteration": start_iteration + it, "step": step,
                             "case_id": case.get("case_id"),
                             "error": "deadline_exceeded"})
                return rows
            model = (ccfg["classifier_model"] if step == STEP_CLASSIFIER
                     else ccfg["summary_model"] if step == STEP_SUMMARY
                     else ccfg["chat_model"])
            ps = _ps_before(model)
            row: Dict[str, Any] = {
                "iteration": start_iteration + it, "step_no": step_no,
                "step": step, "case_id": case.get("case_id"),
                "model": model,
                "warm_cold": "cold" if not ps.get("resident") else "warm",
                "resident_ctx_before": ps.get("ctx"),
            }
            t0 = time.monotonic()
            first_sentence_at: Optional[float] = None
            first_sentence_text: List[str] = []
            call_idx = len(recorder.calls)
            outcome: Any = None
            try:
                if step == STEP_CLASSIFIER:
                    outcome = classify_with_ollama(
                        text, url=url, model=model,
                        timeout_s=ccfg["classify_timeout_s"])
                elif step == STEP_SUMMARY:
                    outcome = summarize_ollama(text, url=url, model=model,
                                               timeout_s=ccfg[
                                                   "summary_timeout_s"])
                else:
                    def _on_sentence(sentence: str) -> None:
                        nonlocal first_sentence_at
                        if first_sentence_at is None:
                            first_sentence_at = time.monotonic()
                            first_sentence_text.append(sentence)

                    outcome = chat_local_stream(
                        text, conversation_prompt="", model=model, url=url,
                        timeout_s=ccfg["timeout_s"],
                        max_tokens=ccfg["max_tokens"],
                        num_ctx=ccfg["num_ctx"], on_sentence=_on_sentence)
            except BenchmarkError:
                raise
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"[:200]
                rows.append(row)
                continue
            # Production callers swallow transport/parse errors and return
            # typed-empty results; a benchmark must record that as failed.
            if outcome is None or (step == STEP_CHAT and not outcome):
                no_call = len(recorder.calls) == call_idx
                row["error"] = (
                    "caller_reported_failure"
                    + (" (no transport call)" if no_call
                       else " (typed empty result)"))
                rows.append(row)
                continue
            _finish_row(row, t0, call_idx, first_sentence_at)
            if step == STEP_CHAT and first_sentence_at is not None \
                    and tts_fn is not None and row.get("error") is None:
                try:
                    synth_ms = tts_fn(first_sentence_text[0][:200])
                    row["tts_ready_ms"] = round(
                        (first_sentence_at - t0) * 1000.0 + synth_ms, 2)
                except Exception as exc:
                    row["tts_ready_ms"] = None
                    row["tts_degraded"] = str(exc)[:120]
            rows.append(row)
    return rows


# ------------------------------------------------------------- summarization
def summarize_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_step: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_step.setdefault(r.get("step", "unknown"), []).append(r)
    summary: Dict[str, Any] = {"per_step": {}}
    total_errors = 0
    for step, items in sorted(by_step.items()):
        errors = [i for i in items if i.get("error")]
        total_errors += len(errors)
        warm_walls = [i["wall_ms"] for i in items
                      if i.get("warm_cold") == "warm" and "wall_ms" in i]
        cold_items = [i for i in items
                      if i.get("warm_cold") == "cold" and "wall_ms" in i]
        loads = [i["gen_metrics"]["load_duration"]
                 for i in items
                 if i.get("gen_metrics", {}).get("load_duration")]
        entry: Dict[str, Any] = {
            "n_samples": len(items), "n_errors": len(errors),
            "n_cold_samples": len(cold_items),
        }
        if warm_walls:
            entry["warm_wall_ms_p50"] = percentile(warm_walls, 50)
            entry["warm_wall_ms_p95"] = percentile(warm_walls, 95)
            entry["warm_wall_ms_max"] = max(warm_walls)
        if cold_items:
            entry["cold_wall_ms_samples"] = [round(i["wall_ms"], 2)
                                             for i in cold_items]
        # Raw warm load_duration distribution: small values (<1 s) are
        # runner-reuse overhead; >=1 s indicates a real reload event.
        warm_loads_ms = [
            (i.get("gen_metrics", {}).get("load_duration") or 0) / 1e6
            for i in items
            if i.get("warm_cold") == "warm"
            and i.get("gen_metrics", {}).get("load_duration") is not None]
        entry["warm_load_duration_ms"] = {
            "p50": percentile(warm_loads_ms, 50),
            "p95": percentile(warm_loads_ms, 95),
            "max": max(warm_loads_ms) if warm_loads_ms else None,
            "n_over_1000ms": sum(1 for v in warm_loads_ms if v > 1000.0),
        }
        summary["per_step"][step] = entry
    summary["total_errors"] = total_errors
    summary["note"] = ("warm and cold samples are reported separately and are "
                       "never averaged together")
    return summary


# ------------------------------------------------------------------ writers
def write_run(out_dir: Path, mode: str, environment: Dict[str, Any],
              rows: List[Dict[str, Any]], extra: Dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(out_dir, 0o700)
    except OSError:
        pass
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    run_dir = out_dir / stamp
    if run_dir.exists():
        run_dir = Path(tempfile.mkdtemp(prefix=f"run-{stamp}-",
                                        dir=str(out_dir)))
    run_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(run_dir, 0o700)
    _atomic_json(run_dir / "environment.json",
                 {"mode": mode, **environment})
    with open(run_dir / "transitions.jsonl", "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    _atomic_json(run_dir / "summary.json", {"mode": mode, **extra})
    return run_dir


def _atomic_json(path: Path, obj: Dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, path)


class OutputLock:
    """Best-effort exclusive lock per output dir; steals dead-owner locks."""

    def __init__(self, out_dir: Path) -> None:
        self.path = out_dir / "benchmark.lock"

    def __enter__(self) -> "OutputLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            owner = _lock_owner_pid(self.path)
            if owner is not None and not _pid_alive(owner):
                self.path.unlink()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
        except FileExistsError as exc:
            raise BenchmarkError(
                f"another benchmark holds {self.path} (delete the stale "
                "lock only after confirming no benchmark is running)") from exc
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def _lock_owner_pid(path: Path) -> Optional[int]:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
