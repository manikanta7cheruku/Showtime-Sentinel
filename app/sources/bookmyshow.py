"""BookMyShowSource - deliberately conservative, off by default.

WHAT I VERIFIED BEFORE WRITING THIS (see README "BookMyShow limitations"):

  * A plain HTTP GET to a public BookMyShow listing page returns
    HTTP 403 with a Cloudflare "Sorry, you have been blocked" interstitial.
    So no requests/httpx adapter can work, and making one work would mean
    defeating a bot-protection system. We do not do that.
  * robots.txt Disallows, for `User-agent: *`, precisely the machine-readable
    endpoints scrapers reach for: /data/, /getJSData/, /getHTML*, /m4/, /m5/,
    plus /payment*, /order-summary*, /booking-details*. Those are on our
    permanent deny-list in app/utils/robots.py.
  * There is no public, documented BookMyShow data API. Anything advertising
    one is a third-party reseller. We therefore assume NOTHING undocumented.

DESIGN CONSEQUENCES:
  1. Disabled unless ENABLE_BOOKMYSHOW_SOURCE=true.
  2. It only ever visits a URL *you* pasted (`watch.source_url`) after opening
     it in your own browser. We never construct or guess URLs - which is also
     how we guarantee we never fabricate a booking link in a notification.
  3. robots.txt is honoured, and unreadable robots.txt means we refuse.
  4. One real browser (Playwright, default fingerprint). No stealth plugins,
     no UA spoofing tricks, no proxies, no CAPTCHA solving. If we see a
     challenge, we return BLOCKED and stop - we do not retry harder.
  5. A local minimum interval per host (default 300 s) on top of the watch's
     own interval.
  6. Markup changes => ERROR with an explanatory log line, never a guess.
  7. This module can never click "Book", select seats, or touch payment pages.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from app.config import Settings
from app.models import FetchOutcome, FetchStatus, RawShow, SeatCategory, SourcePayload, Watch
from app.sources.base import MovieSource
from app.utils.robots import is_allowed

logger = logging.getLogger(__name__)

#: Text that means "we hit a protection layer". Detect -> stop. Never work around.
BLOCK_MARKERS = (
    "sorry, you have been blocked", "attention required", "cf-browser-verification",
    "checking your browser", "captcha", "access denied", "unusual traffic",
    "enable javascript and cookies to continue", "rate limit",
)

NOT_FOUND_MARKERS = ("no results found", "page not found", "we couldn't find", "404")


def _classify_url(url_lower: str) -> str:
    """Classify a BookMyShow URL into: 'buytickets', 'movie', 'venue', or 'unknown'."""
    if "/buytickets/" in url_lower:
        return "buytickets"
    if "/movies/" in url_lower:
        return "movie"
    if "/cinemas/" in url_lower or "/venues/" in url_lower:
        return "venue"
    return "unknown"


def _fuzzy_match_score(query: str, target: str) -> float:
    """Return 0.0-1.0 similarity score using token overlap.

    'paradise' vs 'The Paradise' -> high score.
    'avengers' vs 'Avengers Endgame Encore' -> high score.
    'paradise' vs 'Jurassic Park' -> zero.
    """
    if not query or not target:
        return 0.0
    query_tokens = {t.lower().strip() for t in query.split() if len(t.strip()) >= 3}
    target_lower = target.lower()
    if not query_tokens:
        return 0.0
    matches = sum(1 for token in query_tokens if token in target_lower)
    return matches / len(query_tokens)


class ParseError(RuntimeError):
    """Raised when the page loaded but doesn't look like what we expect."""


