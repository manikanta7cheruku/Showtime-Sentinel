"""robots.txt gate for the real adapter.

BookMyShow's robots.txt Disallows, for User-agent: *, exactly the paths that
scrapers normally abuse (/data/, /getJSData/, /getHTML*, /m4/, /m5/) plus the
payment and booking-detail paths. We refuse those unconditionally, and we also
honour the live file. If we can't read robots.txt we fail CLOSED.
"""
from __future__ import annotations

import logging
import urllib.robotparser
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Hard deny-list. Even if robots.txt changed tomorrow, this project does not go here.
FORBIDDEN_PREFIXES = (
    "/payment", "/payment_v2", "/payments-mt", "/order-summary",
    "/booking-details", "/confirmation.bms", "/data/", "/getJSData/",
    "/getHTML", "/m4/", "/m5/", "/partners/", "/ibv", "/api/",
)

_CACHE: dict[str, urllib.robotparser.RobotFileParser | None] = {}


def _load(origin: str) -> urllib.robotparser.RobotFileParser | None:
    if origin in _CACHE:
        return _CACHE[origin]
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(f"{origin}/robots.txt")
    try:
        parser.read()
    except Exception as exc:                # noqa: BLE001
        logger.warning("could not read %s/robots.txt (%s) - failing closed", origin, exc)
        parser = None
    _CACHE[origin] = parser
    return parser


def is_allowed(url: str, user_agent: str = "*", *, respect_robots: bool = True) -> tuple[bool, str]:
    """Return (allowed, reason). Reason is always safe to log and to show a user."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, f"not an http(s) URL: {url!r}"

    path = parsed.path or "/"
    for prefix in FORBIDDEN_PREFIXES:
        if path.startswith(prefix):
            return False, f"path {path!r} is on this project's permanent deny-list ({prefix})"

    if not respect_robots:
        return True, "robots.txt check disabled by configuration (not recommended)"

    parser = _load(f"{parsed.scheme}://{parsed.netloc}")
    if parser is None:
        return False, "robots.txt unreadable; refusing to fetch (fail-closed)"
    if not parser.can_fetch(user_agent, url):
        return False, f"robots.txt disallows {path!r} for user-agent {user_agent!r}"
    return True, "allowed by robots.txt and deny-list"
