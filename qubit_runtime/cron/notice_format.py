"""Shared, transport-aware formatting for user-visible cron notices.

Scheduled-job output reaches chat surfaces through ``cron.scheduler_delivery._deliver_result``.
That function used to wrap every report in a plain ``Cronjob Response:`` header with an ASCII
divider. This module builds the same information once, as a structured :class:`CronNotice`, and
renders it for the target transport:

* Markdown (Telegram MarkdownV2, Discord, Slack, TUI/desktop/dashboard) uses bold labels and
  Markdown links; the platform adapter still owns escaping/conversion.
* WhatsApp uses its single-asterisk bold and no link markup.
* Everything else gets a plain-text fallback with no Markdown at all.

Machine-readable state (job records, delivery receipts, execution ids) is never parsed back out of
the rendered text; callers keep the structured fields and render from them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from qubit_time import get_timezone, now as _qubit_now, safe_strftime

STATUS_ICONS: dict[str, str] = {
    "scheduled": "⏰",
    "started": "▶️",
    "success": "✅",
    "partial": "⚠️",
    "failed": "❌",
    "paused": "⏸️",
    "retry": "🔁",
    "receipt": "🧾",
}

STATUS_LABELS: dict[str, str] = {
    "scheduled": "scheduled",
    "started": "started",
    "success": "completed",
    "partial": "partial",
    "failed": "failed",
    "paused": "paused",
    "retry": "retrying",
    "receipt": "receipt",
}

DEFAULT_MAX_CHARS = 1500
SUMMARY_MAX_LINES = 3
SUMMARY_MAX_CHARS = 320
FAILURE_SUMMARY_MAX_CHARS = 700
TRUNCATION_MARKER = "…"

_MARKDOWN_STYLES = frozenset({
    "telegram", "discord", "slack", "mattermost", "matrix",
    "cli", "tui", "desktop", "api_server", "web", "dashboard",
})
_WHATSAPP_STYLES = frozenset({"whatsapp", "whatsapp_cloud"})

_STATUS_ICON_RE = re.compile(
    "(?:⚠️|⚠|⛔|" + "|".join(re.escape(icon) for icon in STATUS_ICONS.values() if icon != "⚠️") + ")"
)
# User-controlled job names are rendered inside bold spans; replace characters that a Markdown
# renderer could interpret as markup with safe lookalikes instead of letting a name inject format.
_NAME_TRANSLATION = str.maketrans({
    "*": "∗", "_": "-", "~": "∼", "|": "∣", "`": "'",
    "[": "(", "]": ")", "<": "‹", ">": "›", "\\": "／",
})
_LEGACY_WRAPPER_HEADER = "Cronjob Response: "
_LEGACY_WRAPPER_FOOTER = '\n\nTo stop or manage this job, send me a new message (e.g. "stop reminder '
_LEGACY_HEADER_LINE_RE = re.compile(r"^Cronjob Response: .*\n\(job_id: [^\n]*\)\n-{5,}\n\n", re.S)


@dataclass(frozen=True)
class CronNotice:
    """One user-visible cron notice.

    ``summary`` is already bounded by the builder for its kind; :func:`render_notice` applies the
    transport hard cap on top. ``link`` must be an operator-configured URL (or a real artifact
    URL) — callers never synthesize one.
    """

    kind: str
    job_name: str
    job_id: str = ""
    summary: str = ""
    schedule: str = ""
    duration_seconds: Optional[float] = None
    next_run_at: Any = None
    run_id: str = ""
    link: str = ""
    link_label: str = "Open dashboard"
    footer: str = ""
    details: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    summary_truncated: bool = False


def icon_for(kind: str) -> str:
    """The single status icon for *kind*; unknown kinds fall back to a receipt."""
    return STATUS_ICONS.get(kind, STATUS_ICONS["receipt"])


def label_for(kind: str) -> str:
    return STATUS_LABELS.get(kind, kind.replace("_", " "))


def _coerce_local(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    target = get_timezone()
    if dt.tzinfo is None:
        target = target or datetime.now().astimezone().tzinfo
        dt = dt.replace(tzinfo=target)
    if target is not None:
        dt = dt.astimezone(target)
    return dt


def format_local_timestamp(value: Any) -> str:
    """Absolute local time with a zone hint, e.g. ``2026-10-26 18:12 EET``."""
    dt = _coerce_local(value)
    if dt is None:
        return str(value or "").strip()
    rendered = safe_strftime(dt, "%Y-%m-%d %H:%M %Z").strip()
    if not rendered.endswith("Z") and not re.search(r"[A-Za-z]{2,}$", rendered):
        rendered = f"{safe_strftime(dt, '%Y-%m-%d %H:%M')} {_offset_zone(dt)}"
    return rendered


def _offset_zone(dt: datetime) -> str:
    offset = dt.utcoffset()
    if offset is None:
        return "UTC"
    seconds = int(offset.total_seconds())
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    hours, minutes = divmod(seconds // 60, 60)
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def humanize_duration(seconds: Optional[float]) -> str:
    """Compact, human duration: ``<1s``, ``1m 05s``, ``2h 03m``, ``1d 04h``."""
    if seconds is None:
        return ""
    try:
        value = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return ""
    if value < 1:
        return "<1s"
    if value < 60:
        if value < 10:
            rendered = f"{value:.1f}"
            if rendered.endswith(".0"):
                rendered = rendered[:-2]
            return rendered + "s"
        return f"{value:.0f}s"
    total = int(round(value))
    if total < 3600:
        minutes, secs = divmod(total, 60)
        return f"{minutes}m {secs:02d}s"
    if total < 86400:
        hours, rem = divmod(total, 3600)
        minutes = rem // 60
        return f"{hours}h {minutes:02d}m"
    days, rem = divmod(total, 86400)
    hours = rem // 3600
    return f"{days}d {hours:02d}h"


def _humanize_relative(seconds: float) -> str:
    value = abs(float(seconds))
    if value < 1:
        return "now"
    if value < 60:
        return f"{int(round(value))}s"
    if value < 3600:
        return f"{int(value // 60)}m"
    if value < 86400:
        hours = int(value // 3600)
        minutes = int((value % 3600) // 60)
        return f"{hours}h {minutes:02d}m" if minutes else f"{hours}h"
    days = int(value // 86400)
    hours = int((value % 86400) // 3600)
    return f"{days}d {hours:02d}h" if hours else f"{days}d"


def resolve_next_run(job: dict, *, now: Optional[datetime] = None) -> Optional[datetime]:
    """The next fire after *now*: the stored future stamp, else recomputed from the schedule.

    Run completion advances ``next_run_at`` only after delivery, so at delivery time the stored
    value is usually the occurrence that just fired. Recomputing gives the notice the real next
    fire without mutating the job or the schedule.
    """
    current = now or _qubit_now()
    raw = _coerce_local(job.get("next_run_at"))
    if raw is not None and raw > current:
        return raw
    schedule = job.get("schedule")
    if not isinstance(schedule, dict):
        return None
    try:
        from cron.jobs import compute_next_run

        computed = compute_next_run(schedule, last_run_at=current.isoformat())
    except Exception:
        return None
    candidate = _coerce_local(computed)
    if candidate is not None and candidate > current:
        return candidate
    return None


def format_next_run(job: dict, *, now: Optional[datetime] = None) -> str:
    """``in 5m (2026-10-26 18:17 EET)``, ``overdue by 2m (...)`` or ``no further runs``."""
    current = now or _qubit_now()
    candidate = resolve_next_run(job, now=current)
    if candidate is not None:
        delta = (candidate - current).total_seconds()
        return f"in {_humanize_relative(delta)} ({format_local_timestamp(candidate)})"
    schedule = job.get("schedule")
    if isinstance(schedule, dict) and schedule.get("kind") == "once":
        return "no further runs"
    raw = _coerce_local(job.get("next_run_at"))
    if raw is not None:
        delta = (raw - current).total_seconds()
        return f"overdue by {_humanize_relative(delta)} ({format_local_timestamp(raw)})"
    return "no further runs"


def schedule_display(job: dict) -> str:
    schedule = job.get("schedule")
    if isinstance(schedule, dict):
        return str(schedule.get("display") or schedule.get("expr") or "").strip()
    if isinstance(schedule, str):
        return schedule.strip()
    return str(job.get("schedule_display") or "").strip()


def _sanitize_name(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.translate(_NAME_TRANSLATION)


def _strip_status_lead(text: str, job_name: str = "") -> str:
    """Remove a leading scheduler status icon and redundant ``Cron '<job>' failed:`` lead-in."""
    text = text.lstrip()
    text = re.sub(rf"^(?:{_STATUS_ICON_RE.pattern})\s*", "", text, count=1)
    if job_name:
        name = re.escape(job_name)
        patterns = (
            rf"^Cron(?: job)?\s+['\"`]?{name}['\"`]?\s+(?:failed|did not run|was auto-paused)\s*:?\s*",
            rf"^Cron\s+(?:watchdog|monitor)\s+['\"`]?{name}['\"`]?\s+(?:script|source)\s+failed\s*:?\s*",
        )
        for pattern in patterns:
            text = re.sub(pattern, "", text, count=1, flags=re.IGNORECASE)
    return text.lstrip()


def _starts_with_status_icon(line: str) -> bool:
    stripped = line.lstrip()
    return _STATUS_ICON_RE.match(stripped) is not None


def _strip_duplicate_icons(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if _starts_with_status_icon(line):
            lines.append(_STATUS_ICON_RE.sub("", line, count=1).lstrip())
        else:
            lines.append(line)
    return "\n".join(lines)


def summarize_excerpt(text: str, *, max_lines: int = SUMMARY_MAX_LINES,
                      max_chars: int = SUMMARY_MAX_CHARS) -> tuple[str, bool]:
    """Bound *text* to a 1–3 line preview, ending with ``…`` when anything was dropped."""
    text = (text or "").strip()
    if not text:
        return "", False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return "", False
    kept = lines[:max_lines]
    joined = "\n".join(kept)
    truncated = len(lines) > max_lines
    if len(joined) > max_chars:
        joined, cut = safe_truncate(joined, max_chars)
        truncated = truncated or cut
    elif truncated:
        joined += TRUNCATION_MARKER
    return joined, truncated


def safe_truncate(text: str, limit: int, *, marker: str = TRUNCATION_MARKER) -> tuple[str, bool]:
    """Truncate to *limit* without splitting a grapheme cluster or Markdown construct."""
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    if len(marker) >= limit:
        return marker[:limit], True
    cut = max(0, limit - len(marker))
    cut = _avoid_grapheme_break(text, cut)
    cut = _avoid_markdown_break(text[:cut], cut)
    prefix = text[:cut].rstrip()
    while prefix.endswith("\\") and not prefix.endswith("\\\\"):
        prefix = prefix[:-1].rstrip()
    return prefix + marker, True


def _avoid_grapheme_break(text: str, cut: int) -> int:
    while 0 < cut < len(text):
        prev = text[cut - 1]
        nxt = text[cut]
        if prev == "\u200d" or nxt == "\u200d":
            cut -= 1
            continue
        if unicodedata.combining(prev) or unicodedata.combining(nxt):
            cut -= 1
            continue
        code = ord(nxt)
        if 0xFE00 <= code <= 0xFE0F or 0x1F3FB <= code <= 0x1F3FF or code == 0x20E3:
            cut -= 1
            continue
        if _is_regional_indicator(prev) and _is_regional_indicator(nxt):
            cut -= 1
            continue
        break
    return cut


def _is_regional_indicator(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def _avoid_markdown_break(prefix: str, cut: int) -> int:
    if prefix.count("```") % 2 == 1:
        cut = prefix.rfind("```")
    if prefix.count("`") % 2 == 1:
        cut = prefix.rfind("`")
    if prefix.count("**") % 2 == 1:
        cut = min(cut, prefix.rfind("**"))
    open_bracket = prefix.rfind("[")
    if open_bracket > prefix.rfind("]") and not prefix[open_bracket:].startswith("[ "):
        cut = min(cut, open_bracket)
    return max(0, cut)


def _to_plain(text: str) -> str:
    """Best-effort Markdown → plain text for transports without any Markdown renderer."""
    if not text:
        return ""
    text = re.sub(r"```[^\n]*\n([\s\S]*?)```", r"\1", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\\([_*\[\]()~`>#\+\-=|{}.!\\])", r"\1", text)
    return text


def _style_for(transport: Optional[str]) -> str:
    name = str(transport or "").strip().lower()
    if name in _MARKDOWN_STYLES:
        return "markdown"
    if name in _WHATSAPP_STYLES:
        return "whatsapp"
    return "plain"


def _detail_lines(notice: CronNotice, style: str, *, next_run: str) -> list[str]:
    bold = "**" if style == "markdown" else "*" if style == "whatsapp" else ""
    job_value = _sanitize_name(notice.job_name)
    if notice.job_id:
        job_value = f"{job_value} ({notice.job_id})"
    rows: list[tuple[str, str]] = [("Job", job_value)]
    if notice.schedule:
        rows.append(("Schedule", notice.schedule))
    if notice.duration_seconds is not None:
        rows.append(("Duration", humanize_duration(notice.duration_seconds)))
    if next_run:
        rows.append(("Next run", next_run))
    if notice.run_id:
        rows.append(("Run id", notice.run_id))
    rows.extend((str(label), str(value)) for label, value in notice.details if value)
    return [f"{bold}{label}:{bold} {value}" for label, value in rows]


def render_notice(notice: CronNotice, transport: Optional[str] = "markdown", *,
                  max_chars: Optional[int] = None) -> str:
    """Render *notice* for *transport*, bounded by the transport's hard cap."""
    style = _style_for(transport)
    if style == "plain":
        text = _render_plain(notice)
    elif style == "whatsapp":
        text = _render_whatsapp(notice)
    else:
        text = _render_markdown(notice)
    limit = max_chars if max_chars is not None else DEFAULT_MAX_CHARS
    text, _ = safe_truncate(text, limit)
    return text


