"""Repositories: the ONLY place that knows SQL.

Every query is parameterised with `?` placeholders - never f-strings - so user
input (a movie name with a quote in it, say) can't alter the query.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Iterable, Protocol, Sequence

from app.database.connection import Database
from app.models import AvailabilityState, NormalizedResult, NotificationKind, Watch, WatchSpec
from app.utils.timeutil import iso_utc, parse_date, parse_iso_utc, utc_now

logger = logging.getLogger(__name__)

_WATCH_COLUMNS = (
    "movie_name", "city", "target_date", "theatre", "screen", "showtime",
    "time_from", "time_to", "seat_category", "exact_seats", "min_seats",
    "poll_interval_seconds", "enabled", "notify_once", "source", "source_url",
    "current_state", "current_hash", "last_checked_at", "last_success_at",
    "last_error", "consecutive_failures", "notification_count",
    "created_at", "updated_at",
)


# --------------------------------------------------------------------------- #
# Interface (Protocol = structural typing: any class with these methods fits)
# --------------------------------------------------------------------------- #
class WatchRepository(Protocol):
    def create(self, spec: WatchSpec) -> Watch: ...
    def get(self, watch_id: int) -> Watch | None: ...
    def list_watches(self, *, enabled_only: bool = False) -> list[Watch]: ...
    def delete(self, watch_id: int) -> bool: ...
    def set_enabled(self, watch_id: int, enabled: bool) -> bool: ...
    def set_interval(self, watch_id: int, seconds: int) -> bool: ...
    def record_check(self, watch_id: int, result: NormalizedResult, *, notified: bool) -> None: ...


# --------------------------------------------------------------------------- #
def _watch_from_row(row) -> Watch:
    return Watch(
        id=row["id"],
        movie_name=row["movie_name"],
        city=row["city"],
        target_date=parse_date(row["target_date"]),
        theatre=row["theatre"],
        screen=row["screen"],
        showtime=row["showtime"],
        time_from=row["time_from"],
        time_to=row["time_to"],
        seat_category=row["seat_category"],
        exact_seats=json.loads(row["exact_seats"] or "[]"),
        min_seats=row["min_seats"],
        poll_interval_seconds=row["poll_interval_seconds"],
        enabled=bool(row["enabled"]),
        notify_once=bool(row["notify_once"]),
        source=row["source"],
        source_url=row["source_url"],
        current_state=AvailabilityState(row["current_state"]),
        current_hash=row["current_hash"],
        last_checked_at=parse_iso_utc(row["last_checked_at"]),
        last_success_at=parse_iso_utc(row["last_success_at"]),
        last_error=row["last_error"],
        consecutive_failures=row["consecutive_failures"],
        notification_count=row["notification_count"],
        created_at=parse_iso_utc(row["created_at"]),
        updated_at=parse_iso_utc(row["updated_at"]),
    )


class SqliteWatchRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # -- writes ------------------------------------------------------------
    def create(self, spec: WatchSpec) -> Watch:
        now = iso_utc(utc_now())
        values: dict[str, Any] = {
            "movie_name": spec.movie_name, "city": spec.city,
            "target_date": spec.target_date.isoformat(), "theatre": spec.theatre,
            "screen": spec.screen, "showtime": spec.showtime,
            "time_from": spec.time_from, "time_to": spec.time_to,
            "seat_category": spec.seat_category,
            "exact_seats": json.dumps(spec.exact_seats),
            "min_seats": spec.min_seats,
            "poll_interval_seconds": spec.poll_interval_seconds,
            "enabled": int(spec.enabled), "notify_once": int(spec.notify_once),
            "source": spec.source, "source_url": spec.source_url,
            "current_state": AvailabilityState.UNKNOWN.value, "current_hash": None,
            # Setting last_checked_at to now prevents the background scheduler from colliding
            # with the interactive Telegram checker immediately upon creation.
            "last_checked_at": now, "last_success_at": None, "last_error": None,
            "consecutive_failures": 0, "notification_count": 0,
            "created_at": now, "updated_at": now,
        }
        placeholders = ", ".join("?" for _ in _WATCH_COLUMNS)
        sql = f"INSERT INTO watches ({', '.join(_WATCH_COLUMNS)}) VALUES ({placeholders})"
        with self.db.connect() as conn:
            cur = conn.execute(sql, tuple(values[c] for c in _WATCH_COLUMNS))
            watch_id = int(cur.lastrowid)
        logger.info("created watch #%s (%s / %s / %s)", watch_id, spec.movie_name, spec.city, spec.target_date)
        created = self.get(watch_id)
        assert created is not None
        return created

    def delete(self, watch_id: int) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))
        deleted = cur.rowcount > 0
        logger.info("delete watch #%s -> %s", watch_id, "ok" if deleted else "not found")
        return deleted

    def set_enabled(self, watch_id: int, enabled: bool) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute(
                "UPDATE watches SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), iso_utc(utc_now()), watch_id),
            )
        return cur.rowcount > 0

    def set_interval(self, watch_id: int, seconds: int) -> bool:
        with self.db.connect() as conn:
            cur = conn.execute(
                "UPDATE watches SET poll_interval_seconds = ?, updated_at = ? WHERE id = ?",
                (int(seconds), iso_utc(utc_now()), watch_id),
            )
        return cur.rowcount > 0

    def record_check(self, watch_id: int, result: NormalizedResult, *, notified: bool) -> None:
        """Single UPDATE holding everything we learned from one check."""
        now = utc_now()
        with self.db.connect() as conn:
            if result.is_failure:
                # If the check failed but is just a transient throttle (UNKNOWN), keep the database state intact.
                # If it is a hard check failure (like ERROR or BLOCKED), update the database state so tests/schedulers know.
                if result.state == AvailabilityState.UNKNOWN:
                    conn.execute(
                        """UPDATE watches
                              SET last_checked_at = ?, last_error = ?,
                                  consecutive_failures = consecutive_failures + 1,
                                  notification_count = notification_count + ?,
                                  updated_at = ?
                            WHERE id = ?""",
                        (iso_utc(now), result.error,
                         1 if notified else 0, iso_utc(now), watch_id),
                    )
                else:
                    conn.execute(
                        """UPDATE watches
                              SET current_state = ?, last_checked_at = ?, last_error = ?,
                                  consecutive_failures = consecutive_failures + 1,
                                  notification_count = notification_count + ?,
                                  updated_at = ?
                            WHERE id = ?""",
                        (result.state.value, iso_utc(now), result.error,
                         1 if notified else 0, iso_utc(now), watch_id),
                    )
            else:
                conn.execute(
                    """UPDATE watches
                          SET current_state = ?, current_hash = ?, last_checked_at = ?,
                              last_success_at = ?, last_error = NULL,
                              consecutive_failures = 0,
                              notification_count = notification_count + ?,
                              updated_at = ?
                        WHERE id = ?""",
                    (result.state.value, result.availability_hash, iso_utc(now),
                     iso_utc(now), 1 if notified else 0, iso_utc(now), watch_id),
                )

    # -- reads -------------------------------------------------------------
    def get(self, watch_id: int) -> Watch | None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
        return _watch_from_row(row) if row else None

    def list_watches(self, *, enabled_only: bool = False) -> list[Watch]:
        sql = "SELECT * FROM watches"
        params: Sequence[Any] = ()
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY id"
        with self.db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_watch_from_row(r) for r in rows]

    def count(self) -> tuple[int, int]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(enabled), 0) AS active FROM watches"
            ).fetchone()
        return int(row["total"]), int(row["active"])


# --------------------------------------------------------------------------- #
class SnapshotRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(self, watch_id: int, result: NormalizedResult) -> int:
        payload = json.dumps(
            [s.model_dump(mode="json") for s in result.matched_shows],
            sort_keys=True, separators=(",", ":"),
        )
        with self.db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO availability_snapshots
                   (watch_id, state, availability_hash, matched_show_count, all_show_count,
                    matched_seat_count, matched_seat_ids, booking_url, error, payload_json,
                    source_name, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (watch_id, result.state.value, result.availability_hash,
                 len(result.matched_shows), result.all_show_count,
                 result.matched_seat_count, json.dumps(result.matched_seat_ids),
                 result.booking_url, result.error, payload, result.source_name,
                 iso_utc(result.checked_at)),
            )
            return int(cur.lastrowid)

    def latest(self, watch_id: int):
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM availability_snapshots WHERE watch_id = ? ORDER BY id DESC LIMIT 1",
                (watch_id,),
            ).fetchone()

    def history(self, watch_id: int, limit: int = 20) -> list[Any]:
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM availability_snapshots WHERE watch_id = ? ORDER BY id DESC LIMIT ?",
                (watch_id, int(limit)),
            ).fetchall()

    def count_for(self, watch_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM availability_snapshots WHERE watch_id = ?", (watch_id,)
            ).fetchone()
        return int(row["c"])


class NotificationRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self, *, watch_id: int | None, kind: NotificationKind, dedupe_key: str,
        channel: str, delivered: bool, message: str | None = None,
        previous_state: str | None = None, new_state: str | None = None,
        error: str | None = None, created_at: datetime | None = None,
    ) -> int:
        with self.db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO notification_events
                   (watch_id, kind, previous_state, new_state, dedupe_key, channel,
                    delivered, message, error, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (watch_id, kind.value, previous_state, new_state, dedupe_key, channel,
                 int(delivered), message, error, iso_utc(created_at or utc_now())),
            )
            return int(cur.lastrowid)

    def exists_within(self, dedupe_key: str, cooldown_seconds: int) -> bool:
        """The anti-spam guard. cooldown_seconds <= 0 means 'forever'."""
        parts = dedupe_key.split("|")
        hash_suffix = f"%|{parts[-1]}" if len(parts) >= 3 else dedupe_key
        watch_id = int(parts[0]) if parts and parts[0].isdigit() else None

        with self.db.connect() as conn:
            if cooldown_seconds <= 0:
                if watch_id is not None and len(parts) >= 3:
                    row = conn.execute(
                        "SELECT 1 FROM notification_events WHERE (dedupe_key = ? OR (watch_id = ? AND dedupe_key LIKE ?)) AND delivered = 1 LIMIT 1",
                        (dedupe_key, watch_id, hash_suffix),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT 1 FROM notification_events WHERE dedupe_key = ? AND delivered = 1 LIMIT 1",
                        (dedupe_key,),
                    ).fetchone()
            else:
                cutoff = iso_utc(utc_now() - timedelta(seconds=cooldown_seconds))
                if watch_id is not None and len(parts) >= 3:
                    row = conn.execute(
                        "SELECT 1 FROM notification_events WHERE (dedupe_key = ? OR (watch_id = ? AND dedupe_key LIKE ?)) AND delivered = 1 AND created_at >= ? LIMIT 1",
                        (dedupe_key, watch_id, hash_suffix, cutoff),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT 1 FROM notification_events WHERE dedupe_key = ? AND delivered = 1 AND created_at >= ? LIMIT 1",
                        (dedupe_key, cutoff),
                    ).fetchone()
        return row is not None

    def count_for(self, watch_id: int) -> int:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM notification_events WHERE watch_id = ?", (watch_id,)
            ).fetchone()
        return int(row["c"])

    def recent(self, limit: int = 20) -> list[Any]:
        with self.db.connect() as conn:
            return conn.execute(
                "SELECT * FROM notification_events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()


class RunRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self, *, watch_id: int, started_at: datetime, finished_at: datetime,
        state: str, ok: bool, notified: bool, attempts: int, error: str | None,
    ) -> int:
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        with self.db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO monitor_runs
                   (watch_id, started_at, finished_at, duration_ms, state, ok, notified, attempts, error)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (watch_id, iso_utc(started_at), iso_utc(finished_at), duration_ms,
                 state, int(ok), int(notified), attempts, error),
            )
            return int(cur.lastrowid)

    def recent(self, watch_id: int | None = None, limit: int = 20) -> list[Any]:
        with self.db.connect() as conn:
            if watch_id is None:
                return conn.execute(
                    "SELECT * FROM monitor_runs ORDER BY id DESC LIMIT ?", (int(limit),)
                ).fetchall()
            return conn.execute(
                "SELECT * FROM monitor_runs WHERE watch_id = ? ORDER BY id DESC LIMIT ?",
                (watch_id, int(limit)),
            ).fetchall()


def rows_to_dicts(rows: Iterable[Any]) -> list[dict]:
    return [dict(r) for r in rows]