from datetime import datetime, timezone

from app.utils.timeutil import IST, iso_utc, parse_date, parse_iso_utc, to_display, to_utc, utc_now


def test_utc_now_is_aware():
    assert utc_now().tzinfo is not None


def test_naive_input_is_treated_as_utc():
    assert to_utc(datetime(2026, 6, 1, 12, 0)).tzinfo == timezone.utc


def test_display_converts_utc_to_ist():
    # 12:00 UTC is 17:30 IST (+05:30)
    text = to_display(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc))
    assert "05:30:00 PM IST" in text and "01 Jun 2026" in text


def test_ist_input_is_stored_and_displayed_consistently():
    ist_time = datetime(2026, 6, 1, 23, 30, tzinfo=IST)
    stored = iso_utc(ist_time)                 # crosses midnight backwards in UTC
    assert stored.startswith("2026-06-01T18:00")
    assert "11:30:00 PM IST" in to_display(parse_iso_utc(stored))


def test_round_trip_through_sqlite_format():
    now = utc_now()
    assert abs((parse_iso_utc(iso_utc(now)) - now).total_seconds()) < 0.001


def test_display_handles_none():
    assert to_display(None) == "never"


def test_date_parsing_accepts_indian_formats():
    assert parse_date("2026-06-01") == parse_date("01-06-2026") == parse_date("01/06/2026")
