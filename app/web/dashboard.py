"""Optional read-only localhost dashboard (stdlib http.server, zero deps).

Secondary by design: it only READS the database, binds to 127.0.0.1, and has no
buttons. Learning happens in the CLI and the bot; this is just a window.
"""
from __future__ import annotations

import html
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.config import Settings
from app.database.repositories import NotificationRepository, SqliteWatchRepository
from app.utils.timeutil import to_display

logger = logging.getLogger(__name__)

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta http-equiv="refresh" content="15"><title>Movie Ticket Monitor</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:2rem;background:#0f1115;color:#e6e6e6}}
 h1{{font-size:1.4rem}} table{{border-collapse:collapse;width:100%;font-size:.9rem}}
 th,td{{padding:.5rem .6rem;border-bottom:1px solid #2a2f3a;text-align:left;vertical-align:top}}
 th{{color:#9aa4b2;font-weight:600}} .ok{{color:#5ddc7c}} .warn{{color:#ffb84d}} .bad{{color:#ff6b6b}}
 .muted{{color:#7c8794;font-size:.8rem}}
</style></head><body>
<h1>🎬 Movie Ticket Monitor <span class="muted">read-only · refreshes every 15s</span></h1>
<p class="muted">TEST_MODE={test_mode} · DRY_RUN={dry_run} · watches: {total} ({active} active)</p>
<table><tr>
<th>#</th><th>Movie</th><th>City / Theatre</th><th>Date / Time</th><th>State</th>
<th>Last check</th><th>Last success</th><th>Interval</th><th>Notifs</th><th>Error</th>
</tr>{rows}</table>
<p class="muted">Notification only. This project never books tickets and never processes payments.</p>
</body></html>"""

GOOD = {"SEATS_AVAILABLE", "BOOKING_OPEN"}
BAD = {"ERROR", "BLOCKED"}


def _render(watch_repo: SqliteWatchRepository, notif_repo: NotificationRepository,
            settings: Settings) -> str:
    total, active = watch_repo.count()
    rows = []
    for w in watch_repo.list_watches():
        state = w.current_state.value
        css = "ok" if state in GOOD else ("bad" if state in BAD else "warn")
        rows.append(
            f"<tr><td>{w.id}</td><td>{html.escape(w.movie_name)}</td>"
            f"<td>{html.escape(w.city)}<br><span class='muted'>{html.escape(w.theatre)}"
            f"{' · ' + html.escape(w.screen) if w.screen else ''}</span></td>"
            f"<td>{w.target_date}<br><span class='muted'>{html.escape(w.time_window_label())}</span></td>"
            f"<td class='{css}'>{state}{'' if w.enabled else ' (paused)'}</td>"
            f"<td>{html.escape(to_display(w.last_checked_at))}</td>"
            f"<td>{html.escape(to_display(w.last_success_at))}</td>"
            f"<td>{w.poll_interval_seconds}s</td>"
            f"<td>{notif_repo.count_for(w.id)}</td>"
            f"<td class='muted'>{html.escape((w.last_error or '')[:90])}</td></tr>"
        )
    return PAGE.format(
        test_mode=settings.test_mode, dry_run=settings.dry_run,
        total=total, active=active, rows="".join(rows) or "<tr><td colspan='10'>No watches yet</td></tr>",
    )


def start_dashboard(settings: Settings, watch_repo, notif_repo) -> HTTPServer | None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                                  # noqa: N802
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            body = _render(watch_repo, notif_repo, settings).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return  # keep our logs clean

    try:
        server = HTTPServer((settings.dashboard_host, settings.dashboard_port), Handler)
    except OSError as exc:
        logger.warning("dashboard not started (%s). Is the port in use?", exc)
        return None

    threading.Thread(target=server.serve_forever, daemon=True, name="dashboard").start()
    logger.info("dashboard: http://%s:%s", settings.dashboard_host, settings.dashboard_port)
    return server
