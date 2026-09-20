from datetime import date

from app.models import AvailabilityState, FetchOutcome, FetchStatus, RawShow, SeatCategory, SourcePayload, Watch
from app.monitoring.normalizer import compute_hash, normalize


def w(**kw) -> Watch:
    return Watch(**{**dict(id=1, movie_name="M", city="C", target_date=date(2026, 6, 1),
                           theatre="PVR"), **kw})


def cat(**kw) -> SeatCategory:
    return SeatCategory(**{**dict(name="GOLD", available_seats=4, seat_ids=["A1", "A2"]), **kw})


def test_hash_is_stable_across_input_ordering():
    a = RawShow(theatre="PVR", screen="S1", showtime="19:30", booking_open=True, categories=[cat()])
    b = RawShow(theatre="PVR", screen="S2", showtime="21:00", booking_open=True, categories=[cat()])
    h1 = compute_hash(AvailabilityState.SEATS_AVAILABLE, [a, b])
    h2 = compute_hash(AvailabilityState.SEATS_AVAILABLE, [b, a])
    assert h1 == h2 and len(h1) == 64


def test_hash_ignores_seat_id_ordering_but_not_membership():
    base = RawShow(theatre="PVR", showtime="19:30", booking_open=True,
                   categories=[cat(seat_ids=["A2", "A1"])])
    same = RawShow(theatre="PVR", showtime="19:30", booking_open=True,
                   categories=[cat(seat_ids=["A1", "A2"])])
    other = RawShow(theatre="PVR", showtime="19:30", booking_open=True,
                    categories=[cat(seat_ids=["A1", "A3"])])
    s = AvailabilityState.SEATS_AVAILABLE
    assert compute_hash(s, [base]) == compute_hash(s, [same])
    assert compute_hash(s, [base]) != compute_hash(s, [other])


def test_hash_changes_with_seat_count_and_state():
    s1 = RawShow(theatre="PVR", showtime="19:30", booking_open=True, categories=[cat(available_seats=4)])
    s2 = RawShow(theatre="PVR", showtime="19:30", booking_open=True, categories=[cat(available_seats=5)])
    assert compute_hash(AvailabilityState.SEATS_AVAILABLE, [s1]) != compute_hash(AvailabilityState.SEATS_AVAILABLE, [s2])
    assert compute_hash(AvailabilityState.BOOKING_OPEN, [s1]) != compute_hash(AvailabilityState.SEATS_AVAILABLE, [s1])


def test_unknown_seat_count_differs_from_zero():
    unknown = RawShow(theatre="PVR", showtime="19:30", booking_open=True,
                      categories=[SeatCategory(name="GOLD", available_seats=None)])
    sold_out = RawShow(theatre="PVR", showtime="19:30", booking_open=True,
                       categories=[SeatCategory(name="GOLD", available_seats=0)])
    assert compute_hash(AvailabilityState.BOOKING_OPEN, [unknown]) != \
           compute_hash(AvailabilityState.BOOKING_OPEN, [sold_out])


def test_normalize_is_deterministic_and_never_invents_a_url():
    payload = SourcePayload(booking_open=True, shows=[
        RawShow(theatre="PVR Forum", showtime="19:30", booking_open=True, categories=[cat()])])
    outcome = FetchOutcome(status=FetchStatus.OK, source_name="fake", payload=payload)
    first = normalize(w(min_seats=2), outcome)
    second = normalize(w(min_seats=2), outcome)
    assert first.availability_hash == second.availability_hash
    assert first.state is AvailabilityState.SEATS_AVAILABLE
    assert first.booking_url is None            # nothing verified -> nothing claimed


def test_normalize_prefers_the_user_verified_url():
    payload = SourcePayload(booking_url="https://example.invalid/from-page", shows=[])
    outcome = FetchOutcome(status=FetchStatus.OK, source_name="fake", payload=payload)
    result = normalize(w(source_url="https://in.bookmyshow.com/verified"), outcome)
    assert result.booking_url == "https://in.bookmyshow.com/verified"