def _headline(notice: CronNotice, style: str) -> str:
    icon = icon_for(notice.kind)
    name = _sanitize_name(notice.job_name)
    status = label_for(notice.kind)
    if style == "markdown":
        return f"{icon} **{name}** — {status}"
    if style == "whatsapp":
        return f"{icon} *{name}* — {status}"
    return f"{icon} {name} — {status}"


def _render_markdown(notice: CronNotice) -> str:
    lines = [_headline(notice, "markdown")]
    summary, summary_truncated = _summary_for(notice)
    if summary:
        lines.append("")
        lines.append(_strip_duplicate_icons(summary))
    rows = _detail_lines(notice, "markdown", next_run=_next_run_text(notice))
    if rows:
        lines.append("")
        lines.extend(rows)
    if summary_truncated and notice.job_id:
        lines.append(f"**Full output:** qubit cron runs {notice.job_id}")
    if notice.link:
        lines.append(f"[{notice.link_label}]({notice.link})")
    if notice.footer:
        lines.append("")
        lines.append(notice.footer)
    return "\n".join(line for line in lines if line is not None).strip()


def _render_whatsapp(notice: CronNotice) -> str:
    lines = [_headline(notice, "whatsapp")]
    summary, summary_truncated = _summary_for(notice)
    if summary:
        lines.append("")
        lines.append(_strip_duplicate_icons(_to_plain(summary)))
    rows = _detail_lines(notice, "whatsapp", next_run=_next_run_text(notice))
    if rows:
        lines.append("")
        lines.extend(rows)
    if summary_truncated and notice.job_id:
        lines.append(f"*Full output:* qubit cron runs {notice.job_id}")
    if notice.link:
        lines.append(f"{notice.link_label}: {notice.link}")
    if notice.footer:
        lines.append("")
        lines.append(notice.footer)
    return "\n".join(lines).strip()


