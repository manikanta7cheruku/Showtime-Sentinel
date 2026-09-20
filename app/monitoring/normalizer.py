"""Normalization + deterministic hashing.

The hash answers one question: "is this the same availability picture as last
time?" It must be stable across process restarts and across dict ordering, so we
build it from a canonical JSON document with sorted keys, sorted shows and
sorted seat ids - and we exclude timestamps, URLs and free-text notes, which
change without the availability changing.
"""
from __future__ import annotations

import hashlib
import json
import logging

from app.models import AvailabilityState, FetchOutcome, NormalizedResult, RawShow, Watch
from app.monitoring.filters import filter_shows
from app.monitoring.state import derive_state

logger = logging.getLogger(__name__)


def _hashable_show(show: RawShow) -> dict:
    return {
        "theatre": show.theatre.strip().casefold(),
        "screen": (show.screen or "").strip().casefold(),
        "showtime": show.showtime or "",
        "booking_open": show.booking_open,
        "categories": sorted(
            (
                {
                    "name": c.name,
                    "available_seats": c.available_seats,   # None stays None, not 0
                    "seat_ids": sorted(c.seat_ids),
                }
                for c in show.categories
            ),
            key=lambda c: c["name"],
        ),
    }


def compute_hash(state: AvailabilityState, matched_shows: list[RawShow]) -> str:
    document = {
        "state": state.value,
        "shows": sorted(
            (_hashable_show(s) for s in matched_shows),
            key=lambda d: (d["theatre"], d["screen"], d["showtime"]),
        ),
    }
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize(watch: Watch, outcome: FetchOutcome) -> NormalizedResult:
    """The one function that turns "what the source saw" into "what we store"."""
    all_shows = list(outcome.payload.shows) if outcome.payload else []
    matched = filter_shows(watch, all_shows)
    state, seat_count, seat_ids = derive_state(watch, outcome, matched)

    notes = list(outcome.payload.notes) if outcome.payload else []
    booking_url = outcome.payload.booking_url if outcome.payload else None
    # Prefer a URL the user verified themselves; otherwise use one actually read
    # from the page. We never synthesise one from movie/city/date.
    booking_url = watch.source_url or booking_url

    result = NormalizedResult(
        state=state,
        source_name=outcome.source_name,
        matched_shows=matched,
        all_show_count=len(all_shows),
        matched_seat_count=seat_count,
        matched_seat_ids=seat_ids,
        availability_hash=compute_hash(state, matched),
        booking_url=booking_url,
        error=outcome.error,
        notes=notes,
        checked_at=outcome.fetched_at,
    )
    logger.debug("watch #%s normalized: %s hash=%s", watch.id, result.summary(),
                 result.availability_hash[:12])
    return result
