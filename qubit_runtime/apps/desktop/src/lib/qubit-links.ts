/**
 * Canonical Qubit links.
 *
 * Every user-facing link the desktop shell opens resolves through this module so
 * the product never points at a legacy upstream surface. The docs / skills-hub /
 * plugin-catalog origin is deployment-configurable: Qubit does not yet ship a
 * hosted docs hub, so set `VITE_QUBIT_DOCS_ORIGIN` to the deployment that serves
 * `/docs/skills?embed=picker`, `/docs/plugins?embed=picker` and
 * `/docs/api/plugins.json` once one exists. Until then it falls back to the
 * canonical repository, and the embedded pickers and catalog lookups degrade to
 * their local fallbacks rather than pointing at a foreign service.
 */

export const QUBIT_REPO_URL = 'https://github.com/cgraph-dev/qubit-voicebridge'
export const QUBIT_REPO_GIT_URL = 'git+https://github.com/cgraph-dev/qubit-voicebridge.git'
export const QUBIT_ISSUES_URL = `${QUBIT_REPO_URL}/issues`
export const QUBIT_RELEASES_URL = `${QUBIT_REPO_URL}/releases`
export const QUBIT_RAW_MAIN_URL = 'https://raw.githubusercontent.com/cgraph-dev/qubit-voicebridge/main'

export const QUBIT_DOCS_ORIGIN = String(
  import.meta.env.VITE_QUBIT_DOCS_ORIGIN || QUBIT_REPO_URL
).replace(/\/+$/, '')
