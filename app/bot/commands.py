"""Telegram command handlers.

SECURITY, PLEASE READ: every handler goes through `authorised()`. If
ALLOWED_TELEGRAM_USER_IDS is empty we deny EVERYONE (fail-closed). The
alternative - treating "empty" as "allow all" - would mean anyone who finds
your bot's @username can list your watches, change intervals and delete your
data, because Telegram bot usernames are discoverable by design. An empty
allowlist is therefore a configuration error, not an open-door policy.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes, ConversationHandler, MessageHandler, filters,
)

from app.config import Settings
from app.database.repositories import (
    NotificationRepository, RunRepository, SnapshotRepository, SqliteWatchRepository,
)
from app.models import ABSOLUTE_MIN_INTERVAL, WatchSpec
from app.monitoring.checker import AvailabilityChecker
from app.notifications.base import NotificationService
from app.notifications.formatter import format_watch_row
from app.sources.fake import SCENARIOS, FakeMovieSource
from app.utils.timeutil import parse_date, to_display

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Conversation states for the interactive /watch flow
# --------------------------------------------------------------------------- #
(
    ASK_MOVIE,
    ASK_CITY,
    ASK_DATE,
    ASK_THEATRE,
    ASK_SCREEN,
    ASK_FORMAT,
    ASK_TIMING,
    ASK_SOURCE,
    ASK_URL,
) = range(9)

HELP = """<b>Movie Ticket Monitor — commands</b>

/start – register and show your chat/user id
/help – this message
/watch – add a new watch (interactive step-by-step)
/watches – list all watches
/status – process and database summary
/pause &lt;id&gt; – stop checking a watch
/resume &lt;id&gt; – start checking it again
/remove &lt;id&gt; – delete it and its history
/setinterval &lt;id&gt; &lt;seconds&gt; – change polling interval
/test – am I authorised?
/test_notification – send a test notification
/simulate &lt;scenario&gt; [id] – run the fake source once (scenarios: {scenarios})
/cancel – cancel the current /watch conversation

