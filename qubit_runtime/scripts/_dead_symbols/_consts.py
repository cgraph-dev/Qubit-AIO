"""Constants shared by the dead-symbol detector's modules."""
from __future__ import annotations

from pathlib import Path

PKG = "qubit_runtime"
SKIP_PARTS = frozenset({
    "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".git",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".kit",
})
# Non-code subtrees inside the package (generated JS bundles, desktop apps, docs).
SKIP_REL_DIRS = frozenset({"website", "ui-tui", "apps", "assets", "locales",
                           "contributors", "optional-skills"})

# First entry is the CANONICAL home (the one scripts/audit_capability_gaps.py
# prefers); the rest are tolerated fallbacks for older checkouts.
UPSTREAM_CANDIDATES = (
    Path.home() / "upstream" / "hermes-agent",
    Path.home() / ".upstream" / "hermes-agent",
    Path.home() / ".hermes" / "hermes-agent",
    Path.home() / ".hermes.pre-retirement" / "hermes-agent",
)

# our path segment -> upstream path segment (the routing the parity audit uses).
OUR_TO_UP = {
    "qubit_cli": "hermes_cli",
    "qubit_platform": "hermes_platform",
    "qubit_state": "hermes_state",
    "qubit-achievements": "hermes-achievements",
}

STUB_DECORATORS = frozenset({"overload", "abstractmethod", "abstractclassmethod",
                             "abstractstaticmethod", "abstractproperty",
                             "runtime_checkable"})
FIXTURE_DECORATORS = frozenset({"fixture", "pytest_fixture"})
# Functions that resolve a symbol from a string at runtime. The string names a
# real call site even though the callee is not written as a Name/Attribute Load.
# `STRING_BOUND_CALLS` is the stdlib/patch family already counted as a use site;
# `LAZY_LOOKUP_CALLS` are module-attribute resolvers. Both are held in
# `dispatch_refs` (not `uses`) so they never make an unrelated name look like a
# module-level assignment.
STRING_BOUND_CALLS = frozenset({"patch", "setattr", "getattr", "delattr"})
LAZY_LOOKUP_CALLS = frozenset({
    "_lazy", "_lazy_attr", "lazy_attr", "_lazy_call", "_lazy_shim", "lazy_import",
})
# Decorator verb -> route registration (FastAPI/Starlette/Flask/Quart/aiohttp
# style). A handler wired this way is invoked by the framework, not by a Name.
ROUTE_DECORATORS = frozenset({
    "get", "post", "put", "patch", "delete", "head", "options", "trace",
    "route", "api_route", "websocket", "websocket_route",
})
# pytest's default collection rule is `python_classes = Test*`; unittest-style
# classes are collected by base class regardless of name.
PYTEST_CLASS_PREFIX = "Test"
UNITTEST_BASES = frozenset({"TestCase"})
HTTP_HANDLER_METHODS = frozenset({
    "do_GET", "do_POST", "do_PUT", "do_DELETE", "do_PATCH", "do_HEAD",
    "do_OPTIONS", "do_CONNECT", "do_TRACE", "log_message", "log_request",
    "log_error", "handle_one_request",
})
HTTP_HANDLER_BASES = frozenset({"BaseHTTPRequestHandler", "SimpleHTTPRequestHandler",
                                "ThreadingHTTPServer"})
ENTRYPOINT_NAMES = frozenset({"register", "setup", "main", "pytest_configure",
                              "pytest_plugin"})
ENTRYPOINT_PREFIXES = ("pytest_", "cli_")
