import asyncio
import threading

from qubit_cli.web_routers import status


def test_get_logs_yields_while_reading_and_filtering(tmp_path, monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "agent.log").write_text("fixture\n", encoding="utf-8")
    monkeypatch.setattr(status, "get_qubit_home", lambda: tmp_path)

    loop_ran = threading.Event()

    def blocking_read(*args, **kwargs):
        assert loop_ran.wait(2), "log reading blocked the event loop"
        return ["fixture"]

    import qubit_cli.logs
    monkeypatch.setattr(qubit_cli.logs, "_read_tail", blocking_read)

    async def exercise():
        asyncio.get_running_loop().call_soon(loop_ran.set)
        return await status.get_logs(file="agent", lines=100)

    assert asyncio.run(exercise()) == {
        "file": "agent", "lines": ["fixture"], "offset": 0, "limit": 100,
        "returned": 1, "has_more": False, "next_offset": None,
    }


def test_get_logs_filtered_pages_reach_old_history(tmp_path, monkeypatch):
    """Exercise the actual API handler, not only the search helper."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "agent.log").write_text(
        "2026-01-01 00:00:00 WARNING old warning\\n"
        + "2026-01-01 00:00:01 INFO filler\\n" * 2500
        + "2026-01-01 00:00:02 WARNING new warning\\n",
        encoding="utf-8")
    monkeypatch.setattr(status, "get_qubit_home", lambda: tmp_path)

    async def exercise():
        latest = await status.get_logs(file="agent", lines=1, level="WARNING")
        older = await status.get_logs(file="agent", lines=1, level="WARNING",
                                      offset=latest["next_offset"])
        return latest, older

    latest, older = asyncio.run(exercise())
    assert "new warning" in latest["lines"][0]
    assert latest["has_more"] is True
    assert latest["next_offset"] == 1
    assert "old warning" in older["lines"][0]
    assert older["has_more"] is False
    assert older["next_offset"] is None


def test_get_logs_huge_unfiltered_offset_uses_bounded_search(tmp_path,
                                                               monkeypatch):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    (logs_dir / "agent.log").write_text("one\\ntwo\\n", encoding="utf-8")
    monkeypatch.setattr(status, "get_qubit_home", lambda: tmp_path)

    import qubit_cli.logs

    def unexpected_tail(*args, **kwargs):
        raise AssertionError("large offsets must not allocate an enormous tail")

    monkeypatch.setattr(qubit_cli.logs, "_read_tail", unexpected_tail)
    page = asyncio.run(status.get_logs(file="agent", lines=10, offset=10**12))
    assert page["lines"] == []
    assert page["returned"] == 0
    assert page["has_more"] is False
    assert page["next_offset"] is None
