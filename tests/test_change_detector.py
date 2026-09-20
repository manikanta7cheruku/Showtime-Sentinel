from datetime import date

import pytest

from app.models import AvailabilityState as S
from app.models import NormalizedResult, NotificationKind, Watch
from app.monitoring.change_detector import ChangeDetector


def w(state: S, failures: int = 0) -> Watch:
    return Watch(id=1, movie_name="M", city="C", target_date=date(2026, 6, 1),
                 theatre="T", current_state=state, consecutive_failures=failures)


def r(state: S, h: str = "hash") -> NormalizedResult:
    return NormalizedResult(state=state, source_name="fake", availability_hash=h,
                            error="err" if state in (S.ERROR, S.BLOCKED) else None)


@pytest.fixture
def detector():
    return ChangeDetector(error_notify_threshold=3)


@pytest.mark.parametrize("previous,new", [
    (S.BOOKING_NOT_OPEN, S.BOOKING_OPEN),
    (S.NO_SEATS_AVAILABLE, S.SEATS_AVAILABLE),
    (S.SHOW_NOT_AVAILABLE, S.BOOKING_OPEN),
    (S.SHOW_NOT_AVAILABLE, S.SEATS_AVAILABLE),
    (S.UNKNOWN, S.SEATS_AVAILABLE),
    (S.BOOKING_OPEN, S.SEATS_AVAILABLE),
    (S.BLOCKED, S.SEATS_AVAILABLE),
])
def test_meaningful_transitions_notify(detector, previous, new):
    decision = detector.decide(w(previous), r(new))
    assert decision.should_notify and decision.kind is NotificationKind.STATE_CHANGE
    assert decision.dedupe_key


@pytest.mark.parametrize("previous,new", [
    (S.SEATS_AVAILABLE, S.SEATS_AVAILABLE),
    (S.BOOKING_OPEN, S.BOOKING_OPEN),
    (S.SEATS_AVAILABLE, S.NO_SEATS_AVAILABLE),   # bad news: logged, not pushed
    (S.SEATS_AVAILABLE, S.BOOKING_OPEN),         # weaker news than we already sent
    (S.BOOKING_OPEN, S.BOOKING_NOT_OPEN),
    (S.UNKNOWN, S.SHOW_NOT_AVAILABLE),
    (S.NOT_FOUND, S.DATE_NOT_AVAILABLE),
])
def test_uninteresting_transitions_stay_silent(detector, previous, new):
    assert detector.decide(w(previous), r(new)).should_notify is False


def test_repeated_identical_state_is_not_a_change(detector):
    decision = detector.decide(w(S.SEATS_AVAILABLE), r(S.SEATS_AVAILABLE))
    assert decision.changed is False and decision.should_notify is False


def test_errors_notify_only_at_the_threshold(detector):
    assert detector.decide(w(S.SEATS_AVAILABLE, failures=0), r(S.ERROR)).should_notify is False
    assert detector.decide(w(S.ERROR, failures=1), r(S.ERROR)).should_notify is False
    third = detector.decide(w(S.ERROR, failures=2), r(S.ERROR))
    assert third.should_notify and third.kind is NotificationKind.ERROR
    # And not again on the fourth
    assert detector.decide(w(S.ERROR, failures=3), r(S.ERROR)).should_notify is False


def test_unknown_result_is_never_a_change(detector):
    assert detector.decide(w(S.SEATS_AVAILABLE), r(S.UNKNOWN)).changed is False


def test_dedupe_key_includes_states_and_hash(detector):
    decision = detector.decide(w(S.BOOKING_NOT_OPEN), r(S.SEATS_AVAILABLE, h="deadbeef"))
    assert decision.dedupe_key == "1|BOOKING_NOT_OPEN->SEATS_AVAILABLE|deadbeef"
