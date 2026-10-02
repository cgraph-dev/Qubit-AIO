/**
 * Open Viking retrieval (`/open-viking/retrieval`).
 *
 * Qubit-owned find/search UI over the local context engine's
 * retrieval API via `@/lib/ocFamilyVikingApi` (`retrieval_search`).
 * A missing bridge row renders honestly disabled with reason.
 */

import { useCallback, useState } from "react";
import { Link } from "react-router";
import { ArrowLeft, Search } from "lucide-react";
import {
  isBridgeRowMissing,
  normalizeSearchHits,
  searchVikingRetrieval,
  type VikingSearchHit,
} from "@/lib/ocFamilyVikingApi";

export default function OpenVikingRetrievalPage() {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<VikingSearchHit[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "done" | "unavailable" | "error">("idle");
  const [error, setError] = useState("");

  const onSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) return;
    setState("loading");
    setError("");
    try {
      const payload = await searchVikingRetrieval(q, 25);
      setHits(normalizeSearchHits(payload));
      setState("done");
    } catch (e) {
      if (isBridgeRowMissing(e)) {
        setHits([]);
        setState("unavailable");
      } else {
        setState("error");
        setError(e instanceof Error ? e.message : "search failed");
      }
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
      {state === "unavailable" && (
        <div
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
          role="note"
          data-testid="viking-retrieval-unavailable"
        >
          Retrieval-search bridge row missing — the J1 lane has not landed it yet.
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
