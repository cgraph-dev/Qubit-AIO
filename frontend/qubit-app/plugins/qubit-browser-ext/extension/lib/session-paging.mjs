// Session list paging for the dashboard profile loader.
//
// The server tells us whether another page exists (has_more, or total). A short
// page is not the end of the list: the loader keeps following that answer and
// stops only when a page brings no new sessions, when the server reports no
// more pages, or at the page cap that bounds a runaway server.

import { shouldStopSessionPaging } from './common.mjs';

export const DASHBOARD_SESSION_PAGE_LIMIT = 100;
export const DASHBOARD_SESSION_MAX_PAGES = 5;
const MAX_PAGES_CEILING = 20;

function sessionRowId(row) {
  return String(row?.id || row?.session_id || '');
}

// fetchPage({ offset, limit, page }) resolves to { rows, hasMore, total }, or to
// null to end paging with the sessions gathered so far (for example a failed
// follow-on page). Rows are merged in server order and de-duplicated by id.
export async function collectSessionPages({
  fetchPage,
  limit = DASHBOARD_SESSION_PAGE_LIMIT,
  maxPages = DASHBOARD_SESSION_MAX_PAGES,
} = {}) {
  if (typeof fetchPage !== 'function') throw new TypeError('fetchPage must be a function.');
  const pageCap = Math.max(1, Math.min(MAX_PAGES_CEILING, Math.trunc(Number(maxPages)) || DASHBOARD_SESSION_MAX_PAGES));
  const pageLimit = Math.max(1, Math.trunc(Number(limit)) || DASHBOARD_SESSION_PAGE_LIMIT);
  const merged = [];
  const seen = new Set();
  let offset = 0;
  for (let page = 0; page < pageCap; page += 1) {
    const result = await fetchPage({ offset, limit: pageLimit, page });
    if (!result) break;
    const rows = Array.isArray(result.rows) ? result.rows : [];
    let added = 0;
    for (const row of rows) {
      const id = sessionRowId(row);
      if (id && seen.has(id)) continue;
      if (id) seen.add(id);
      merged.push(row);
      added += 1;
    }
    // An empty page, or one that only repeats sessions already gathered, means
    // the server is not advancing. Asking again would only re-read the same rows.
    if (!added) break;
    offset += rows.length;
    const stop = shouldStopSessionPaging({
      rowCount: rows.length,
      offset,
      total: Number(result.total || 0),
      hasMore: Boolean(result.hasMore),
    });
    if (stop) break;
  }
  return merged;
}
