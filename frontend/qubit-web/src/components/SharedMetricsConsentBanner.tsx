import { useEffect, useState } from "react";
import { BarChart3, X } from "lucide-react";
import { api, type SharedMetricsConsent } from "@/lib/api";
import { useProfileScope } from "@/contexts/useProfileScope";

const STORAGE_KEY = "sharedMetricsOfferDismissed";

/**
 * The dashboard's first-run shared-metrics offer: the twin of the Desktop composer
 * strip and the terminal's offer, with the same three equal answers. Shown while the
 * active profile has no answer in its config.yaml; answering writes it (every surface
 * then stops asking), while the X only hides the banner for this browser session.
 *
 * Ported 1:1 from the upstream dashboard component. Two deliberate differences: the
 * docs URL points at this repository (upstream's qubit.nousresearch.com page does not
 * describe this build), and there is no "Help improve Hermes?" fallback string.
 */
export function SharedMetricsConsentBanner() {
  const { profile } = useProfileScope();
  // Keyed by the profile it was read for, so a profile switch hides a stale answer at once.
  const [loaded, setLoaded] = useState<{ profile: string; consent: SharedMetricsConsent } | null>(
    null,
  );
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  const [dismissed, setDismissed] = useState(() => {
    try {
      return sessionStorage.getItem(STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    let live = true;
    api
      .getSharedMetricsConsent()
      .then((next) => {
        if (live) setLoaded({ profile, consent: next });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [profile]);

  const consent = loaded?.profile === profile ? loaded.consent : null;
  if (dismissed || !consent || consent.decided || consent.managed) return null;

  const answer = (enabled: boolean, send: boolean) => {
    setSaving(true);
    setFailed(false);
    api
      .saveSharedMetricsConsent({ enabled, send })
      .then((next) => setLoaded({ profile, consent: next }))
      .catch(() => setFailed(true))
      .finally(() => setSaving(false));
  };
  const dismiss = () => {
    try {
      sessionStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* ignore */
    }
    setDismissed(true);
  };
  const choice =
    "shrink-0 rounded border border-current/30 px-2 py-0.5 hover:bg-current/10 disabled:opacity-50";

  return (
    <div
      role="region"
      aria-label="Help improve Qubit?"
      data-testid="shared-metrics-consent-banner"
      className="flex flex-wrap items-center gap-2 border-b border-current/20 bg-current/5 px-4 py-1.5 text-xs text-midground"
    >
      <BarChart3 className="h-3.5 w-3.5 shrink-0" />
      <span className="font-semibold">Help improve Qubit?</span>
      <span className="min-w-0 flex-1 opacity-80">
        {failed
          ? "Couldn't save your choice"
          : ("Shared metrics are bounded counters, never prompts, files, paths or error text. Collection stays on this machine; sending them off this machine is a separate choice."){" "}
      </span>
      <button type="button" disabled={saving} className={choice} onClick={() => answer(true, true)}>
        {Share counters}
      </button>
      <button type="button" disabled={saving} className={choice} onClick={() => answer(true, false)}>
        {Local only}
      </button>
      <button type="button" disabled={saving} className={choice} onClick={() => answer(false, false)}>
        {No thanks}
      </button>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={dismiss}
        className="shrink-0 opacity-70 hover:opacity-100"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
