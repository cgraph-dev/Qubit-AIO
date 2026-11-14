"""Sandbox profile and egress policy for the Business OS.

This module is a policy seam, not a container runtime. It validates the shape
of a sandbox before a caller hands it to a real isolation backend:

* unprivileged, read-only base with an isolated writable workspace;
* no host socket paths, ``/proc``/``/sys``/``/dev`` or metadata services;
* filesystem allowlist with canonicalisation that refuses ``..`` and symlink
  escapes;
* deny network by default, then an explicit egress proxy allowlist that also
  blocks private, link-local, loopback and metadata addresses on every hop;
* command argv allowlists with no shell interpolation;
* CPU/memory/disk/time/process limits; and
* an injectable artifact scan hook.

Every refusal is a typed :class:`~voicebridge.business.errors.BusinessError`.
``build_argv`` always returns a list, never a shell string, and never executes
anything.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlsplit

from .errors import BAD_INPUT_CODE, BusinessError

SANDBOX_NETWORK_DENIED_CODE = "BUSINESS_SANDBOX_NETWORK_DENIED"
SANDBOX_EGRESS_NOT_ALLOWED_CODE = "BUSINESS_SANDBOX_EGRESS_NOT_ALLOWED"
SANDBOX_EGRESS_BLOCKED_CODE = "BUSINESS_SANDBOX_EGRESS_BLOCKED"
SANDBOX_EGRESS_UNRESOLVED_CODE = "BUSINESS_SANDBOX_EGRESS_UNRESOLVED"
SANDBOX_PATH_ESCAPE_CODE = "BUSINESS_SANDBOX_PATH_ESCAPE"
SANDBOX_PATH_FORBIDDEN_CODE = "BUSINESS_SANDBOX_PATH_FORBIDDEN"
SANDBOX_SYMLINK_ESCAPE_CODE = "BUSINESS_SANDBOX_SYMLINK_ESCAPE"
SANDBOX_ARGV_REQUIRED_CODE = "BUSINESS_SANDBOX_ARGV_REQUIRED"
SANDBOX_COMMAND_DENIED_CODE = "BUSINESS_SANDBOX_COMMAND_DENIED"
SANDBOX_SHELL_FORBIDDEN_CODE = "BUSINESS_SANDBOX_SHELL_FORBIDDEN"
SANDBOX_ARGUMENT_FORBIDDEN_CODE = "BUSINESS_SANDBOX_ARGUMENT_FORBIDDEN"
SANDBOX_LIMIT_EXCEEDED_CODE = "BUSINESS_SANDBOX_LIMIT_EXCEEDED"
SANDBOX_ARTIFACT_REJECTED_CODE = "BUSINESS_SANDBOX_ARTIFACT_REJECTED"
SANDBOX_POLICY_UNSAFE_CODE = "BUSINESS_SANDBOX_POLICY_UNSAFE"

# Absolute locations that must never be reachable from a sandbox workspace.
FORBIDDEN_ROOTS = (
    "/proc",
    "/sys",
    "/dev",
    "/run",
    "/var/run",
    "/boot",
    "/etc",
    "/root",
    "/home",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
)
HOST_SOCKET_NAMES = frozenset(
    {
        "docker.sock",
        "containerd.sock",
        "podman.sock",
        "systemd-private",
        "bus",
        "dbus",
        "X11",
    }
)
METADATA_HOSTS = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "169.254.169.254",
        "fd00:ec2::254",
        "100.100.100.200",
    }
)
SHELL_EXECUTABLES = frozenset(
    {"sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "cmd", "cmd.exe",
     "powershell", "powershell.exe", "pwsh", "env", "xargs", "eval"}
)
SHELL_OPERATORS = frozenset(
    {";", ";;", "|", "||", "&", "&&", ">", ">>", "<", "<<", "<<<", "2>", "2>>",
     "&>", ">&", "`"}
)
_SHELL_SUBSTRINGS = ("`", "$(", "${", ";", "|", "\n", "\r", "\x00")


def _refuse(code: str, detail: str) -> "BusinessError":
    return BusinessError(code, detail)


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Per-run resource ceilings enforced by the isolation backend."""

    cpu_seconds: float = 30.0
    memory_bytes: int = 512 * 1024 * 1024
    disk_bytes: int = 256 * 1024 * 1024
    wall_seconds: float = 60.0
    max_processes: int = 32

    def as_dict(self) -> dict[str, float | int]:
        return {
            "cpu_seconds": self.cpu_seconds,
            "memory_bytes": self.memory_bytes,
            "disk_bytes": self.disk_bytes,
            "wall_seconds": self.wall_seconds,
            "max_processes": self.max_processes,
        }


