/**
 * The URL prefix the vendored Sakuga scripts are served from.
 *
 * The assets are copied from `public/sakuga-vfx/` into the built renderer, so
 * they are siblings of the document in a packaged app (`loadFile`) and served
 * under the dev server root in `vite dev`. Resolving a relative path against
 * `document.baseURI` covers both shapes; `import.meta.env.BASE_URL` is `./`,
 * which is not a usable script prefix, so it is deliberately not used.
 */
export function sakugaBaseUrl(baseURI: string): string {
  return new URL('sakuga-vfx/', baseURI).href
}
