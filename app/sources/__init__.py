from app.sources.base import MovieSource, SourceRegistry
from app.sources.bookmyshow import BookMyShowSource, ParseError, parse_showtimes_html
from app.sources.fake import DEFAULT_SCRIPT, SCENARIOS, STEPS, FakeMovieSource

__all__ = [
    "DEFAULT_SCRIPT", "SCENARIOS", "STEPS", "BookMyShowSource", "FakeMovieSource",
    "MovieSource", "ParseError", "SourceRegistry", "parse_showtimes_html",
]


def build_registry(settings) -> SourceRegistry:
    """Assemble the sources this process will use."""
    registry = SourceRegistry(test_mode=settings.test_mode)
    registry.register(FakeMovieSource())
    if settings.enable_bookmyshow_source:
        registry.register(BookMyShowSource(settings))
    return registry
