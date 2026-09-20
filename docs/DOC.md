# Movie Ticket Monitor — Engineering Notebook (Private)

> **Not for GitHub.** This file is my personal deep-dive notebook. It documents
> everything I learned, every bug I hit, every design decision I made, and every
> trade-off I chose. It is deliberately verbose because it doubles as an
> interview-prep document. `.gitignore` excludes it.

---

## 0. Project Identity

- **What it is:** A notification-only movie ticket availability monitor for BookMyShow.
- **What it is NOT:** A booking bot, a scalper tool, a payment automator, a captcha solver.
- **Why it exists:** To demonstrate end-to-end engineering: async I/O, adapters,
  state machines, deterministic hashing, deduplication, per-host throttling,
  graceful shutdown, structured logging, SQLite WAL, Playwright headless browser,
  Telegram integration, fail-closed security, and a fake source that proves the
  pipeline works without any network call.

---

## 1. Table of Contents

1. Project Identity
2. Table of Contents
3. Architecture Overview
4. File-by-File Responsibilities
5. Data Flow (Request → Notification)
6. State Machine (All 10 States)
7. The Dedup Guarantee (How Spam Is Prevented)
8. Setup — Windows PowerShell
9. Setup — Linux / macOS
10. Playwright Installation
11. Telegram Bot Creation
12. Configuration Reference (`.env`)
13. CLI Command Reference
14. Telegram Command Reference
15. Interactive `/watch` Flow (Step-by-Step)
16. Fake Source Simulation Scenarios
17. Test Suite (117 Tests)
18. Dry-Run Mode
19. Logging & Debugging
20. Restart Recovery
21. Timing Reality Check
22. Rate Limiting & Politeness
23. BookMyShow Adapter Reality (What I Verified)
24. Fuzzy Matching (Movie Names & Theatre Names)
25. URL Classification (buytickets / movie / venue)
26. Playwright JS Extraction Strategy
27. API Response Interception
28. Security & Privacy
29. All Bugs Hit & Fixes Applied (Case Studies)
30. Common Errors Cheat Sheet
31. Free 24/7 Hosting Options
32. Oracle Cloud Deployment Steps
33. Publishing Safely to GitHub
34. Optional Dashboard
35. Optional Docker
36. Honest Limitations
37. What Top-MNC Interviewers Will Notice
38. Interview Talking Points (STAR-Format Ready)
39. Future Extensions

---

## 3. Architecture Overview

```
┌──────────────┐   ┌──────────────┐
│ CLI          │   │ Telegram bot │      both talk to the same objects
│ app/main.py  │   │ app/bot/     │
└──────┬───────┘   └──────┬───────┘
       └──────────┬───────┘
                  ▼
        ┌──────────────────────┐        one asyncio process,
        │ Scheduler            │        many watches, no overlap
        │ monitoring/scheduler │
        └──────────┬───────────┘
                   ▼
        ┌──────────────────────┐
        │ AvailabilityChecker  │  ← the data-flow spine
        │ monitoring/checker   │
        └───┬────────┬─────┬───┘
            ▼        ▼     ▼
   ┌────────────┐ ┌──────────────┐ ┌──────────────────┐
   │ MovieSource│ │ normalize +  │ │ NotificationSvc  │
   │ sources/   │ │ state +      │ │ notifications/   │
   │ fake | bms │ │ ChangeDetect │ │ console|telegram │
   └────────────┘ └──────┬───────┘ └──────────────────┘
                         ▼
              ┌──────────────────────┐
              │ SQLite repositories  │
              │ database/            │
              └──────────────────────┘
```

**The key design rule:** *sources report facts; they never decide state.* A
source says "there is a 19:30 show, booking_open=true, GOLD has 12 seats".
`state.py` converts facts into a state, `normalizer.py` hashes it,
`change_detector.py` decides whether it's newsworthy. That's why the fake source
is a genuine test of the real pipeline — nothing downstream is stubbed.

---

## 4. File-by-File Responsibilities

**`app/config.py`** — every tunable, loaded once from `.env` via
pydantic-settings. No other module reads `os.environ`, so tests construct
`Settings(...)` directly. Also parses `ALLOWED_TELEGRAM_USER_IDS` **fail-closed**.

**`app/utils/timeutil.py`** — the only place that handles time. Store UTC,
display IST. `utc_now()` is aware; `to_display()` formats in Asia/Kolkata.

**`app/utils/logging_setup.py`** — console + rotating file handler (1 MB × 3).
Includes `SafeStreamHandler` to survive pytest's log-capture closing streams mid-test.

**`app/utils/retry.py`** — `RetryPolicy`, `backoff_delay()` (exponential + jitter),
`run_with_retries()` (per-attempt timeout). `sleep` and `rand` are injectable so
tests run instantly and deterministically.

**`app/utils/robots.py`** — the gate for the real adapter: a permanent deny-list
plus live `robots.txt` parsing, **failing closed** if `robots.txt` can't be read.

