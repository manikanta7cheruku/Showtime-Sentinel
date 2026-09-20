from datetime import date

import pytest

from app.models import AvailabilityState, FetchOutcome, FetchStatus, RawShow, SeatCategory, SourcePayload, Watch
from app.monitoring.filters import filter_shows, showtime_matches
from app.monitoring.state import derive_state


def watch(**overrides) -> Watch:
    base = dict(id=1, movie_name="M", city="C", target_date=date(2026, 6, 1),
                theatre="PVR Forum", min_seats=1)
    return Watch(**{**base, **overrides})


def ok(payload: SourcePayload) -> FetchOutcome:
    return FetchOutcome(status=FetchStatus.OK, source_name="fake", payload=payload)


def show(**kw) -> RawShow:
    base = dict(theatre="PVR Forum Mall", screen="Screen 3", showtime="19:30", booking_open=True)
    return RawShow(**{**base, **kw})


# --- filtering -------------------------------------------------------------
def test_theatre_matches_on_substring_case_insensitively():
    assert filter_shows(watch(theatre="pvr forum"), [show()])


def test_screen_filter_excludes_other_screens():
    assert filter_shows(watch(screen="Screen 1"), [show()]) == []


def test_missing_screen_on_page_fails_an_explicit_screen_filter():
    assert filter_shows(watch(screen="Screen 3"), [show(screen=None)]) == []
    assert filter_shows(watch(), [show(screen=None)])      # no filter -> matches


@pytest.mark.parametrize("showtime,expected", [("19:30", True), ("19:00", False)])
def test_exact_showtime(showtime, expected):
    assert showtime_matches(watch(showtime="19:30"), show(showtime=showtime)) is expected


@pytest.mark.parametrize("showtime,expected", [
    ("18:00", True), ("19:30", True), ("21:00", True), ("17:59", False), ("21:01", False),
])
def test_time_range_is_inclusive(showtime, expected):
    w = watch(time_from="18:00", time_to="21:00")
    assert showtime_matches(w, show(showtime=showtime)) is expected


def test_multiple_shows_only_matching_ones_kept_and_sorted():
    shows = [show(showtime="22:00"), show(showtime="19:30"), show(theatre="INOX", showtime="20:00")]
    kept = filter_shows(watch(time_from="19:00", time_to="23:00"), shows)
    assert [s.showtime for s in kept] == ["19:30", "22:00"]


def test_category_filter_strips_non_matching_categories():
    s = show(categories=[SeatCategory(name="GOLD", available_seats=5),
                         SeatCategory(name="SILVER", available_seats=9)])
    kept = filter_shows(watch(seat_category="gold"), [s])
    assert [c.name for c in kept[0].categories] == ["GOLD"]


# --- states ----------------------------------------------------------------
def test_blocked_and_error_short_circuit():
    for status, expected in ((FetchStatus.BLOCKED, AvailabilityState.BLOCKED),
                             (FetchStatus.ERROR, AvailabilityState.ERROR)):
        outcome = FetchOutcome(status=status, source_name="fake")
        assert derive_state(watch(), outcome, [])[0] is expected


def test_rate_limited_yields_unknown_not_a_guess():
    outcome = FetchOutcome(status=FetchStatus.RATE_LIMITED, source_name="fake")
    assert derive_state(watch(), outcome, [])[0] is AvailabilityState.UNKNOWN


def test_movie_and_date_problems():
    assert derive_state(watch(), ok(SourcePayload(movie_found=False)), [])[0] is AvailabilityState.NOT_FOUND
    assert derive_state(watch(), ok(SourcePayload(date_available=False)), [])[0] is AvailabilityState.DATE_NOT_AVAILABLE


def test_show_not_available_when_nothing_matched():
    payload = SourcePayload(shows=[show(theatre="INOX")])
    assert derive_state(watch(), ok(payload), [])[0] is AvailabilityState.SHOW_NOT_AVAILABLE


def test_booking_not_open():
    s = show(booking_open=False)
    assert derive_state(watch(), ok(SourcePayload(shows=[s])), [s])[0] is AvailabilityState.BOOKING_NOT_OPEN


def test_booking_open_when_seat_counts_unknown():
    s = show(categories=[SeatCategory(name="GOLD", available_seats=None)])
    state, count, _ = derive_state(watch(), ok(SourcePayload(shows=[s])), [s])
    assert state is AvailabilityState.BOOKING_OPEN and count is None


def test_no_seats_when_count_is_zero():
    s = show(categories=[SeatCategory(name="GOLD", available_seats=0)])
    state, count, _ = derive_state(watch(), ok(SourcePayload(shows=[s])), [s])
    assert state is AvailabilityState.NO_SEATS_AVAILABLE and count == 0


def test_min_seats_is_respected():
    s = show(categories=[SeatCategory(name="GOLD", available_seats=2)])
    assert derive_state(watch(min_seats=2), ok(SourcePayload(shows=[s])), [s])[0] is AvailabilityState.SEATS_AVAILABLE
    assert derive_state(watch(min_seats=5), ok(SourcePayload(shows=[s])), [s])[0] is AvailabilityState.NO_SEATS_AVAILABLE


def test_exact_seats_must_actually_be_offered():
    s = show(categories=[SeatCategory(name="GOLD", available_seats=3, seat_ids=["A1", "A2", "A9"])])
    w = watch(exact_seats=["A1", "A2"], min_seats=2)
    state, count, ids = derive_state(w, ok(SourcePayload(shows=[s])), [s])
    assert state is AvailabilityState.SEATS_AVAILABLE and count == 2 and ids == ["A1", "A2"]

    w2 = watch(exact_seats=["Z9"], min_seats=1)
    assert derive_state(w2, ok(SourcePayload(shows=[s])), [s])[0] is AvailabilityState.NO_SEATS_AVAILABLE
