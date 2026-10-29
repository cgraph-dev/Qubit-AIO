/**
 * Qubit TV (`/qubit-tv`).
 *
 * Upstream attribution: adapted from the MIT-licensed HermesTV plugin,
 * repo `asimons81/hermestv`, commit
 * `fbd445b381ff5f371bf46bdc96a7d79362d9510f` (MIT License, Copyright (c)
 * 2025 Nous Research). Only the service list and the dashboard shape
 * (service grid, search, favorites) are carried across; the native Electron
 * media host is not.
 *
 * Honest web variant: upstream is an Electron media plugin with a native
 * player surface. A dashboard page cannot embed or control cross-origin DRM
 * players (Netflix, Prime Video, Disney+, etc.), so those services open in a
 * browser window. YouTube is the only playback path that actually embeds.
 */

import { useCallback, useMemo, useState } from "react";
import {
  ExternalLink,
  Play,
  Search,
  Star,
  Tv,
  Youtube,
} from "lucide-react";

import { cn } from "@/lib/utils";
import {
  TV_SERVICES,
  parseYouTubeId,
  readTvFavorites,
  writeTvFavorites,
  type TvService,
} from "@/lib/qubit-tv";

const SERVICE_OPEN_FEATURES = "noopener,noreferrer,width=1280,height=860";

