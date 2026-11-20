import { SakugaLoading } from "@/sakuga/ui";
import { QubitAtomLoader } from "@/components/QubitAtomLoader";

/**
 * Presentation-only native /qubit-chat loader. Reuses Sakuga's accessible
 * activity state and its real CSS effects, with a separate gold atom SVG.
 * The track is intentionally indeterminate: the chat API does not provide
 * a reliable loading percentage for restoring a session.
 */
export function MangaChatLoader({ label = "Loading conversation…" }: { label?: string }) {
  // Preserve the existing loader for non-Manga themes. The Manga gate sets
  // this marker before the lazy native ChatPage mounts.
  if (typeof document === "undefined" ||
      document.documentElement.dataset.qubitManga !== "on") {
    return <QubitAtomLoader label="Loading chat…" size="sm" />;
  }
  return (
    <div className="qchat__manga-loader" data-testid="manga-chat-loader">
      <img
        className="qchat__manga-atom"
        src="/manga-ui/qubit-chat/atom.svg"
        width={128}
        height={128}
        alt=""
        aria-hidden="true"
        draggable={false}
      />
      <SakugaLoading label={label} />
      <span className="qchat__manga-indeterminate" aria-hidden="true" />
    </div>
  );
}
