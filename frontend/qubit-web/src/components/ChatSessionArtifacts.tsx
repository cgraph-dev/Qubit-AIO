/**
 * A33/A36 — per-session chat artifacts panel.
 *
 * Renders the canonical `chat_stores` read routes for the active session:
 * per-session file drafts (A36) and operator comments + structured CI details
 * (A33). The backend routes are the same store the CLI uses; this is a
 * read-only renderer, so it never creates a second chat surface or state.
 */
import { useEffect, useState } from "react";

import { Spinner } from "@nous-research/ui/ui/components/spinner";

import {
  api,
  type ChatCiEntry,
  type ChatCommentEntry,
  type ChatDraftEntry,
} from "@/lib/api";

type LoadState = "loading" | "ready" | "error";

export function ChatSessionArtifacts({ sessionId }: { sessionId: string }) {
  const [state, setState] = useState<LoadState>(
    sessionId ? "loading" : "ready",
  );
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<ChatDraftEntry[]>([]);
  const [comments, setComments] = useState<ChatCommentEntry[]>([]);
  const [ci, setCi] = useState<ChatCiEntry[]>([]);

  useEffect(() => {
    let cancelled = false;
    if (!sessionId) {
      return () => {
        cancelled = true;
      };
    }
    const call = <T,>(
      fn: ((id: string) => Promise<T>) | undefined,
      fallback: T,
    ): Promise<T> =>
      typeof fn === "function"
        ? fn(sessionId).catch(() => fallback)
        : Promise.resolve(fallback);
    Promise.all([
      call(api.getChatSessionDrafts, { drafts: [] as ChatDraftEntry[] }),
      call(api.getChatSessionComments, {
        comments: [] as ChatCommentEntry[],
      }),
      call(api.getChatSessionCi, { ci: [] as ChatCiEntry[] }),
    ])
      .then(([d, c, k]) => {
        if (cancelled) return;
        setDrafts(d.drafts ?? []);
        setComments(c.comments ?? []);
        setCi(k.ci ?? []);
        setState("ready");
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const total = drafts.length + comments.length + ci.length;

  return (
    <details
      data-state={state}
      className="shrink-0 border border-border bg-background-base/60 px-2 py-1 text-xs"
    >
      <summary
        aria-label="Session artifacts"
        className="cursor-pointer list-none text-text-secondary"
      >
        Session artifacts ({total})
      </summary>
      {state === "loading" ? (
        <p className="flex items-center gap-2 py-1 text-text-tertiary">
          <Spinner className="size-3" /> Loading artifacts…
        </p>
      ) : state === "error" ? (
        <p role="alert" className="py-1 text-destructive">
          {error}
        </p>
      ) : total === 0 ? (
        <p className="py-1 text-text-tertiary">No drafts, comments, or CI.</p>
      ) : (
        <div className="flex flex-col gap-1 py-1">
          {drafts.length > 0 && (
            <div>
              <p className="text-text-secondary">Drafts ({drafts.length})</p>
              <ul className="font-mono text-[0.6875rem] text-text-tertiary">
                {drafts.slice(0, 8).map((d, i) => (
                  <li key={`${d.path}-${i}`} className="truncate">
                    {d.path} · {d.bytes}B
                  </li>
                ))}
              </ul>
            </div>
          )}
          {comments.length > 0 && (
            <div>
              <p className="text-text-secondary">Comments ({comments.length})</p>
              <ul className="text-[0.6875rem] text-text-tertiary">
                {comments.slice(0, 5).map((c, i) => (
                  <li key={`${c.id}-${i}`} className="truncate">
                    {c.author ? `${c.author}: ` : ""}
                    {c.body}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {ci.length > 0 && (
            <div>
              <p className="text-text-secondary">CI ({ci.length})</p>
              <ul className="font-mono text-[0.6875rem] text-text-tertiary">
                {ci.slice(0, 5).map((k, i) => (
                  <li key={`${k.id}-${i}`} className="truncate">
                    {k.check} · {k.status}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </details>
  );
}

export default ChatSessionArtifacts;
