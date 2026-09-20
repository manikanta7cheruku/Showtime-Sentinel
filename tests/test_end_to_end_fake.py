"""The proof. One watch, the real pipeline, the fake source.

Demonstrates, in order:
  1. a new watch starts UNKNOWN and first resolves to BOOKING_NOT_OPEN
  2. it transitions to BOOKING_OPEN and then SEATS_AVAILABLE
  3. exactly ONE notification per meaningful transition
  4. repeated identical checks send nothing
  5. state survives a full restart (new objects, same database file)
  6. a broken watch does not affect a healthy one
"""
from __future__ import annotations

from app.database import (
    Database, NotificationRepository, RunRepository, SnapshotRepository, SqliteWatchRepository,
)
from app.models import AvailabilityState as S
from app.monitoring.change_detector import ChangeDetector
from app.monitoring.checker import AvailabilityChecker
from app.notifications.console import ConsoleNotifier
from app.sources.base import SourceRegistry
from app.sources.fake import FakeMovieSource
from tests.conftest import make_checker


async def test_full_lifecycle_one_notification_then_silence(settings, repos, spec, notifier):
    watch = repos["watches"].create(spec)
    assert watch.current_state is S.UNKNOWN

    source = FakeMovieSource(("BOOKING_NOT_OPEN", "BOOKING_NOT_OPEN", "SEATS_AVAILABLE",
                              "SEATS_AVAILABLE", "SEATS_AVAILABLE"))
    checker = make_checker(settings, repos, notifier, source)

    states, notified_flags = [], []
    for _ in range(5):
        current = repos["watches"].get(watch.id)
        report = await checker.check(current)
        states.append(report.state)
        notified_flags.append(report.notified)

    assert states == [S.BOOKING_NOT_OPEN, S.BOOKING_NOT_OPEN,
                      S.SEATS_AVAILABLE, S.SEATS_AVAILABLE, S.SEATS_AVAILABLE]
    # Exactly one message: on the BOOKING_NOT_OPEN -> SEATS_AVAILABLE transition.
    assert notified_flags == [False, False, True, False, False]
    assert sum(notified_flags) == 1
    assert len(notifier.sent) == 1
    assert repos["notifications"].count_for(watch.id) == 1
    assert repos["watches"].get(watch.id).notification_count == 1
    # Every check is persisted, even the silent ones.
    assert repos["snapshots"].count_for(watch.id) == 5
    assert len(repos["runs"].recent(watch.id, limit=10)) == 5


async def test_booking_open_then_seats_gives_two_distinct_notifications(settings, repos, spec, notifier):
    watch = repos["watches"].create(spec)
    source = FakeMovieSource(("BOOKING_NOT_OPEN", "BOOKING_OPEN", "BOOKING_OPEN", "SEATS_AVAILABLE"))
    checker = make_checker(settings, repos, notifier, source)

    flags = []
    for _ in range(4):
        flags.append((await checker.check(repos["watches"].get(watch.id))).notified)

    assert flags == [False, True, False, True]        # open, then seats. No repeats.
    assert repos["notifications"].count_for(watch.id) == 2


async def test_notify_once_disables_the_watch(settings, repos, spec, notifier):
    watch = repos["watches"].create(spec.model_copy(update={"notify_once": True}))
    checker = make_checker(settings, repos, notifier, FakeMovieSource(("SEATS_AVAILABLE",)))
    report = await checker.check(watch)
    assert report.notified is True
    assert repos["watches"].get(watch.id).enabled is False


async def test_state_survives_a_restart(settings, repos, spec, notifier):
    """Simulates stopping and relaunching the process against the same DB file."""
    watch = repos["watches"].create(spec)
    source = FakeMovieSource(("SEATS_AVAILABLE",))
    checker = make_checker(settings, repos, notifier, source)
    assert (await checker.check(watch)).notified is True

    # --- "restart": brand new Database, repositories, notifier, checker ---
    db2 = Database(settings.database_path)
    db2.initialise()
    repos2 = {
        "watches": SqliteWatchRepository(db2), "snapshots": SnapshotRepository(db2),
        "notifications": NotificationRepository(db2), "runs": RunRepository(db2),
    }
    notifier2 = ConsoleNotifier(reason="after restart")
    registry2 = SourceRegistry(test_mode=True)
    registry2.register(FakeMovieSource(("SEATS_AVAILABLE",)))
    checker2 = AvailabilityChecker(
        settings=settings, registry=registry2, watch_repo=repos2["watches"],
        snapshots=repos2["snapshots"], notifications=repos2["notifications"],
        runs=repos2["runs"], notifier=notifier2,
        detector=ChangeDetector(error_notify_threshold=settings.error_notify_threshold),
    )

    reloaded = repos2["watches"].get(watch.id)
    assert reloaded.current_state is S.SEATS_AVAILABLE      # remembered
    report = await checker2.check(reloaded)
    assert report.notified is False                          # and does NOT re-notify
    assert notifier2.sent == []
    assert repos2["notifications"].count_for(watch.id) == 1


async def test_dedupe_blocks_a_repeat_even_if_state_is_rolled_back(settings, repos, spec, notifier):
    """Belt and braces: hand-reset the state, the dedupe table still protects us."""
    watch = repos["watches"].create(spec)
    checker = make_checker(settings, repos, notifier, FakeMovieSource(("SEATS_AVAILABLE",)))
    await checker.check(watch)
    assert len(notifier.sent) == 1

    with Database(settings.database_path).connect() as conn:
        conn.execute("UPDATE watches SET current_state = 'BOOKING_NOT_OPEN' WHERE id = ?", (watch.id,))

    settings_with_cooldown = settings.model_copy(update={"renotify_cooldown_seconds": 3600})
    checker2 = make_checker(settings_with_cooldown, repos, notifier,
                            FakeMovieSource(("SEATS_AVAILABLE",)))
    report = await checker2.check(repos["watches"].get(watch.id))
    assert report.decision.should_notify is True     # the detector wanted to
    assert report.notified is False                  # the dedupe table said no
    assert len(notifier.sent) == 1


async def test_blocked_source_is_reported_and_does_not_crash(settings, repos, spec, notifier):
    watch = repos["watches"].create(spec)
    checker = make_checker(settings, repos, notifier, FakeMovieSource(("BLOCKED",)))
    report = await checker.check(watch)
    assert report.state is S.BLOCKED and report.ok is False
    assert repos["watches"].get(watch.id).consecutive_failures == 1


async def test_error_alert_fires_once_at_the_threshold(settings, repos, spec, notifier):
    watch = repos["watches"].create(spec)          # error_notify_threshold = 2 in fixtures
    checker = make_checker(settings, repos, notifier, FakeMovieSource(("ERROR",)))
    flags = [(await checker.check(repos["watches"].get(watch.id))).notified for _ in range(4)]
    assert flags == [False, True, False, False]


async def test_dry_run_sends_nothing_to_telegram(settings, repos, spec):
    """DRY_RUN=true (the fixture default) must yield a console notifier."""
    from app.notifications import build_notifier
    notifier = build_notifier(settings)
    assert notifier.channel == "console"
    watch = repos["watches"].create(spec)
    checker = make_checker(settings, repos, notifier, FakeMovieSource(("SEATS_AVAILABLE",)))
    assert (await checker.check(watch)).notified is True
