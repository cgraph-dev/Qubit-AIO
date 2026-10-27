"""Read-only Apple Music library connector.

On macOS a caller injects a MusicKit/``osascript``-shaped adapter exposing
``list_tracks(limit=)`` and returning library rows. The connector maps rows to
bounded :class:`MusicTrack` records. It has no play, add, remove, rate or
download path and never writes to the library.

On any other platform ``list_tracks`` raises ``not_supported`` with the host
platform and the required framework before the adapter is touched.

Field shape was informed by OpenJarvis ``0583901``
(``src/openjarvis/connectors/apple_music.py``, Apache-2.0); no upstream code is
copied. See ``connectors/APPLE_PROVENANCE.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from connectors.apple_common import (
    MAX_ID_CHARS,
    MAX_TEXT_CHARS,
    AppleConnectorError,
    backend_rows,
    bounded_text,
    coerce_limit,
    coerce_nonnegative_float,
    coerce_nonnegative_int,
    current_platform,
    ensure_supported,
    map_backend_error,
    normalize_timestamp,
    require_backend,
)

CONNECTOR_NAME = "Apple Music"
REQUIRED_FRAMEWORK = "MusicKit (Music.app AppleScript)"
SUPPORTED_PLATFORM = "darwin"

DEFAULT_LIMIT = 20
MAX_LIMIT = 200
MAX_TRACK_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class MusicTrack:
    track_id: str
    name: str
    artist: str = ""
    album: str = ""
    duration_s: Optional[float] = None
    genre: str = ""
    play_count: Optional[int] = None
    played_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "name": self.name,
            "artist": self.artist,
            "album": self.album,
            "duration_s": self.duration_s,
            "genre": self.genre,
            "play_count": self.play_count,
            "played_at": self.played_at,
        }


def _played_at(value: object) -> str:
    if isinstance(value, str) and value.strip().casefold() == "never":
        return ""
    return normalize_timestamp(value)


def _track_from_row(row: dict[str, Any]) -> Optional[MusicTrack]:
    name = bounded_text(row.get("name") or row.get("title"), MAX_TEXT_CHARS)
    if not name:
        return None
    artist = bounded_text(row.get("artist"), MAX_TEXT_CHARS)
    track_id = bounded_text(
        row.get("track_id") or row.get("id") or row.get("persistent_id"),
        MAX_ID_CHARS,
    )
    if not track_id:
        track_id = f"{name}:{artist}"[:MAX_ID_CHARS]
    duration = coerce_nonnegative_float(
        row.get("duration_s", row.get("duration")))
    if duration is not None:
        duration = round(min(duration, MAX_TRACK_SECONDS), 2)
    return MusicTrack(
        track_id=track_id,
        name=name,
        artist=artist,
        album=bounded_text(row.get("album"), MAX_TEXT_CHARS),
        duration_s=duration,
        genre=bounded_text(row.get("genre"), MAX_TEXT_CHARS),
        play_count=coerce_nonnegative_int(
            row.get("play_count", row.get("played_count"))),
        played_at=_played_at(
            row.get("played_at") or row.get("played_date")),
    )


class AppleMusicConnector:
    """Narrow read-only Music library surface over an injected adapter."""

    connector_id = "apple_music"

    def __init__(self, *, platform: Optional[str] = None, backend: Any = None) -> None:
        self._platform = platform or current_platform()
        self._backend = backend

    @property
    def platform(self) -> str:
        return self._platform

    def available(self) -> bool:
        return self._platform == SUPPORTED_PLATFORM and self._backend is not None

    def list_tracks(self, *, limit: object = DEFAULT_LIMIT) -> list[MusicTrack]:
        ensure_supported(
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        bounded = coerce_limit(limit, default=DEFAULT_LIMIT, maximum=MAX_LIMIT)
        backend = require_backend(
            self._backend,
            platform=self._platform,
            required_framework=REQUIRED_FRAMEWORK,
            connector=CONNECTOR_NAME,
        )
        try:
            payload = backend.list_tracks(limit=bounded)
        except AppleConnectorError:
            raise
        except Exception as exc:
            raise map_backend_error(
                exc,
                connector=CONNECTOR_NAME,
                required_framework=REQUIRED_FRAMEWORK,
                platform=self._platform,
            ) from None
        rows = backend_rows(
            payload,
            connector=CONNECTOR_NAME,
            required_framework=REQUIRED_FRAMEWORK,
            platform=self._platform,
        )
        tracks: list[MusicTrack] = []
        for row in rows:
            track = _track_from_row(row)
            if track is not None:
                tracks.append(track)
            if len(tracks) >= bounded:
                break
        return tracks

