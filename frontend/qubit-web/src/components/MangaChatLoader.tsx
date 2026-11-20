import { SakugaLoading } from "@/sakuga/ui";
import { QubitAtomLoader } from "@/components/QubitAtomLoader";

/**
 * Presentation-only native /qubit-chat loader. Reuses Sakuga's accessible
 * activity state and its real CSS effects, with a separate gold atom SVG.
 * The track is intentionally indeterminate: the chat API does not provide
 * a reliable loading percentage for restoring a session.
 */
export function MangaChatLoader({
  label = "Loading conversation…",
  size = "md",
}: { label?: string; size?: "md" | "inline" }) {
  // Preserve existing status and visual marks in every non-Manga theme.
  const mangaOn = typeof document !== "undefined" &&
    document.documentElement.dataset.qubitManga === "on";
  if (!mangaOn) {
    return size === "inline"
      ? <QubitAtomLoader size="inline" showLabel={false} showBar={false} showCat={false} />
      : <QubitAtomLoader label="Loading chat…" size="sm" />;
  }
  // Streaming already has an aria-live message on its parent. Do not create
  // a second live-region announcement or a large spinner in each message row.
  if (size === "inline") {
    return (
      <img
        className="qchat__manga-atom-inline"
        src="/manga-ui/qubit-chat/atom.svg"
        width={25}
        height={25}
        alt=""
        aria-hidden="true"
        draggable={false}
      />
    );
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
