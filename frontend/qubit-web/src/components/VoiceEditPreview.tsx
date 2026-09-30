/** Qubit voice-edit preview card: original, proposed, inline diff, actions.
 *
 *  Presentation only — it never captures, transforms, or inserts text itself.
 *  Every action is reported to the caller, which owns the side effects.
 */
import { useMemo, useState } from "react";
import { wordDiff } from "@/lib/voiceDiff";
import type { TransformPreview } from "@/lib/voiceEdit";

export interface VoiceEditPreviewProps {
  preview: TransformPreview;
  busy?: boolean;
  statusMessage?: string;
  onApply?: () => void;
  onCopy?: () => void;
  onEdit?: (text: string) => void;
  onRetry?: () => void;
  onCancel?: () => void;
  onRevert?: () => void;
}

export function VoiceEditPreview({
  preview,
  busy = false,
  statusMessage = "",
  onApply,
  onCopy,
  onEdit,
  onRetry,
  onCancel,
  onRevert,
}: VoiceEditPreviewProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(preview.proposed);
  const diff = useMemo(
    () => wordDiff(preview.original ?? "", draft),
    [preview.original, draft],
  );
  const detected = preview.detected ?? { action: "", style: "", language: "", audience: "" };

  return (
    <section
      className="rounded-lg border border-border bg-card p-4 text-sm"
      data-testid="voice-edit-preview"
      aria-label="Qubit voice edit preview"
    >
      <header className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span data-testid="voice-edit-detected">
          command: <strong className="text-foreground">{detected.action || "rewrite"}</strong>
          {detected.style ? ` · ${detected.style}` : ""}
          {detected.language ? ` · ${detected.language}` : ""}
        </span>
        <span data-testid="voice-edit-model">
          model: <strong className="text-foreground">{preview.model || "local"}</strong>
          {preview.provider ? ` (${preview.provider})` : ""}
        </span>
        <span data-testid="voice-edit-privacy">
          privacy: {preview.privacy_mode || "auto"} · {preview.locality || "local"}
        </span>
        {typeof preview.elapsed_ms === "number" ? (
          <span data-testid="voice-edit-latency">{preview.elapsed_ms} ms</span>
        ) : null}
      </header>

      {preview.injection_suspected ? (
        <p
          className="mb-2 rounded bg-amber-500/10 px-2 py-1 text-xs text-amber-600"
          data-testid="voice-edit-injection-warning"
        >
          Instruction-like text was detected in the selection and treated as data.
        </p>
      ) : null}
      {preview.clarification_returned ? (
        <p
          className="mb-2 rounded bg-amber-500/10 px-2 py-1 text-xs text-amber-600"
          data-testid="voice-edit-clarification-warning"
        >
          The model replied with a question instead of a rewrite — edit it or retry.
        </p>
      ) : null}
      {preview.facts_dropped && preview.facts_dropped.length > 0 ? (
        <p
          className="mb-2 rounded bg-amber-500/10 px-2 py-1 text-xs text-amber-600"
          data-testid="voice-edit-facts-warning"
        >
          {preview.facts_dropped.length} identifier(s) from the original were not found in
          the result — review before applying.
        </p>
      ) : null}

      <div className="grid gap-3 md:grid-cols-2">
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Original
          </h3>
          <pre
            className="max-h-56 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-xs"
            data-testid="voice-edit-original"
          >
            {preview.original}
          </pre>
        </div>
        <div>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Proposed
          </h3>
          {editing ? (
            <textarea
              className="h-56 w-full rounded border border-border bg-background p-2 text-xs"
              aria-label="Edit proposed text"
              data-testid="voice-edit-edit-box"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          ) : (
            <pre
              className="max-h-56 overflow-auto whitespace-pre-wrap rounded bg-muted/40 p-2 text-xs"
              data-testid="voice-edit-proposed"
            >
              {diff.map((tok, i) => (
                <span
                  key={i}
                  data-diff={tok.type}
                  className={
                    tok.type === "add"
                      ? "bg-emerald-500/25"
                      : tok.type === "del"
                        ? "bg-rose-500/25 text-muted-foreground line-through"
                        : undefined
                  }
                >
                  {tok.text}
                </span>
              ))}
            </pre>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {editing ? (
          <>
            <button
              type="button"
              className="rounded bg-primary px-3 py-1 text-xs font-medium text-primary-foreground"
              onClick={() => {
                setEditing(false);
                onEdit?.(draft);
              }}
            >
              Save edit
            </button>
            <button
              type="button"
              className="rounded border border-border px-3 py-1 text-xs"
              onClick={() => {
                setDraft(preview.proposed);
                setEditing(false);
              }}
            >
              Discard edit
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              data-testid="voice-edit-apply"
              disabled={busy}
              className="rounded bg-primary px-3 py-1 text-xs font-medium text-primary-foreground disabled:opacity-50"
              onClick={onApply}
            >
              Apply
            </button>
            <button
              type="button"
              data-testid="voice-edit-copy"
              className="rounded border border-border px-3 py-1 text-xs"
              onClick={onCopy}
            >
              Copy
            </button>
            <button
              type="button"
              data-testid="voice-edit-edit"
              className="rounded border border-border px-3 py-1 text-xs"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
            <button
              type="button"
              data-testid="voice-edit-retry"
              className="rounded border border-border px-3 py-1 text-xs"
              onClick={onRetry}
            >
              Retry
            </button>
            <button
              type="button"
              data-testid="voice-edit-revert"
              className="rounded border border-border px-3 py-1 text-xs"
              onClick={onRevert}
            >
              Revert
            </button>
            <button
              type="button"
              data-testid="voice-edit-cancel"
              className="rounded border border-border px-3 py-1 text-xs"
              onClick={onCancel}
            >
              Cancel
            </button>
          </>
        )}
      </div>
      {statusMessage ? (
        <p className="mt-2 text-xs text-muted-foreground" data-testid="voice-edit-status">
          {statusMessage}
        </p>
      ) : null}
    </section>
  );
}

export default VoiceEditPreview;
