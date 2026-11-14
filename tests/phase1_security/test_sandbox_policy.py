"""P1-07 — sandbox profile, filesystem jail, argv allowlist and egress denial.

Hermetic: throwaway directories under ``tmp_path``, injected DNS resolvers, no
network calls and no sandbox backend execution.
"""
from __future__ import annotations

import os

import pytest

from voicebridge.business import sandbox as sandbox_mod
from voicebridge.business.errors import BusinessError
from voicebridge.business.sandbox import (
    ResourceLimits,
    SandboxPolicy,
    SandboxProfile,
)


def _profile(tmp_path, **policy_kwargs):
    policy = SandboxPolicy(**policy_kwargs)
    return SandboxProfile(tmp_path / "workspace", policy)


def _resolver(mapping):
    def resolve(host, port):
        return mapping.get(host, [])
    return resolve


# ---------------------------------------------------------------------------
# Filesystem jail
# ---------------------------------------------------------------------------
def test_relative_path_inside_workspace_is_allowed(tmp_path):
    profile = _profile(tmp_path)
    profile.workspace.mkdir(parents=True)
    target = profile.workspace / "notes"
    target.mkdir()
    assert profile.resolve_path("notes") == target.resolve()


def test_path_traversal_is_refused(tmp_path):
    profile = _profile(tmp_path)
    with pytest.raises(BusinessError) as caught:
        profile.resolve_path("../etc/passwd")
    assert caught.value.code == sandbox_mod.SANDBOX_PATH_ESCAPE_CODE


def test_absolute_path_outside_workspace_is_refused(tmp_path):
    profile = _profile(tmp_path)
    with pytest.raises(BusinessError) as caught:
        profile.resolve_path("/etc/passwd")
    assert caught.value.code in (
        sandbox_mod.SANDBOX_PATH_FORBIDDEN_CODE,
        sandbox_mod.SANDBOX_PATH_ESCAPE_CODE,
    )


def test_symlink_escape_is_refused(tmp_path):
    profile = _profile(tmp_path)
    profile.workspace.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = profile.workspace / "escape"
    os.symlink(outside, link)
    with pytest.raises(BusinessError) as caught:
        profile.resolve_path("escape")
    assert caught.value.code == sandbox_mod.SANDBOX_SYMLINK_ESCAPE_CODE


def test_symlink_inside_workspace_is_allowed(tmp_path):
    profile = _profile(tmp_path)
    profile.workspace.mkdir(parents=True)
    real = profile.workspace / "real.txt"
    real.write_text("ok", encoding="utf-8")
    link = profile.workspace / "link.txt"
    os.symlink(real, link)
    assert profile.resolve_path("link.txt") == real.resolve()


@pytest.mark.parametrize("raw", [
    "/proc/self/environ",
    "/sys/kernel",
    "/dev/kmsg",
    "/var/run/docker.sock",
    "/run/user/1000/bus",
])
def test_reserved_and_host_socket_paths_are_refused(tmp_path, raw):
    profile = _profile(tmp_path)
    with pytest.raises(BusinessError) as caught:
        profile.resolve_path(raw)
    assert caught.value.code in (
        sandbox_mod.SANDBOX_PATH_FORBIDDEN_CODE,
        sandbox_mod.SANDBOX_PATH_ESCAPE_CODE,
    )


def test_host_socket_name_is_refused_even_inside_workspace(tmp_path):
    profile = _profile(tmp_path)
    profile.workspace.mkdir(parents=True)
    with pytest.raises(BusinessError) as caught:
        profile.resolve_path("docker.sock")
    assert caught.value.code == sandbox_mod.SANDBOX_PATH_FORBIDDEN_CODE


# ---------------------------------------------------------------------------
# Commands / argv
# ---------------------------------------------------------------------------
def test_allowlisted_argv_passes_and_is_never_a_shell_string(tmp_path):
    profile = _profile(tmp_path, command_allowlist=("git",))
    argv = profile.build_argv(["git", "status", "--porcelain"])
    assert argv == ["git", "status", "--porcelain"]
    assert isinstance(argv, list)


def test_command_string_is_refused(tmp_path):
    profile = _profile(tmp_path, command_allowlist=("git",))
    with pytest.raises(BusinessError) as caught:
        profile.build_argv("git status; rm -rf /")
    assert caught.value.code == sandbox_mod.SANDBOX_ARGV_REQUIRED_CODE


def test_unlisted_command_is_refused(tmp_path):
    profile = _profile(tmp_path, command_allowlist=("git",))
    with pytest.raises(BusinessError) as caught:
        profile.build_argv(["curl", "https://example.com"])
    assert caught.value.code == sandbox_mod.SANDBOX_COMMAND_DENIED_CODE


