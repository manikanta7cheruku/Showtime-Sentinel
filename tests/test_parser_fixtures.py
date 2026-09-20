"""Parser fixtures: HTML in, facts out. No browser, no network.

These are SYNTHETIC pages in the shape the parser expects, not captured
BookMyShow output (which we neither redistribute nor rely on). They pin the
parser's behaviour so you can safely re-point the selectors later.
"""
from datetime import date

import pytest

from app.models import AvailabilityState, FetchOutcome, FetchStatus, Watch
from app.monitoring.normalizer import normalize
from app.sources.bookmyshow import ParseError, parse_showtimes_html

PAGE = """<html><body>
<div data-venue-name="{venue}" data-screen="{screen}">{shows}</div>
{extra}</body></html>"""
SHOW = ('<a data-showtime="{time}" data-category="{cat}" data-available-seats="{seats}" '
        'class="{cls}">{time}</a>')


def page(shows, venue="PVR Forum Mall", screen="Screen 3", extra=""):
    return PAGE.format(venue=venue, screen=screen, shows=shows, extra=extra)


def watch(**kw) -> Watch:
    return Watch(**{**dict(id=1, movie_name="M", city="Bengaluru",
                           target_date=date(2026, 6, 1), theatre="PVR Forum"), **kw})


def as_outcome(payload):
    return FetchOutcome(status=FetchStatus.OK, source_name="bookmyshow", payload=payload)


# --- fixtures for each required scenario -----------------------------------
def test_missing_movie_page_raises_parse_error_not_a_guess():
    with pytest.raises(ParseError):
        parse_showtimes_html("<html><body><p>No results found</p></body></html>")


def test_unavailable_date_coming_soon():
    payload = parse_showtimes_html(page("", extra="<p>Booking opens soon</p>"))
    assert payload.date_available is False
    assert normalize(watch(), as_outcome(payload)).state is AvailabilityState.DATE_NOT_AVAILABLE


def test_closed_booking_sold_out_class():
    html = page(SHOW.format(time="19:30", cat="GOLD", seats=0, cls="showtime-pill _soldout"))
    payload = parse_showtimes_html(html)
    assert payload.shows[0].booking_open is False
    state = normalize(watch(showtime="19:30"), as_outcome(payload)).state
    assert state is AvailabilityState.BOOKING_NOT_OPEN


def test_open_booking_without_seat_counts():
    html = page('<a data-showtime="19:30" class="showtime-pill">07:30 PM</a>')
    payload = parse_showtimes_html(html)
    result = normalize(watch(showtime="19:30"), as_outcome(payload))
    assert result.state is AvailabilityState.BOOKING_OPEN
    assert result.matched_seat_count is None


def test_available_seats():
    html = page(SHOW.format(time="19:30", cat="GOLD", seats=12, cls="showtime-pill"))
    result = normalize(watch(showtime="19:30", min_seats=2), as_outcome(parse_showtimes_html(html)))
    assert result.state is AvailabilityState.SEATS_AVAILABLE and result.matched_seat_count == 12


def test_unavailable_seats_zero_count():
    html = page(SHOW.format(time="19:30", cat="GOLD", seats=0, cls="showtime-pill"))
    result = normalize(watch(showtime="19:30"), as_outcome(parse_showtimes_html(html)))
    assert result.state is AvailabilityState.NO_SEATS_AVAILABLE


def test_multiple_shows_are_all_parsed():
    html = page("".join(
        SHOW.format(time=t, cat="GOLD", seats=5, cls="showtime-pill")
        for t in ("10:00", "13:15", "19:30", "22:45")))
    payload = parse_showtimes_html(html)
    assert [s.showtime for s in payload.shows] == ["10:00", "13:15", "19:30", "22:45"]
    result = normalize(watch(time_from="18:00", time_to="23:00"), as_outcome(payload))
    assert len(result.matched_shows) == 2 and result.all_show_count == 4


def test_missing_screen_attribute_is_tolerated():
    html = PAGE.format(venue="INOX Nexus", screen="", shows=SHOW.format(
        time="19:30", cat="GOLD", seats=5, cls="showtime-pill"), extra="")
    payload = parse_showtimes_html(html)
    assert payload.shows[0].screen in (None, "")
    # A watch that demands a specific screen must not match a page without one.
    assert normalize(watch(theatre="INOX", screen="Screen 9"),
                     as_outcome(payload)).state is AvailabilityState.SHOW_NOT_AVAILABLE


def test_twelve_hour_times_are_converted():
    html = page('<a class="showtime-pill">10:15 PM</a><a class="showtime-pill">12:30 AM</a>')
    payload = parse_showtimes_html(html)
    assert [s.showtime for s in payload.shows] == ["22:15", "00:30"]


def test_real_anchor_is_used_as_booking_url():
    html = page(SHOW.format(time="19:30", cat="GOLD", seats=3, cls="showtime-pill"),
                extra='<a href="https://in.bookmyshow.com/buytickets/abc">Book</a>')
    assert parse_showtimes_html(html).booking_url == "https://in.bookmyshow.com/buytickets/abc"


def test_unrecognised_markup_raises_rather_than_reporting_sold_out():
    with pytest.raises(ParseError):
        parse_showtimes_html("<html><body><div class='totally-new-design'></div></body></html>")
