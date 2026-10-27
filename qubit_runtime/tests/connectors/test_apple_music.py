"""Hermetic tests for the platform-gated read-only Apple Music connector."""

from __future__ import annotations

import pytest

from connectors.apple_common import (
    REASON_INVALID_RESPONSE,
    REASON_NOT_SUPPORTED,
    REASON_TOOL_ERROR,
    AppleConnectorError,
)
from connectors.apple_music import (
    MAX_LIMIT,
    REQUIRED_FRAMEWORK,
    AppleMusicConnector,
)


class _FakeMusicBackend:
    def __init__(self, rows=None, *, error=None):
        self.rows = rows if rows is not None else []
        self.error = error
        self.calls: list[dict] = []

    def list_tracks(self, *, limit):
        self.calls.append({"limit": limit})
        if self.error is not None:
            raise self.error
        return self.rows

    def play_track(self, *args, **kwargs):
        raise AssertionError("the connector must never play or write")


def test_non_darwin_refuses_before_touching_the_adapter():
    backend = _FakeMusicBackend([{"name": "should not be read"}])
    connector = AppleMusicConnector(platform="linux", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.list_tracks()

    exc = caught.value
    assert exc.reason == REASON_NOT_SUPPORTED
    assert exc.platform == "linux"
    assert exc.required_framework == REQUIRED_FRAMEWORK
    assert "linux" in exc.detail
    assert "MusicKit" in exc.detail
    assert backend.calls == []
    assert connector.available() is False


def test_darwin_maps_the_track_shape_with_bounded_fields():
    backend = _FakeMusicBackend([
        {
            "persistent_id": "track-1",
            "name": "Song " + "x" * 500,
            "artist": "Artist",
            "album": "Album",
            "duration_s": 245.678,
            "genre": "Electronic",
            "play_count": 12,
            "played_date": "2026-03-15T14:30:00Z",
        }
    ])
    connector = AppleMusicConnector(platform="darwin", backend=backend)

    tracks = connector.list_tracks(limit=5)

    assert len(tracks) == 1
    track = tracks[0]
    assert track.track_id == "track-1"
    assert track.name.startswith("Song ")
    assert len(track.name) <= 200
    assert track.artist == "Artist"
    assert track.album == "Album"
    assert track.duration_s == 245.68
    assert track.genre == "Electronic"
    assert track.play_count == 12
    assert track.played_at == "2026-03-15T14:30:00+00:00"
    assert backend.calls == [{"limit": 5}]
    assert connector.available() is True


def test_darwin_derives_a_stable_id_and_handles_never_played():
    backend = _FakeMusicBackend([
        {"name": "Song", "artist": "Artist", "played_date": "never"},
        {"name": "Song", "artist": "Artist", "duration": 999999999},
    ])
    connector = AppleMusicConnector(platform="darwin", backend=backend)

    tracks = connector.list_tracks()

    assert tracks[0].track_id == "Song:Artist"
    assert tracks[0].played_at == ""
    assert tracks[0].duration_s is None
    assert tracks[1].track_id == "Song:Artist"
    assert tracks[1].duration_s == 86400.0


def test_darwin_clamps_the_limit_and_stops_early():
    backend = _FakeMusicBackend([
        {"name": "One", "track_id": "1"},
        {"name": "Two", "track_id": "2"},
        {"name": "Three", "track_id": "3"},
    ])
    connector = AppleMusicConnector(platform="darwin", backend=backend)

    tracks = connector.list_tracks(limit=1)
    connector.list_tracks(limit=9999)

    assert [track.name for track in tracks] == ["One"]
    assert backend.calls == [{"limit": 1}, {"limit": MAX_LIMIT}]


def test_darwin_skips_rows_without_a_name():
    backend = _FakeMusicBackend([
        {"artist": "No Name"},
        object(),
        {"name": "Keep"},
    ])
    connector = AppleMusicConnector(platform="darwin", backend=backend)

    assert [track.name for track in connector.list_tracks()] == ["Keep"]


def test_darwin_rejects_an_unexpected_payload():
    backend = _FakeMusicBackend({"tracks": []})
    connector = AppleMusicConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.list_tracks()

    assert caught.value.reason == REASON_INVALID_RESPONSE


def test_darwin_redacts_a_framework_failure():
    backend = _FakeMusicBackend(
        error=RuntimeError("token=abc /Users/alice/Music"))
    connector = AppleMusicConnector(platform="darwin", backend=backend)

    with pytest.raises(AppleConnectorError) as caught:
        connector.list_tracks()

    exc = caught.value
    assert exc.reason == REASON_TOOL_ERROR
    assert "RuntimeError" in exc.detail
    assert "token" not in exc.detail
    assert "/Users/alice" not in exc.detail


def test_music_surface_has_no_write_operations():
    connector = AppleMusicConnector(platform="darwin", backend=[])
    for name in (
        "play_track",
        "add_track",
        "remove_track",
        "rate_track",
        "download_track",
    ):
        assert not hasattr(connector, name)