export default function QubitTvPage() {
  const [filter, setFilter] = useState("");
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [favorites, setFavorites] = useState<string[]>(readTvFavorites);
  const [videoInput, setVideoInput] = useState("");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [videoError, setVideoError] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    return TV_SERVICES.filter((service) => {
      if (favoritesOnly && !favorites.includes(service.id)) return false;
      return needle === "" || service.label.toLowerCase().includes(needle);
    });
  }, [favorites, favoritesOnly, filter]);

  const openService = useCallback((service: TvService) => {
    // Cross-origin DRM players cannot be embedded here, so the browser window
    // is the whole integration. noopener/noreferrer keep it isolated.
    window.open(service.url, "_blank", SERVICE_OPEN_FEATURES);
  }, []);

  const toggleFavorite = useCallback((id: string) => {
    setFavorites((previous) => {
      const next = previous.includes(id)
        ? previous.filter((favorite) => favorite !== id)
        : [...previous, id];
      writeTvFavorites(next);
      return next;
    });
  }, []);

  const loadVideo = useCallback(() => {
    const id = parseYouTubeId(videoInput);
    if (!id) {
      setVideoId(null);
      setVideoError(
        "Enter a valid YouTube URL or 11-character video ID.",
      );
      return;
    }
    setVideoError(null);
    setVideoId(id);
  }, [videoInput]);

  return (
    <div className="settings-page" data-testid="qubit-tv-page">
      <header className="settings-page__header">
        <div className="settings-page__copy">
          <h1 className="settings-page__title flex items-center gap-2">
            <Tv className="h-5 w-5 text-primary" aria-hidden="true" />
            Qubit TV
          </h1>
          <p className="settings-page__desc">
            Subscription services open in a new browser window. Their
            DRM-protected playback cannot be embedded or controlled from this
            dashboard. YouTube videos embed directly in the page below.
          </p>
        </div>
      </header>

      <section
        className="rounded-[18px] border border-border bg-card p-4"
        aria-label="YouTube embed"
        data-testid="qubit-tv-youtube"
      >
        <div className="flex flex-wrap items-center gap-2">
          <Youtube className="h-5 w-5 text-primary" aria-hidden="true" />
          <h2 className="mr-auto text-sm font-semibold text-foreground">
            Watch a YouTube video
          </h2>
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          Paste a YouTube link or an 11-character video ID. This is the only
          service whose playback can be embedded.
        </p>
        <form
          className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center"
          onSubmit={(event) => {
            event.preventDefault();
            loadVideo();
          }}
        >
          <label className="sr-only" htmlFor="qubit-tv-youtube-input">
            YouTube URL or video ID
          </label>
          <input
            id="qubit-tv-youtube-input"
            type="text"
            value={videoInput}
            onChange={(event) => setVideoInput(event.target.value)}
            placeholder="https://www.youtube.com/watch?v=… or video ID"
            aria-label="YouTube URL or video ID"
            className="min-w-0 flex-1 rounded-[9px] border border-border bg-background-base px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button
            type="submit"
            className="inline-flex items-center justify-center gap-2 rounded-[9px] border border-border bg-secondary px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
          >
            <Play className="h-4 w-4" aria-hidden="true" />
            Load video
          </button>
        </form>
        {videoError ? (
          <p className="mt-2 text-xs text-destructive" role="alert">
            {videoError}
          </p>
        ) : null}
        {videoId ? (
          <div className="mt-3" data-testid="qubit-tv-embed">
            <div className="relative aspect-video w-full overflow-hidden rounded-[12px] border border-border bg-black">
              <iframe
                className="absolute inset-0 h-full w-full"
                src={`https://www.youtube.com/embed/${videoId}`}
                title="Qubit TV YouTube player"
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share"
                allowFullScreen
              />
            </div>
            <div className="mt-2 flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span>
                Video <span className="font-mono text-foreground">{videoId}</span>
              </span>
              <button
                type="button"
                className="rounded-[7px] border border-border px-2 py-1 text-xs text-foreground hover:bg-secondary"
                onClick={() => {
                  setVideoId(null);
                  setVideoInput("");
                  setVideoError(null);
                }}
              >
                Clear
              </button>
            </div>
          </div>
        ) : null}
      </section>

      <section
        className="mt-4 rounded-[18px] border border-border bg-card p-4"
        aria-label="Streaming services"
        data-testid="qubit-tv-services"
      >
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="mr-auto text-sm font-semibold text-foreground">
            Streaming services
          </h2>
          <button
            type="button"
            className="rounded-[9px] border border-border bg-secondary px-3 py-1.5 text-xs font-medium text-foreground hover:bg-accent"
            onClick={() => setFavoritesOnly((value) => !value)}
            aria-pressed={favoritesOnly}
          >
            {favoritesOnly ? "Show all services" : "Show favorites"}
          </button>
        </div>

        <div className="relative mt-3 max-w-md">
          <Search
            className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Find a service"
            aria-label="Find a service"
            className="w-full rounded-[9px] border border-border bg-background-base py-2 pl-8 pr-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>

        <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filtered.map((service) => {
            const isFavorite = favorites.includes(service.id);
            return (
              <article
                key={service.id}
                className={cn(
                  "flex items-center gap-2 rounded-[12px] border border-border bg-background-base/60 p-2",
                  isFavorite && "border-primary/60",
                )}
                data-testid={`qubit-tv-service-${service.id}`}
              >
                <button
                  type="button"
                  onClick={() => openService(service)}
                  className="flex min-w-0 flex-1 items-center justify-between gap-2 rounded-[8px] px-2 py-1.5 text-left text-sm font-medium text-foreground hover:bg-secondary"
                  aria-label={`Open ${service.label}`}
                >
                  <span className="truncate">{service.label}</span>
                  <ExternalLink
                    className="h-4 w-4 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                </button>
                <button
                  type="button"
                  onClick={() => toggleFavorite(service.id)}
                  aria-label={`Toggle ${service.label} favorite`}
                  aria-pressed={isFavorite}
                  className={cn(
                    "rounded-[8px] border border-border p-1.5 text-muted-foreground hover:bg-secondary",
                    isFavorite && "text-primary",
                  )}
                >
                  <Star
                    className="h-4 w-4"
                    fill={isFavorite ? "currentColor" : "none"}
                    aria-hidden="true"
                  />
                </button>
              </article>
            );
          })}
        </div>

        {filtered.length === 0 ? (
          <p className="mt-3 text-xs text-muted-foreground" role="status">
            No services match the current filter.
          </p>
        ) : null}

        <p className="mt-3 text-xs text-muted-foreground">
          Service playback and sign-in depend on each provider and on your
          browser; Qubit never handles your streaming credentials.
        </p>
      </section>
    </div>
  );
}
