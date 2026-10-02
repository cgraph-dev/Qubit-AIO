/**
 * Open Viking retrieval (`/open-viking/retrieval`).
 *
 * Qubit-owned find/search UI over the external console server's
 * retrieval API via `@/lib/ocFamilyVikingApi` (`retrieval_search`).
 * Unconfigured servers render the typed SERVER_UNCONFIGURED state.
 */

import { useCallback, useState } from "react";
import { Link } from "react-router";
import { ArrowLeft, Search } from "lucide-react";
import {
  isServerUnconfiguredPayload,
  normalizeSearchHits,
  searchVikingRetrieval,
  type VikingSearchHit,
} from "@/lib/ocFamilyVikingApi";

export default function OpenVikingRetrievalPage() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<VikingSearchHit[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "done" | "unconfigured" | "error">("idle");
  const [error, setError] = useState("");

  const onSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setState("loading");
    setError("");
    try {
      const payload = await searchVikingRetrieval(q, 25);
      if (isServerUnconfiguredPayload(payload)) {
        setState("unconfigured");
        setHits([]);
        return;
      }
      setHits(normalizeSearchHits(payload));
      setState("done");
    } catch (e) {
      setState("error");
      setError(e instanceof Error ? e.message : "search failed");
    }
  }, [query]);

  return (
    <div className="flex flex-col gap-4 p-4" data-testid="open-viking-retrieval-page">
      <header className="flex items-center gap-3">
        <Link to="/open-viking" className="flex items-center gap-1 text-sm hover:underline">
          <ArrowLeft className="h-4 w-4" aria-hidden /> Open Viking
        </Link>
        <h1 className="text-xl font-semibold">Retrieval</h1>
      </header>

      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void onSearch();
        }}
      >
        <label className="sr-only" htmlFor="viking-retrieval-q">
          Search the VikingBot context
        </label>
        <input
          id="viking-retrieval-q"
          className="flex-1 rounded border px-3 py-2 text-sm"
          placeholder="Find related context…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          type="submit"
          className="flex items-center gap-2 rounded bg-primary px-4 py-2 text-sm text-primary-foreground"
        >
          <Search className="h-4 w-4" aria-hidden /> Find
        </button>
      </form>

      {state === "loading" && <p className="text-sm text-muted-foreground">Searching…</p>}
      {state === "unconfigured" && (
        <div className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm" role="note">
          <strong>SERVER_UNCONFIGURED</strong> — no Open Viking server is configured.
        </div>
      )}
      {state === "error" && <p className="text-sm text-destructive">{error}</p>}
      {state === "done" && hits.length === 0 && (
        <p className="text-sm text-muted-foreground">No matches.</p>
      )}
      {hits.length > 0 && (
        <ul className="flex flex-col gap-2" data-testid="viking-retrieval-hits">
          {hits.map((hit) => (
            <li key={`${hit.id}-${hit.title}`} className="rounded-md border p-3">
              <p className="text-sm font-medium">{hit.title}</p>
              {hit.snippet && <p className="mt-1 text-sm text-muted-foreground">{hit.snippet}</p>}
              {hit.path && <p className="mt-1 font-mono text-xs text-muted-foreground">{hit.path}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
