/**
 * Recent-reports derivation for `/qubit-chat`.
 *
 * `/qubit-chat` is the report-reading surface: a finished `/chat` worker build
 * turn persists as an assistant message carrying `report` / `reportText` /
 * `taskKey` (see `ChatPage.toChatMessage` + the stream `done` handler). The
 * transcript shows those inline, but scrolling to find the last report is not a
 * report LIST. This module derives a first-class, newest-first list of the
 * reports in a transcript so the page can surface them as a navigable index.
 *
 * Pure and total: it never fabricates a report and returns `[]` for a
 * transcript with none.
 */

/**
 * The subset of a transcript render message this module needs. `ChatPage`
 * defines its own richer `ChatMessage`; keeping a structural subset here means
 * this pure module has no import back into the page (no cycle) and stays
 * independently testable.
 */
export interface ReportSourceMessage {
  role?: string;
  text?: string;
  report?: boolean;
  reportText?: string;
  taskKey?: string;
}

export interface ReportEntry {
  /** Stable id: the transcript index the report message lives at. */
  id: string;
  /** Transcript index (used to scroll/highlight the message). */
  index: number;
  /** One-line label for the list row. */
  title: string;
  /** The report body (full `reportText` when present, else the summary). */
  body: string;
  /** The `/chat` build-task key, when the report carries one. */
  taskKey?: string;
  /** Heuristic: the report reads like a build/failure/status summary. */
  kind: "build" | "summary";
}

/** First non-empty line of the body, trimmed to a bounded length. */
function firstLine(body: string, max = 90): string {
  const line = body
    .split("\n")
    .map((l) => l.replace(/^[#>*\-\s]+/, "").trim())
    .find((l) => l.length > 0);
  if (!line) return "Report";
  return line.length > max ? `${line.slice(0, max - 1)}…` : line;
}

/** True when a render message is a report worth indexing. */
export function isReportMessage(message: ReportSourceMessage): boolean {
  return Boolean(message.report) || Boolean(message.taskKey);
}

/** The full report body for a report message (full text wins over summary). */
export function reportBody(message: ReportSourceMessage): string {
  return (message.reportText ?? message.text ?? "").trim();
}

/**
 * Newest-first report index for a transcript. Index ascending in the source,
 * returned newest (highest index) first so the most recent report is on top.
 */
export function collectReports(messages: ReportSourceMessage[]): ReportEntry[] {
  const out: ReportEntry[] = [];
  messages.forEach((message, index) => {
    if (!isReportMessage(message)) return;
    const body = reportBody(message);
    out.push({
      id: `report-${index}`,
      index,
      title: firstLine(body),
      body,
      taskKey: message.taskKey,
      kind: message.report ? "build" : "summary",
    });
  });
  return out.reverse();
}