def test_shell_interpreter_is_refused_even_if_allowlisted(tmp_path):
    profile = _profile(tmp_path, command_allowlist=("bash",))
    with pytest.raises(BusinessError) as caught:
        profile.build_argv(["bash", "-c", "id"])
    assert caught.value.code == sandbox_mod.SANDBOX_SHELL_FORBIDDEN_CODE


@pytest.mark.parametrize("arg", ["; rm -rf /", "$(cat /etc/passwd)", "`id`", "a\nb"])
def test_shell_injection_arguments_are_refused(tmp_path, arg):
    profile = _profile(tmp_path, command_allowlist=("git",))
    with pytest.raises(BusinessError) as caught:
        profile.build_argv(["git", arg])
    assert caught.value.code == sandbox_mod.SANDBOX_ARGUMENT_FORBIDDEN_CODE


def test_environment_allowlist_is_enforced(tmp_path):
    profile = _profile(
        tmp_path,
        command_allowlist=("git",),
        environment_allowlist=("GIT_AUTHOR_NAME",),
    )
    assert profile.build_argv(
        ["git", "commit"], env={"GIT_AUTHOR_NAME": "Qubit"}
    ) == ["git", "commit"]
    with pytest.raises(BusinessError) as caught:
        profile.build_argv(["git", "commit"], env={"AWS_SECRET_ACCESS_KEY": "x"})
    assert caught.value.code == sandbox_mod.SANDBOX_ARGUMENT_FORBIDDEN_CODE


# ---------------------------------------------------------------------------
# Egress
# ---------------------------------------------------------------------------
def test_network_is_denied_by_default(tmp_path):
    profile = _profile(tmp_path, egress_allowlist=("api.example.com",))
    with pytest.raises(BusinessError) as caught:
        profile.check_egress("https://api.example.com/v1", resolver=_resolver({}))
    assert caught.value.code == sandbox_mod.SANDBOX_NETWORK_DENIED_CODE


def test_allowlisted_public_host_passes_with_injected_resolver(tmp_path):
    calls = []

    def resolver(host, port):
        calls.append((host, port))
        return ["93.184.216.34"]

    profile = _profile(
        tmp_path,
        allow_network=True,
        egress_allowlist=("api.example.com",),
    )
    assert profile.check_egress("https://api.example.com/v1", resolver=resolver) == (
        "https://api.example.com/v1"
    )
    assert calls == [("api.example.com", 443)]


def test_host_outside_allowlist_is_refused(tmp_path):
    profile = _profile(
        tmp_path, allow_network=True, egress_allowlist=("api.example.com",)
    )
    with pytest.raises(BusinessError) as caught:
        profile.check_egress("https://evil.example.net", resolver=_resolver({}))
    assert caught.value.code == sandbox_mod.SANDBOX_EGRESS_NOT_ALLOWED_CODE


def test_empty_allowlist_denies_all_hostnames(tmp_path):
    profile = _profile(tmp_path, allow_network=True)
    with pytest.raises(BusinessError) as caught:
        profile.check_egress("https://api.example.com", resolver=_resolver({}))
    assert caught.value.code == sandbox_mod.SANDBOX_EGRESS_NOT_ALLOWED_CODE


@pytest.mark.parametrize("address", [
    "169.254.169.254",
    "10.0.0.5",
    "127.0.0.1",
    "192.168.1.20",
    "fd00::1",
    "::1",
])
def test_private_metadata_and_linklocal_resolution_is_blocked(tmp_path, address):
    profile = _profile(
        tmp_path, allow_network=True, egress_allowlist=("api.example.com",)
    )
    with pytest.raises(BusinessError) as caught:
        profile.check_egress(
            "https://api.example.com", resolver=_resolver({"api.example.com": [address]})
        )
    assert caught.value.code == sandbox_mod.SANDBOX_EGRESS_BLOCKED_CODE


def test_metadata_ip_literal_is_blocked(tmp_path):
    profile = _profile(
        tmp_path, allow_network=True, egress_allowlist=("169.254.169.254",)
    )
    with pytest.raises(BusinessError) as caught:
        profile.check_egress("http://169.254.169.254/latest/meta-data")
    assert caught.value.code == sandbox_mod.SANDBOX_EGRESS_BLOCKED_CODE


def test_metadata_host_name_is_blocked(tmp_path):
    profile = _profile(
        tmp_path, allow_network=True, egress_allowlist=("metadata.google.internal",)
    )
    with pytest.raises(BusinessError) as caught:
        profile.check_egress("http://metadata.google.internal/")
    assert caught.value.code == sandbox_mod.SANDBOX_EGRESS_BLOCKED_CODE


def test_redirect_to_private_address_is_blocked(tmp_path):
    profile = _profile(
        tmp_path,
        allow_network=True,
        egress_allowlist=("api.example.com", "169.254.169.254"),
    )
    with pytest.raises(BusinessError) as caught:
        profile.check_redirect_chain(
            ["https://api.example.com/start", "http://169.254.169.254/latest"],
            resolver=_resolver({"api.example.com": ["203.0.113.10"]}),
        )
    assert caught.value.code == sandbox_mod.SANDBOX_EGRESS_BLOCKED_CODE


