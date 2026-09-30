"""Packet 5B: scoped SQLite/FTS5 personal memory.
Packet 8M: optional GENOME-compatible semantic memory adapter."""
from .store import (MemoryStore, MemoryStoreError, SecretRejectedError,
                    TIERS, normalize_text, norm_hash, redact_secrets,
                    scoped_key)
from .semantic import (SemanticMemory, SemanticMemoryError,
                       SemanticProviderUnavailable, BeliefFormatError,
                       SemanticStateError,
                       HashProjection, OllamaEmbed, RecallResult,
                       BeliefConflict, PROVIDERS, SOURCES,
                       SEMANTIC_STATE_SCHEMA, load_semantic_state,
                       write_semantic_state)
from .journal import (Journal, JournalCorruptionError, snapshot_hash,
                      verify_chain, replay, verify_journal, apply_op)
from .workspace import (WorkspaceError, workspace_dir, write_note, read_note,
                        list_notes, forget_note, dream, providers,
                        estop_engaged)

__all__ = ["MemoryStore", "MemoryStoreError", "SecretRejectedError",
           "TIERS", "normalize_text", "norm_hash", "redact_secrets",
           "scoped_key",
           "SemanticMemory", "SemanticMemoryError",
           "SemanticProviderUnavailable", "BeliefFormatError",
           "SemanticStateError",
           "HashProjection", "OllamaEmbed", "RecallResult", "BeliefConflict",
           "PROVIDERS", "SOURCES", "SEMANTIC_STATE_SCHEMA",
           "load_semantic_state", "write_semantic_state",
           "Journal", "JournalCorruptionError", "snapshot_hash",
           "verify_chain", "replay", "verify_journal", "apply_op",
           "WorkspaceError", "workspace_dir", "write_note", "read_note",
           "list_notes", "forget_note", "dream", "providers",
           "estop_engaged"]