def _render_plain(notice: CronNotice) -> str:
    lines = [_headline(notice, "plain")]
    summary, summary_truncated = _summary_for(notice)
    if summary:
        lines.append("")
        lines.append(_strip_duplicate_icons(_to_plain(summary)))
    rows = _detail_lines(notice, "plain", next_run=_next_run_text(notice))
    if rows:
        lines.append("")
        lines.extend(rows)
    if summary_truncated and notice.job_id:
        lines.append(f"Full output: qubit cron runs {notice.job_id}")
    if notice.link:
        lines.append(f"{notice.link_label}: {notice.link}")
    if notice.footer:
        lines.append("")
        lines.append(notice.footer)
    return "\n".join(lines).strip()


def _summary_for(notice: CronNotice) -> tuple[str, bool]:
    return notice.summary.strip(), notice.summary_truncated


def _next_run_text(notice: CronNotice) -> str:
    value = notice.next_run_at
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        return value
    return format_local_timestamp(value)


def _management_footer(job: dict) -> str:
    name = _sanitize_name(job.get("name") or job.get("id") or "")
    job_id = str(job.get("id") or "").strip()
    hint = f'To manage this job, reply "stop reminder {name}".' if name else "To manage this job, reply with a new message."
    if job_id:
        hint += f" CLI: qubit cron pause {job_id}"
    return hint