def _default_limits() -> ResourceLimits:
    return ResourceLimits()


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Declarative sandbox policy. Deny wins over every allowlist."""

    unprivileged: bool = True
    read_only_base: bool = True
    allow_network: bool = False
    egress_allowlist: tuple[str, ...] = ()
    filesystem_allowlist: tuple[Path, ...] = ()
    command_allowlist: tuple[str, ...] = ()
    environment_allowlist: tuple[str, ...] = ()
    limits: ResourceLimits = field(default_factory=_default_limits)
    artifact_scan_hook: Callable[[Path], Any] | None = None
    allow_absolute_paths: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "egress_allowlist",
            tuple(h.strip().lower().rstrip(".") for h in self.egress_allowlist if h),
        )
        object.__setattr__(
            self,
            "command_allowlist",
            tuple(c.strip() for c in self.command_allowlist if c and c.strip()),
        )


@dataclass(frozen=True, slots=True)
class SandboxProfile:
    """A workspace root plus the policy that constrains it."""

    root: Path
    policy: SandboxPolicy = field(default_factory=SandboxPolicy)

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    @property
    def workspace(self) -> Path:
        return self.root

    def validate(self) -> "SandboxProfile":
        """Refuse a policy posture that is not an isolated sandbox."""
        self._validate_posture()
        return self

    def _validate_posture(self) -> None:
        if not self.policy.unprivileged:
            raise _refuse(
                SANDBOX_POLICY_UNSAFE_CODE,
                "a sandbox must run unprivileged",
            )
        if not self.policy.read_only_base:
            raise _refuse(
                SANDBOX_POLICY_UNSAFE_CODE,
                "a sandbox must present a read-only base",
            )

    def describe(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "unprivileged": self.policy.unprivileged,
            "read_only_base": self.policy.read_only_base,
            "network": bool(self.policy.allow_network),
            "egress_allowlist": list(self.policy.egress_allowlist),
            "filesystem_allowlist": [str(p) for p in self._allowed_roots()],
            "command_allowlist": list(self.policy.command_allowlist),
            "limits": self.policy.limits.as_dict(),
        }

    # -- filesystem ----------------------------------------------------
    def _allowed_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = [self.root.resolve()]
        for entry in self.policy.filesystem_allowlist:
            roots.append(Path(entry).expanduser().resolve())
        return tuple(roots)

    def resolve_path(self, raw: str | Path) -> Path:
        """Canonicalise ``raw`` inside an allowlisted root or refuse.

        Refuses traversal (``..``), absolute paths outside the allowlist,
        symlink escapes, host socket paths and the reserved ``/proc``,
        ``/sys``, ``/dev``, ``/run`` families.
        """
        self._validate_posture()
        if isinstance(raw, Path):
            text = str(raw)
        elif isinstance(raw, str):
            text = raw
        else:
            raise _refuse(BAD_INPUT_CODE, "path must be a string or Path")
        if not text.strip() or "\x00" in text:
            raise _refuse(BAD_INPUT_CODE, "path must be a non-empty string")
        raw_path = Path(text)
        if ".." in raw_path.parts:
            raise _refuse(SANDBOX_PATH_ESCAPE_CODE, "path traversal is not allowed")
        if "~" in raw_path.parts:
            raise _refuse(SANDBOX_PATH_ESCAPE_CODE, "home expansion is not allowed")
        roots = self._allowed_roots()
        if raw_path.is_absolute():
            if not self.policy.allow_absolute_paths:
                raise _refuse(
                    SANDBOX_PATH_FORBIDDEN_CODE,
                    "absolute paths are not allowed; use a workspace-relative path",
                )
            candidate = raw_path
        else:
            candidate = self.root / raw_path
        if raw_path.is_absolute():
            self._reject_reserved(raw_path)
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError):
            raise _refuse(SANDBOX_PATH_ESCAPE_CODE, "path cannot be resolved")
        self._reject_socket(resolved)
        if not any(_is_within(resolved, root) for root in roots):
            if candidate.is_symlink() or self._contains_symlink(raw_path):
                raise _refuse(
                    SANDBOX_SYMLINK_ESCAPE_CODE,
                    "symlink resolves outside the sandbox allowlist",
                )
            raise _refuse(
                SANDBOX_PATH_ESCAPE_CODE,
                "path resolves outside the sandbox allowlist",
            )
        return resolved

    def _contains_symlink(self, raw: Path) -> bool:
        candidate = raw if raw.is_absolute() else self.root / raw
        current = candidate
        while True:
            try:
                if current.is_symlink():
                    return True
            except OSError:
                return False
            if current == self.root or current.parent == current:
                return False
            current = current.parent

    @staticmethod
    def _reject_socket(path: Path) -> None:
        name = path.name.lower()
        if name in HOST_SOCKET_NAMES or name.endswith(".sock"):
            raise _refuse(
                SANDBOX_PATH_FORBIDDEN_CODE,
                "host sockets are not reachable from the sandbox",
            )

    @staticmethod
    def _reject_reserved(path: Path) -> None:
        try:
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError):
            resolved = path
        text = str(resolved)
        for root in FORBIDDEN_ROOTS:
            if text == root or text.startswith(root + os.sep):
                raise _refuse(
                    SANDBOX_PATH_FORBIDDEN_CODE,
                    f"{root} is not available inside a sandbox",
                )

    def scan_artifact(self, raw: str | Path, *, hook: Callable[[Path], Any] | None = None) -> dict:
        """Run the configured artifact scan hook, failing closed on rejection."""
        path = self.resolve_path(raw)
        scanner = hook or self.policy.artifact_scan_hook
        if scanner is None:
            return {"path": str(path), "scanned": False, "clean": True}
        try:
            verdict = scanner(path)
        except Exception as exc:  # a scanner failure is a refusal, not an allow
            raise _refuse(
                SANDBOX_ARTIFACT_REJECTED_CODE,
                f"artifact scan failed: {type(exc).__name__}",
            ) from exc
        clean = _clean_verdict(verdict)
        if not clean:
            raise _refuse(
                SANDBOX_ARTIFACT_REJECTED_CODE,
                "artifact was rejected by the scan hook",
            )
        return {"path": str(path), "scanned": True, "clean": True}

    # -- commands ------------------------------------------------------
    def build_argv(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Validate and return an argv list. Never returns a shell string."""
        self._validate_posture()
        return build_argv(self.policy, argv, cwd=cwd, env=env,
                          workspace=self.root)

    # -- network -------------------------------------------------------
    def check_egress(self, url: str, *, resolver: Callable | None = None) -> str:
        self._validate_posture()
        return check_egress(url, self.policy, resolver=resolver)

    def check_redirect(self, from_url: str, to_url: str,
                       *, resolver: Callable | None = None) -> str:
        return check_redirect(from_url, to_url, self.policy, resolver=resolver)

    def check_redirect_chain(self, urls: Sequence[str],
                             *, resolver: Callable | None = None) -> str:
        if not urls:
            raise _refuse(BAD_INPUT_CODE, "a redirect chain needs at least one URL")
        last = urls[0]
        check_egress(last, self.policy, resolver=resolver)
        for nxt in urls[1:]:
            last = check_redirect(last, nxt, self.policy, resolver=resolver)
        return last

    # -- limits --------------------------------------------------------
    def enforce_limits(
        self,
        requested: ResourceLimits | Mapping[str, Any] | None = None,
    ) -> ResourceLimits:
        """Return the effective limits or refuse when a request exceeds a cap."""
        caps = self.policy.limits
        if requested is None:
            return caps
        if isinstance(requested, Mapping):
            try:
                wanted = ResourceLimits(**dict(requested))
            except TypeError as exc:
                raise _refuse(BAD_INPUT_CODE, f"unknown limit: {exc}") from exc
        elif isinstance(requested, ResourceLimits):
            wanted = requested
        else:
            raise _refuse(BAD_INPUT_CODE, "limits must be ResourceLimits or a mapping")
        for name in ("cpu_seconds", "memory_bytes", "disk_bytes", "wall_seconds",
                     "max_processes"):
            value = getattr(wanted, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise _refuse(BAD_INPUT_CODE, f"{name} must be a positive number")
            if value > getattr(caps, name):
                raise _refuse(
                    SANDBOX_LIMIT_EXCEEDED_CODE,
                    f"requested {name}={value} exceeds the sandbox cap "
                    f"{getattr(caps, name)}",
                )
        return wanted

    def with_limits(self, limits: ResourceLimits) -> "SandboxProfile":
        return replace(self, policy=replace(self.policy, limits=limits))


# ---------------------------------------------------------------------------
# Module-level validators (usable without constructing a profile)
# ---------------------------------------------------------------------------
def resolve_path(
    raw: str | Path,
    root: str | Path,
    *,
    policy: SandboxPolicy | None = None,
) -> Path:
    return SandboxProfile(Path(root).resolve(), policy or SandboxPolicy()).resolve_path(raw)


def build_argv(
    policy: SandboxPolicy,
    argv: Sequence[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, Any] | None = None,
    workspace: str | Path | None = None,
) -> list[str]:
    """Validate an argv against ``policy`` and return a fresh list.

    A string command is refused: the caller must pass an argv sequence so no
    component can be re-split by a shell. Shell executables and shell
    metacharacters are refused even when the executable is allowlisted.
    """
    _validate_policy_posture(policy)
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence):
        raise _refuse(
            SANDBOX_ARGV_REQUIRED_CODE,
            "commands must be an argv sequence, never a shell string",
        )
    items = list(argv)
    if not items:
        raise _refuse(SANDBOX_ARGV_REQUIRED_CODE, "argv must not be empty")
    for item in items:
        if not isinstance(item, str) or not item:
            raise _refuse(SANDBOX_ARGV_REQUIRED_CODE, "every argv item must be text")
        if "\x00" in item:
            raise _refuse(SANDBOX_ARGUMENT_FORBIDDEN_CODE, "argv must not contain NUL")
    executable = items[0]
    basename = Path(executable).name.lower()
    if basename in SHELL_EXECUTABLES:
        raise _refuse(
            SANDBOX_SHELL_FORBIDDEN_CODE,
            f"shell/user-command interpreter {basename!r} is refused",
        )
    if not _command_allowed(policy.command_allowlist, executable, basename):
        raise _refuse(
            SANDBOX_COMMAND_DENIED_CODE,
            f"command {basename!r} is not in the sandbox allowlist",
        )
    for index, item in enumerate(items):
        if index == 0:
            continue
        lowered = item.strip().lower()
        if lowered in SHELL_OPERATORS or lowered in SHELL_EXECUTABLES:
            raise _refuse(
                SANDBOX_ARGUMENT_FORBIDDEN_CODE,
                f"argument {item!r} is a shell operator or interpreter",
            )
        if any(token in item for token in _SHELL_SUBSTRINGS):
            raise _refuse(
                SANDBOX_ARGUMENT_FORBIDDEN_CODE,
                "argument contains shell interpolation characters",
            )
    if cwd is not None:
        if workspace is not None:
            SandboxProfile(Path(workspace), policy).resolve_path(cwd)
        elif Path(cwd).is_absolute():
            raise _refuse(
                SANDBOX_ARGV_REQUIRED_CODE,
                "an absolute cwd requires an explicit workspace root",
            )
    if env is not None:
        validate_env(policy, env)
    return items


