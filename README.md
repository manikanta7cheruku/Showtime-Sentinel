# Movie Ticket Monitor

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-117%20passing-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: PEP8](https://img.shields.io/badge/code%20style-PEP8-black.svg)]()

> A notification-only movie ticket availability monitor with a pluggable adapter architecture, deterministic deduplication, and a fail-closed Telegram bot.
> **This project observes and notifies. It does not book, purchase, or bypass any protection.**

---

## Highlights

- **Pluggable source adapters** — `FakeMovieSource` for testing, `BookMyShowSource` for real use
- **10-state availability machine** with explicit, testable transitions
- **SHA-256 deterministic deduplication** — the same observation never notifies twice, even after restart
- **Fail-closed Telegram authorisation** — empty allowlist denies all users
- **117 passing tests, zero network calls** — the entire pipeline is provable offline
- **Playwright-powered browser automation** with API response interception, JS extraction, fuzzy movie/theatre matching
- **Async I/O throughout** — one asyncio process, many watches, no overlap
- **Exponential backoff with jitter** — respects rate limits and per-host throttling
- **Graceful shutdown** — in-flight checks allowed to finish
- **SQLite WAL mode** — restart-safe persistence with parameterised queries
- **UTC internally, IST displayed** — clean timezone discipline
- **Optional read-only localhost dashboard** — stdlib-only HTTP server

---

## Quick Start

### Requirements
- Python 3.11+
- Git

### Windows PowerShell

```powershell
git clone https://github.com/<you>/<repo-name>.git
cd <repo-name>

python -m venv venv
.\venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
python scripts/init_db.py
pytest -v
```

### Linux / macOS

```bash
git clone https://github.com/<you>/<repo-name>.git
cd <repo-name>

python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
python scripts/init_db.py
pytest -v
```

### Try the fake source end-to-end (no network)

```bash
python scripts/demo.py
python -m app.main simulate booking-open --cycles 6
```

---

## Configuration

Copy `.env.example` to `.env` and fill in:

```dotenv
TELEGRAM_BOT_TOKEN=123456789:AAH...
TELEGRAM_CHAT_ID=987654321
ALLOWED_TELEGRAM_USER_IDS=987654321
TEST_MODE=false
DRY_RUN=false
ENABLE_BOOKMYSHOW_SOURCE=true
BOOKMYSHOW_HEADLESS=true
DEFAULT_POLL_INTERVAL_SECONDS=60
```

See `.env.example` for the full list.

---

## Usage

### CLI

```bash
# Interactive
python -m app.main add-watch

# One-shot
python -m app.main add-watch --movie "Avengers" --city "Hyderabad" \
    --date 2026-09-25 --theatre "Art Cinemas" \
    --source bookmyshow --source-url "https://..." --keep-monitoring

# Manage
python -m app.main list-watches
python -m app.main check <id>
python -m app.main pause-watch <id>
python -m app.main resume-watch <id>
python -m app.main remove-watch <id>

# Run
python -m app.main run                    # monitor + Telegram bot
python -m app.main run --no-bot           # scheduler only
python -m app.main run --dashboard        # + localhost dashboard
```

### Telegram

Send `/start` to your bot, then:

- `/watch` — interactive step-by-step add
- `/watches` — list all watches
- `/status` — process + DB summary
- `/pause N` `/resume N` `/remove N` — manage watches
- `/setinterval N seconds` — change polling
- `/test_notification` — verify delivery
- `/simulate <scenario>` — dry-run fake source

---

## Setting Up Telegram

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`
2. Copy the token BotFather returns — put in `.env` as `TELEGRAM_BOT_TOKEN`
3. Send `/start` to your new bot
4. Message [@userinfobot](https://t.me/userinfobot) to get your numeric ID
5. Put your ID in `.env` as `TELEGRAM_CHAT_ID` and `ALLOWED_TELEGRAM_USER_IDS`
6. Verify: `python -m app.main test-notification`

---

## Architecture

```
CLI / Telegram Bot
       ↓
   Scheduler (asyncio, per-watch non-overlap)
       ↓
   AvailabilityChecker
   ├─→ MovieSource (Fake | BookMyShow)  → SourcePayload
   ├─→ normalize + state + hash          → NormalizedResult
   ├─→ ChangeDetector                    → TransitionDecision
   ├─→ NotificationService (Console | Telegram)
   └─→ SQLite Repositories (WAL mode)
```

Every abstraction is a `Protocol` or `ABC`, so components are interchangeable
and independently testable.

---

## Running the Real BookMyShow Adapter

The real adapter is **disabled by default**. Before enabling, understand:

- Plain HTTP is blocked by Cloudflare (verified)
- BookMyShow's `robots.txt` disallows scraping endpoints (respected)
- No public API exists
- The adapter uses ordinary Playwright — **no stealth, no captcha bypass, no proxies**
- Enforces per-host minimum interval
- Only visits URLs **you** paste (never constructs URLs)
- Returns `BLOCKED` on challenge pages — never retries harder

Enable via `ENABLE_BOOKMYSHOW_SOURCE=true`. Install Playwright:

```bash
pip install playwright
playwright install chromium
```

---

## Testing

```bash
pytest                          # 117 tests, no network
pytest -v                       # verbose
pytest -k "transition or dedupe"
pytest --tb=short -x
```

Coverage: models, validation, timezone handling, SQLite persistence, cascade
deletes, all 10 states, deterministic hashing, transition rules, dedup, retries,
backoff, scheduler non-overlap, failure isolation, message formatting, parser
fixtures, and full end-to-end fake pipeline.

---

## Security

- **Fail-closed authorisation:** empty `ALLOWED_TELEGRAM_USER_IDS` denies all
- **Parameterised SQL only** — SQL injection literally impossible
- **HTML-escaped Telegram messages** — user text never breaks markup
- **No persistent browser storage** — fresh context per fetch, no cookies saved
- **Read-only dashboard** — bound to `127.0.0.1` only
- **Token in `.env`** — git-ignored, never logged or printed
- **No credentials, no login, no payment code** anywhere in the project

---

## Project Structure

```
app/
├── bot/                  Telegram command handlers + interactive /watch
├── database/             Schema + connection + parameterised repositories
├── models/               Pydantic models, enums, watch/result contracts
├── monitoring/           Scheduler, checker, filters, state, normalizer
├── notifications/        Formatter, Console + Telegram delivery
├── sources/              MovieSource ABC + Fake + BookMyShow adapters
├── utils/                Retry, timeutil, logging, robots
├── web/                  Optional dashboard (stdlib http.server)
├── config.py             Central Pydantic settings
└── main.py               CLI + wiring
tests/                    117 tests, no network
scripts/                  init_db.py, demo.py
```

---

## License

MIT License — see [LICENSE](LICENSE).

Users are responsible for complying with the terms of any website they point
this tool at. This project is educational and notification-only.

---

## Acknowledgements

Built with:

- [pydantic](https://docs.pydantic.dev/) — data validation
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) — Telegram API
- [playwright](https://playwright.dev/python/) — headless browser
- [httpx](https://www.python-httpx.org/) — async HTTP
- [beautifulsoup4](https://www.crummy.com/software/BeautifulSoup/) — HTML parsing