<i>Notification only. This bot never books tickets and never touches payments.</i>
"""

TIMING_PRESETS = {
    "morning":   ("06:00", "12:00"),
    "afternoon": ("12:00", "17:00"),
    "evening":   ("17:00", "21:00"),
    "night":     ("21:00", "23:59"),
    "any":       (None, None),
    "all":       (None, None),
}


@dataclass
class BotContext:
    """Everything the handlers need, injected once - no globals."""
    settings: Settings
    watch_repo: SqliteWatchRepository
    snapshots: SnapshotRepository
    notifications: NotificationRepository
    runs: RunRepository
    checker: AvailabilityChecker
    notifier: NotificationService


def _authorised(ctx: BotContext, update: Update) -> bool:
    user = update.effective_user
    allowed = ctx.settings.allowed_user_ids
    if not allowed:
        logger.warning(
            "denied command from user %s: ALLOWED_TELEGRAM_USER_IDS is empty (fail-closed)",
            user.id if user else "?",
        )
        return False
    if user is None or user.id not in allowed:
        logger.warning("denied command from unauthorised user %s", user.id if user else "?")
        return False
    return True


async def _deny(update: Update) -> None:
    user_id = update.effective_user.id if update.effective_user else "unknown"
    await update.effective_message.reply_text(
        "Not authorised.\n\n"
        f"Your Telegram user id is: {user_id}\n"
        "Add it to ALLOWED_TELEGRAM_USER_IDS in .env and restart the bot.\n"
        "(An empty allowlist blocks everyone on purpose.)"
    )


def _guard(ctx: BotContext, handler):
    async def wrapper(update: Update, tg_ctx: ContextTypes.DEFAULT_TYPE):
        if not _authorised(ctx, update):
            await _deny(update)
            return
        try:
            await handler(ctx, update, tg_ctx)
        except Exception as exc:                          # noqa: BLE001
            logger.exception("command handler failed")
            await update.effective_message.reply_text(f"Command failed: {type(exc).__name__}: {exc}")
    return wrapper


# --------------------------------------------------------------------------- #
# Simple commands
# --------------------------------------------------------------------------- #
async def cmd_start(ctx, update, _tg):
    user = update.effective_user
    await update.effective_message.reply_text(
        f"Hello {user.first_name if user else 'there'}!\n\n"
        f"Your user id: <code>{user.id}</code>\n"
        f"This chat id: <code>{update.effective_chat.id}</code>\n\n"
        "Put those in .env as ALLOWED_TELEGRAM_USER_IDS and TELEGRAM_CHAT_ID.\n"
        "Send /help for the command list.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_help(ctx, update, _tg):
    await update.effective_message.reply_text(
        HELP.format(scenarios=", ".join(sorted(SCENARIOS))), parse_mode=ParseMode.HTML
    )


async def cmd_watches(ctx, update, _tg):
    watches = ctx.watch_repo.list_watches()
    if not watches:
        await update.effective_message.reply_text("No watches yet. Add one with /watch.")
        return
    chunks, current = [], ""
    for watch in watches:
        row = format_watch_row(watch) + "\n\n"
        if len(current) + len(row) > 3500:
            chunks.append(current)
            current = ""
        current += row
    chunks.append(current)
    for chunk in chunks:
        await update.effective_message.reply_text(chunk, parse_mode=ParseMode.HTML)


async def cmd_status(ctx, update, _tg):
    total, active = ctx.watch_repo.count()
    recent = ctx.runs.recent(limit=1)
    last_run = to_display(__import__("app.utils.timeutil", fromlist=["parse_iso_utc"])
                          .parse_iso_utc(recent[0]["started_at"])) if recent else "never"
    await update.effective_message.reply_text(
        "<b>Status</b>\n"
        f"Watches: {total} ({active} active)\n"
        f"Last check started: {last_run}\n"
        f"TEST_MODE: {ctx.settings.test_mode} | DRY_RUN: {ctx.settings.dry_run}\n"
        f"Notifier: {ctx.notifier.channel}\n"
        f"BookMyShow adapter: {'enabled' if ctx.settings.enable_bookmyshow_source else 'disabled'}\n"
        f"Default interval: {ctx.settings.default_poll_interval_seconds}s",
        parse_mode=ParseMode.HTML,
    )


async def cmd_pause(ctx, update, tg):
    await _toggle(ctx, update, tg, enabled=False)


async def cmd_resume(ctx, update, tg):
    await _toggle(ctx, update, tg, enabled=True)


async def _toggle(ctx, update, tg, *, enabled: bool):
    watch_id = _int_arg(tg.args, 0)
    if watch_id is None:
        await update.effective_message.reply_text("Usage: /pause <id>  or  /resume <id>")
        return
    ok = ctx.watch_repo.set_enabled(watch_id, enabled)
    verb = "resumed" if enabled else "paused"
    await update.effective_message.reply_text(
        f"Watch #{watch_id} {verb}" if ok else f"No watch #{watch_id}"
    )


async def cmd_remove(ctx, update, tg):
    watch_id = _int_arg(tg.args, 0)
    if watch_id is None:
        await update.effective_message.reply_text("Usage: /remove <id>")
        return
    ok = ctx.watch_repo.delete(watch_id)
    await update.effective_message.reply_text(
        f"Watch #{watch_id} and its history deleted" if ok else f"No watch #{watch_id}"
    )


async def cmd_setinterval(ctx, update, tg):
    watch_id, seconds = _int_arg(tg.args, 0), _int_arg(tg.args, 1)
    if watch_id is None or seconds is None:
        await update.effective_message.reply_text("Usage: /setinterval <id> <seconds>")
        return
    floor = max(ctx.settings.min_poll_interval_seconds, ABSOLUTE_MIN_INTERVAL)
    if seconds < floor:
        await update.effective_message.reply_text(
            f"Minimum interval is {floor}s. Polling faster is rude to the website "
            "and gets you blocked; it does not make you first in the queue."
        )
        return
    ok = ctx.watch_repo.set_interval(watch_id, seconds)
    await update.effective_message.reply_text(
        f"Watch #{watch_id} now polls every {seconds}s" if ok else f"No watch #{watch_id}"
    )


async def cmd_test(ctx, update, _tg):
    await update.effective_message.reply_text("Authorised. The bot is alive and listening.")


async def cmd_test_notification(ctx, update, _tg):
    sent = await ctx.notifier.send_test_message()
    await update.effective_message.reply_text(
        f"Test notification sent via {ctx.notifier.channel}." if sent
        else "Sending failed. Check the logs."
    )


async def cmd_simulate(ctx, update, tg):
    scenario = (tg.args[0] if tg.args else "booking-open").lower()
    if scenario not in SCENARIOS:
        await update.effective_message.reply_text(
            f"Unknown scenario. Try: {', '.join(sorted(SCENARIOS))}"
        )
        return
    watch_id = _int_arg(tg.args, 1)
    watches = ctx.watch_repo.list_watches()
    watch = ctx.watch_repo.get(watch_id) if watch_id else (watches[0] if watches else None)
    if watch is None:
        await update.effective_message.reply_text("No watch to simulate against. Add one with /watch.")
        return

    fake = FakeMovieSource(SCENARIOS[scenario])
    original = ctx.checker.registry
    from app.sources.base import SourceRegistry
    temp = SourceRegistry(test_mode=True)
    temp.register(fake)
    ctx.checker.registry = temp
    try:
        report = await ctx.checker.check(watch)
    finally:
        ctx.checker.registry = original

    await update.effective_message.reply_text(
        f"Scenario <b>{scenario}</b> on watch #{watch.id}\n"
        f"State: <b>{report.state.value}</b>\n"
        f"Transition: {report.decision.label}\n"
        f"Reason: {report.decision.reason}\n"
        f"Notification sent: {report.notified}",
        parse_mode=ParseMode.HTML,
    )


# --------------------------------------------------------------------------- #
# Interactive /watch conversation
# --------------------------------------------------------------------------- #
async def _watch_entry(ctx, update, tg):
    """Entry point for /watch. If key=value args are given, parse directly."""
    if not _authorised(ctx, update):
        await _deny(update)
        return ConversationHandler.END

    # Fast path: key=value syntax in one message
    if tg.args:
        fields = _parse_kv(" ".join(tg.args))
        try:
            spec = _spec_from_fields(fields, ctx.settings)
        except Exception as exc:
            await update.effective_message.reply_text(f"Could not parse that: {exc}")
            return ConversationHandler.END
        watch = ctx.watch_repo.create(spec)
        await update.effective_message.reply_text(
            f"Watch created.\n\n{format_watch_row(watch)}", parse_mode=ParseMode.HTML
        )
        return ConversationHandler.END

    # Interactive path: ask step by step
    tg.user_data.clear()
    await update.effective_message.reply_text(
        "Let's set up a new watch.\n\n"
        "What movie do you want to monitor?\n"
        "(e.g., Avengers: Endgame Encore)"
    )
    return ASK_MOVIE


async def _receive_movie(update, tg):
    tg.user_data["movie"] = update.message.text.strip()
    await update.message.reply_text("Which city?\n(e.g., Hyderabad)")
    return ASK_CITY


async def _receive_city(update, tg):
    tg.user_data["city"] = update.message.text.strip()
    await update.message.reply_text("Which date? (YYYY-MM-DD)\n(e.g., 2026-09-26)")
    return ASK_DATE


async def _receive_date(update, tg):
    text = update.message.text.strip()
    try:
        parse_date(text)
    except Exception:
        await update.message.reply_text(
            "That doesn't look like a valid date. Please use YYYY-MM-DD format.\n"
            "(e.g., 2026-09-26)"
        )
        return ASK_DATE
    tg.user_data["date"] = text
    await update.message.reply_text("Which theatre?\n(e.g., Prasads)")
    return ASK_THEATRE


async def _receive_theatre(update, tg):
    tg.user_data["theatre"] = update.message.text.strip()
    await update.message.reply_text(
        "Which screen?\n"
        "(e.g., PCX, IMAX, 4DX, or type 'any' for all screens)"
    )
    return ASK_SCREEN


async def _receive_screen(update, tg):
    text = update.message.text.strip()
    tg.user_data["screen"] = None if text.lower() in ("any", "all", "-") else text
    await update.message.reply_text(
        "Which format/screen type?\n\n"
        "  EPIQ   (premium large format)\n"
        "  IMAX\n"
        "  4DX\n"
        "  3D\n"
        "  2D\n"
        "  PCX    (Prasads premium)\n"
        "  any    (all formats)\n"
        "  or type a specific format name"
    )
    return ASK_FORMAT


async def _receive_format(update, tg):
    text = update.message.text.strip()
    tg.user_data["format"] = None if text.lower() in ("any", "all", "-") else text.upper()
    await update.message.reply_text(
        "Preferred timings?\n\n"
        "  morning   (6 AM - 12 PM)\n"
        "  afternoon (12 PM - 5 PM)\n"
        "  evening   (5 PM - 9 PM)\n"
        "  night     (9 PM - midnight)\n"
        "  any       (all showtimes)\n"
        "  or type a range like 18:00-22:00"
    )
    return ASK_TIMING


async def _receive_timing(update, tg):
    text = update.message.text.strip().lower()

    if text in TIMING_PRESETS:
        time_from, time_to = TIMING_PRESETS[text]
    elif "-" in text:
        parts = text.split("-")
        if len(parts) == 2:
            time_from = parts[0].strip()
            time_to = parts[1].strip()
        else:
            await update.message.reply_text(
                "Invalid format. Use 'morning', 'evening', 'any', or a range like 18:00-22:00"
            )
            return ASK_TIMING
    else:
        await update.message.reply_text(
            "Invalid timing. Choose: morning, afternoon, evening, night, any, "
            "or a range like 18:00-22:00"
        )
        return ASK_TIMING

    tg.user_data["time_from"] = time_from
    tg.user_data["time_to"] = time_to

    await update.message.reply_text(
        "Which source?\n"
        "  fake      (demo/testing)\n"
        "  bookmyshow (real, requires a verified URL)"
    )
    return ASK_SOURCE


async def _receive_source(update, tg):
    text = update.message.text.strip().lower()
    if text not in ("fake", "bookmyshow"):
        await update.message.reply_text("Please type 'fake' or 'bookmyshow'.")
        return ASK_SOURCE
    tg.user_data["source"] = text

    if text == "bookmyshow":
        await update.message.reply_text(
            "Paste the BookMyShow URL you verified in your browser.\n"
            "(Open the movie page, select the date, and copy the full URL from the address bar.)"
        )
        return ASK_URL

    return await _create_watch(update, tg)


async def _receive_url(update, tg):
    import re
    text = update.message.text.strip()

    # User may have replied to a date mismatch prompt
    if text.lower() in ("use url date", "use", "keep", "yes", "proceed") and "pending_url" in tg.user_data:
        tg.user_data["url"] = tg.user_data.pop("pending_url")
        tg.user_data["date"] = tg.user_data.pop("mismatched_url_date")
        return await _create_watch(update, tg)

    url = text
    if not url.startswith("http"):
        await update.message.reply_text(
            "That doesn't look like a URL. Please paste the full https://... link."
        )
        return ASK_URL

    # Validate URL date against user-specified date
    url_date_match = re.search(r'/(\d{8})', url)
    if url_date_match:
        url_date_str = url_date_match.group(1)
        user_date_str = tg.user_data.get("date", "").replace("-", "")
        if user_date_str and url_date_str != user_date_str:
            correct_date = f"{url_date_str[:4]}-{url_date_str[4:6]}-{url_date_str[6:8]}"
            tg.user_data["pending_url"] = url
            tg.user_data["mismatched_url_date"] = correct_date
            await update.message.reply_text(
                f"⚠️ <b>Date Mismatch Detected!</b>\n\n"
                f"• Target date you entered: <b>{tg.user_data['date']}</b>\n"
                f"• Date in pasted URL: <b>{correct_date}</b>\n\n"
                f"What would you like to do?\n"
                f"1. <b>Paste the correct URL</b> for {tg.user_data['date']}\n"
                f"2. Reply <b>use url date</b> to monitor {correct_date} instead\n"
                f"3. Send /cancel to start over",
                parse_mode=ParseMode.HTML,
            )
            return ASK_URL

    # Detect URL type and guide the user
    url_lower = url.lower()
    if "/buytickets/" in url_lower:
        url_kind = "buy-tickets"
    elif "/movies/" in url_lower:
        url_kind = "movie"
    elif "/cinemas/" in url_lower or "/venues/" in url_lower:
        url_kind = "venue"
    else:
        url_kind = "unknown"

    if url_kind == "venue":
        await update.message.reply_text(
            f"ℹ️ <b>Venue URL detected</b>\n\n"
            f"You pasted a cinema page. I will scan it for "
            f"'<b>{tg.user_data.get('movie', '')}</b>' and monitor it at "
            f"'<b>{tg.user_data.get('theatre', '')}</b>'.\n\n"
            f"If the movie is not currently listed at this venue (tickets not open yet), "
            f"I will keep checking every 60 seconds and notify you the moment it appears.",
            parse_mode=ParseMode.HTML,
        )
    elif url_kind == "movie":
        await update.message.reply_text(
            f"ℹ️ <b>Movie page URL detected</b>\n\n"
            f"I will monitor this movie and filter for shows at "
            f"'<b>{tg.user_data.get('theatre', '')}</b>'.\n\n"
            f"When BookMyShow adds showtimes at your chosen theatre, "
            f"you will get an instant notification.",
            parse_mode=ParseMode.HTML,
        )

    tg.user_data["url"] = url
    return await _create_watch(update, tg)


async def _create_watch(update, tg):
    """Build the WatchSpec from collected data and persist it."""
    d = tg.user_data
    screen = d.get("screen")
    fmt = d.get("format")
    time_from = d.get("time_from")
    time_to = d.get("time_to")
    source = d.get("source", "fake")

    # Combine format into screen field for filtering (e.g., "EPIQ", "PCX", "3D")
    screen_label_parts = []
    if fmt:
        screen_label_parts.append(fmt)
    if screen:
        screen_label_parts.append(screen)
    combined_screen = " ".join(screen_label_parts) if screen_label_parts else None

    try:
        spec = WatchSpec(
            movie_name=d["movie"],
            city=d["city"],
            target_date=parse_date(d["date"]),
            theatre=d["theatre"],
            screen=combined_screen,
            showtime=None,
            time_from=time_from,
            time_to=time_to,
            seat_category=fmt,
            exact_seats="",
            min_seats=1,
            poll_interval_seconds=60,
            notify_once=False,
            source=source,
            source_url=d.get("url"),
        )
    except Exception as exc:
        await update.message.reply_text(f"Could not create the watch: {exc}")
        return ConversationHandler.END

    if spec.source == "bookmyshow" and not spec.source_url:
        await update.message.reply_text(
            "The bookmyshow source requires a verified URL. Please start over with /watch."
        )
        return ConversationHandler.END

    # Access the BotContext from the application's bot_data
    ctx: BotContext = tg.bot_data.get("bot_ctx")
    if ctx is None:
        await update.message.reply_text("Internal error: bot context not found.")
        return ConversationHandler.END

    watch = ctx.watch_repo.create(spec)

    timing_label = "any showtime"
    if time_from and time_to:
        timing_label = f"{time_from} - {time_to}"
    elif time_from:
        timing_label = f"from {time_from}"

    screen_display = combined_screen or "any"
    format_display = fmt or "any"

    # --- Run an immediate check to report current status ---
    status_block = "Checking current availability..."
    try:
        report = await ctx.checker.check(watch)
        state_val = report.state.value
        shows = report.result.matched_shows if report.result else []

        if state_val in ("BOOKING_OPEN", "SEATS_AVAILABLE"):
            lines = [f"  - {s.showtime} | {s.screen or 'Standard'} | {s.theatre}" for s in shows[:10]]
            shows_list = "\n".join(lines) if lines else "  (shows detected)"
            status_block = (
                f"Status: {state_val}\n"
                f"{len(shows)} show(s) already available!\n"
                f"{shows_list}\n\n"
                f"Monitoring active. I will alert you if show availability changes."
            )
        elif state_val in ("SHOW_NOT_AVAILABLE", "BOOKING_NOT_OPEN", "DATE_NOT_AVAILABLE"):
            status_block = (
                f"Status: {state_val}\n"
                f"Tickets are not yet open for this date.\n"
                f"I will notify you the moment they open."
            )
        elif state_val in ("ERROR", "BLOCKED"):
            detail = report.decision.reason if report.decision else ""
            status_block = (
                f"Status: {state_val}\n"
                f"Could not read the page right now.\n"
                f"Detail: {detail}\n"
                f"I will keep trying every 60 seconds."
            )
        else:
            status_block = f"Status: {state_val}\nMonitoring started."
    except Exception as exc:
        logger.exception("immediate check failed for watch #%s", watch.id)
        status_block = f"Status check pending ({type(exc).__name__}: {exc}). First check within 60s."

    await update.message.reply_text(
        f"Watch #{watch.id} created!\n\n"
        f"Movie: {spec.movie_name}\n"
        f"City: {spec.city}\n"
        f"Date: {spec.target_date}\n"
        f"Theatre: {spec.theatre}\n"
        f"Format: {format_display}\n"
        f"Screen: {screen_display}\n"
        f"Timings: {timing_label}\n"
        f"Source: {spec.source}\n\n"
        f"{status_block}"
    )

    return ConversationHandler.END


async def _cancel(update, _tg):
    await update.message.reply_text("Cancelled. Send /watch to start over.")
    return ConversationHandler.END


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _int_arg(args, index: int) -> int | None:
    try:
        return int(args[index])
    except (IndexError, ValueError, TypeError):
        return None


def _parse_kv(text: str) -> dict[str, str]:
    """Parse 'movie=Some Long Name city=Pune date=2026-01-01' into a dict."""
    known = {"movie", "city", "date", "theatre", "theater", "screen", "time", "from", "to",
             "category", "seat_ids", "seats", "interval", "once", "source", "url"}
    fields: dict[str, str] = {}
    key, buffer = None, []
    for token in text.split():
        if "=" in token:
            candidate, _, rest = token.partition("=")
            if candidate.lower() in known:
                if key:
                    fields[key] = " ".join(buffer).strip()
                key, buffer = candidate.lower(), ([rest] if rest else [])
                continue
        buffer.append(token)
    if key:
        fields[key] = " ".join(buffer).strip()
    return fields


def _spec_from_fields(fields: dict[str, str], settings: Settings) -> WatchSpec:
    required = ("movie", "city", "date")
    missing = [r for r in required if not fields.get(r)]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")
    theatre = fields.get("theatre") or fields.get("theater")
    if not theatre:
        raise ValueError("missing required field: theatre")

    once = fields.get("once", "true").lower() not in ("false", "0", "no")
    return WatchSpec(
        movie_name=fields["movie"],
        city=fields["city"],
        target_date=parse_date(fields["date"]),
        theatre=theatre,
        screen=fields.get("screen"),
        showtime=fields.get("time"),
        time_from=fields.get("from"),
        time_to=fields.get("to"),
        seat_category=fields.get("category"),
        exact_seats=fields.get("seat_ids", ""),
        min_seats=int(fields.get("seats", 1)),
        poll_interval_seconds=int(fields.get("interval", settings.default_poll_interval_seconds)),
        notify_once=once,
        source=fields.get("source", "fake"),
        source_url=fields.get("url"),
    )


def register_handlers(application: Application, ctx: BotContext) -> None:
    # Store context in bot_data so conversation handlers can access it
    application.bot_data["bot_ctx"] = ctx

    # Simple commands
    simple = {
        "start": cmd_start, "help": cmd_help, "watches": cmd_watches,
        "status": cmd_status, "pause": cmd_pause, "resume": cmd_resume,
        "remove": cmd_remove, "setinterval": cmd_setinterval, "test": cmd_test,
        "test_notification": cmd_test_notification, "simulate": cmd_simulate,
    }
    for name, handler in simple.items():
        application.add_handler(CommandHandler(name, _guard(ctx, handler)))

    # Interactive /watch conversation
    watch_conv = ConversationHandler(
        entry_points=[CommandHandler("watch", lambda u, t: _watch_entry(ctx, u, t))],
        states={
            ASK_MOVIE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_movie)],
            ASK_CITY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_city)],
            ASK_DATE:    [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_date)],
            ASK_THEATRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_theatre)],
            ASK_SCREEN:  [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_screen)],
            ASK_FORMAT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_format)],
            ASK_TIMING:  [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_timing)],
            ASK_SOURCE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_source)],
            ASK_URL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, _receive_url)],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
    )
    application.add_handler(watch_conv)

    logger.info("registered %s Telegram commands + interactive /watch", len(simple))