def validate_env(policy: SandboxPolicy, env: Mapping[str, Any]) -> dict[str, str]:
    """Filter an environment down to the allowlist; refuse unsafe values."""
    if not env:
        return {}
    if not policy.environment_allowlist:
        raise _refuse(
            SANDBOX_ARGUMENT_FORBIDDEN_CODE,
            "environment injection is denied unless an allowlist is configured",
        )
    allowed = set(policy.environment_allowlist)
    clean: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or key not in allowed:
            raise _refuse(
                SANDBOX_ARGUMENT_FORBIDDEN_CODE,
                f"environment key {key!r} is not allowlisted",
            )
        text = str(value)
        if "\x00" in text or "\n" in text:
            raise _refuse(
                SANDBOX_ARGUMENT_FORBIDDEN_CODE,
                f"environment value for {key!r} contains control characters",
            )
        clean[key] = text
    return clean


def _validate_policy_posture(policy: SandboxPolicy) -> None:
    if not policy.unprivileged:
        raise _refuse(SANDBOX_POLICY_UNSAFE_CODE, "a sandbox must run unprivileged")
    if not policy.read_only_base:
        raise _refuse(
            SANDBOX_POLICY_UNSAFE_CODE,
            "a sandbox must present a read-only base",
        )


def _command_allowed(allowlist: Sequence[str], executable: str, basename: str) -> bool:
    for entry in allowlist:
        if entry == executable or Path(entry).name == basename:
            return True
    return False


