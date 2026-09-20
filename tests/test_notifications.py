from datetime import date, datetime, timezone

import pytest

from app.models import (
    AvailabilityState as S, NormalizedResult, NotificationKind, RawShow,
    SeatCategory, TransitionDecision, Watch,
)
from app.notifications.console import ConsoleNotifier
from app.notifications.formatter import format_error, format_state_change, format_test_message, format_watch_row
from app.notifications.telegram import TelegramNotifier


def watch(**kw) -> Watch:
    return Watch(**{**dict(id=7, movie_name="Interstellar", city="Bengaluru",
                           target_date=date(2026, 6, 1), theatre="PVR Forum Mall",
                           screen="Screen 3", showtime="19:30", seat_category="GOLD",
                           min_seats=2), **kw})


def result(**kw) -> NormalizedResult:
    base = dict(
        state=S.SEATS_AVAILABLE, source_name="fake", availability_hash="h" * 64,
        matched_seat_count=4, matched_seat_ids=["A1", "A2"],
        matched_shows=[RawShow(theatre="PVR Forum Mall", screen="Screen 3", showtime="19:30",
                               booking_open=True,
                               categories=[SeatCategory(name="GOLD", available_seats=4)])],
        checked_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc),
    )
    return NormalizedResult(**{**base, **kw})


def decision(**kw) -> TransitionDecision:
    base = dict(changed=True, should_notify=True, kind=NotificationKind.STATE_CHANGE,
                previous_state=S.BOOKING_NOT_OPEN, new_state=S.SEATS_AVAILABLE,
                reason="seats freed up", dedupe_key="k")
    return TransitionDecision(**{**base, **kw})


def test_state_change_message_has_every_required_field():
    text = format_state_change(watch(), result(), decision())
    for expected in ("Interstellar", "01 Jun 2026", "Bengaluru", "PVR Forum Mall",
                     "Screen 3", "19:30", "SEATS_AVAILABLE", "BOOKING_NOT_OPEN",
                     "05:30:00 PM IST", "A1, A2"):
        assert expected in text, f"missing {expected!r}"


def test_message_never_invents_a_booking_url():
    text = format_state_change(watch(source_url=None), result(booking_url=None), decision())
    assert "http" not in text.replace("https://", "") or "never invents URLs" in text
    assert "bookmyshow.com/buytickets" not in text


def test_verified_url_is_rendered_as_a_link():
    url = "https://in.bookmyshow.com/some/verified/page"
    text = format_state_change(watch(), result(booking_url=url), decision())
    assert f'href="{url}"' in text


def test_html_in_user_input_is_escaped():
    text = format_state_change(watch(movie_name="<script>x</script>"), result(), decision())
    assert "<script>" not in text and "&lt;script&gt;" in text


def test_unknown_seat_count_is_stated_honestly():
    text = format_state_change(watch(), result(state=S.BOOKING_OPEN, matched_seat_count=None,
                                               matched_seat_ids=[]), decision(new_state=S.BOOKING_OPEN))
    assert "not published on the page" in text


def test_error_message_explains_blocked():
    text = format_error(watch(), result(state=S.BLOCKED, error="challenge page"),
                        decision(kind=NotificationKind.ERROR, new_state=S.BLOCKED,
                                 reason="3 consecutive failures"))
    assert "BLOCKED means" in text and "does not attempt to bypass" in text


def test_every_notification_carries_the_no_purchase_disclaimer():
    assert "never book" in format_test_message() or "No booking" in format_test_message()
    assert "cannot and will not book" in format_state_change(watch(), result(), decision())


def test_messages_fit_telegram_limits():
    long_watch = watch(movie_name="X" * 200, theatre="Y" * 200)
    long_result = result(matched_seat_ids=[f"S{i}" for i in range(500)])
    assert len(format_state_change(long_watch, long_result, decision())) < 4096


def test_watch_row_shows_state_and_interval():
    row = format_watch_row(watch(current_state=S.BOOKING_OPEN, poll_interval_seconds=90))
    assert "#7" in row and "BOOKING_OPEN" in row and "every 90s" in row


async def test_console_notifier_records_and_never_raises(capsys):
    notifier = ConsoleNotifier(reason="test")
    assert await notifier.send_booking_open(watch(), result(), decision()) is True
    assert len(notifier.sent) == 1
    assert "Interstellar" in capsys.readouterr().out


def test_telegram_notifier_requires_credentials():
    with pytest.raises(ValueError):
        TelegramNotifier("", "")


async def test_telegram_failure_returns_false_instead_of_raising(monkeypatch):
    import httpx

    notifier = TelegramNotifier("token", "chat")

    class Boom:
        async def post(self, *_a, **_k):
            raise httpx.ConnectError("no network")

    monkeypatch.setattr(notifier, "_http", lambda: _async_value(Boom()))
    assert await notifier.send_message("hi") is False


async def _async_value(value):
    return value