def build_run_notice(
    job: dict,
    content: str,
    *,
    success: bool,
    partial: bool = False,
    duration_seconds: Optional[float] = None,
    output_path: Any = None,
    dashboard_url: str = "",
    now: Optional[datetime] = None,
) -> CronNotice:
    """Build a completed/failed/partial run notice from the job record and run output."""
    kind = "success" if success and not partial else "partial" if success else "failed"
    name = _sanitize_name(job.get("name") or job.get("id") or "cron job")
    body = content or ""
    if not success:
        body = _strip_status_lead(body, name)
        summary, truncated = summarize_excerpt(
            body, max_chars=FAILURE_SUMMARY_MAX_CHARS, max_lines=SUMMARY_MAX_LINES)
    else:
        summary, truncated = summarize_excerpt(body)
    details: list[tuple[str, str]] = []
    if output_path and truncated:
        details.append(("Saved", str(output_path)))
    return CronNotice(
        kind=kind,
        job_name=name,
        job_id=str(job.get("id") or ""),
        summary=summary,
        summary_truncated=truncated,
        schedule=schedule_display(job),
        duration_seconds=duration_seconds,
        next_run_at=format_next_run(job, now=now),
        run_id=str(job.get("execution_id") or ""),
        link=safe_url(dashboard_url),
        footer=_management_footer(job),
        details=tuple(details),
    )