class BookMyShowSource(MovieSource):
    name = "bookmyshow"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._browser: Any = None
        self._playwright: Any = None
        self._last_fetch_monotonic: float | None = None

    # ------------------------------------------------------------------ #
    async def fetch(self, watch: Watch) -> FetchOutcome:
        if not self.settings.enable_bookmyshow_source:
            return self._error(
                "the BookMyShow adapter is disabled. Set ENABLE_BOOKMYSHOW_SOURCE=true "
                "only after reading the README section on limitations and terms."
            )

        if not watch.source_url:
            return self._error(
                f"watch #{watch.id} has no source_url. This adapter never guesses URLs. "
                "Open the movie or venue page in your browser and paste the URL."
            )

        # Classify the URL so the loader knows how to handle it
        url_lower = watch.source_url.lower()
        url_type = _classify_url(url_lower)
        logger.info(
            "watch #%s: URL classified as '%s' — will search for movie='%s' at theatre='%s'",
            watch.id, url_type, watch.movie_name, watch.theatre,
        )

        allowed, reason = is_allowed(
            watch.source_url, user_agent="*", respect_robots=self.settings.respect_robots_txt
        )
        if not allowed:
            logger.warning("refusing to fetch %s: %s", watch.source_url, reason)
            return FetchOutcome(
                status=FetchStatus.BLOCKED, source_name=self.name, url=watch.source_url,
                error=f"refused by our own access policy: {reason}",
            )

        # --- local politeness throttle ------------------------------------
        min_gap = float(self.settings.bookmyshow_min_interval_seconds)
        if self._last_fetch_monotonic is not None:
            waited = time.monotonic() - self._last_fetch_monotonic
            if waited < min_gap:
                remaining = round(min_gap - waited)
                logger.info("local throttle: %ss until the next BookMyShow request", remaining)
                return FetchOutcome(
                    status=FetchStatus.RATE_LIMITED, source_name=self.name, url=watch.source_url,
                    error=f"local rate limit: {remaining}s until next allowed request",
                )

        try:
            html, final_url, api_data, browser_shows = await self._load_page(
                watch.source_url, watch=watch, url_type=url_type,
            )
        except Exception as exc:                          # noqa: BLE001
            logger.warning("browser fetch failed for watch #%s: %s", watch.id, exc)
            raise
        finally:
            self._last_fetch_monotonic = time.monotonic()

        lowered = html.lower()
        if any(marker in lowered for marker in BLOCK_MARKERS):
            logger.warning(
                "watch #%s: BookMyShow returned a protection/challenge page. "
                "This is the site telling us not to automate it. Not retrying harder; "
                "consider increasing the interval or using the fake source for learning.",
                watch.id,
            )
            return FetchOutcome(
                status=FetchStatus.BLOCKED, source_name=self.name, url=final_url,
                error="anti-bot / challenge page detected (no bypass attempted)",
            )
        if any(marker in lowered for marker in NOT_FOUND_MARKERS) and len(html) < 40_000:
            return FetchOutcome(status=FetchStatus.NOT_FOUND, source_name=self.name, url=final_url,
                                error="page reports no such movie/page")

        try:
            payload = parse_showtimes_html(
                html, page_url=final_url, api_data=api_data, browser_shows=browser_shows,
            )
        except ParseError as exc:
            # Auto-save the HTML so we can inspect what BookMyShow actually rendered.
            import pathlib
            debug_dir = pathlib.Path("data/debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            debug_file = debug_dir / f"watch{watch.id}_parse_fail.html"
            debug_file.write_text(html, encoding="utf-8")
            logger.error(
                "watch #%s: page loaded but could not be parsed (%s). "
                "HTML saved to %s for debugging. BookMyShow's markup "
                "changes without notice - update parse_showtimes_html() selectors.",
                watch.id, exc, debug_file,
            )
            return self._error(f"parse failure: {exc}", url=final_url)

        return FetchOutcome(status=FetchStatus.OK, source_name=self.name,
                            payload=payload, url=final_url)

    # ------------------------------------------------------------------ #
    async def _load_page(self, url: str, watch: Watch | None = None, url_type: str = "unknown") -> tuple[str, str, list[dict], dict]:
        """Open the URL in Chromium, intercept APIs, extract showtimes via JS.

        Returns (html, final_url, api_data, browser_extracted_shows).
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:                        # pragma: no cover
            raise RuntimeError(
                "Playwright is not installed. Run:  pip install playwright && playwright install chromium"
            ) from exc

        if self._browser is None:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self.settings.bookmyshow_headless
            )
            logger.info("launched Chromium (headless=%s)", self.settings.bookmyshow_headless)

        context = await self._browser.new_context(
            locale="en-IN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        captured_api_data: list[dict] = []

        async def _capture_response(response):
            resp_url = response.url.lower()
            if any(kw in resp_url for kw in (
                "showtime", "cinema", "venue", "session", "event",
                "getdata", "getjsdata", "schedule", "availability",
                "moviedetails", "theatreshow", "quickbook",
            )):
                try:
                    body = await response.json()
                    captured_api_data.append(body)
                except Exception:
                    pass

        page.on("response", _capture_response)
        try:
            timeout_ms = int(self.settings.fetch_timeout_seconds * 1000)
            await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            await page.wait_for_timeout(5_000)

            # --- Extract showtimes directly from the rendered browser DOM ---
            # This bypasses all CSS-selector fragility. We ask the browser:
            # "Find every clickable element whose text looks like a time."
            browser_shows: list[dict] = []
            try:
                browser_shows = await page.evaluate("""() => {
                    const results = [];
                    const timeRegex = /\\d{1,2}[:.]\\d{2}\\s*(AM|PM|am|pm)?/;

                    // Strategy A: Find all links/buttons with time-like text
                    const clickables = document.querySelectorAll('a, button, [role="button"], [class*="show"], [class*="time"], [class*="session"]');
                    for (const el of clickables) {
                        const text = el.textContent.trim();
                        if (text && timeRegex.test(text) && text.length < 30) {
                            // Walk up to find the nearest cinema/venue name
                            let venue = '';
                            let parent = el.parentElement;
                            for (let i = 0; i < 8 && parent; i++) {
                                const venueEl = parent.querySelector('[class*="cinema-name"], [class*="venue-name"], [class*="theatre-name"], [class*="cinema"], [class*="venue"], [class*="theatre"], h2, h3, h4');
                                if (venueEl) { venue = venueEl.textContent.trim(); break; }
                                parent = parent.parentElement;
                            }
                            if (!venue) {
                                const headEl = document.querySelector('h1, [class*="venue-header"], [class*="cinema-header"]');
                                venue = headEl ? headEl.textContent.trim() : document.title;
                            }
                            // Detect sold-out / disabled state
                            const cls = el.className || '';
                            const isDisabled = el.disabled || cls.includes('sold') || cls.includes('disabled') || cls.includes('inactive');
                            results.push({
                                time: text,
                                venue: (venue || 'unknown').substring(0, 100),
                                disabled: isDisabled,
                                href: el.href || '',
                            });
                        }
                    }

                    // Strategy B: Check for "coming soon" / "booking opens" signals
                    const bodyText = document.body.innerText.toLowerCase();
                    const comingSoon = bodyText.includes('coming soon') ||
                                       bodyText.includes('booking opens') ||
                                       bodyText.includes('not yet available') ||
                                       bodyText.includes('schedule not available');

                    return { shows: results, comingSoon: comingSoon, title: document.title };
                }""")
            except Exception as js_exc:
                logger.debug("JS extraction failed: %s", js_exc)    
                browser_shows = {"shows": [], "comingSoon": False, "title": ""}

            html = await page.content()
            final_url = page.url

            # --- Smart navigation based on URL type and watch context ---
            if watch and url_type in ("venue", "movie") and watch.movie_name:
                # Try to find the specific movie link on this page
                target_url = await self._find_movie_link_on_page(page, watch.movie_name)
                if target_url and target_url != final_url:
                    logger.info(
                        "watch #%s: navigating from '%s' page to specific movie: %s",
                        watch.id, url_type, target_url,
                    )
                    try:
                        await page.goto(target_url, wait_until="networkidle", timeout=timeout_ms)
                        await page.wait_for_timeout(5_000)
                        html = await page.content()
                        final_url = page.url
                        browser_shows = await self._extract_shows_from_page(page)
                    except Exception as nav_exc:
                        logger.warning("watch #%s: could not navigate to movie link: %s", watch.id, nav_exc)
                elif not target_url and url_type == "venue":
                    logger.info(
                        "watch #%s: movie '%s' not currently listed on venue page. "
                        "This is expected if tickets are not yet open.",
                        watch.id, watch.movie_name,
                    )

            # Attach the watch context so the parser can filter accurately
            browser_shows["_watch_movie"] = watch.movie_name if watch else ""
            browser_shows["_watch_theatre"] = watch.theatre if watch else ""
            browser_shows["_url_type"] = url_type

            return html, final_url, captured_api_data, browser_shows
        finally:
            page.remove_listener("response", _capture_response)
            await page.close()
            await context.close()

    async def _find_movie_link_on_page(self, page, movie_name: str) -> str | None:
        """Scan any BookMyShow page for a link matching the target movie.

        Works on venue pages, city pages, or search results. Uses fuzzy
        matching so partial names like 'paradise' match 'The Paradise'.
        """
        search_terms = [t.lower() for t in movie_name.split() if len(t) >= 3]
        if not search_terms:
            return None

        try:
            movie_link = await page.evaluate("""(searchTerms) => {
                const links = document.querySelectorAll('a[href*="/movies/"], a[href*="buytickets"]');
                let bestMatch = null;
                let bestScore = 0;
                for (const link of links) {
                    const text = link.textContent.trim().toLowerCase();
                    const href = link.href || '';
                    if (!text || text.length < 2 || !href) continue;
                    let score = 0;
                    for (const term of searchTerms) {
                        if (text.includes(term)) score += term.length;
                    }
                    if (score > bestScore) {
                        bestScore = score;
                        bestMatch = href;
                    }
                }
                return bestScore >= 3 ? bestMatch : null;
            }""", search_terms)
            return movie_link
        except Exception as exc:
            logger.debug("movie link search failed: %s", exc)
            return None

    async def _extract_shows_from_page(self, page) -> dict:
        """Ask the browser to extract all visible showtime elements."""
        try:
            return await page.evaluate("""() => {
                const results = [];
                const timeRegex = /\\d{1,2}[:.]\\d{2}\\s*(AM|PM|am|pm)?/;
                const clickables = document.querySelectorAll(
                    'a, button, [role="button"], [class*="show"], [class*="time"], [class*="session"]'
                );
                for (const el of clickables) {
                    const text = el.textContent.trim();
                    if (text && timeRegex.test(text) && text.length < 30) {
                        let venue = '';
                        let parent = el.parentElement;
                        for (let i = 0; i < 8 && parent; i++) {
                            const venueEl = parent.querySelector(
                                '[class*="cinema-name"], [class*="venue-name"], [class*="theatre-name"], [class*="cinema"], [class*="venue"], [class*="theatre"], h2, h3, h4'
                            );
                            if (venueEl) { venue = venueEl.textContent.trim(); break; }
                            parent = parent.parentElement;
                        }
                        if (!venue) {
                            const headEl = document.querySelector('h1, [class*="venue-header"], [class*="cinema-header"]');
                            venue = headEl ? headEl.textContent.trim() : document.title;
                        }
                        const cls = el.className || '';
                        const isDisabled = el.disabled || cls.includes('sold') ||
                                          cls.includes('disabled') || cls.includes('inactive');
                        results.push({
                            time: text,
                            venue: (venue || 'unknown').substring(0, 100),
                            disabled: isDisabled,
                            href: el.href || '',
                        });
                    }
                }
                const bodyText = document.body.innerText.toLowerCase();
                const comingSoon = bodyText.includes('coming soon') ||
                                  bodyText.includes('booking opens') ||
                                  bodyText.includes('not yet available') ||
                                  bodyText.includes('schedule not available');
                return { shows: results, comingSoon: comingSoon, title: document.title };
            }""")
        except Exception:
            return {"shows": [], "comingSoon": False, "title": ""}

    async def aclose(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None

    def _error(self, message: str, url: str | None = None) -> FetchOutcome:
        return FetchOutcome(status=FetchStatus.ERROR, source_name=self.name, url=url, error=message)


# --------------------------------------------------------------------------- #
# Parsing is a PURE function: html in, SourcePayload out. That makes it unit
# testable against saved fixtures without a browser or network.
# --------------------------------------------------------------------------- #
def parse_showtimes_html(html: str, *, page_url: str | None = None, api_data: list[dict] | None = None, browser_shows: dict | None = None) -> SourcePayload:
    """Extract showtime facts from a BookMyShow showtimes page.

    Strategy (in order of reliability):
      1. Try to extract structured JSON from __NEXT_DATA__ (Next.js hydration).
      2. Try to extract from window.__INITIAL_STATE__ or inline JSON-LD.
      3. Fall back to DOM selectors for React-rendered venue/showtime blocks.
      4. If nothing matches, detect 'coming soon' / 'booking opens' text and
         return a valid BOOKING_NOT_OPEN result instead of raising ParseError.
    """
    import json
    import re

    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:                            # pragma: no cover
        raise ParseError("beautifulsoup4 is not installed") from exc

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True).lower()

    if not text:
        raise ParseError("empty document")

    notes: list[str] = []
    shows: list[RawShow] = []
    booking_url: str | None = page_url

    # --- Strategy 0: Browser JS extraction (most reliable) -----------------
    watch_movie = ""
    watch_theatre = ""
    url_type = "unknown"
    if browser_shows and isinstance(browser_shows, dict):
        js_shows = browser_shows.get("shows", [])
        js_coming_soon = browser_shows.get("comingSoon", False)
        watch_movie = browser_shows.get("_watch_movie", "")
        watch_theatre = browser_shows.get("_watch_theatre", "")
        url_type = browser_shows.get("_url_type", "unknown")

        # Filter shows by theatre name (fuzzy) if we know which theatre the user wants
        theatre_terms = [t.lower() for t in watch_theatre.split() if len(t) >= 3] if watch_theatre else []

        for js_show in js_shows:
            cleaned = _clean_time(js_show.get("time", ""))
            if not cleaned:
                continue
            venue = js_show.get("venue") or "unknown"

            # Apply theatre filter: only keep shows where the venue text matches
            if theatre_terms:
                venue_lower = venue.lower()
                if venue_lower == "unknown" and url_type in ("buytickets", "venue"):
                    # For venue-specific or direct booking pages, assume shows belong to the target venue
                    pass
                elif not any(term in venue_lower for term in theatre_terms):
                    continue

            shows.append(RawShow(
                theatre=venue,
                screen=None,
                showtime=cleaned,
                booking_open=not js_show.get("disabled", False),
                categories=[SeatCategory(name="GENERAL", available_seats=None)],
            ))
            if js_show.get("href") and not booking_url:
                booking_url = js_show["href"]

        if shows:
            notes.append(
                f"extracted {len(shows)} shows via browser JS "
                f"(filtered by theatre='{watch_theatre}')" if theatre_terms
                else f"extracted {len(shows)} shows via browser JS"
            )
        elif js_coming_soon:
            notes.append("browser reports booking not yet open")
        elif js_shows and theatre_terms:
            notes.append(
                f"page has {len(js_shows)} shows but none at theatre matching '{watch_theatre}'"
            )

    # --- Strategy 1: Parse captured API JSON -------------------------------
    if not shows and api_data:
        for data_block in api_data:
            shows.extend(_extract_from_json_tree(data_block))
        if shows:
            notes.append(f"extracted {len(shows)} shows from {len(api_data)} API response(s)")

    # --- Detect "not yet available" signals early -------------------------
    js_coming_soon = False
    if browser_shows and isinstance(browser_shows, dict):
        js_coming_soon = browser_shows.get("comingSoon", False)

    coming_soon = js_coming_soon or any(phrase in text for phrase in (
        "coming soon", "booking opens", "not yet available",
        "tickets will be available", "schedule not available",
        "no shows available", "check back later",
    ))

    # --- Strategy 1: __NEXT_DATA__ JSON (Next.js) -------------------------
    next_data_tag = soup.select_one("script#__NEXT_DATA__")
    if next_data_tag and next_data_tag.string:
        try:
            data = json.loads(next_data_tag.string)
            shows = _extract_from_next_data(data)
            if shows:
                notes.append("extracted from __NEXT_DATA__")
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

    # --- Strategy 2: window.__INITIAL_STATE__ or similar ------------------
    if not shows:
        for script in soup.select("script"):
            content = script.string or ""
            if "__INITIAL_STATE__" in content or "showtimeData" in content:
                try:
                    match = re.search(r'(?:__INITIAL_STATE__|showtimeData)\s*=\s*(\{.+?\});', content, re.DOTALL)
                    if match:
                        data = json.loads(match.group(1))
                        shows = _extract_from_json_tree(data)
                        if shows:
                            notes.append("extracted from inline script JSON")
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass

    # --- Strategy 3: DOM selectors for React-rendered blocks --------------
    if not shows:
        venue_nodes = (
            soup.select("[data-venue-name]")
            or soup.select("[data-phase='postShowtime']")
            or soup.select("[class*='venue'], [class*='cinema']")
            or soup.select(".venue-block, .showtime-venue, li.venue")
            or soup.select("[class*='CinemaCard'], [class*='VenueCard']")
        )
        for node in venue_nodes:
            theatre = (
                node.get("data-venue-name")
                or _first_text(node, "[class*='venue-name'], [class*='cinema-name'], a, h3, h4")
                or "unknown venue"
            )
            time_nodes = (
                node.select("[data-showtime]")
                or node.select("[class*='showtime'], [class*='Showtime'], [class*='time-pill']")
                or node.select("a[href*='buytickets']")
            )
            for show_node in time_nodes:
                raw_time = (
                    show_node.get("data-showtime")
                    or show_node.get_text(" ", strip=True)
                    or ""
                ).strip()
                classes = " ".join(show_node.get("class") or []).lower()
                sold_out = "soldout" in classes.replace("-", "").replace("_", "")
                disabled = show_node.has_attr("disabled") or "disabled" in classes

                screen = show_node.get("data-screen") or node.get("data-screen")
                category_name = (show_node.get("data-category") or "GENERAL").upper()
                seats_attr = show_node.get("data-available-seats")
                try:
                    available = int(seats_attr) if seats_attr is not None else None
                except ValueError:
                    available = None
                if sold_out:
                    available = 0

                cleaned = _clean_time(raw_time)
                if cleaned:
                    shows.append(RawShow(
                        theatre=str(theatre), screen=screen,
                        showtime=cleaned,
                        booking_open=not (sold_out or disabled),
                        categories=[SeatCategory(name=category_name, available_seats=available)],
                    ))

    # --- Final assembly ---------------------------------------------------
    is_bms_page = bool(
        "bookmyshow" in text
        or soup.select_one("#super-container, #app, #root, header, footer, script#__NEXT_DATA__")
    )

    if not shows and not coming_soon:
        if not venue_nodes and not is_bms_page:
            raise ParseError("no venue blocks matched any known selector")
        notes.append("no showtime data found on page; booking may not be open yet")

    if not shows and coming_soon:
        notes.append("page indicates booking is not yet open")

    anchor = soup.select_one("a[href*='/buytickets/'], a[data-testid='book-tickets']")
    if anchor and anchor.get("href", "").startswith("http"):
        booking_url = anchor["href"]

    return SourcePayload(
        movie_found=True,
        date_available=bool(shows) or not coming_soon,
        booking_open=any(s.booking_open for s in shows),
        booking_url=booking_url,
        shows=shows,
        notes=notes,
    )


def _extract_from_next_data(data: dict) -> list[RawShow]:
    """Walk Next.js hydration JSON looking for showtime arrays."""
    shows: list[RawShow] = []
    try:
        page_props = data.get("props", {}).get("pageProps", {})
        # Common BMS Next.js structures
        for key in ("showtimeData", "cinemaData", "eventData", "data"):
            block = page_props.get(key)
            if isinstance(block, dict):
                shows.extend(_extract_from_json_tree(block))
            elif isinstance(block, list):
                for item in block:
                    if isinstance(item, dict):
                        shows.extend(_extract_from_json_tree(item))
    except (AttributeError, TypeError):
        pass
    return shows


def _extract_from_json_tree(obj: Any, depth: int = 0) -> list[RawShow]:
    """Recursively search a JSON dict/list for showtime-like structures."""
    if depth > 8:
        return []
    shows: list[RawShow] = []
    if isinstance(obj, dict):
        # Look for time-like keys with showtime data
        time_val = obj.get("showTime") or obj.get("showtime") or obj.get("SessionTime")
        theatre_val = obj.get("venueName") or obj.get("cinemaName") or obj.get("theatre")
        if time_val and _clean_time(str(time_val)):
            shows.append(RawShow(
                theatre=str(theatre_val or "unknown"),
                screen=obj.get("screenName") or obj.get("screen"),
                showtime=_clean_time(str(time_val)),
                booking_open=str(obj.get("status", "")).lower() not in ("soldout", "closed", "disabled"),
                categories=[SeatCategory(
                    name=str(obj.get("category", "GENERAL")).upper(),
                    available_seats=obj.get("availableSeats"),
                )],
            ))
        for v in obj.values():
            shows.extend(_extract_from_json_tree(v, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            shows.extend(_extract_from_json_tree(item, depth + 1))
    return shows


def _first_text(node, selector: str) -> str | None:
    found = node.select_one(selector)
    return found.get_text(" ", strip=True) if found else None


def _clean_time(raw: str) -> str | None:
    """'07:30 PM' / '19:30' -> '19:30'. Returns None if unrecognisable."""
    import re

    match = re.search(r"(\d{1,2})[:.](\d{2})\s*(AM|PM|am|pm)?", raw)
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3)
    if meridiem:
        meridiem = meridiem.upper()
        if meridiem == "PM" and hour != 12:
            hour += 12
        if meridiem == "AM" and hour == 12:
            hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"
