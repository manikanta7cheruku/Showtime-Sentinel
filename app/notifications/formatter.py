"""Message text. Kept separate from delivery so it's trivial to unit test.

Rule: a notification states only what we actually observed. If we have no
verified booking URL we say so rather than constructing a plausible-looking
link, because a wrong link during a ticket rush is worse than no link.
"""
from __future__ import annotations

import html

from app.models import AvailabilityState, NormalizedResult, TransitionDecision, Watch
from app.utils.timeutil import to_display, utc_now

STATE_ICON = {
    AvailabilityState.SEATS_AVAILABLE: "🎟️",
    AvailabilityState.BOOKING_OPEN: "✅",
    AvailabilityState.BOOKING_NOT_OPEN: "⏳",
    AvailabilityState.NO_SEATS_AVAILABLE: "🚫",
    AvailabilityState.SHOW_NOT_AVAILABLE: "❔",
    AvailabilityState.DATE_NOT_AVAILABLE: "📅",
    AvailabilityState.NOT_FOUND: "🔍",
    AvailabilityState.BLOCKED: "⛔",
    AvailabilityState.ERROR: "⚠️",
    AvailabilityState.UNKNOWN: "•",
}

STATE_LABELS = {
    AvailabilityState.SEATS_AVAILABLE: "Seats Available",
    AvailabilityState.BOOKING_OPEN: "Booking Open",
    AvailabilityState.BOOKING_NOT_OPEN: "Booking Not Open",
    AvailabilityState.NO_SEATS_AVAILABLE: "No Seats Available",
    AvailabilityState.SHOW_NOT_AVAILABLE: "Show Not Available",
    AvailabilityState.DATE_NOT_AVAILABLE: "Date Not Available",
    AvailabilityState.NOT_FOUND: "Not Found",
    AvailabilityState.BLOCKED: "Access Blocked by Site",
    AvailabilityState.ERROR: "Error",
    AvailabilityState.UNKNOWN: "First Check",
}


def e(value) -> str:
    return html.escape(str(value), quote=False)


def _watch_lines(watch: Watch) -> list[str]:
    lines = [
        f"Movie: {e(watch.movie_name)}",
        f"Date: {e(watch.target_date.strftime('%d %b %Y'))}",
        f"City: {e(watch.city)}",
        f"Theatre: {e(watch.theatre)}",
    ]
    if watch.screen:
        lines.append(f"Screen: {e(watch.screen)}")
    lines.append(f"Showtime: {e(watch.time_window_label())}")
    if watch.seat_category:
        lines.append(f"Category: {e(watch.seat_category)}")
    if watch.exact_seats:
        lines.append(f"Wanted seats: {e(', '.join(watch.exact_seats))}")
    return lines


def format_state_change(
    watch: Watch, result: NormalizedResult, decision: TransitionDecision
) -> str:
    lines = [f"[{e(decision.reason.upper())}]", ""]
    lines += _watch_lines(watch)
    
    prev_label = STATE_LABELS.get(decision.previous_state, decision.previous_state.value)
    new_label = STATE_LABELS.get(result.state, result.state.value)
    
    lines += [
        "",
        f"Status: {e(decision.previous_state.value)} -> {e(result.state.value)} ({prev_label} -> {new_label})",
    ]
    if result.matched_seat_count is not None:
        lines.append(f"Matching seats: {result.matched_seat_count} "
                     f"(you asked for >= {watch.min_seats})")
    else:
        lines.append("Matching seats: not published on the page")
    if result.matched_seat_ids:
        shown = ", ".join(result.matched_seat_ids[:12])
        more = "" if len(result.matched_seat_ids) <= 12 else f" (+{len(result.matched_seat_ids) - 12} more)"
        lines.append(f"Seat ids: {e(shown)}{more}")

    lines.append(f"Check execution time: {e(to_display(result.checked_at))}")

    # Prioritize user's original watch URL so it takes them back to the exact target date
    target_link = watch.source_url or result.booking_url
    if target_link:
        lines += ["", f'<a href="{e(target_link)}">Open the booking page</a>']
    else:
        lines += ["", "No verified booking link for this watch — open BookMyShow directly."]

    lines += ["", "Notification only. This bot cannot and will not book or pay for anything."]
    return "\n".join(lines)


def format_error(watch: Watch, result: NormalizedResult, decision: TransitionDecision) -> str:
    lines = [f"[WATCH IS FAILING]", ""]
    lines += _watch_lines(watch)
    
    current_label = STATE_LABELS.get(result.state, result.state.value)
    
    lines += [
        "",
        f"Status: {e(result.state.value)} ({current_label})",
        f"Reason: {e(decision.reason)}",
        f"Detail: {e((result.error or 'no detail')[:400])}",
        f"Check execution time: {e(to_display(result.checked_at))}",
    ]
    if result.state is AvailabilityState.BLOCKED:
        lines += [
            "",
            "BLOCKED means the website refused automated access. That is a "
            "legitimate signal to stop — this bot does not attempt to bypass it. "
            "Consider increasing the check interval.",
        ]
    lines += ["", "Monitoring continues; other watches are unaffected."]
    return "\n".join(lines)


def format_test_message() -> str:
    return (
        "Movie Ticket Monitor — test message\n\n"
        f"Sent at: {e(to_display(utc_now()))}\n"
        "Your token, chat id and network path all work.\n\n"
        "Notification only. No booking, no payment, ever."
    )


def format_watch_row(watch: Watch) -> str:
    flag = "▶️" if watch.enabled else "⏸️"
    icon = STATE_ICON.get(watch.current_state, "•")
    current_label = STATE_LABELS.get(watch.current_state, watch.current_state.value)
    return (
        f"{flag} <b>#{watch.id}</b> {e(watch.movie_name)} — {e(watch.city)}\n"
        f"    {e(watch.theatre)} · {e(watch.time_window_label())} · {e(str(watch.target_date))}\n"
        f"    {icon} {e(watch.current_state.value)} ({current_label}) · every {watch.poll_interval_seconds}s · "
        f"{watch.notification_count} notifications\n"
        f"    last check: {e(to_display(watch.last_checked_at))}"
    )