def check_egress(
    url: str,
    policy: SandboxPolicy | None = None,
    *,
    resolver: Callable | None = None,
) -> str:
    """Validate one egress hop and return the normalised URL or refuse."""
    policy = policy or SandboxPolicy()
    _validate_policy_posture(policy)
    if not policy.allow_network:
        raise _refuse(SANDBOX_NETWORK_DENIED_CODE, "network egress is denied by policy")
    if not isinstance(url, str) or not url.strip():
        raise _refuse(BAD_INPUT_CODE, "URL must be a non-empty string")
    if any(ch in url for ch in ("\x00", "\n", "\r")):
        raise _refuse(SANDBOX_EGRESS_BLOCKED_CODE, "URL contains control characters")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise _refuse(SANDBOX_EGRESS_BLOCKED_CODE, "URL cannot be parsed") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise _refuse(SANDBOX_EGRESS_BLOCKED_CODE, "only http/https egress is allowed")
    if parts.username or parts.password:
        raise _refuse(SANDBOX_EGRESS_BLOCKED_CODE, "URL credentials are forbidden")
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise _refuse(SANDBOX_EGRESS_BLOCKED_CODE, "URL has no host")
    if host in METADATA_HOSTS or host.endswith(".metadata.google.internal"):
        raise _refuse(SANDBOX_EGRESS_BLOCKED_CODE, "metadata host is blocked")
    if host in ("localhost",) or host.endswith(".localhost"):
        raise _refuse(SANDBOX_EGRESS_BLOCKED_CODE, "loopback host is blocked")
    if not _host_allowed(policy.egress_allowlist, host):
        raise _refuse(
            SANDBOX_EGRESS_NOT_ALLOWED_CODE,
            f"host {host!r} is not in the egress allowlist",
        )
    if _is_ip_literal(host):
        if _ip_blocked(host):
            raise _refuse(SANDBOX_EGRESS_BLOCKED_CODE, "private or metadata IP is blocked")
    else:
        addresses = _resolve(host, parts.port or _default_port(scheme), resolver)
        if not addresses:
            raise _refuse(
                SANDBOX_EGRESS_UNRESOLVED_CODE,
                f"host {host!r} did not resolve to an address",
            )
        for address in addresses:
            if _ip_blocked(address):
                raise _refuse(
                    SANDBOX_EGRESS_BLOCKED_CODE,
                    f"host {host!r} resolves to a private or metadata address",
                )
    return url.strip()


