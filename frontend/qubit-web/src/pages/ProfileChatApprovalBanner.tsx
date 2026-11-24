import { useEffect, useRef, useState } from "react";

import { api, type ProfileChatApproval, type ProfileChatApprovalChoice } from "@/lib/api";
import { Button } from "@nous-research/ui/ui/components/button";

const POLL_MS = 2500;

export function ProfileChatApprovalBanner({ session, profile }: {
  session: string;
  profile: string;
}) {
  const [approval, setApproval] = useState<ProfileChatApproval | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const revision = useRef(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: number | undefined;
    async function poll() {
      const atStart = revision.current;
      try {
        const result = await api.getProfileChatApproval(session, profile, controller.signal);
        if (!controller.signal.aborted && atStart === revision.current) {
          setApproval(result.approval);
        }
      } catch {
        // The owner can restart while this chat is visible. Invalidate any
        // previously rendered request until the next successful owner read.
        if (!controller.signal.aborted && atStart === revision.current) {
          setApproval(null);
        }
      } finally {
        if (!controller.signal.aborted) timer = window.setTimeout(poll, POLL_MS);
      }
    }
    void poll();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [session, profile]);

  async function respond(choice: ProfileChatApprovalChoice) {
    if (!approval || submitting) return;
    revision.current += 1;
    setSubmitting(true);
    setError("");
    try {
      await api.respondToProfileChatApproval(session, profile, approval, choice);
      setApproval(null);
    } catch {
      setApproval(null);
      setError("The approval response could not be confirmed. Check the pending request before trying again.");
    } finally {
      revision.current += 1;
      setSubmitting(false);
    }
  }

  if (!approval && !error) return null;
  return (
    <div className="mt-2 rounded-md border border-accent/50 bg-accent/10 px-3 py-2 text-sm text-text-primary">
      {approval ? (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start">
          <div className="min-w-0 flex-1" role="status" aria-live="polite">
            <p className="font-semibold">Approval required</p>
            {approval.description ? (
              <p className="break-words text-text-secondary">{approval.description}</p>
            ) : null}
            <code className="block whitespace-pre-wrap break-all bg-transparent text-text-primary">{approval.command}</code>
          </div>
          <div className="flex flex-wrap gap-2">
            {approval.choices.includes("once") ? (
              <Button type="button" className="min-h-[44px]" onClick={() => void respond("once")} disabled={submitting}>
                Approve once
              </Button>
            ) : null}
            {approval.choices.includes("deny") ? (
              <Button type="button" outlined className="min-h-[44px]" onClick={() => void respond("deny")} disabled={submitting}>
                Deny
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
      {error ? <p role="alert" className="text-warning">{error}</p> : null}
    </div>
  );
}
