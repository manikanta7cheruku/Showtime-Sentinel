from app.utils.robots import is_allowed


def test_permanent_denylist_blocks_payment_and_data_paths():
    for path in ("/payment/x", "/order-summary", "/data/foo", "/getJSData/bar",
                 "/m4/anything", "/api/v1/shows", "/booking-details/9"):
        allowed, reason = is_allowed(f"https://in.bookmyshow.com{path}", respect_robots=False)
        assert allowed is False and "deny-list" in reason


def test_non_http_urls_are_rejected():
    assert is_allowed("file:///etc/passwd")[0] is False
    assert is_allowed("javascript:alert(1)")[0] is False


def test_normal_path_passes_the_denylist_when_robots_is_skipped():
    allowed, _ = is_allowed("https://in.bookmyshow.com/movies/x/ET123", respect_robots=False)
    assert allowed is True
