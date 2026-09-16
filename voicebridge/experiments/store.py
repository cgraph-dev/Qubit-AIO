"""Packet 8P controlled evaluation — composed store.

Composes the clearly owned persistence, run/promotion, and transfer components
into the public ``ExperimentStore``. Carries only the composition and the
journal/log/clock bridge; all logic lives in the mixins.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict

from ..logging_setup import log_event
from ..state_journal import HashChainJournal
from ._promote import PromoteMixin
from ._run import RunMixin
from ._transfer import TransferMixin


class ExperimentStore(RunMixin, PromoteMixin, TransferMixin):
    """Persistent, offline, single-writer controlled-evaluation store.

    Fully offline; never changes production model/router/permission
    configuration. Promotion never auto-applies; a promoted experiment is a
    stored decision record only.
    """

    def __init__(self, path: Path | str, *,
                 clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._open(path)
        self._journal_obj = HashChainJournal(
            Path(path).with_name(Path(path).name + ".journal.jsonl"),
            event_prefix="experiments", clock=clock)
        if not self._journal_obj.path.exists():
            try:
                self._journal("init", {"version": 1})
            except Exception:
                pass

    def _journal(self, action: str, payload: Dict[str, Any]) -> None:
        self._journal_obj.append(action, payload)

    def _log(self, event: str, **fields: Any) -> None:
        log_event(f"experiments.{event}", **fields)

    @property
    def journal_path(self) -> Path:
        return self._journal_obj.path

    def journal_verify(self) -> Dict[str, Any]:
        return self._journal_obj.verify()


def experiments_store(cfg, *, path: Optional[Path | str] = None) -> ExperimentStore:
    """Open (or create) the controlled-evaluation store. Disabled-by-default is
    a caller-level gate; the store is inert until used."""
    if path is None:
        path = cfg.get("experiments", "state_path", default="") or \
            (cfg.state_dir() / "experiments" / "experiments.db")
    return ExperimentStore(Path(path).expanduser())
