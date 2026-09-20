from app.bot.commands import _parse_kv, _spec_from_fields
from app.config import Settings


def test_kv_parser_handles_values_with_spaces():
    fields = _parse_kv("movie=Mission Impossible 8 city=Navi Mumbai date=2026-06-01 "
                       "theatre=PVR Forum Mall time=19:30 seats=3")
    assert fields["movie"] == "Mission Impossible 8"
    assert fields["city"] == "Navi Mumbai"
    assert fields["theatre"] == "PVR Forum Mall"
    assert fields["seats"] == "3"


def test_spec_from_fields_applies_defaults():
    spec = _spec_from_fields(_parse_kv(
        "movie=Dune city=Pune date=2026-06-01 theatre=PVR seat_ids=a1,a2 seats=2"), Settings())
    assert spec.exact_seats == ["A1", "A2"] and spec.min_seats == 2 and spec.notify_once is True


def test_missing_required_field_raises():
    import pytest
    with pytest.raises(ValueError, match="theatre"):
        _spec_from_fields(_parse_kv("movie=Dune city=Pune date=2026-06-01"), Settings())


def test_empty_allowlist_authorises_nobody():
    assert Settings(allowed_telegram_user_ids="").allowed_user_ids == frozenset()
    assert Settings(allowed_telegram_user_ids="111, 222").allowed_user_ids == frozenset({111, 222})
    # A typo must not widen access.
    assert Settings(allowed_telegram_user_ids="111,oops").allowed_user_ids == frozenset({111})