def test_redirect_chain_to_allowlisted_host_passes(tmp_path):
    def resolver(host, port):
        return ["93.184.216.34"]

    profile = _profile(
        tmp_path, allow_network=True, egress_allowlist=("api.example.com",)
    )
    assert profile.check_redirect_chain(
        ["https://api.example.com/a", "https://api.example.com/b"],
        resolver=resolver,
    ) == "https://api.example.com/b"


def test_url_credentials_are_refused(tmp_path):
    profile = _profile(
        tmp_path, allow_network=True, egress_allowlist=("api.example.com",)
    )
    with pytest.raises(BusinessError) as caught:
        profile.check_egress(
            "https://user:pass@api.example.com", resolver=_resolver({})
        )
    assert caught.value.code == sandbox_mod.SANDBOX_EGRESS_BLOCKED_CODE


def test_unresolved_host_fails_closed(tmp_path):
    profile = _profile(
        tmp_path, allow_network=True, egress_allowlist=("api.example.com",)
    )
    with pytest.raises(BusinessError) as caught:
        profile.check_egress("https://api.example.com", resolver=_resolver({}))
    assert caught.value.code == sandbox_mod.SANDBOX_EGRESS_UNRESOLVED_CODE


# ---------------------------------------------------------------------------
# Limits and artifacts
# ---------------------------------------------------------------------------
def test_limits_within_caps_are_returned(tmp_path):
    profile = _profile(tmp_path, limits=ResourceLimits(cpu_seconds=10))
    wanted = ResourceLimits(cpu_seconds=5, memory_bytes=64 * 1024 * 1024)
    assert profile.enforce_limits(wanted) == wanted


def test_limits_above_caps_are_refused(tmp_path):
    profile = _profile(tmp_path, limits=ResourceLimits(cpu_seconds=10))
    with pytest.raises(BusinessError) as caught:
        profile.enforce_limits({"cpu_seconds": 11})
    assert caught.value.code == sandbox_mod.SANDBOX_LIMIT_EXCEEDED_CODE


def test_invalid_limits_are_refused(tmp_path):
    profile = _profile(tmp_path)
    with pytest.raises(BusinessError) as caught:
        profile.enforce_limits({"memory_bytes": 0})
    assert caught.value.code == sandbox_mod.BAD_INPUT_CODE


def test_artifact_scan_rejection_fails_closed(tmp_path):
    profile = _profile(tmp_path, artifact_scan_hook=lambda path: {"clean": False})
    profile.workspace.mkdir(parents=True)
    artifact = profile.workspace / "out.bin"
    artifact.write_bytes(b"malware")
    with pytest.raises(BusinessError) as caught:
        profile.scan_artifact("out.bin")
    assert caught.value.code == sandbox_mod.SANDBOX_ARTIFACT_REJECTED_CODE


def test_artifact_scan_acceptance_and_scanner_error_fail_closed(tmp_path):
    profile = _profile(tmp_path, artifact_scan_hook=lambda path: True)
    profile.workspace.mkdir(parents=True)
    (profile.workspace / "out.txt").write_text("ok", encoding="utf-8")
    assert profile.scan_artifact("out.txt")["clean"] is True

    def broken(path):
        raise RuntimeError("scanner crash")

    with pytest.raises(BusinessError) as caught:
        profile.scan_artifact("out.txt", hook=broken)
    assert caught.value.code == sandbox_mod.SANDBOX_ARTIFACT_REJECTED_CODE


def test_describe_exposes_policy_without_secrets(tmp_path):
    profile = _profile(
        tmp_path,
        allow_network=True,
        egress_allowlist=("api.example.com",),
        command_allowlist=("git",),
    )
    described = profile.describe()
    assert described["network"] is True
    assert described["egress_allowlist"] == ["api.example.com"]
    assert described["limits"]["cpu_seconds"] > 0
    assert "secret" not in str(described).lower()


def test_privileged_or_writable_base_policy_is_refused(tmp_path):
    privileged = SandboxProfile(
        tmp_path / "ws1", SandboxPolicy(unprivileged=False)
    )
    with pytest.raises(BusinessError) as caught:
        privileged.validate()
    assert caught.value.code == sandbox_mod.SANDBOX_POLICY_UNSAFE_CODE

    writable = SandboxProfile(
        tmp_path / "ws2", SandboxPolicy(read_only_base=False)
    )
    with pytest.raises(BusinessError) as caught:
        writable.build_argv(["git", "status"])
    assert caught.value.code == sandbox_mod.SANDBOX_POLICY_UNSAFE_CODE

    valid = SandboxProfile(tmp_path / "ws3", SandboxPolicy())
    assert valid.validate() is valid
