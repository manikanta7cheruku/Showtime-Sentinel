from datetime import date

import pytest
from pydantic import ValidationError

from app.models import AvailabilityState, WatchSpec, is_show_available


def test_times_are_normalised():
    w = WatchSpec(movie_name="M", city="C", target_date=date(2026, 6, 1), theatre="T", showtime="7:5")
    assert w.showtime == "07:05"


def test_seat_ids_are_canonical_sorted_and_deduped():
    w = WatchSpec(movie_name="M", city="C", target_date=date(2026, 6, 1), theatre="T",
                  exact_seats=" b2 , a1, a1 ")
    assert w.exact_seats == ["A1", "B2"]


def test_range_must_be_ordered():
    with pytest.raises(ValidationError):
        WatchSpec(movie_name="M", city="C", target_date=date(2026, 6, 1), theatre="T",
                  time_from="21:00", time_to="10:00")


def test_exact_time_and_range_are_mutually_exclusive():
    with pytest.raises(ValidationError):
        WatchSpec(movie_name="M", city="C", target_date=date(2026, 6, 1), theatre="T",
                  showtime="19:30", time_from="18:00", time_to="20:00")


def test_min_seats_cannot_exceed_named_seats():
    with pytest.raises(ValidationError):
        WatchSpec(movie_name="M", city="C", target_date=date(2026, 6, 1), theatre="T",
                  exact_seats="A1", min_seats=3)


def test_source_url_must_be_http():
    with pytest.raises(ValidationError):
        WatchSpec(movie_name="M", city="C", target_date=date(2026, 6, 1), theatre="T",
                  source_url="javascript:alert(1)")


def test_show_available_predicate():
    assert is_show_available(AvailabilityState.BOOKING_NOT_OPEN)
    assert is_show_available(AvailabilityState.SEATS_AVAILABLE)
    assert not is_show_available(AvailabilityState.SHOW_NOT_AVAILABLE)
    assert not is_show_available(AvailabilityState.ERROR)
