"""CLI entry point and dependency wiring.

Read `build_app()` first: it is a one-screen map of the entire system.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.database import (
    Database, NotificationRepository, RunRepository, SnapshotRepository, SqliteWatchRepository,
)
from app.models import ABSOLUTE_MIN_INTERVAL, WatchSpec
from app.monitoring.change_detector import ChangeDetector
from app.monitoring.checker import AvailabilityChecker
from app.monitoring.scheduler import Scheduler, install_signal_handlers
from app.notifications import build_notifier
from app.notifications.base import NotificationService
from app.sources import SCENARIOS, FakeMovieSource, SourceRegistry, build_registry
from app.utils.logging_setup import setup_logging
from app.utils.timeutil import parse_date, to_display

logger = logging.getLogger(__name__)


@dataclass
class AppContainer:
    settings: Settings
    db: Database
    watch_repo: SqliteWatchRepository
    snapshots: SnapshotRepository
    notifications: NotificationRepository
    runs: RunRepository
    notifier: NotificationService
    registry: SourceRegistry
    checker: AvailabilityChecker


def build_app(settings: Settings | None = None) -> AppContainer:
    """Wire every component together. The whole architecture, in one function."""
    settings = settings or get_settings()
    settings.ensure_directories()
    setup_logging(settings.log_level, settings.log_file)

    db = Database(settings.database_path)
    db.initialise()

    watch_repo = SqliteWatchRepository(db)
    snapshots = SnapshotRepository(db)
    notifications = NotificationRepository(db)
    runs = RunRepository(db)

    notifier = build_notifier(settings)
    registry = build_registry(settings)

    checker = AvailabilityChecker(
        settings=settings, registry=registry, watch_repo=watch_repo,
        snapshots=snapshots, notifications=notifications, runs=runs,
        notifier=notifier, detector=ChangeDetector(error_notify_threshold=settings.error_notify_threshold),
    )
    return AppContainer(settings, db, watch_repo, snapshots, notifications,
                        runs, notifier, registry, checker)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_add_watch(app: AppContainer, args) -> int:
    interactive = args.movie is None

    def ask(prompt: str, default: str | None = None, required: bool = False) -> str | None:
        suffix = f" [{default}]" if default else ""
        while True:
            value = input(f"{prompt}{suffix}: ").strip() or (default or "")
            if value or not required:
                return value or None
            print("  ! required")

    if interactive:
        print("\n— New watch (blank = skip optional fields) —")
        movie = ask("Movie name", required=True)
        city = ask("City", required=True)
        date_str = ask("Date (YYYY-MM-DD)", required=True)
        theatre = ask("Theatre", required=True)
        screen = ask("Screen (optional)")
        showtime = ask("Exact showtime HH:MM (optional)")
        time_from = ask("Earliest showtime HH:MM (optional)") if not showtime else None
        time_to = ask("Latest showtime HH:MM (optional)") if not showtime else None
        category = ask("Seat category (optional)")
        seat_ids = ask("Exact seat ids, comma separated (optional)")
        min_seats = ask("Minimum seats", "1")
        interval = ask("Poll interval seconds", str(app.settings.default_poll_interval_seconds))
        source = ask("Source (fake/bookmyshow)", "fake")
        url = ask("Verified showtimes URL (optional, required for bookmyshow)")
        notify_once = (ask("Notify once then stop? (y/n)", "y") or "y").lower().startswith("y")
    else:
        movie, city, date_str, theatre = args.movie, args.city, args.date, args.theatre
        screen, showtime = args.screen, args.time
        time_from, time_to = args.time_from, args.time_to
        category, seat_ids = args.category, args.seat_ids
        min_seats = str(args.min_seats)
        interval = str(args.interval or app.settings.default_poll_interval_seconds)
        source, url, notify_once = args.source, args.source_url, not args.keep_monitoring

    try:
        spec = WatchSpec(
            movie_name=movie, city=city, target_date=parse_date(date_str), theatre=theatre,
            screen=screen or None, showtime=showtime or None,
            time_from=time_from or None, time_to=time_to or None,
            seat_category=category or None, exact_seats=seat_ids or "",
            min_seats=int(min_seats or 1),
            poll_interval_seconds=max(int(interval or 60),
                                      max(app.settings.min_poll_interval_seconds, ABSOLUTE_MIN_INTERVAL)),
            notify_once=notify_once, source=source or "fake", source_url=url or None,
        )
    except Exception as exc:                              # noqa: BLE001
        print(f"❌ Invalid watch: {exc}")
        return 2

    if spec.source == "bookmyshow" and not spec.source_url:
        print("❌ The bookmyshow source requires --source-url: a showtimes URL you opened\n"
              "   and verified in your own browser. This project never guesses URLs.")
        return 2

    watch = app.watch_repo.create(spec)
    print(f"✅ Created watch #{watch.id}: {watch.describe()}")

    # Send a confirmation to the user's Telegram so they know the watch is active.
    confirmation = (
        f"🎬 New watch created!\n\n"
        f"Movie: {watch.movie_name}\n"
        f"City: {watch.city}\n"
        f"Date: {watch.target_date}\n"
        f"Theatre: {watch.theatre}\n"
        f"Screen: {watch.screen or 'Any'}\n"
        f"Showtime: {watch.showtime or 'Any'}\n"
        f"Min seats: {watch.min_seats}\n"
        f"Source: {watch.source}\n\n"
        f"I will notify you the moment tickets open. "
        f"Sit back and relax."
    )
    try:
        asyncio.run(app.notifier.send_message(confirmation))
    except Exception:
        pass  # Confirmation is best-effort; the watch is created regardless.

    return 0


def cmd_list_watches(app: AppContainer, _args) -> int:
    watches = app.watch_repo.list_watches()
    if not watches:
        print("No watches. Create one with:  python -m app.main add-watch")
        return 0
    print(f"\n{'ID':<4}{'ST':<4}{'STATE':<20}{'MOVIE':<24}{'THEATRE':<24}{'DATE':<12}{'EVERY':<8}NOTIFS")
    print("-" * 104)
    for w in watches:
        print(f"{w.id:<4}{'▶' if w.enabled else '⏸':<4}{w.current_state.value:<20}"
              f"{w.movie_name[:22]:<24}{w.theatre[:22]:<24}{str(w.target_date):<12}"
              f"{str(w.poll_interval_seconds) + 's':<8}{w.notification_count}")
    print()
    return 0


def cmd_remove_watch(app: AppContainer, args) -> int:
    ok = app.watch_repo.delete(args.watch_id)
    print(f"🗑️  Deleted watch #{args.watch_id}" if ok else f"❌ No watch #{args.watch_id}")
    return 0 if ok else 1


def cmd_enable_watch(app: AppContainer, args) -> int:
    ok = app.watch_repo.set_enabled(args.watch_id, True)
    print(f"▶️  Watch #{args.watch_id} enabled" if ok else f"❌ No watch #{args.watch_id}")
    return 0 if ok else 1


def cmd_disable_watch(app: AppContainer, args) -> int:
    ok = app.watch_repo.set_enabled(args.watch_id, False)
    print(f"⏸️  Watch #{args.watch_id} disabled" if ok else f"❌ No watch #{args.watch_id}")
    return 0 if ok else 1


def cmd_check(app: AppContainer, args) -> int:
    watch = app.watch_repo.get(args.watch_id)
    if watch is None:
        print(f"❌ No watch #{args.watch_id}")
        return 1
    report = asyncio.run(_check_once(app, watch))
    print(f"\nWatch #{watch.id}: {watch.describe()}")
    print(f"  state      : {report.state.value}")
    print(f"  transition : {report.decision.label}")
    print(f"  reason     : {report.decision.reason}")
    print(f"  notified   : {report.notified}")
    print(f"  hash       : {report.result.availability_hash[:16]}")
    print(f"  checked at : {to_display(report.result.checked_at)}\n")
    return 0


async def _check_once(app: AppContainer, watch):
    try:
        return await app.checker.check(watch)
    finally:
        await app.registry.aclose_all()
        await app.notifier.aclose()


def cmd_test_notification(app: AppContainer, _args) -> int:
    async def run():
        try:
            return await app.notifier.send_test_message()
        finally:
            await app.notifier.aclose()

    sent = asyncio.run(run())
    print(f"✅ Test notification sent via {app.notifier.channel}" if sent
          else "❌ Failed — check the log for the Telegram error")
    return 0 if sent else 1


def cmd_status(app: AppContainer, _args) -> int:
    total, active = app.watch_repo.count()
    print("\n── Movie Ticket Monitor status ──")
    print(f"  database        : {app.settings.database_path} ({'ok' if app.db.healthcheck() else 'PROBLEM'})")
    print(f"  watches         : {total} total, {active} active")
    print(f"  TEST_MODE       : {app.settings.test_mode}")
    print(f"  DRY_RUN         : {app.settings.dry_run}")
    print(f"  notifier        : {app.notifier.channel}")
    print(f"  sources         : {', '.join(app.registry.names())}")
    print(f"  bookmyshow      : {'ENABLED' if app.settings.enable_bookmyshow_source else 'disabled'}")
    print(f"  default interval: {app.settings.default_poll_interval_seconds}s")
    print(f"  allowlisted ids : {len(app.settings.allowed_user_ids) or 'NONE (bot commands blocked)'}")
    for w in app.watch_repo.list_watches():
        print(f"\n  #{w.id} {w.movie_name} — {w.current_state.value}")
        print(f"      last check   : {to_display(w.last_checked_at)}")
        print(f"      last success : {to_display(w.last_success_at)}")
        print(f"      failures     : {w.consecutive_failures}   notifications: {w.notification_count}")
        if w.last_error:
            print(f"      last error   : {w.last_error[:100]}")
    print()
    return 0


def cmd_run(app: AppContainer, args) -> int:
    from app.bot.commands import BotContext
    from app.bot.runner import run_bot_and_scheduler

    if args.dashboard:
        from app.web import start_dashboard
        start_dashboard(app.settings, app.watch_repo, app.notifications)

    scheduler = Scheduler(settings=app.settings, watch_repo=app.watch_repo, checker=app.checker)
    bot_ctx = BotContext(
        settings=app.settings, watch_repo=app.watch_repo, snapshots=app.snapshots,
        notifications=app.notifications, runs=app.runs, checker=app.checker, notifier=app.notifier,
    )

    async def main() -> None:
        install_signal_handlers(scheduler)
        print("\n🎬 Monitoring started. Press Ctrl+C to stop.\n"
              f"   Detection latency is roughly your interval "
              f"({app.settings.default_poll_interval_seconds}s) plus fetch/parse/delivery time.\n")
        try:
            if args.no_bot:
                await scheduler.run()
            else:
                await run_bot_and_scheduler(bot_ctx, scheduler)
        finally:
            await app.registry.aclose_all()
            await app.notifier.aclose()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Stopped.")
    return 0


def cmd_simulate(app: AppContainer, args) -> int:
    """Prove the whole pipeline with the fake source, end to end."""
    scenario = args.scenario
    if scenario not in SCENARIOS:
        print(f"❌ Unknown scenario. Available: {', '.join(sorted(SCENARIOS))}")
        return 2

    watch = app.watch_repo.get(args.watch_id) if args.watch_id else None
    if watch is None:
        existing = app.watch_repo.list_watches()
        watch = existing[0] if existing else app.watch_repo.create(WatchSpec(
            movie_name="Simulation Movie", city="Bengaluru",
            target_date=parse_date("2026-12-25"), theatre="Demo Cinema",
            screen="Screen 1", showtime="19:30", seat_category="GOLD",
            min_seats=2, poll_interval_seconds=60, notify_once=False, source="fake",
        ))
        print(f"using watch #{watch.id}: {watch.describe()}")

    if not watch.enabled:
        app.watch_repo.set_enabled(watch.id, True)

    fake = FakeMovieSource(SCENARIOS[scenario])
    registry = SourceRegistry(test_mode=True)
    registry.register(fake)
    app.checker.registry = registry

    async def run():
        print(f"\n── simulating '{scenario}' × {args.cycles} checks ──\n")
        notifications = 0
        for cycle in range(1, args.cycles + 1):
            current = app.watch_repo.get(watch.id)
            if current is None:
                break
            if cycle > 1 and not current.enabled:
                print(f"cycle {cycle}: watch disabled (notify_once) — nothing more to do")
                break
            report = await app.checker.check(current)
            notifications += int(report.notified)
            print(f"cycle {cycle}: {report.decision.previous_state.value:>18} -> "
                  f"{report.state.value:<18} notified={str(report.notified):<5} "
                  f"hash={report.result.availability_hash[:10]}")
        print(f"\n✅ {args.cycles} checks produced {notifications} notification(s) "
              f"and {app.snapshots.count_for(watch.id)} stored snapshot(s) total.")
        print("   Repeated identical states produce NO extra messages — that is the "
              "change detector plus the dedupe table working.\n")
        await app.notifier.aclose()

    asyncio.run(run())
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.main",
        description="Educational movie-ticket availability monitor (notification only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add-watch", help="create a watch (interactive if no flags given)")
    add.add_argument("--movie"); add.add_argument("--city"); add.add_argument("--date")
    add.add_argument("--theatre"); add.add_argument("--screen")
    add.add_argument("--time", help="exact showtime HH:MM")
    add.add_argument("--time-from"); add.add_argument("--time-to")
    add.add_argument("--category"); add.add_argument("--seat-ids", help="comma separated")
    add.add_argument("--min-seats", type=int, default=1)
    add.add_argument("--interval", type=int)
    add.add_argument("--source", default="fake", choices=["fake", "bookmyshow"])
    add.add_argument("--source-url", help="a showtimes URL you verified in your browser")
    add.add_argument("--keep-monitoring", action="store_true",
                     help="keep notifying after the first hit (default: notify once)")
    add.set_defaults(func=cmd_add_watch)

    sub.add_parser("list-watches", help="list all watches").set_defaults(func=cmd_list_watches)

    for name, func, helptext in (
        ("remove-watch", cmd_remove_watch, "delete a watch and its history"),
        ("enable-watch", cmd_enable_watch, "resume a watch"),
        ("disable-watch", cmd_disable_watch, "pause a watch"),
        ("check", cmd_check, "run one check now"),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("watch_id", type=int)
        p.set_defaults(func=func)

    sub.add_parser("test-notification", help="send a test message").set_defaults(func=cmd_test_notification)
    sub.add_parser("status", help="show configuration and watch status").set_defaults(func=cmd_status)

    run = sub.add_parser("run", help="start the monitor (and Telegram bot)")
    run.add_argument("--no-bot", action="store_true", help="scheduler only")
    run.add_argument("--dashboard", action="store_true", help="also serve the localhost dashboard")
    run.set_defaults(func=cmd_run)

    sim = sub.add_parser("simulate", help="drive the pipeline with the fake source")
    sim.add_argument("scenario", nargs="?", default="booking-open", choices=sorted(SCENARIOS))
    sim.add_argument("--watch-id", type=int)
    sim.add_argument("--cycles", type=int, default=6)
    sim.set_defaults(func=cmd_simulate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    app = build_app()
    return args.func(app, args)


if __name__ == "__main__":
    sys.exit(main())
