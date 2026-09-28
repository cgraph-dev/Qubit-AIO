"""The single source of truth for Qubit's public installer location.

Owner decision 2026-09-27: the canonical Qubit domain is not purchased yet, so
the public GitHub repository is used until the owner supplies a domain or a
final release URL. Change ``DEFAULT_BASE_URL`` (or set ``QUBIT_INSTALL_BASE_URL``)
in ONE place and every installer/reinstall message follows.

Never point this at a Hermes/OpenClaw URL: it is Qubit-owned product output.

For a GitHub repository the raw installer URLs are
``<base>/raw/<branch>/scripts/install.{sh,ps1}``. A future canonical domain can
either serve the same ``/raw/<branch>/scripts/install.*`` paths or override the
``install_*_url`` helpers via the environment.
"""
from __future__ import annotations

import os

#: Public Qubit GitHub repository (owner decision 2026-09-27). Override with
#: ``QUBIT_INSTALL_BASE_URL`` for the future canonical domain or a pinned release
#: asset; override the ref with ``QUBIT_INSTALL_BRANCH``.
DEFAULT_BASE_URL = "https://github.com/cgraph-dev/Qubit-AIO"
DEFAULT_BRANCH = "main"

#: GitHub ``owner/name`` for the Qubit project (one configurable place). Used by
#: update/channel identity checks; override with ``QUBIT_REPOSITORY``.
DEFAULT_REPOSITORY = "cgraph-dev/Qubit-AIO"

#: Documentation root printed in help/setup/provider text. The public GitHub
#: repository's ``docs`` tree until a canonical docs domain exists; override
#: with ``QUBIT_DOCS_BASE_URL``.
DEFAULT_DOCS_BASE_URL = "https://github.com/cgraph-dev/Qubit-AIO/tree/main/docs"

#: Release-asset host used for internal update-channel metadata (never printed).
#: Override with ``QUBIT_RELEASE_ASSETS_BASE_URL`` once a Qubit asset host or
#: final release URL is supplied by the owner.
DEFAULT_RELEASE_ASSETS_BASE_URL = "https://github.com/cgraph-dev/Qubit-AIO"

_BASE_ENV = "QUBIT_INSTALL_BASE_URL"
_BRANCH_ENV = "QUBIT_INSTALL_BRANCH"
_REPOSITORY_ENV = "QUBIT_REPOSITORY"
_DOCS_ENV = "QUBIT_DOCS_BASE_URL"
_RELEASE_ASSETS_ENV = "QUBIT_RELEASE_ASSETS_BASE_URL"
_RAW_ASSET_ENV = "QUBIT_RAW_ASSET_BASE_URL"


def base_url() -> str:
    """Repository/asset root for the Qubit installer (one configurable place)."""
    return (os.environ.get(_BASE_ENV) or DEFAULT_BASE_URL).rstrip("/")


def branch() -> str:
    """Git ref the installer is served from (default ``main``)."""
    return (os.environ.get(_BRANCH_ENV) or DEFAULT_BRANCH).strip("/") or DEFAULT_BRANCH


def install_sh_url(base: str | None = None, ref: str | None = None) -> str:
    return f"{(base or base_url()).rstrip('/')}/raw/{(ref or branch()).strip('/')}/scripts/install.sh"


def install_ps1_url(base: str | None = None, ref: str | None = None) -> str:
    return f"{(base or base_url()).rstrip('/')}/raw/{(ref or branch()).strip('/')}/scripts/install.ps1"


def releases_base_url(base: str | None = None) -> str:
    """Qubit-owned release-asset root (e.g. a GitHub ``/releases`` page)."""
    return f"{(base or base_url()).rstrip('/')}/releases"


def official_repository() -> str:
    """GitHub ``owner/name`` for the Qubit project (one configurable place)."""
    return (os.environ.get(_REPOSITORY_ENV) or DEFAULT_REPOSITORY).strip("/")


def official_repo_urls() -> set[str]:
    """The canonical ``origin`` spellings that identify the Qubit repository."""
    repo = official_repository()
    return {
        f"https://github.com/{repo}.git",
        f"git@github.com:{repo}.git",
        f"https://github.com/{repo}",
        f"git@github.com:{repo}",
    }


def official_repo_url() -> str:
    """The canonical HTTPS ``origin`` URL (``.git`` suffix)."""
    return f"https://github.com/{official_repository()}.git"


def docs_base_url() -> str:
    """Qubit documentation root printed on public surfaces.

    Defaults to the public Qubit repository's ``docs`` tree; override with
    ``QUBIT_DOCS_BASE_URL`` when a canonical docs domain exists. Callers append
    the page path (``"user-guide/…"``); this helper never yields a Hermes URL.
    """
    return (os.environ.get(_DOCS_ENV) or DEFAULT_DOCS_BASE_URL).rstrip("/")


def docs_url(path: str = "") -> str:
    """Join ``path`` onto :func:`docs_base_url` (leading ``/`` tolerated)."""
    base = docs_base_url()
    if not path:
        return base
    return f"{base}/{path.lstrip('/')}"


def release_assets_base_url(base: str | None = None) -> str:
    """Qubit-owned release-asset root for internal update metadata.

    This is **not** printed to users; it is the base the channel reader fetches
    release pointers from. Override with ``QUBIT_RELEASE_ASSETS_BASE_URL`` (or the
    ``base`` argument) once the owner supplies a Qubit asset host / final release
    URL. Preserves the upstream channel protocol; the default follows the owner's
    "use the public repo until a domain exists" decision.
    """
    return (
        base or os.environ.get(_RELEASE_ASSETS_ENV) or DEFAULT_RELEASE_ASSETS_BASE_URL
    ).rstrip("/")


def raw_base_url(ref: str | None = None) -> str:
    """Raw-content root for Qubit-hosted assets (in-repo JSON manifests).

    Defaults to the raw GitHub content endpoints of the public Qubit repo; the
    default ref follows :func:`branch`. Override with
    ``QUBIT_RAW_ASSET_BASE_URL`` when a canonical asset host exists.
    """
    base = os.environ.get(_RAW_ASSET_ENV)
    if base:
        return base.rstrip("/")
    return f"https://raw.githubusercontent.com/{official_repository()}/{(ref or branch()).strip('/')}"


def raw_asset_url(path: str, ref: str | None = None) -> str:
    """Join ``path`` (e.g. ``website/static/api/model-catalog.json``) onto raw."""
    return f"{raw_base_url(ref)}/{path.lstrip('/')}"




def reinstall_one_liner(windows: bool) -> str:
    """The one-line reinstall command for the configured installer source.

    Mirrors the upstream shape (PowerShell ``irm`` / POSIX ``curl | bash``) but
    always resolves to a Qubit-owned URL.
    """
    return f"iex (irm {install_ps1_url()})" if windows else f"curl -fsSL {install_sh_url()} | bash"
