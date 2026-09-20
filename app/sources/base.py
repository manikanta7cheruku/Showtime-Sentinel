"""The MovieSource interface - the seam that makes this project teachable.

A source does exactly one thing: turn a Watch into a FetchOutcome. It must not
raise for "expected" problems (blocked, not found); it returns a status instead.
It MAY raise for transient faults (timeout, connection reset) because the retry
wrapper in checker.py knows how to handle those.
"""
from __future__ import annotations

import abc

from app.models import FetchOutcome, Watch


class MovieSource(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def fetch(self, watch: Watch) -> FetchOutcome:
        """Return facts about this watch's movie/date/theatre. Never a state."""

    async def aclose(self) -> None:
        """Release resources (browser, HTTP client). Default: nothing to do."""
        return None


class SourceRegistry:
    """Maps a watch's `source` string to a live source object.

    TEST_MODE=true rewires everything to the fake source, which is why you can
    safely develop without ever touching bookmyshow.com.
    """

    def __init__(self, *, test_mode: bool = True) -> None:
        self._sources: dict[str, MovieSource] = {}
        self.test_mode = test_mode

    def register(self, source: MovieSource) -> None:
        self._sources[source.name] = source

    def get(self, name: str) -> MovieSource:
        if self.test_mode and "fake" in self._sources:
            return self._sources["fake"]
        if name not in self._sources:
            raise KeyError(
                f"source {name!r} is not registered "
                f"(available: {sorted(self._sources)}). "
                "If you meant the real adapter, set ENABLE_BOOKMYSHOW_SOURCE=true."
            )
        return self._sources[name]

    def names(self) -> list[str]:
        return sorted(self._sources)

    async def aclose_all(self) -> None:
        for source in self._sources.values():
            try:
                await source.aclose()
            except Exception as exc:                      # noqa: BLE001
                import logging
                logging.getLogger(__name__).warning("error closing %s: %s", source.name, exc)
