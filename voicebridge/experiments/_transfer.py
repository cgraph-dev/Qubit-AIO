"""Packet 8P controlled evaluation — snapshot/restore mixin.

Owns deterministic, digest-verified snapshot and restore for the experiment
store. Restore requires a human owner, verifies the digest, and writes a
pre-restore backup.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from ._models import EXPERIMENT_SCHEMA, ExperimentError
from ._persist import PersistMixin


class TransferMixin(PersistMixin):
    """Adds snapshot/restore to the composed store."""

    def snapshot(self, *, path: Optional[Path | str] = None) -> Dict[str, Any]:
        payload = {
            "schema": EXPERIMENT_SCHEMA,
            "generated_at": self._clock(),
            "candidates": [self.candidate(c["id"]).to_dict()
                           for c in self._conn.execute(
                               "SELECT id FROM candidates ORDER BY id")],
            "experiments": [self._row_experiment(r).to_dict()
                            for r in self._conn.execute(
                                "SELECT * FROM experiments ORDER BY id")],
            "trials": [self._row_trial(r).to_dict()
                       for r in self._conn.execute(
                           "SELECT * FROM trials ORDER BY id")],
            "signals": [self._row_signal(r).to_dict()
                        for r in self._conn.execute(
                            "SELECT * FROM signals ORDER BY id")],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        payload["digest"] = digest
        if path is not None:
            self._write_file(Path(path), payload)
        return payload

    def restore(self, data: Dict[str, Any], *, requested_by: str,
                merge: bool = False) -> Dict[str, Any]:
        if requested_by != "user":
            raise ExperimentError("only a human may restore experiment state")
        if not isinstance(data, dict) or data.get("schema") != EXPERIMENT_SCHEMA:
            raise ExperimentError("invalid or unsupported snapshot schema")
        digest = data.get("digest")
        check = dict(data)
        check.pop("digest", None)
        expect = hashlib.sha256(
            json.dumps(check, sort_keys=True).encode("utf-8")).hexdigest()
        if digest != expect:
            raise ExperimentError("snapshot digest mismatch (reject)")
        try:
            self.snapshot(path=self._path.with_name(self._path.name
                                                    + ".pre-restore.json"))
        except Exception:
            pass
        with self._lock:
            for table in ("candidates", "experiments", "trials", "signals"):
                self._conn.execute(f"DELETE FROM {table}")
            for c in data.get("candidates", []):
                self._conn.execute(
                    """INSERT INTO candidates(id,role,label,model,description,
                       fingerprint,created_at) VALUES(?,?,?,?,?,?,?)""",
                    (c["id"], c["role"], c["label"], c["model"],
                     c["description"], c["fingerprint"], c["created_at"]))
            for e in data.get("experiments", []):
                self._conn.execute(
                    """INSERT INTO experiments(id,champion_config_id,
                       challenger_config_id,status,started_at,decided_at,
                       decision_posterior,owner_approved,gate_report)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (e["id"], e["champion_config_id"], e["challenger_config_id"],
                     e["status"], e["started_at"], e["decided_at"],
                     e["decision_posterior"], int(e.get("owner_approved", False)),
                     e.get("gate_report", "")))
            for t in data.get("trials", []):
                self._conn.execute(
                    """INSERT INTO trials(id,experiment_id,variant,config_id,
                       started_at,ended_at,outcome,note)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (t["id"], t["experiment_id"], t["variant"], t["config_id"],
                     t["started_at"], t["ended_at"], t["outcome"],
                     t.get("note", "")))
            for s in data.get("signals", []):
                self._conn.execute(
                    """INSERT INTO signals(id,trial_id,kind,source,value,
                       recorded_at) VALUES(?,?,?,?,?,?)""",
                    (s["id"], s["trial_id"], s["kind"], s["source"],
                     s["value"], s["recorded_at"]))
            self._conn.commit()
            self._journal("restore",
                          {"count": len(data.get("candidates", []))})
        self._log("restore", count=len(data.get("candidates", [])))
        return {"state": "OK", "restored": True}

    def _write_file(self, out: Path, payload: Dict[str, Any]) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(out.parent), prefix=".esnap.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, out)
            os.chmod(out, 0o600)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
