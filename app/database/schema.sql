-- Schema for the movie-ticket monitor. Applied idempotently at startup.
-- All timestamps are ISO-8601 strings in UTC. Booleans are 0/1 INTEGERs.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS watches (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    movie_name            TEXT    NOT NULL,
    city                  TEXT    NOT NULL,
    target_date           TEXT    NOT NULL,          -- YYYY-MM-DD
    theatre               TEXT    NOT NULL,
    screen                TEXT,
    showtime              TEXT,
    time_from             TEXT,
    time_to               TEXT,
    seat_category         TEXT,
    exact_seats           TEXT    NOT NULL DEFAULT '[]',  -- JSON array
    min_seats             INTEGER NOT NULL DEFAULT 1,
    poll_interval_seconds INTEGER NOT NULL DEFAULT 60,
    enabled               INTEGER NOT NULL DEFAULT 1,
    notify_once           INTEGER NOT NULL DEFAULT 1,
    source                TEXT    NOT NULL DEFAULT 'fake',
    source_url            TEXT,
    current_state         TEXT    NOT NULL DEFAULT 'UNKNOWN',
    current_hash          TEXT,
    last_checked_at       TEXT,
    last_success_at       TEXT,
    last_error            TEXT,
    consecutive_failures  INTEGER NOT NULL DEFAULT 0,
    notification_count    INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT    NOT NULL,
    updated_at            TEXT    NOT NULL
);

-- The scheduler lists enabled watches on every tick: worth an index.
CREATE INDEX IF NOT EXISTS idx_watches_enabled ON watches(enabled);
CREATE INDEX IF NOT EXISTS idx_watches_state   ON watches(current_state);

CREATE TABLE IF NOT EXISTS availability_snapshots (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id           INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    state              TEXT    NOT NULL,
    availability_hash  TEXT    NOT NULL,
    matched_show_count INTEGER NOT NULL DEFAULT 0,
    all_show_count     INTEGER NOT NULL DEFAULT 0,
    matched_seat_count INTEGER,                        -- NULL = unknown
    matched_seat_ids   TEXT    NOT NULL DEFAULT '[]',
    booking_url        TEXT,
    error              TEXT,
    payload_json       TEXT,                           -- normalized matched shows
    source_name        TEXT    NOT NULL DEFAULT 'unknown',
    created_at         TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_watch_time
    ON availability_snapshots(watch_id, created_at DESC);

CREATE TABLE IF NOT EXISTS notification_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id       INTEGER REFERENCES watches(id) ON DELETE CASCADE,
    kind           TEXT    NOT NULL,
    previous_state TEXT,
    new_state      TEXT,
    dedupe_key     TEXT    NOT NULL,
    channel        TEXT    NOT NULL,                   -- 'telegram' | 'console'
    delivered      INTEGER NOT NULL DEFAULT 0,
    message        TEXT,
    error          TEXT,
    created_at     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_dedupe ON notification_events(dedupe_key, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notifications_watch  ON notification_events(watch_id, created_at DESC);

CREATE TABLE IF NOT EXISTS monitor_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id      INTEGER REFERENCES watches(id) ON DELETE CASCADE,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    duration_ms   INTEGER,
    state         TEXT,
    ok            INTEGER NOT NULL DEFAULT 0,
    notified      INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 1,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_watch_time ON monitor_runs(watch_id, started_at DESC);
