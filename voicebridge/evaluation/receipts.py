"""OC-52 — immutable execution-identity receipts.

Every brokered execution is bound to a content-addressed *execution identity*
(run, actor, workspace, tool, start) and an append-only receipt chain. Each
receipt commits to the previous receipt's hash, so any edit, reorder, or
deletion breaks verification. Receipts carry digests and labels only — never
inputs, outputs, or secret values.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional

SCHEMA = "qubit-execution-identity/1"
GENESIS = "0" * 64


class ReceiptError(Exception):
    """Typed receipt failure."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class ExecutionIdentity:
    """Content-addressed identity for one execution run."""

    __slots__ = ("run_id", "actor", "workspace", "tool", "session",
                 "started_at", "identity_id")

    def __init__(self, *, run_id: str, actor: str, workspace: str = "",
                 tool: str = "", session: str = "",
                 started_at: Optional[float] = None,
                 identity_id: str = "") -> None:
        self.run_id = str(run_id or "")
        self.actor = str(actor or "user")
        self.workspace = str(workspace or "")
        self.tool = str(tool or "")
        self.session = str(session or "")
        self.started_at = float(started_at if started_at is not None
                                else time.time())
        self.identity_id = identity_id or _digest(self._body())

    def _body(self) -> Dict[str, Any]:
        return {"run_id": self.run_id, "actor": self.actor,
                "workspace": self.workspace, "tool": self.tool,
                "session": self.session, "started_at": self.started_at}

    def as_dict(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "identity_id": self.identity_id,
                **self._body()}

    @classmethod
    def create(cls, **kwargs: Any) -> "ExecutionIdentity":
        return cls(**kwargs)


class ExecutionReceipt:
    """One immutable, hash-chained execution receipt."""

    __slots__ = ("identity_id", "run_id", "seq", "status", "inputs_digest",
                 "outputs_digest", "approval_id", "prev_hash", "ts",
                 "content_hash")

    def __init__(self, *, identity_id: str, run_id: str, seq: int,
                 status: str, inputs_digest: str = "",
                 outputs_digest: str = "", approval_id: str = "",
                 prev_hash: str = GENESIS, ts: Optional[float] = None,
                 content_hash: str = "") -> None:
        self.identity_id = str(identity_id)
        self.run_id = str(run_id)
        self.seq = int(seq)
        self.status = str(status or "UNKNOWN").upper()
        self.inputs_digest = str(inputs_digest or "")
        self.outputs_digest = str(outputs_digest or "")
        self.approval_id = str(approval_id or "")
        self.prev_hash = str(prev_hash or GENESIS)
        self.ts = float(ts if ts is not None else time.time())
        self.content_hash = content_hash or _digest(self._body())

    def _body(self) -> Dict[str, Any]:
        return {"identity_id": self.identity_id, "run_id": self.run_id,
                "seq": self.seq, "status": self.status,
                "inputs_digest": self.inputs_digest,
                "outputs_digest": self.outputs_digest,
                "approval_id": self.approval_id, "prev_hash": self.prev_hash,
                "ts": self.ts}

    def as_dict(self) -> Dict[str, Any]:
        return {"schema": SCHEMA, "content_hash": self.content_hash,
                **self._body()}

    def verify(self) -> bool:
        return self.content_hash == _digest(self._body())


class ReceiptChain:
    """In-memory append-only chain bound to one execution identity."""

    def __init__(self, identity: ExecutionIdentity) -> None:
        if not isinstance(identity, ExecutionIdentity):
            raise ReceiptError("RECEIPT_BAD_IDENTITY",
                               "a chain requires an ExecutionIdentity")
        self.identity = identity
        self._receipts: List[ExecutionReceipt] = []

    @property
    def head(self) -> str:
        return self._receipts[-1].content_hash if self._receipts else GENESIS

    @property
    def length(self) -> int:
        return len(self._receipts)

    def append(self, *, status: str, inputs_digest: str = "",
               outputs_digest: str = "", approval_id: str = "",
               ts: Optional[float] = None) -> ExecutionReceipt:
        receipt = ExecutionReceipt(
            identity_id=self.identity.identity_id,
            run_id=self.identity.run_id, seq=len(self._receipts),
            status=status, inputs_digest=inputs_digest,
            outputs_digest=outputs_digest, approval_id=approval_id,
            prev_hash=self.head, ts=ts)
        self._receipts.append(receipt)
        return receipt

    def receipts(self) -> List[Dict[str, Any]]:
        return [r.as_dict() for r in self._receipts]

    def verify(self) -> Dict[str, Any]:
        """Verify hashes and linkage; report the first broken index."""
        prev = GENESIS
        for i, r in enumerate(self._receipts):
            if not r.verify():
                return {"valid": False, "broken_at": i,
                        "reason": "content_hash mismatch (tampered)"}
            if r.prev_hash != prev:
                return {"valid": False, "broken_at": i,
                        "reason": "prev_hash mismatch (reordered or deleted)"}
            if r.seq != i:
                return {"valid": False, "broken_at": i,
                        "reason": "sequence mismatch"}
            prev = r.content_hash
        return {"valid": True, "broken_at": -1, "length": len(self._receipts),
                "head": prev}


def verify_receipts(receipts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Verify a serialized chain (as returned by ``chain.receipts()``)."""
    chain = ReceiptChain(ExecutionIdentity(run_id="_verify", actor="_verify"))
    objs: List[ExecutionReceipt] = []
    for i, data in enumerate(receipts or []):
        if not isinstance(data, dict):
            return {"valid": False, "broken_at": i, "reason": "not an object"}
        try:
            entry = ExecutionReceipt(
                identity_id=data.get("identity_id", ""),
                run_id=data.get("run_id", ""), seq=int(data.get("seq", i)),
                status=data.get("status", "UNKNOWN"),
                inputs_digest=data.get("inputs_digest", ""),
                outputs_digest=data.get("outputs_digest", ""),
                approval_id=data.get("approval_id", ""),
                prev_hash=data.get("prev_hash", GENESIS),
                ts=data.get("ts"), content_hash=data.get("content_hash", ""))
        except (TypeError, ValueError) as exc:
            return {"valid": False, "broken_at": i, "reason": str(exc)}
        objs.append(entry)
    chain._receipts = objs
    return chain.verify()