def check_redirect(
    from_url: str,
    to_url: str,
    policy: SandboxPolicy | None = None,
    *,
    resolver: Callable | None = None,
) -> str:
    """Re-check every redirect target; a redirect never widens egress."""
    check_egress(from_url, policy, resolver=resolver)
    return check_egress(to_url, policy, resolver=resolver)


def _default_port(scheme: str) -> int:
    return 443 if scheme == "https" else 80


def _host_allowed(allowlist: Sequence[str], host: str) -> bool:
    for entry in allowlist:
        if not entry:
            continue
        if host == entry:
            return True
        if entry.startswith(".") and host.endswith(entry):
            return True
        if host.endswith("." + entry):
            return True
    return False


def _resolve(host: str, port: int, resolver: Callable | None) -> list[str]:
    try:
        if resolver is not None:
            result = resolver(host, port)
        else:  # pragma: no cover - exercised with an injected resolver in tests
            result = [
                info[4][0]
                for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            ]
    except (OSError, ValueError):
        raise _refuse(SANDBOX_EGRESS_UNRESOLVED_CODE, f"host {host!r} could not be resolved")
    addresses: list[str] = []
    for item in result or ():
        text = str(item)
        if text not in addresses:
            addresses.append(text)
    return addresses


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _ip_blocked(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        address = mapped
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _clean_verdict(verdict: Any) -> bool:
    if verdict is None:
        return True
    if isinstance(verdict, bool):
        return verdict
    if isinstance(verdict, Mapping):
        for key in ("clean", "ok", "safe", "passed"):
            if key in verdict:
                return bool(verdict[key])
        return False
    return bool(verdict)