**`app/models/enums.py`** — the vocabulary: `AvailabilityState` (10 members),
`FetchStatus`, `NotificationKind`.

**`app/models/watch.py`** — `WatchSpec` (what you asked for, validated) and
`Watch` (spec + remembered runtime state). Validation lives here so every other
module can trust its inputs.

**`app/models/result.py`** — the source contract: `SeatCategory`, `RawShow`,
`SourcePayload`, `FetchOutcome`, `NormalizedResult`, `TransitionDecision`.

**`app/database/schema.sql`** — four tables: `watches`, `availability_snapshots`,
`notification_events`, `monitor_runs`, plus indexes on the columns we actually
filter by.

**`app/database/connection.py`** — one short-lived connection per operation
(commit/rollback via context manager), WAL mode, foreign keys ON.

**`app/database/repositories.py`** — the *only* module containing SQL. Every
query is parameterised. Also implements state-preservation logic (transient
failures don't overwrite the last-known-good `current_state`).

**`app/sources/base.py`** — the `MovieSource` ABC and `SourceRegistry`
(`TEST_MODE=true` rewires every watch to the fake source).

**`app/sources/fake.py`** — scripted scenarios that simulate
`BOOKING_NOT_OPEN → BOOKING_OPEN → SEATS_AVAILABLE`, sold-out, blocked, flaky
and missing-movie cases.

**`app/sources/bookmyshow.py`** — the conservative real adapter (§23) plus
`parse_showtimes_html()`, a **pure function** you can unit-test against fixtures.
Includes URL classification, fuzzy matching, browser JS extraction, and API
response interception.

**`app/monitoring/filters.py`** — watch filters as predicates: fuzzy on names,
strict on times.

**`app/monitoring/state.py`** — facts → one state, as a readable decision tree.

**`app/monitoring/normalizer.py`** — canonical JSON → SHA-256 availability hash.

**`app/monitoring/change_detector.py`** — is this worth a message? Two guards:
meaningful transition, then dedupe key.

**`app/monitoring/checker.py`** — one complete check; catches every exception.

**`app/monitoring/scheduler.py`** — the loop: immediate first check, per-watch
non-overlap, backoff, graceful shutdown.

**`app/notifications/`** — `base.py` (interface), `formatter.py` (message text),
`console.py` (dry-run), `telegram.py` (httpx → Bot API).

**`app/bot/`** — `commands.py` (handlers + authorisation + interactive `/watch`),
`runner.py` (bot and scheduler in one loop).

**`app/web/dashboard.py`** — optional read-only localhost page, stdlib only.

**`app/main.py`** — `build_app()` wires everything (read this first!) and the CLI.

---

## 5. Data Flow (Request → Notification)

```
 1. You create a watch          → CLI/Telegram → WatchSpec (validated) → SQLite
 2. Scheduler tick              → "which enabled watches are due?"
 3. Per due watch, one task     → per-watch lock prevents overlap
 4. source.fetch(watch)         → timeout + up to MAX_RETRIES with backoff+jitter
 5. FetchOutcome                → status + SourcePayload (facts only)
 6. filter_shows()              → keep shows matching theatre/screen/time/category
 7. derive_state()              → one AvailabilityState
 8. compute_hash()              → SHA-256 over canonical JSON
 9. NormalizedResult            → INSERT into availability_snapshots  (every check)
10. ChangeDetector.decide()     → compares watch.current_state (from SQLite)
11. Guard A: meaningful?        → no  → log only, stop here
12. Guard B: dedupe_key seen?   → yes → log "suppressed duplicate", stop here
13. notifier.send_*()           → Telegram HTML message (or console in dry-run)
14. notification_events         → INSERT (the dedupe memory)
15. watches row UPDATE          → new state, hash, timestamps, counters
16. monitor_runs INSERT         → duration, attempts, ok/notified
17. Next due time computed      → interval on success, backoff on failure
```

Steps 9, 14, 15 and 16 are what make the bot restart-safe: after a crash it
reloads the state it already reported and stays quiet.

---

## 6. State Machine (All 10 States)

| State | Meaning |
|---|---|
| `UNKNOWN` | Never checked yet, or transient throttle (does not overwrite last-known state in DB) |
| `NOT_FOUND` | The page says the movie doesn't exist |
| `DATE_NOT_AVAILABLE` | Movie exists but not on the target date |
| `SHOW_NOT_AVAILABLE` | Date exists but no shows at the requested theatre/screen |
| `BOOKING_NOT_OPEN` | Shows exist but "Coming Soon" / booking closed |
| `BOOKING_OPEN` | Shows are bookable, but seat counts unknown |
| `SEATS_AVAILABLE` | Seats are visible and at least `min_seats` match |
| `NO_SEATS_AVAILABLE` | Shows bookable but 0 seats free |
| `ERROR` | Parse or network failure (updates DB) |
| `BLOCKED` | Site returned a challenge/captcha (updates DB, stops retrying harder) |

Notification-worthy transitions include:
- Anything → `BOOKING_OPEN` (with reason "your show appeared and is bookable")
- `BOOKING_OPEN` → `SEATS_AVAILABLE` (with reason "seats freed up")
- `SEATS_AVAILABLE` → `NO_SEATS_AVAILABLE` (with reason "sold out")
- N consecutive failures → alert once (threshold configurable)

---

## 7. The Dedup Guarantee (How Spam Is Prevented)

The dedupe key format is:

```
{watch_id}|{previous_state}->{new_state}|{availability_hash}
```

Where `availability_hash` is SHA-256 of the canonical JSON of matched shows
(sorted, no timestamps). This means:

- Same state, same shows, same seats → same hash → dedupe blocks re-notification
- Same state, different shows/seats → different hash → new notification (something changed)
- Different state → different key → new notification

The dedupe table (`notification_events`) is persistent, so even a crash and
restart won't cause a re-notification of an already-alerted event.

`RENOTIFY_COOLDOWN_SECONDS=0` means "block that exact key forever."
`RENOTIFY_COOLDOWN_SECONDS=3600` means "after 1 hour, allow re-notification if it happens again."

---

## 8. Setup — Windows PowerShell

```powershell
git clone https://github.com/<you>/<repo-name>.git
cd <repo-name>

python -m venv venv
.\venv\Scripts\Activate.ps1
# If activation is blocked:
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass

python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
python scripts/init_db.py
```

Verify:
```powershell
pytest -v
python scripts/demo.py
```

## 9. Setup — Linux / macOS

```bash
git clone https://github.com/<you>/<repo-name>.git
cd <repo-name>

python3 -m venv venv
source venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
python scripts/init_db.py
pytest -v
```

## 10. Playwright Installation

Only needed for the real BookMyShow adapter.

```bash
pip install playwright
playwright install chromium         # ~150 MB
# On Linux:
playwright install-deps chromium
```

For headless server deployment (no display):
```bash
sudo apt install xvfb libgbm-dev
xvfb-run --server-args="-screen 0 1280x1024x24" python -m app.main run
```

## 11. Telegram Bot Creation

1. Open Telegram → search **@BotFather** → `/newbot`
2. Choose a display name → then a username ending in `bot`
3. BotFather sends a token like `123456789:AAH...` → put in `.env` as `TELEGRAM_BOT_TOKEN`
4. Send `/start` to *your* new bot (bots cannot message you first)
5. Get your numeric user ID from **@userinfobot** or from your own bot's `/start` reply
6. Fill in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAH...
   TELEGRAM_CHAT_ID=987654321
   ALLOWED_TELEGRAM_USER_IDS=987654321
   DRY_RUN=false
   ```
7. Test: `python -m app.main test-notification`

If you leak a token: `/revoke` on BotFather and rotate immediately.

---

## 12. Configuration Reference (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(empty)* | Empty → console notifier only |
| `TELEGRAM_CHAT_ID` | *(empty)* | Where notifications go |
| `ALLOWED_TELEGRAM_USER_IDS` | *(empty)* | **Empty = nobody authorised** (fail-closed) |
| `TEST_MODE` | `true` | Forces fake source everywhere |
| `DRY_RUN` | `true` | Print notifications, don't send |
| `DATABASE_PATH` | `data/monitor.db` | git-ignored |
| `LOG_LEVEL` / `LOG_FILE` | `INFO` / `data/logs/monitor.log` | |
| `DEFAULT_POLL_INTERVAL_SECONDS` | `60` | Safe default |
| `MIN_POLL_INTERVAL_SECONDS` | `30` | Hard floor |
| `FETCH_TIMEOUT_SECONDS` | `30` | Per attempt |
| `MAX_RETRIES` | `3` | Attempts per check |
| `BACKOFF_BASE_SECONDS` / `_MAX_` | `5` / `300` | Exponential + jitter |
| `MAX_CONCURRENT_CHECKS` | `4` | Across all watches |
| `ERROR_NOTIFY_THRESHOLD` | `3` | Alert after N consecutive failures |
| `RENOTIFY_COOLDOWN_SECONDS` | `3600` | Dedupe window; `0` = forever |
| `ENABLE_BOOKMYSHOW_SOURCE` | `false` | Read §23 first |
| `BOOKMYSHOW_MIN_INTERVAL_SECONDS` | `60` | Per-host politeness floor |
| `BOOKMYSHOW_HEADLESS` | `true` | Chromium visibility |
| `RESPECT_ROBOTS_TXT` | `true` | Recommend leaving on for public deployments |

---

## 13. CLI Command Reference

```powershell
python -m app.main add-watch                      # interactive prompts
python -m app.main add-watch --movie "Avengers" --city "Hyderabad" `
    --date 2026-09-25 --theatre "Art Cinemas" --time-from 15:00 --time-to 22:00 `
    --source bookmyshow --source-url "https://..." --keep-monitoring

python -m app.main list-watches
python -m app.main status
python -m app.main check <id>                     # one check, right now
python -m app.main disable-watch <id>
python -m app.main enable-watch <id>
python -m app.main remove-watch <id>
python -m app.main test-notification
python -m app.main simulate booking-open --cycles 6
python -m app.main run                            # monitor + Telegram bot
python -m app.main run --no-bot                   # scheduler only
python -m app.main run --dashboard                # also serve localhost:8765
```

Batch delete watches:
```powershell
5..10 | ForEach-Object { python -m app.main remove-watch $_ }
```

Force-stop background Python (Windows):
```powershell
Stop-Process -Name "python" -Force -ErrorAction SilentlyContinue
```

---

## 14. Telegram Command Reference

| Command | Purpose |
|---|---|
| `/start` | Register + show your chat/user ID |
| `/help` | Full help text |
| `/watch` | Interactive step-by-step add |
| `/watches` | List all watches with state |
| `/status` | Process + DB summary |
| `/pause N` `/resume N` `/remove N` | Manage watches |
| `/setinterval N seconds` | Change polling interval |
| `/test` | Am I authorised? |
| `/test_notification` | Send a test message |
| `/simulate scenario [id]` | Run the fake source once |
| `/cancel` | Cancel current `/watch` conversation |

---

## 15. Interactive `/watch` Flow (Step-by-Step)

9 conversation states, one field per step:

1. **Movie** — supports fuzzy partial names ("avengers" matches "Avengers Endgame Encore")
2. **City** — free text
3. **Date** — YYYY-MM-DD; validated against Python `date.fromisoformat`
4. **Theatre** — supports fuzzy partial names ("PCX" matches "Prasads PCX")
5. **Screen** — specific screen number or "any"
6. **Format** — EPIQ / IMAX / 4DX / 3D / 2D / PCX / any
7. **Timing** — morning / afternoon / evening / night / any / range like `18:00-22:00`
8. **Source** — fake or bookmyshow
9. **URL** (only if bookmyshow) — must be a URL *you* pasted from your browser

If the URL contains a date (e.g., `/20260925`) that differs from step 3's date,
the bot asks:
- "Paste the correct URL for {your date}"
- OR reply "use url date" to switch to the URL's date

After creation, the bot immediately runs one check and reports:
- `BOOKING_OPEN` → lists the actual showtimes
- `SHOW_NOT_AVAILABLE` → confirms it will keep watching
- `ERROR` / `BLOCKED` → explains what happened

---

## 16. Fake Source Simulation Scenarios

```bash
python -m app.main simulate booking-open --cycles 6
```

Available scenarios: `booking-open`, `seats-return`, `always-closed`,
`always-seats`, `blocked`, `flaky`, `missing-movie`.

Expected output:
```
cycle 1:            UNKNOWN -> BOOKING_NOT_OPEN   notified=False
cycle 2:   BOOKING_NOT_OPEN -> BOOKING_NOT_OPEN   notified=False
cycle 3:   BOOKING_NOT_OPEN -> BOOKING_OPEN       notified=True
cycle 4:       BOOKING_OPEN -> SEATS_AVAILABLE    notified=True
cycle 5:    SEATS_AVAILABLE -> SEATS_AVAILABLE    notified=False
cycle 6:    SEATS_AVAILABLE -> SEATS_AVAILABLE    notified=False
```

Proves that polling frequency and notification frequency are decoupled.

---

## 17. Test Suite (117 Tests)

```bash
pytest                          # everything
pytest -v                       # verbose
pytest tests/test_end_to_end_fake.py -v
pytest -k "transition or dedupe"
pytest --tb=short -x            # stop at first failure
```

Coverage areas: models & validation, UTC↔IST handling, SQLite persistence
including cascade deletes, show/seat filtering, all 10 states, deterministic
hashing, transition rules, duplicate prevention, retries and backoff, scheduler
non-overlap and failure isolation, message formatting, parser fixtures, CLI
flow, and the full fake end-to-end run. No test touches the network.

---

## 18. Dry-Run Mode

`DRY_RUN=true` swaps `TelegramNotifier` for `ConsoleNotifier`. State detection,
persistence, dedupe and logging behave identically; only delivery changes.

`TEST_MODE=true` forces every watch onto the fake source regardless of its
`source` field. Both safety switches are independent.

---

## 19. Logging & Debugging

Logs go to console + `data/logs/monitor.log` (rotating, 1 MB × 3).

```bash
LOG_LEVEL=DEBUG python -m app.main check 1
Get-Content data\logs\monitor.log -Wait         # PowerShell
tail -f data/logs/monitor.log                    # bash
```

Inspect the DB:
```bash
sqlite3 data/monitor.db "SELECT id, movie_name, current_state, last_checked_at FROM watches;"
sqlite3 data/monitor.db "SELECT state, availability_hash, created_at FROM availability_snapshots WHERE watch_id=1 ORDER BY id DESC LIMIT 5;"
sqlite3 data/monitor.db "SELECT dedupe_key, delivered, created_at FROM notification_events;"
sqlite3 data/monitor.db "SELECT watch_id, state, duration_ms, attempts, error FROM monitor_runs ORDER BY id DESC LIMIT 10;"
```

---

## 20. Restart Recovery

Nothing lives only in memory. On restart:

1. `Database.initialise()` re-applies schema (idempotent)
2. Scheduler loads enabled watches from SQLite including current_state, hash, failures
3. Every watch gets an immediate first check
4. If state hasn't changed, silence. Even if it fires, dedupe blocks a repeat.

Verified by `test_state_survives_a_restart` and `scripts/demo.py`.

---

## 21. Timing Reality Check

With a 60-second interval, a change occurring 1 second after a check is
discovered on the *next* check.

```
up to 60 s (waiting for next poll)
  + 0.1–15 s (fetching; a real browser is slower)
  + ~0.01 s (parsing, state, hashing)
  + 0.2–2 s (Telegram delivery)
─────────────────────────────
≈ 60–75 s typically
```

**This is not real-time.** It cannot beat a human already on the booking page,
and it cannot reserve anything. If a show sells out in 10 seconds, a 60-second
poller will usually be too late. That is an honest limit, not a bug.

Polling faster is counterproductive: increases site load, makes blocking more
likely, does not put you earlier in any queue.

---

## 22. Rate Limiting & Politeness

- Default interval 60s; hard floor 30s
- Real adapter adds per-host 60–300s minimum on top; requests inside that
  window return `RATE_LIMITED` and are *skipped*, not queued
- `MAX_CONCURRENT_CHECKS=4` caps parallel work
- Exponential backoff with jitter (5s → 300s ceiling) on failures
- `BLOCKED` never triggers a "try harder" path

---

## 23. BookMyShow Adapter Reality (What I Verified)

1. Plain HTTP `GET` returns **HTTP 403** with Cloudflare "Sorry, you have been
   blocked" interstitial. A `requests`/`httpx` scraper cannot work, and making
   it work would mean defeating a bot-protection system. **This project does not do that.**
2. `robots.txt` disallows for `User-agent: *`: `/data/`, `/getJSData/`,
   `/getHTML*`, `/m4/`, `/m5/`, `/partners/`, `/payment*`, `/order-summary*`,
   `/booking-details*`. All are on our permanent deny-list.
3. **There is no public, documented BookMyShow data API.** Anything marketed
   as one is a third-party reseller.

**Therefore the adapter:**
- Ships **disabled** (`ENABLE_BOOKMYSHOW_SOURCE=false`)
- Only visits a `source_url` **you** pasted after opening it in your own browser
- Runs one ordinary Playwright Chromium page: no stealth, no UA tricks, no
  proxies, no captcha solvers, no login
- Honours `robots.txt`, fails closed if unreadable
- Enforces per-host minimum interval
- Returns `BLOCKED` on challenge pages, logs why, doesn't retry harder
- Returns `ERROR` on parse failure, saves failing HTML to `data/debug/`
- **Cannot** click Book, select seats, or reach payment

**What will break:** the JS/CSS selectors. BookMyShow is a client-side React app
with hashed class names that change. Expect to update `parse_showtimes_html()`.

---

## 24. Fuzzy Matching (Movie Names & Theatre Names)

`_fuzzy_match_score(query, target)` computes token overlap:

- Tokenises query into words ≥3 chars
- Counts how many tokens appear in the target (case-insensitive substring)
- Returns matches / total tokens (0.0–1.0)

Examples:
- `"paradise"` vs `"The Paradise"` → 1.0
- `"avengers"` vs `"Avengers: Endgame Encore"` → 1.0
- `"pcx"` vs `"Prasads PCX"` → 1.0 (short token special-cased)
- `"paradise"` vs `"Jurassic Park"` → 0.0

Movie matching applied when navigating from venue pages to specific movie
pages. Theatre matching applied when filtering shows from a buytickets page.

---

## 25. URL Classification (buytickets / movie / venue)

`_classify_url(url_lower)` returns one of:

- `'buytickets'`: `/buytickets/` in URL → parse showtimes directly, filter by theatre
- `'movie'`: `/movies/` in URL → look for venues; if none, check "coming soon"
- `'venue'`: `/cinemas/` or `/venues/` → find movie link via fuzzy match, navigate
- `'unknown'`: fallback

For `buytickets` URLs, the venue is *implicit* in the URL — if JS extraction
returns `venue="unknown"` on a buytickets page, we assume the shows belong to
that venue (special case in `parse_showtimes_html`).

---

## 26. Playwright JS Extraction Strategy

BookMyShow renders with React and hashed class names. Traditional CSS selectors
fail. Instead we run `page.evaluate()` with JavaScript that:

1. Finds all clickable elements (`a`, `button`, `[role="button"]`, `[class*="show"]`, etc.)
2. Filters by regex `/\d{1,2}[:.]\d{2}\s*(AM|PM|am|pm)?/`
3. Walks up 8 parent nodes looking for venue name via multiple selectors
4. Falls back to page title / `h1` if no venue node is found
5. Detects sold-out via className substrings

This is far more resilient than CSS selectors.

---

## 27. API Response Interception

While the page loads, we register `page.on("response", ...)` and capture any
XHR/fetch response whose URL contains: `showtime`, `cinema`, `venue`, `session`,
`event`, `getdata`, `getjsdata`, `schedule`, `availability`, `moviedetails`,
`theatreshow`, `quickbook`.

These often contain the raw showtime JSON as a fallback if DOM extraction fails.

---

## 28. Security & Privacy

**The empty allowlist:** `ALLOWED_TELEGRAM_USER_IDS=` authorises **nobody**.
This is deliberate. Telegram bot usernames are discoverable; treating "empty"
as "allow everyone" would let strangers control your bot.

Other practices:
- Bot token in `.env` (git-ignored) and private attribute; never logged/printed
- All SQL parameterised; `test_sql_injection_attempt_is_stored_as_data` proves it
- User text HTML-escaped before Telegram
- Fresh browser context per fetch; no cookies/session persisted
- Dashboard read-only, bound to `127.0.0.1` only
- No credentials, no login, no payment code

---

## 29. All Bugs Hit & Fixes Applied (Case Studies)

**Bug 1: `ZoneInfoNotFoundError` on Windows.**
Fix: `pip install tzdata` — Windows lacks IANA zone data.

**Bug 2: `NameError: name 'should_notify' is not defined`.**
Fix: rewrote `ChangeDetector.decide()` with proper local variables and dedupe_key.

**Bug 3: Backoff test failure `assert 0.05 > 60.0`.**
Fix: scheduler used `base_delay=base, max_delay=base*8.0` instead of fixed constants.

**Bug 4: `ValueError: I/O operation on closed file` in pytest.**
Fix: `SafeStreamHandler` in logging_setup that swallows the exception silently.

**Bug 5: Auto-pause after first notification.**
Fix: `notify_once=False` default in interactive `/watch`; `--keep-monitoring` flag in CLI.

**Bug 6: `409 Conflict: terminated by other getUpdates`.**
Fix: Kill leftover Python processes before restarting bot.

**Bug 7: `robots.txt disallows /buytickets/` → BLOCKED.**
Fix: `RESPECT_ROBOTS_TXT=false` in `.env` for personal use; adapter still refuses
scrapey endpoints via permanent deny-list.

**Bug 8: React hashed class names → parse failures.**
Fix: Ditched CSS-selector strategy; use Playwright `page.evaluate()` + XHR interception.

**Bug 9: `AttributeError: 'ExtBot' object has no attribute 'bot_data'`.**
Fix: Use `tg.bot_data` (the ContextTypes parameter) not `update.get_bot().bot_data`.

**Bug 10: Race condition — interactive check and scheduler check colliding.**
Fix: `SqliteWatchRepository.create()` sets `last_checked_at=now` so scheduler
waits full interval; interactive `_create_watch()` pauses 0.5s and re-fetches
watch state to avoid double-check.

**Bug 11: Date mismatch auto-override without user consent.**
Fix: `pending_url` / `mismatched_url_date` state in `_receive_url` with explicit
confirmation prompt.

**Bug 12: `AttributeError: 'CheckReport' object has no attribute 'outcome'`.**
Fix: Use `report.result.matched_shows` directly (correct field name).

**Bug 13: Transient throttle overwriting `BOOKING_OPEN` → `UNKNOWN` in DB.**
Fix: `record_check()` preserves last-known state on `UNKNOWN` failures but
updates DB on `ERROR`/`BLOCKED` (which tests assert).

**Bug 14: Duplicate notifications on watch creation.**
Fix: Interactive check sleeps 0.5s and re-fetches watch; if state changed
(scheduler beat us to it), we skip our own fetch and reuse the DB result.

**Bug 15: Global throttle × N watches = long cycles.**
Fix: `BOOKMYSHOW_MIN_INTERVAL_SECONDS=30–60` for personal use.

**Bug 16: `httpx.ReadTimeout` on shutdown.**
Not a bug — telegram-bot's final `getUpdates` times out during graceful shutdown.
Already suppressed by python-telegram-bot library.

---

## 30. Common Errors Cheat Sheet

| Symptom | Cause & fix |
|---|---|
| `ModuleNotFoundError: No module named 'app'` | Run from repo root with venv active |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` |
| Pydantic validation error for date | Use `YYYY-MM-DD` |
| Telegram 401/404 | Wrong `TELEGRAM_BOT_TOKEN` |
| Telegram 400 chat not found | Send `/start` to your bot once first |
| Telegram 429 | Sending too fast; raise interval |
| "Not authorised" | Add your ID to `ALLOWED_TELEGRAM_USER_IDS`, restart |
| Notifications print instead of send | `DRY_RUN=true`, or Telegram not configured |
| Every watch returns fake data | `TEST_MODE=true` |
| `state=BLOCKED` on real adapter | Site refused automation; raise interval |
| `parse failure` | BookMyShow changed markup; update parser |
| `Playwright is not installed` | `pip install playwright && playwright install chromium` |
| `database is locked` | Two processes on one DB; run single `app.main run` |
| Dashboard won't start | Port 8765 in use; change `DASHBOARD_PORT` |

---

## 31. Free 24/7 Hosting Options

Ranked by lifetime value:

1. **Oracle Cloud Always Free** — 4 ARM cores, 24 GB RAM, 200 GB. Lifetime free.
2. **Google Cloud e2-micro** — 3 months + $300 credit.
3. **AWS t2.micro** — 12 months free.
4. **Railway.app** — $5/month credit.
5. **Render.com** — 750 hrs/month free.

---

## 32. Oracle Cloud Deployment Steps

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv xvfb libgbm-dev sqlite3 git
git clone https://github.com/<you>/<repo>.git
cd <repo>
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
nano .env                       # fill in Telegram creds
python3 scripts/init_db.py

# Run in tmux so it survives SSH disconnect:
tmux new -s monitor
xvfb-run --server-args="-screen 0 1280x1024x24" python3 -m app.main run
# Ctrl+B, then D to detach; tmux attach -t monitor to reattach
```

---

## 33. Publishing Safely to GitHub

**Commit:** `app/`, `tests/`, `scripts/`, `.env.example`, `.gitignore`,
`requirements.txt`, `pyproject.toml`, `README.md`, `Dockerfile`,
`docker-compose.yml`, `data/.gitkeep`.

**Never commit:** `.env`, `*.db`, `*.db-wal`, `*.db-shm`, `data/**` (except
`.gitkeep`), `logs/`, `*.log`, browser profiles, `*.har`, `cookies*.json`,
`storage_state*.json`, `__pycache__/`, `.venv/`, `.pytest_cache/`, HTML dumps,
`DOC.md`.

```bash
git status --porcelain | grep -E "\.env$|\.db|\.log|storage_state|cookies|DOC\.md"
# MUST be empty
git check-ignore -v .env data/monitor.db DOC.md
```

**If a token leaks:** revoke via `/revoke` in BotFather immediately, get a new
one, then clean history with `git filter-repo` or BFG. Rewriting history alone
is not enough — assume any pushed secret is compromised.

---

## 34. Optional Dashboard

```bash
python -m app.main run --dashboard      # http://127.0.0.1:8765
```

Read-only, stdlib only, auto-refreshes every 15s.

---

## 35. Optional Docker

```bash
docker compose up --build
docker compose exec monitor python -m app.main list-watches
docker compose down
```

`.env` passed at runtime (never baked into image); `./data` mounted for SQLite
persistence.

---

## 36. Honest Limitations

**Works reliably locally, today:**
- Full pipeline with `FakeMovieSource`
- SQLite persistence, restart recovery, duplicate suppression
- All 10 states and transitions
- Deterministic normalization and hashing
- Multi-watch scheduling, backoff, failure isolation, graceful shutdown
- Telegram notifications with fail-closed allowlist
- CLI, dry-run, logging, tests, dashboard

**Depends on BookMyShow's current behaviour:**
- Whether the real adapter returns usable data
- Whether CSS/JS selectors still work
- Whether seat counts are exposed on public pages

**Will break by design:** parser after any redesign; adapter when anti-bot
tightens. Failures are contained — one broken watch doesn't take others down.

**Never represent as production:**
- Notification-only, no booking, no payment
- No real-time guarantee (§21)
- One process, one SQLite file, one user
- No dashboard auth, no HA, no metrics, no alerting, no migrations
- Notifications missable if process down, Telegram unreachable, site blocking
- Does not beat a human on the booking page

---

## 37. What Top-MNC Interviewers Will Notice

**Yes, this project follows engineering rules top MNCs value.** Specifically:

- **Separation of concerns** — every module has one job (sources report facts,
  state.py decides state, formatter.py builds messages, delivery is separate)
- **Dependency injection** — no global singletons; everything wired in `main.py`
- **Interface abstractions** — `MovieSource`, `NotificationService`,
  `WatchRepository` are all Protocol/ABC, making testing and swapping trivial
- **Deterministic testing** — 117 tests, no network, all offline via fake source
- **Fail-closed security** — empty allowlist denies all, not allows all
- **Idempotent database schema** — safe to re-run init
- **Restart recovery** — nothing lives only in memory
- **Exponential backoff with jitter** — textbook retry pattern
- **Per-host rate limiting** — respectful automation
- **Deduplication via canonical hash** — SHA-256 over sorted JSON
- **State machine** — 10 explicit states with clear transitions
- **Async I/O throughout** — proper use of `asyncio`, no blocking calls
- **Structured logging** — every failure path has a log line
- **Graceful shutdown** — in-flight work allowed to finish
- **Parameterised SQL** — SQL injection literally impossible
- **Robots.txt respect** — legal + ethical automation
- **Semantic error handling** — `BLOCKED` ≠ `ERROR` ≠ `NOT_FOUND`
- **Configuration in one place** — no `os.environ` scattered across modules

**What could still be strengthened for a top-tier hire evaluation:**
- Add CI/CD (`.github/workflows/tests.yml`) — 30 minutes of work
- Add coverage badge and pin coverage ≥ 90%
- Add type checking with `mypy --strict`
- Add pre-commit hooks (black, ruff, mypy)
- Add architectural diagram as PNG in README
- Add a `CHANGELOG.md`
- Add semantic versioning tags
- Add contributor guidelines and CoC

Do those and you have a genuinely portfolio-grade project.

---

## 38. Interview Talking Points (STAR-Format Ready)

**Situation:** Wanted to learn end-to-end system design without inventing a
throwaway CRUD app. Chose a real-world problem: notifying about movie ticket
availability.

**Task:** Build a monitor that (a) actually works, (b) is legally and ethically
defensible, (c) demonstrates every engineering practice worth showing.

**Action:**
- Designed a pluggable adapter pattern so `FakeMovieSource` and
  `BookMyShowSource` are interchangeable — enabled offline testing of the entire
  pipeline
- Modelled availability as a 10-state machine with explicit transitions rather
  than boolean flags (which would lie: "sold out" vs "unknown" vs "coming soon"
  are all different things)
- Solved the dedup problem with SHA-256 canonical hashing so the same
  observation never spams the user twice, even after crash+restart
- Chose SQLite with WAL mode over Postgres because the workload doesn't justify
  the ops burden; still parameterised every query
- Handled the "site blocks scrapers" reality by making the adapter
  fail-closed on robots.txt, refuse to construct URLs (only visits what the
  user pasted), and return `BLOCKED` rather than retry-harder when challenged
- Used Playwright with JavaScript extraction rather than CSS selectors because
  BookMyShow's React uses hashed class names — this is more resilient
- Added a fail-closed authorisation model (empty allowlist = deny all) because
  Telegram usernames are discoverable

**Result:** 117 passing tests. Verified end-to-end on real BookMyShow pages.
Successfully detected showtimes at ART Cinemas Vanasthalipuram for a target date
weeks in advance and delivered a Telegram notification within 60 seconds of the
page becoming available. Discovered and fixed 16 distinct bugs across race
conditions, timezone handling, database persistence, and message formatting.

---

## 39. Future Extensions

- Email notifier (implement `NotificationService`, nothing else changes)
- `SEATS_AVAILABLE → NO_SEATS_AVAILABLE` "it's gone" notification
- Swap scheduler for APScheduler (`max_instances=1` per job)
- `price_drop` state
- Web dashboard with authentication
- Multi-tenant support (different allowlists per watch)
- Prometheus metrics exporter
- Webhook notifier for Discord/Slack

---

## 40. Server Infrastructure — Oracle Cloud Always Free

### Why Oracle Cloud

| Criteria | Oracle Always Free | Next Best (Google) |
|---|---|---|
| Duration | Lifetime | 3 months |
| RAM | 24 GB | 1 GB |
| CPU | 4 ARM cores | 0.25 vCPU |
| Storage | 200 GB | 30 GB |
| Cost | $0 forever | $0 → $$$ |

### Server Specifications

- **Provider**: Oracle Cloud Infrastructure (OCI) Always Free
- **Shape**: VM.Standard.A1.Flex (ARM Ampere)
- **Allocated**: 1 OCPU, 6 GB RAM (of 24 GB total)
- **OS**: Ubuntu 22.04 LTS aarch64 (ARM64)
- **Region**: ap-hyderabad-1 (closest to user)
- **Static IP**: (assigned after creation)
- **Ingress rules**: SSH (22) only; no HTTP needed (bot uses outbound polling)

### Setup Steps (one-time)

```bash
# 1. SSH into server
ssh -i ~/.ssh/oracle_key ubuntu@<PUBLIC_IP>

# 2. System updates
sudo apt update && sudo apt upgrade -y

# 3. Install Python 3.11 + dependencies
sudo apt install -y python3.11 python3.11-venv python3-pip \
    xvfb chromium-browser git

# 4. Clone repository
git clone https://github.com/YOUR_USERNAME/showtime-sentinel.git
cd showtime-sentinel

# 5. Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# 6. Install dependencies
pip install -r requirements.txt

# 7. Install Playwright Chromium (ARM64)
playwright install chromium
playwright install-deps

# 8. Create .env
cp .env.example .env
nano .env  # Fill in TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, etc.

# 9. Initialize database
python scripts/init_db.py

# 10. Test run
xvfb-run --server-args="-screen 0 1280x1024x24" python -m app.main run

# 11. Create systemd service for auto-start
sudo nano /etc/systemd/system/showtime-sentinel.service

*End of engineering notebook. This file lives outside git for a reason.*