// Large-paste clipboard artifacts (issues #94).
//
// A paste too big to live in the composer becomes a text attachment: the
// composer keeps only the user's typed words, the turn payload carries a
// head+tail excerpt inside the BCP v2 per-attachment budget (4,000 chars), and
// the full text is preserved locally on the attachment instead of being
// silently trimmed at the 6,000-char human-input budget. Only that excerpt is
// ever sent to the gateway — the full body never leaves this device, so no
// surface may promise otherwise. Pastes beyond the local preservation ceiling
// are rejected by the caller rather than silently clamped. Everything here is
// pure so thresholds, naming, stats, and the excerpt policy are unit-testable
// without a DOM.

export const PASTE_ARTIFACT_MIN_CHARS = 1_500;
export const PASTE_ARTIFACT_MIN_LINES = 25;
// Beyond this the browser keeps the head only; the artifact labels the clamp
// honestly instead of freezing on a multi-megabyte paste.
export const PASTE_ARTIFACT_MAX_CHARS = 400_000;
// Excerpt budget: head + marker + tail must stay under the protocol's
// `attachmentTextChars` (4,000) so the envelope builder records no truncation.
export const PASTE_ARTIFACT_EXCERPT_HEAD_CHARS = 2_400;
export const PASTE_ARTIFACT_EXCERPT_TAIL_CHARS = 1_200;
// Mirrors BROWSER_CONTEXT_TURN_BUDGETS.maxAttachments.
export const PASTE_ARTIFACT_MAX_ATTACHMENTS = 12;

function lineCountOf(value) {
  const text = String(value || '');
  return text ? text.split(/\r\n|\r|\n/).length : 0;
}

function byteLengthOf(value) {
  try {
    return new TextEncoder().encode(String(value || '')).length;
  } catch {
    return String(value || '').length;
  }
}

export function classifyPastedText(text = '', {
  minChars = PASTE_ARTIFACT_MIN_CHARS,
  minLines = PASTE_ARTIFACT_MIN_LINES,
} = {}) {
  const value = String(text || '');
  const chars = value.length;
  const lines = lineCountOf(value);
  const overChars = chars > minChars;
  const overLines = lines > minLines;
  return {
    large: overChars || overLines,
    reason: overChars ? 'chars' : (overLines ? 'lines' : 'none'),
    chars,
    lines,
  };
}

export function pasteArtifactBaseName(date = new Date()) {
  const pad = (value) => String(value).padStart(2, '0');
  return `pasted-text-${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}.txt`;
}

// Local preservation is bounded: pastes up to the ceiling keep their full text
// in the composer attachment; anything larger is rejected outright (never
// silently clamped) so the user can decide, e.g. attach it as a file instead.
export function pasteArtifactExceedsLocalLimit(text = '', { maxChars = PASTE_ARTIFACT_MAX_CHARS } = {}) {
  return String(text || '').length > maxChars;
}

export function buildPasteArtifact(text = '', { now = new Date() } = {}) {
  const raw = String(text || '');
  const kept = raw.length > PASTE_ARTIFACT_MAX_CHARS ? raw.slice(0, PASTE_ARTIFACT_MAX_CHARS) : raw;
  return {
    kind: 'text',
    source: 'paste',
    label: pasteArtifactBaseName(now),
    charCount: raw.length,
    lineCount: lineCountOf(raw),
    sizeBytes: byteLengthOf(kept),
    truncated: kept.length < raw.length,
    text: kept,
  };
}

export function pasteArtifactExcerpt(text = '', {
  head = PASTE_ARTIFACT_EXCERPT_HEAD_CHARS,
  tail = PASTE_ARTIFACT_EXCERPT_TAIL_CHARS,
} = {}) {
  const value = String(text || '');
  if (value.length <= head + tail) return value;
  const omitted = value.length - head - tail;
  const marker = `\n\n[… ${omitted} of ${value.length} pasted characters omitted from this excerpt …]\n\n`;
  return `${value.slice(0, head)}${marker}${value.slice(value.length - tail)}`;
}

export function isPasteArtifact(attachment) {
  return Boolean(attachment && attachment.kind === 'text' && attachment.source === 'paste');
}

// What the composer must say before a paste artifact is sent: the turn carries
// only the head and tail of the paste, so the omitted middle is named. Returns
// null when the whole paste fits in the excerpt (nothing is shortened).
export function pasteArtifactShortening(attachment, {
  head = PASTE_ARTIFACT_EXCERPT_HEAD_CHARS,
  tail = PASTE_ARTIFACT_EXCERPT_TAIL_CHARS,
} = {}) {
  if (!isPasteArtifact(attachment)) return null;
  const total = String(attachment.text || '').length;
  const omitted = total - head - tail;
  if (omitted <= 0) return null;
  return Object.freeze({ head, tail, total, omitted });
}