def build_simple_notice(
    job: dict,
    kind: str,
    *,
    summary: str = "",
    link: str = "",
    footer: Optional[str] = None,
    now: Optional[datetime] = None,
) -> CronNotice:
    """A notice for lifecycle events that carry no run output (reminder/started/paused/retry/receipt)."""
    bounded, truncated = summarize_excerpt(summary)
    return CronNotice(
        kind=kind,
        job_name=_sanitize_name(job.get("name") or job.get("id") or "cron job"),
        job_id=str(job.get("id") or ""),
        summary=bounded,
        summary_truncated=truncated,
        schedule=schedule_display(job),
        next_run_at=format_next_run(job, now=now),
        run_id=str(job.get("execution_id") or ""),
        link=safe_url(link),
        footer=_management_footer(job) if footer is None else footer,
    )


def safe_url(url: Any) -> str:
    """An operator-configured http(s) URL with any userinfo removed, or ""."""
    text = str(url or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://[^\s<>\"']+$", text, re.IGNORECASE):
        return ""
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(text)
    except ValueError:
        return ""
    if not parts.hostname:
        return ""
    hostname = parts.hostname
    if not hostname:
        return ""
    if ":" in hostname:
        hostname = f"[{hostname}]"
    try:
        port = parts.port
    except ValueError:
        return ""
    netloc = f"{hostname}:{port}" if port else hostname
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, ""))


def strip_legacy_cron_wrapper(content: str) -> str:
    """Strip the retired ``Cronjob Response:`` wrapper; new notices pass through unchanged."""
    if not content.startswith(_LEGACY_WRAPPER_HEADER):
        return content
    match = _LEGACY_HEADER_LINE_RE.match(content)
    if not match:
        return content
    body = content[match.end():]
    footer_pos = body.rfind(_LEGACY_WRAPPER_FOOTER)
    if footer_pos >= 0:
        body = body[:footer_pos]
    stripped = body.strip()
    return stripped or content
