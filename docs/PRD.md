# Product Requirements Document

**Product**: Showtime Sentinel
**Version**: 1.0
**Status**: Active Development
**Owner**: Manikanta Cheruku
**Repository**: https://github.com/manikanta7cheruku/Showtime-Sentinel
**Last Updated**: 2025

---

## 1. Executive Summary

Showtime Sentinel is an automated movie ticket availability monitoring system for BookMyShow (India's largest movie ticketing platform). It continuously polls specified movies, theatres, dates, and formats, and sends instant Telegram notifications when booking opens or seats become available.

The system is designed for personal use with a small group of trusted users (1–5). It is a portfolio-grade engineering project demonstrating asynchronous Python, resilient distributed system patterns, browser automation, and production deployment practices.

---

## 2. Problem Statement

### 2.1 Context

Movie ticket booking in India follows a predictable pattern for high-demand releases:

1. Studios announce booking-open dates
2. Bookings open at unpredictable times (often late night or early morning)
3. Premium format screenings (IMAX, PCX, EPIQ, 4DX, Dolby Atmos) sell out within minutes
4. Users who miss the initial window must settle for inferior seats, formats, or dates

### 2.2 User Pain Points

| Pain Point | Frequency | Impact |
|---|---|---|
| Missing the exact moment bookings open | Every major release | Cannot get preferred format/seats |
| Manually refreshing BookMyShow multiple times per day | Daily during release windows | Time waste, frustration |
| Not knowing which theatres will screen a specific movie | Every release | Suboptimal theatre choice |
| Premium formats selling out before user notices | Every IMAX/PCX release | Forced to standard formats |

### 2.3 Business Case (for portfolio context)

This project demonstrates the following engineering competencies to prospective employers:
- Asynchronous I/O and concurrent programming
- Resilient system design (retries, backoff, failure isolation)
- Browser automation and anti-bot evasion patterns
- State machine design and event-driven architecture
- Full-stack ownership (backend, database, CI/CD, deployment)

---

## 3. Solution Overview

An always-on monitoring service that:

1. Accepts user-defined "watches" specifying movie, city, date, theatre, and format preferences
2. Polls BookMyShow at configurable intervals (default 60 seconds)
3. Detects transitions between 10 explicit availability states
4. Sends deduplicated Telegram notifications with direct booking links
5. Runs 24/7 on a free Oracle Cloud VM with automatic restart on failure

---

## 4. Target Users

### 4.1 Primary Persona: "The Serious Movie Fan"

- **Name**: Manikanta (self)
- **Age**: 25–35
- **Tech proficiency**: High (developer)
- **Behavior**: Watches 20+ movies per year, always chooses premium formats
- **Frustration**: Missed booking window for Avengers Endgame IMAX in 2019
- **Goal**: Never miss a booking window again

### 4.2 Secondary Persona: "Trusted Circle"

- Friends and family added via `ALLOWED_TELEGRAM_USER_IDS`
- Maximum 5 users
- Not paying customers — this is not a SaaS product

### 4.3 Explicitly NOT Targeted

- General public (not a SaaS)
- Ticket resellers (system does not purchase tickets)
- Movie theatres (not a B2B product)

---

## 5. Functional Requirements

### 5.1 Watch Management (P0 — Must Have)

| ID | Requirement | Status |
|---|---|---|
| FR-1.1 | User can create a watch via Telegram interactive conversation (9 steps) | Done |
| FR-1.2 | User can create a watch via CLI (`add-watch` command) | Done |
| FR-1.3 | User can list all watches with current status via `/watches` | Done |
| FR-1.4 | User can pause an active watch via `/pause <id>` | Done |
| FR-1.5 | User can resume a paused watch via `/resume <id>` | Done |
| FR-1.6 | User can remove a watch permanently via `/remove <id>` | Done |
| FR-1.7 | User can configure per-watch polling interval via `/setinterval` | Done |
| FR-1.8 | System runs immediate availability check upon watch creation | Done |

### 5.2 Availability Detection (P0 — Must Have)

| ID | Requirement | Status |
|---|---|---|
| FR-2.1 | System detects 10 explicit states: UNKNOWN, NOT_FOUND, DATE_NOT_AVAILABLE, SHOW_NOT_AVAILABLE, BOOKING_NOT_OPEN, BOOKING_OPEN, NO_SEATS_AVAILABLE, SEATS_AVAILABLE, ERROR, BLOCKED | Done |
| FR-2.2 | Fuzzy matching for partial movie names (e.g., "paradise" matches "The Paradise") | Done |
| FR-2.3 | Fuzzy matching for partial theatre names (e.g., "PCX" matches "Prasads PCX") | Done |
| FR-2.4 | Supports all three BookMyShow URL types: movie detail, venue, buy-tickets | Done |
| FR-2.5 | Extracts showtimes with format labels (Standard, IMAX, 3D, 4DX, PCX, EPIQ) | Done |
| FR-2.6 | Filters showtimes by timing preference (morning, afternoon, evening, night, any) | Done |
| FR-2.7 | System never constructs URLs — only navigates to user-provided URLs | Done |
| FR-2.8 | System validates URL date matches watch date; prompts user on mismatch | Done |

### 5.3 Notifications (P0 — Must Have)

| ID | Requirement | Status |
|---|---|---|
| FR-3.1 | Send Telegram message on state change | Done |
| FR-3.2 | Console output when in dry-run mode | Done |
| FR-3.3 | SHA-256 canonical hash prevents duplicate notifications within time window | Done |
| FR-3.4 | Notification includes direct booking link for user's target date | Done |
| FR-3.5 | Display both raw enum name and human-friendly state label | Done |
| FR-3.6 | Include matched showtimes with venue, time, and format | Done |

### 5.4 Resilience (P0 — Must Have)

| ID | Requirement | Status |
|---|---|---|
| FR-4.1 | Exponential backoff with jitter on repeated failures | Done |
| FR-4.2 | Per-host request throttling (minimum interval between BookMyShow requests) | Done |
| FR-4.3 | One watch failing does not block other watches | Done |
| FR-4.4 | Graceful shutdown on SIGINT/SIGTERM (finishes in-flight checks) | Done |
| FR-4.5 | Restart recovery — resumes from last-known state after crash/reboot | Done |
| FR-4.6 | Transient UNKNOWN failures preserve last-known state in database | Done |

### 5.5 Security (P0 — Must Have)

| ID | Requirement | Status |
|---|---|---|
| FR-5.1 | Fail-closed authentication — empty `ALLOWED_TELEGRAM_USER_IDS` blocks all commands | Done |
| FR-5.2 | No secrets committed to git (verified via `.gitignore` and pre-commit hook) | Done |
| FR-5.3 | All SQL queries use parameterized statements (no injection) | Done |
| FR-5.4 | robots.txt compliance configurable via environment variable | Done |
| FR-5.5 | Sensitive fields (bot token, chat ID) loaded only from `.env` file | Done |

### 5.6 Observability (P1 — Should Have)

| ID | Requirement | Status |
|---|---|---|
| FR-6.1 | Structured logging with configurable level | Done |
| FR-6.2 | Optional local dashboard on port 8765 | Done |
| FR-6.3 | CLI status command shows all active watches | Done |
| FR-6.4 | systemd journal integration on production server | Planned |

---

## 6. Non-Functional Requirements

| ID | Requirement | Target | Actual |
|---|---|---|---|
| NFR-1 | Detection latency (booking opens → notification received) | Less than 90 seconds | 30–60 seconds |
| NFR-2 | Memory usage — idle | Less than 100 MB | ~60 MB |
| NFR-3 | Memory usage — during browser check | Less than 1 GB | ~600 MB |
| NFR-4 | Test count | 100+ | 117 |
| NFR-5 | Test pass rate | 100% | 100% |
| NFR-6 | Supported Python versions | 3.11+ | 3.11, 3.12 |
| NFR-7 | Monthly hosting cost | $0 | $0 (Oracle Always Free) |
| NFR-8 | Uptime target | 99% monthly | Expected with systemd |
| NFR-9 | Database size after 6 months (5 watches, 60s polling) | Less than 500 MB | Estimated 200 MB |
| NFR-10 | Recovery time after crash | Less than 30 seconds | systemd `RestartSec=10` |

---

## 7. User Stories

### 7.1 Story: Create First Watch

**As** a movie fan
**I want to** create a watch for Avengers Endgame at PCX Hyderabad on Sept 25, 2026
**So that** I get notified the instant booking opens

**Acceptance Criteria**:
- I can start the flow with `/watch` on Telegram
- The bot guides me through 9 questions (movie, city, date, theatre, screen, format, timing, source, URL)
- Partial names are accepted (I can type "avengers" not the full title)
- The bot runs an immediate check and reports current status
- I receive a confirmation with the watch ID

### 7.2 Story: Booking Opens

**As** a user with an active watch
**I want to** be notified within 60 seconds of booking opening
**So that** I can book tickets before they sell out

**Acceptance Criteria**:
- Notification arrives via Telegram
- Notification includes state transition (e.g., "BOOKING_NOT_OPEN → BOOKING_OPEN")
- Notification includes at least one bookable showtime with venue and time
- Notification includes a direct link to the booking page
- No duplicate notification arrives within the deduplication window

### 7.3 Story: Manage Watches

**As** a user with multiple active watches
**I want to** view, pause, resume, and delete watches
**So that** I can manage what I'm monitoring

**Acceptance Criteria**:
- `/watches` shows all watches with current state
- `/pause <id>` stops polling without deleting
- `/resume <id>` restarts polling
- `/remove <id>` deletes permanently with confirmation

---

## 8. Explicit Non-Goals (Out of Scope)

The following features will NOT be implemented. This is deliberate scope discipline.

| Feature | Rationale |
|---|---|
| Automated ticket purchasing | Legal/ethical concerns; monitoring only |
| Payment integration | Out of scope entirely |
| CAPTCHA solving/bypass | Violates BookMyShow terms of service |
| Undocumented API scraping | Uses public browser interface only |
| Multi-tenancy / SaaS platform | Personal tool, not a commercial product |
| Native mobile app | Telegram bot serves as the mobile interface |
| Email or SMS notifications | Telegram is faster, free, and richer |
| Historical price tracking | BookMyShow prices are fixed per format |
| Seat map analysis | Complex, low value for "notify when open" use case |
| Support for platforms other than BookMyShow | Focused scope; pluggable adapter allows future expansion |

---

## 9. Architecture Decisions Record (ADR)

| Decision | Chosen | Alternatives Considered | Rationale |
|---|---|---|---|
| Language | Python 3.11 | Node.js, Go, Rust | Best library ecosystem for browser automation, async, Telegram |
| Async framework | asyncio (stdlib) | Trio, AnyIO | Standard library; widest library compatibility |
| Database | SQLite (WAL mode) | PostgreSQL, MongoDB, Redis | Zero-config; single-file; sufficient for 1–5 users |
| ORM | Raw SQL with parameterized queries | SQLAlchemy, Tortoise | Simplicity; full control; no ORM overhead |
| Browser engine | Playwright + Chromium | Selenium, Puppeteer, requests | Modern API; auto-wait; better anti-bot resilience |
| Bot framework | python-telegram-bot v20+ | Telethon, aiogram | Most mature; async-native; largest community |
| Configuration | Pydantic v2 Settings + .env | ConfigParser, YAML | Type-safe; validation; industry standard |
| Testing | pytest + pytest-asyncio | unittest | Better fixtures; async support; larger ecosystem |
| CI/CD | GitHub Actions | Jenkins, CircleCI, GitLab CI | Free; native GitHub integration |
| Hosting | Oracle Cloud Always Free | AWS, GCP, Azure, Railway | Lifetime free; 24GB RAM; 4 ARM cores |
| Process manager | systemd | supervisord, Docker, pm2 | Native to Linux; no extra dependency |
| Notifications | Telegram Bot API | Email, SMS, Push, Slack | Free; instant; mobile-first; rich formatting |

---

## 10. Success Metrics

| Metric | Target | Measurement Method |
|---|---|---|
| Detection accuracy | 100% for user-provided URLs | Manual verification against BookMyShow website |
| False positive rate | Less than 1% | Weekly review of notification history |
| Notification latency | Less than 60 seconds P95 | Timestamp diff (state change vs Telegram receipt) |
| System uptime | 99%+ monthly | systemd status + journalctl analysis |
| Test pass rate | 100% on every commit | GitHub Actions dashboard |
| Memory leak | Zero unbounded growth | Weekly `systemctl status` memory check |

---

## 11. Risks and Mitigations

| Risk | Impact | Probability | Mitigation |
|---|---|---|---|
| BookMyShow changes HTML structure | Parser breaks silently | Medium | 6 extraction strategies (JS eval, XHR intercept, `__NEXT_DATA__`, regex, CSS, fallback); alerts on ERROR/BLOCKED state |
| BookMyShow blocks bot IP | Cannot monitor from server | Low | Headless mode with realistic user-agent; respectful 60s polling; per-host throttle |
| Oracle reclaims idle VM | Service stops without warning | Low | Monthly login script; systemd keeps CPU active |
| Bot token leaked in git history | Unauthorized access | Occurred once | Fail-closed `ALLOWED_TELEGRAM_USER_IDS`; token revoked; pre-commit hook added |
| Chromium memory leak over days | OOM kill | Medium | systemd `Restart=always`; browser context disposed after each check |
| Test suite becomes stale | Regressions ship | Low | CI runs on every push; 3 Python versions tested |
| Ethical concerns from BookMyShow | Cease and desist | Very Low | Notification-only; no purchases; respects robots.txt (configurable); low-frequency polling |

---

## 12. Roadmap

| Phase | Version | Features | Timeline | Status |
|---|---|---|---|---|
| 1 | 1.0 | Core monitoring, Telegram bot, 10 states, 117 tests, dedup, resilience | Completed | Done |
| 2 | 1.1 | CI/CD pipeline, mypy type checking, PRD documentation | Current | In Progress |
| 3 | 1.2 | Oracle Cloud deployment, systemd service, journalctl logging | Next | Planned |
| 4 | 1.3 | Dockerfile for reproducible deployment | Future | Planned |
| 5 | 2.0 | Additional notification channels (email, webhook, Discord) | Future | Idea |
| 6 | 2.1 | Web dashboard with real-time WebSocket updates | Future | Idea |
| 7 | 3.0 | Additional source adapters (Paytm Insider, District) | Future | Idea |

---

## 13. Glossary

| Term | Definition |
|---|---|
| Watch | A user-defined monitoring target (movie + city + date + theatre + format) |
| State | One of 10 explicit availability values for a watch |
| Adapter | Pluggable source module (currently: BookMyShow, Fake for testing) |
| Dedup key | SHA-256 hash of (watch_id, previous_state, new_state, availability_hash) |
| Fail-closed | Security posture where absence of configuration blocks access (opposite of fail-open) |
| Throttle | Enforced minimum time between requests to the same host |
| Jitter | Random component added to retry delay to prevent thundering herd |
| systemd | Linux init system that manages long-running services |
| Always Free | Oracle Cloud tier that never expires (as opposed to trial credits) |

---

**End of Product Requirements Document**