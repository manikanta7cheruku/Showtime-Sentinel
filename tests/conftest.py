"""Shared fixtures. Every test uses a throwaway SQLite file and the fake source."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

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


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="", telegram_chat_id="", allowed_telegram_user_ids="42",
        test_mode=True, dry_run=True,
        database_path=tmp_path / "test.db", log_file=tmp_path / "test.log",
        default_poll_interval_seconds=60, min_poll_interval_seconds=5,
        max_retries=3, backoff_base_seconds=0.01, backoff_max_seconds=0.05,
        error_notify_threshold=2, renotify_cooldown_seconds=0, enable_bookmyshow_source=False,
    )


@pytest.fixture
def db(settings) -> Database:
    database = Database(settings.database_path)
    database.initialise()
    return database


@pytest.fixture
def repos(db):
    return {
        "watches": SqliteWatchRepository(db),
        "snapshots": SnapshotRepository(db),
        "notifications": NotificationRepository(db),
        "runs": RunRepository(db),
    }


@pytest.fixture
def spec() -> WatchSpec:
    return WatchSpec(
        movie_name="Interstellar", city="Bengaluru",
        target_date=date.today() + timedelta(days=7),
        theatre="PVR Forum Mall", screen="Screen 3", showtime="19:30",
        seat_category="GOLD", min_seats=2, poll_interval_seconds=60,
        notify_once=False, source="fake",
    )


@pytest.fixture
def notifier() -> ConsoleNotifier:
    return ConsoleNotifier(reason="test")


def make_checker(settings, repos, notifier, source) -> AvailabilityChecker:
    registry = SourceRegistry(test_mode=True)
    registry.register(source)
    return AvailabilityChecker(
        settings=settings, registry=registry, watch_repo=repos["watches"],
        snapshots=repos["snapshots"], notifications=repos["notifications"],
        runs=repos["runs"], notifier=notifier,
        detector=ChangeDetector(error_notify_threshold=settings.error_notify_threshold),
    )


@pytest.fixture
def checker(settings, repos, notifier):
    return make_checker(settings, repos, notifier, FakeMovieSource())