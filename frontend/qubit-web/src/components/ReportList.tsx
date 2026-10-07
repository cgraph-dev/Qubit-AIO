/**
 * ReportList — the `/qubit-chat` recent-reports index.
 *
 * `/qubit-chat` is the report-reading surface, so the reports already present
 * in the transcript deserve a first-class, keyboard-navigable list rather than
 * only inline disclosure. Each row jumps the transcript to the report message
 * and highlights it (the page owns the scroll/highlight). Purely presentational
 * over {@link collectReports}; it never fetches and never fabricates a row.
 */
import { FileText, Terminal } from "lucide-react";

import { collectReports, type ReportSourceMessage } from "@/lib/reports";

export interface ReportListProps {
  /** The current transcript render messages. */
  messages: ReportSourceMessage[];
  /** Open/focus a report at a transcript index. */
  onOpen: (index: number) => void;
  className?: string;
}

export function ReportList({ messages, onOpen, className }: ReportListProps) {
  const reports = collectReports(messages);

  return (
    <section
      className={className ?? "qchat__reports"}
      aria-label="Recent reports"
      data-testid="report-list"
    >
      <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold tracking-wide text-text-secondary">
        <FileText className="h-3.5 w-3.5" aria-hidden="true" />
        <span>Reports</span>
        <span className="text-text-tertiary">({reports.length})</span>
      </div>
      {reports.length === 0 ? (
        <p
          className="text-xs text-text-tertiary"
          data-testid="report-list-empty"
        >
          No reports yet — a finished build turn appears here.
        </p>
      ) : (
        <ul className="flex list-none flex-col gap-1">
          {reports.map((report) => (
            <li key={report.id}>
              <button
                type="button"
                data-testid={`report-row-${report.index}`}
                data-kind={report.kind}
                aria-label={`Open report: ${report.title}`}
                onClick={() => onOpen(report.index)}
                className="flex w-full items-start gap-1.5 rounded px-1 py-0.5 text-left text-xs text-text-secondary hover:bg-current/5 hover:text-foreground"
              >
                <Terminal
                  className="mt-0.5 h-3 w-3 shrink-0 text-text-tertiary"
                  aria-hidden="true"
                />
                <span className="min-w-0 flex-1 break-words">
                  {report.title}
                  {report.taskKey ? (
                    <span className="ml-1 font-mono text-text-tertiary">
                      {report.taskKey}
                    </span>
                  ) : null}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default ReportList;
