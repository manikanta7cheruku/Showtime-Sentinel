"""Facts -> one explicit state. The single source of truth for "what's going on".

The order of the checks is the whole design; read it top to bottom as a decision
tree. Note how `available_seats=None` ("unknown") is kept distinct from `0`
("sold out"): that is what separates BOOKING_OPEN from NO_SEATS_AVAILABLE
instead of guessing.
"""
from __future__ import annotations

from app.models import AvailabilityState, FetchOutcome, FetchStatus, RawShow, Watch
from app.monitoring.filters import matching_seat_ids


def derive_state(
    watch: Watch, outcome: FetchOutcome, matched_shows: list[RawShow]
) -> tuple[AvailabilityState, int | None, list[str]]:
    """Return (state, matched_seat_count_or_None, matched_seat_ids)."""

    # 1. Transport problems first: never infer availability from a failed fetch.
    if outcome.status is FetchStatus.BLOCKED:
        return AvailabilityState.BLOCKED, None, []
    if outcome.status is FetchStatus.ERROR:
        return AvailabilityState.ERROR, None, []
    if outcome.status is FetchStatus.RATE_LIMITED:
        # Our own throttle skipped the check. Nothing was learned, so we keep
        # UNKNOWN - and the change detector treats UNKNOWN as "no news".
        return AvailabilityState.UNKNOWN, None, []
    if outcome.status is FetchStatus.NOT_FOUND or outcome.payload is None:
        return AvailabilityState.NOT_FOUND, None, []

    payload = outcome.payload

    # 2. Does the movie/date even exist?
    if not payload.movie_found:
        return AvailabilityState.NOT_FOUND, None, []
    if not payload.date_available:
        return AvailabilityState.DATE_NOT_AVAILABLE, None, []

    # 3. Does anything match THIS watch's theatre/screen/time?
    if not matched_shows:
        return AvailabilityState.SHOW_NOT_AVAILABLE, None, []

    # 4. Is any matching show bookable at all?
    bookable = [s for s in matched_shows if s.booking_open]
    if not bookable:
        return AvailabilityState.BOOKING_NOT_OPEN, None, []

    # 5. Seat-level detail, if the source gave us any.
    counts = [s.known_seat_count for s in bookable if s.known_seat_count is not None]
    seat_ids = matching_seat_ids(watch, [c for s in bookable for c in s.categories])

    if watch.exact_seats:
        # The user named specific seats: only those count.
        if len(seat_ids) >= watch.min_seats:
            return AvailabilityState.SEATS_AVAILABLE, len(seat_ids), seat_ids
        if counts or any(c.seat_ids for s in bookable for c in s.categories):
            return AvailabilityState.NO_SEATS_AVAILABLE, len(seat_ids), seat_ids
        return AvailabilityState.BOOKING_OPEN, None, seat_ids

    if not counts:
        # Bookable but seat counts unknown - the honest answer is BOOKING_OPEN.
        return AvailabilityState.BOOKING_OPEN, None, seat_ids

    total = sum(counts)
    if total >= watch.min_seats:
        return AvailabilityState.SEATS_AVAILABLE, total, seat_ids
    return AvailabilityState.NO_SEATS_AVAILABLE, total, seat_ids
