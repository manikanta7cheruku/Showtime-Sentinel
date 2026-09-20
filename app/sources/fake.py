"""FakeMovieSource - the star of the learning project.

It emits the *same payload shape* the real adapter emits, so the normalizer,
state machine, change detector, database and notifier are all genuinely
exercised. Nothing is stubbed downstream.

Scenarios are scripted lists of step names. Each call to fetch() advances one
step per watch and then repeats the final step forever - which is exactly what
makes "one notification, then silence" easy to demonstrate.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Sequence

from app.models import FetchOutcome, FetchStatus, RawShow, SeatCategory, SourcePayload, Watch
from app.sources.base import MovieSource

logger = logging.getLogger(__name__)

STEPS = (
    "MOVIE_NOT_FOUND", "DATE_NOT_AVAILABLE", "SHOW_NOT_AVAILABLE",
    "BOOKING_NOT_OPEN", "BOOKING_OPEN", "SEATS_AVAILABLE", "SEATS_UNAVAILABLE",
    "BLOCKED", "ERROR",
)

#: The default story: closed -> closed -> open -> seats -> seats (stays).
DEFAULT_SCRIPT: tuple[str, ...] = (
    "BOOKING_NOT_OPEN", "BOOKING_NOT_OPEN", "BOOKING_OPEN", "SEATS_AVAILABLE", "SEATS_AVAILABLE",
)

SCENARIOS: dict[str, tuple[str, ...]] = {
    "booking-open": DEFAULT_SCRIPT,
    "seats-return": ("SEATS_UNAVAILABLE", "SEATS_UNAVAILABLE", "SEATS_AVAILABLE"),
    "always-closed": ("BOOKING_NOT_OPEN",),
    "always-seats": ("SEATS_AVAILABLE",),
    "blocked": ("BLOCKED",),
    "flaky": ("ERROR", "ERROR", "SEATS_AVAILABLE"),
    "missing-movie": ("MOVIE_NOT_FOUND",),
}


class FakeMovieSource(MovieSource):
    name = "fake"

    def __init__(
        self,
        script: Sequence[str] = DEFAULT_SCRIPT,
        *,
        per_watch_scripts: dict[int, Sequence[str]] | None = None,
        raise_on_error_step: bool = False,
    ) -> None:
        self.script = list(script)
        self.per_watch_scripts = {k: list(v) for k, v in (per_watch_scripts or {}).items()}
        # When True, the ERROR step raises instead of returning ERROR - useful for
        # proving the retry/backoff path and that one broken watch is isolated.
        self.raise_on_error_step = raise_on_error_step
        self.calls: dict[int, int] = defaultdict(int)

    # ------------------------------------------------------------------ #
    def script_for(self, watch: Watch) -> list[str]:
        return self.per_watch_scripts.get(watch.id or -1, self.script)

    def peek(self, watch: Watch) -> str:
        script = self.script_for(watch)
        index = min(self.calls[watch.id or -1], len(script) - 1)
        return script[index]

    def reset(self) -> None:
        self.calls.clear()

    async def fetch(self, watch: Watch) -> FetchOutcome:
        key = watch.id or -1
        step = self.peek(watch)
        self.calls[key] += 1
        logger.info("[fake] watch #%s step %s (call %s)", key, step, self.calls[key])
        return self._build(watch, step)

    # ------------------------------------------------------------------ #
    def _build(self, watch: Watch, step: str) -> FetchOutcome:
        if step not in STEPS:
            raise ValueError(f"unknown fake step {step!r}; valid: {STEPS}")

        if step == "BLOCKED":
            return FetchOutcome(
                status=FetchStatus.BLOCKED, source_name=self.name,
                error="simulated anti-bot challenge page",
            )
        if step == "ERROR":
            if self.raise_on_error_step:
                raise TimeoutError("simulated network timeout")
            return FetchOutcome(
                status=FetchStatus.ERROR, source_name=self.name,
                error="simulated fetch failure",
            )
        if step == "MOVIE_NOT_FOUND":
            return self._ok(SourcePayload(movie_found=False, notes=["fake: movie not listed"]))
        if step == "DATE_NOT_AVAILABLE":
            return self._ok(SourcePayload(date_available=False, notes=["fake: date not offered"]))
        if step == "SHOW_NOT_AVAILABLE":
            # A real theatre listing that simply doesn't match this watch.
            return self._ok(SourcePayload(
                booking_open=True,
                shows=[RawShow(theatre="Some Other Cinema", screen="Screen 1",
                               showtime="10:00", booking_open=True,
                               categories=[SeatCategory(name="GENERAL", available_seats=40)])],
                notes=["fake: shows exist but none match the watch filters"],
            ))

        # From here on the show matches the watch, so build it from the watch.
        showtime = watch.showtime or watch.time_from or "19:30"
        screen = watch.screen or "Screen 3"
        category = (watch.seat_category or "GOLD").upper()
        url = watch.source_url  # only ever a URL the user supplied; never invented

        if step == "BOOKING_NOT_OPEN":
            return self._ok(SourcePayload(
                booking_open=False, booking_url=url,
                shows=[RawShow(theatre=watch.theatre, screen=screen, showtime=showtime,
                               booking_open=False, categories=[])],
                notes=["fake: show listed, booking not open yet"],
            ))
        if step == "BOOKING_OPEN":
            # Bookable, but the page did not expose seat counts -> available_seats=None
            return self._ok(SourcePayload(
                booking_open=True, booking_url=url,
                shows=[RawShow(theatre=watch.theatre, screen=screen, showtime=showtime,
                               booking_open=True,
                               categories=[SeatCategory(name=category, available_seats=None, price=350.0)])],
                notes=["fake: booking open, seat counts unknown"],
            ))
        if step == "SEATS_UNAVAILABLE":
            return self._ok(SourcePayload(
                booking_open=True, booking_url=url,
                shows=[RawShow(theatre=watch.theatre, screen=screen, showtime=showtime,
                               booking_open=True,
                               categories=[SeatCategory(name=category, available_seats=0, price=350.0)])],
                notes=["fake: sold out"],
            ))

        # SEATS_AVAILABLE
        seat_ids = watch.exact_seats or [f"{category[0]}-A{i}" for i in range(1, 6)]
        return self._ok(SourcePayload(
            booking_open=True, booking_url=url,
            shows=[RawShow(theatre=watch.theatre, screen=screen, showtime=showtime,
                           booking_open=True,
                           categories=[SeatCategory(name=category, available_seats=len(seat_ids),
                                                    price=350.0, seat_ids=seat_ids)])],
            notes=["fake: seats available"],
        ))

    def _ok(self, payload: SourcePayload) -> FetchOutcome:
        return FetchOutcome(status=FetchStatus.OK, source_name=self.name, payload=payload)
