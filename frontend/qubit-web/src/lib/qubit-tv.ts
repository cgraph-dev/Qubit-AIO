/**
 * Qubit TV data helpers (`/qubit-tv`).
 *
 * Upstream attribution: the service list is adapted from the MIT-licensed
 * HermesTV plugin, repo `asimons81/hermestv`, commit
 * `fbd445b381ff5f371bf46bdc96a7d79362d9510f` (MIT License, Copyright (c)
 * 2025 Nous Research).
 */

export interface TvService {
  id: string;
  label: string;
  url: string;
}

/** The upstream service set, unchanged. */
export const TV_SERVICES: TvService[] = [
  { id: "youtube", label: "YouTube", url: "https://www.youtube.com/" },
  { id: "tv", label: "YouTube TV", url: "https://tv.youtube.com/" },
  { id: "netflix", label: "Netflix", url: "https://www.netflix.com/" },
  { id: "prime-video", label: "Prime Video", url: "https://www.primevideo.com/" },
  { id: "disney-plus", label: "Disney+", url: "https://www.disneyplus.com/" },
  { id: "hulu", label: "Hulu", url: "https://www.hulu.com/" },
  { id: "hbo-max", label: "HBO Max", url: "https://www.hbomax.com/" },
  { id: "apple-tv", label: "Apple TV+", url: "https://tv.apple.com/" },
  { id: "peacock", label: "Peacock", url: "https://www.peacocktv.com/" },
  { id: "paramount-plus", label: "Paramount+", url: "https://www.paramountplus.com/" },
];

export const TV_FAVORITES_KEY = "qubit-tv:favorites";

/** A YouTube id is exactly 11 URL-safe characters. */
function asVideoId(value: string | null | undefined): string | null {
  const trimmed = (value ?? "").trim();
  return /^[A-Za-z0-9_-]{11}$/.test(trimmed) ? trimmed : null;
}

/** Accept a bare id, a short link, or a youtube.com watch/embed/shorts URL. */
export function parseYouTubeId(input: string): string | null {
  const value = input.trim();
  if (!value) return null;

  const bare = asVideoId(value);
  if (bare) return bare;

  let url: URL;
  try {
    url = new URL(value.includes("://") ? value : `https://${value}`);
  } catch {
    return null;
  }

  const host = url.hostname.replace(/^www\./i, "").toLowerCase();
  if (host === "youtu.be") {
    return asVideoId(url.pathname.split("/").filter(Boolean)[0]);
  }
  if (
    host === "youtube.com" ||
    host === "m.youtube.com" ||
    host === "music.youtube.com"
  ) {
    if (url.pathname === "/watch") {
      return asVideoId(url.searchParams.get("v"));
    }
    const match = url.pathname.match(
      /^\/(?:embed|shorts|live|v)\/([A-Za-z0-9_-]{11})(?:[/?]|$)/,
    );
    if (match) return match[1];
  }
  return null;
}

export function readTvFavorites(): string[] {
  try {
    const raw = window.localStorage.getItem(TV_FAVORITES_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((id): id is string => typeof id === "string");
  } catch {
    return [];
  }
}

export function writeTvFavorites(favorites: string[]) {
  try {
    window.localStorage.setItem(TV_FAVORITES_KEY, JSON.stringify(favorites));
  } catch {
    // Private-mode/quota failures must never break the page.
  }
}
