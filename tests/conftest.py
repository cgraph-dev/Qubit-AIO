"""Test-session isolation for the 5C-compiled chat path (Packet 5C).

Every _user_content() call in tests uses a throwaway memory DB so the live
integration compiler never touches the user's real private state. Env
overrides also let tests force compiler mode (4b rollback path) and reset
the module-level compiler cache between cases.
"""
import os
import tempfile

os.environ["VOICE_BRIDGE_MEMORY_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="vb-mem-"), "mem.db")


def pytest_runtest_setup(item):  # noqa: D103
    # Fresh compiler cache per test so mode/env changes apply immediately.
    import voicebridge.local_llm as llm
    llm._reset_compiler_cache()