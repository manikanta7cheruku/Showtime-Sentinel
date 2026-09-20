"""Turning a watch's wishes into predicates over shows.

Matching rules (deliberately forgiving on names, strict on times):
  * theatre / screen / category: case-insensitive substring, because real
    listings read "PVR: Forum Mall, Koramangala" and nobody types that exactly.
  * showtime: exact "HH:MM", or inside [time_from, time_to] inclusive.
  * exact_seats: at least one requested seat id present in the category.
"""
from __future__ import annotations

from app.models import RawShow, SeatCategory, Watch
from app.utils.timeutil import parse_hhmm


def _contains(haystack: str | None, needle: str | None) -> bool:
    if not needle:
        return True          # no filter -> everything matches
    if not haystack:
        return False         # filter given but page has no value -> no match
    return needle.casefold().strip() in haystack.casefold()


def showtime_matches(watch: Watch, show: RawShow) -> bool:
    if watch.showtime:
        return show.showtime == watch.showtime
    if watch.time_from and watch.time_to:
        if not show.showtime:
            return False
        try:
            actual = parse_hhmm(show.showtime)
        except ValueError:
            return False
        return parse_hhmm(watch.time_from) <= actual <= parse_hhmm(watch.time_to)
    return True


def show_matches(watch: Watch, show: RawShow) -> bool:
    return (
        _contains(show.theatre, watch.theatre)
        and _contains(show.screen, watch.screen)
        and showtime_matches(watch, show)
    )


def category_matches(watch: Watch, category: SeatCategory) -> bool:
    return _contains(category.name, watch.seat_category)


def matching_categories(watch: Watch, show: RawShow) -> list[SeatCategory]:
    return [c for c in show.categories if category_matches(watch, c)]


def matching_seat_ids(watch: Watch, categories: list[SeatCategory]) -> list[str]:
    """Which of the user's exact seats are actually on offer."""
    if not watch.exact_seats:
        return sorted({s for c in categories for s in c.seat_ids})
    wanted = set(watch.exact_seats)
    return sorted({s for c in categories for s in c.seat_ids if s in wanted})


def filter_shows(watch: Watch, shows: list[RawShow]) -> list[RawShow]:
    """Keep matching shows, and inside them only matching seat categories.

    Returns NEW RawShow objects (copies) so the caller can hash them safely
    without mutating what the source produced.
    """
    kept: list[RawShow] = []
    for show in shows:
        if not show_matches(watch, show):
            continue
        cats = matching_categories(watch, show)
        kept.append(show.model_copy(update={"categories": cats}))
    # Deterministic order is required for a stable hash.
    return sorted(kept, key=lambda s: s.sort_key())
