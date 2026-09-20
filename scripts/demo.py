"""One-command proof of the whole pipeline. Uses a throwaway DB, touches no network.

    python scripts/demo.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

# Add project root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings
from app.database import (
    Database, NotificationRepository, RunRepository, SnapshotRepository, SqliteWatchRepository,
)
from app.models import WatchSpec
from app.monitoring.change_detector import ChangeDetector
from app.monitoring.checker import AvailabilityChecker
from app.notifications.console import ConsoleNotifier
from app.sources.base import SourceRegistry
from app.sources.fake import FakeMovieSource
from app.utils.logging_setup import setup_logging


def build(db_path: Path, settings: Settings, source):
    db = Database(db_path)
    db.initialise()
    repos = (SqliteWatchRepository(db), SnapshotRepository(db),
             NotificationRepository(db), RunRepository(db))
    notifier = ConsoleNotifier(reason="demo")
    registry = SourceRegistry(test_mode=True)
    registry.register(source)
    checker = AvailabilityChecker(
        settings=settings, registry=registry, watch_repo=repos[0], snapshots=repos[1],
        notifications=repos[2], runs=repos[3], notifier=notifier,
        detector=ChangeDetector(error_notify_threshold=settings.error_notify_threshold),
    )
    return repos, notifier, checker


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    settings = Settings(database_path=tmp / "demo.db", log_file=tmp / "demo.log",
                        test_mode=True, dry_run=True, renotify_cooldown_seconds=0,
                        error_notify_threshold=2, telegram_bot_token="", telegram_chat_id="")
    settings.ensure_directories()
    setup_logging("INFO", settings.log_file)

    source = FakeMovieSource(("BOOKING_NOT_OPEN", "BOOKING_NOT_OPEN", "SEATS_AVAILABLE",
                              "SEATS_AVAILABLE", "SEATS_AVAILABLE"))
    (watches, snapshots, notifications, runs), notifier, checker = build(tmp / "demo.db", settings, source)

    good = watches.create(WatchSpec(
        movie_name="Demo Movie", city="Bengaluru", target_date=date.today() + timedelta(days=10),
        theatre="Demo Cinema", screen="Screen 1", showtime="19:30", seat_category="GOLD",
        min_seats=2, poll_interval_seconds=60, notify_once=False, source="fake"))
    bad = watches.create(WatchSpec(
        movie_name="Broken Watch", city="Bengaluru", target_date=date.today() + timedelta(days=10),
        theatre="Broken Cinema", poll_interval_seconds=60, notify_once=False, source="fake"))

    # The bad watch RAISES on every check; the good one must be unaffected.
    source.per_watch_scripts[bad.id] = ["ERROR"]
    source.raise_on_error_step = True

    print("\n=== STEP 1-5: five checks of each watch ===")
    for cycle in range(1, 6):
        for watch_id in (good.id, bad.id):
            watch = watches.get(watch_id)
            report = await checker.check(watch)
            print(f"  cycle {cycle} watch #{watch_id:<2} {report.decision.previous_state.value:>18}"
                  f" -> {report.state.value:<18} notified={report.notified}")

    assert notifications.count_for(good.id) == 1, "expected exactly one notification"
    assert watches.get(good.id).current_state.value == "SEATS_AVAILABLE"
    assert watches.get(bad.id).consecutive_failures >= 1
    print(f"\n  ✅ good watch: 1 notification, {snapshots.count_for(good.id)} snapshots")
    print(f"  ✅ bad watch failed {watches.get(bad.id).consecutive_failures}x "
          f"without stopping the good one")

    print("\n=== STEP 6: restart against the same database ===")
    (watches2, _, notifications2, _), notifier2, checker2 = build(
        tmp / "demo.db", settings, FakeMovieSource(("SEATS_AVAILABLE",)))
    reloaded = watches2.get(good.id)
    print(f"  remembered state after restart: {reloaded.current_state.value}")
    report = await checker2.check(reloaded)
    print(f"  re-check notified: {report.notified}  (must be False)")
    assert report.notified is False and notifications2.count_for(good.id) == 1

    print("\n🎉 Pipeline verified: create → fetch → normalize → persist → compare → "
          "one notification → no spam → restart-safe → failure-isolated.")
    print(f"   (demo data in {tmp}, safe to delete)\n")


if __name__ == "__main__":
    asyncio.run(main())
