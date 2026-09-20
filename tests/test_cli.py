from app.config import Settings
from app.main import build_app, main


def _settings(tmp_path) -> Settings:
    return Settings(database_path=tmp_path / "cli.db", log_file=tmp_path / "cli.log",
                    test_mode=True, dry_run=True, telegram_bot_token="", telegram_chat_id="")


def test_add_list_check_remove_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "cli.log"))
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("TEST_MODE", "true")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    from app.config import get_settings
    get_settings.cache_clear()

    assert main(["add-watch", "--movie", "Dune", "--city", "Pune", "--date", "2026-06-01",
                 "--theatre", "PVR Icon", "--time", "19:30", "--min-seats", "2"]) == 0
    assert "Created watch #1" in capsys.readouterr().out

    assert main(["list-watches"]) == 0
    assert "Dune" in capsys.readouterr().out

    assert main(["check", "1"]) == 0
    assert "BOOKING_NOT_OPEN" in capsys.readouterr().out

    assert main(["disable-watch", "1"]) == 0
    assert main(["status"]) == 0
    assert "1 total, 0 active" in capsys.readouterr().out

    assert main(["simulate", "booking-open", "--watch-id", "1", "--cycles", "5"]) == 0
    out = capsys.readouterr().out
    assert out.count("notified=True") == 1     # exactly one notification in 5 cycles

    assert main(["remove-watch", "1"]) == 0
    assert main(["remove-watch", "1"]) == 1    # already gone
    get_settings.cache_clear()


def test_invalid_watch_is_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "x.log"))
    from app.config import get_settings
    get_settings.cache_clear()
    assert main(["add-watch", "--movie", "M", "--city", "C", "--date", "nonsense",
                 "--theatre", "T"]) == 2
    assert "Invalid watch" in capsys.readouterr().out
    get_settings.cache_clear()


def test_bookmyshow_source_requires_a_verified_url(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "y.db"))
    monkeypatch.setenv("LOG_FILE", str(tmp_path / "y.log"))
    from app.config import get_settings
    get_settings.cache_clear()
    code = main(["add-watch", "--movie", "M", "--city", "C", "--date", "2026-06-01",
                 "--theatre", "T", "--source", "bookmyshow"])
    assert code == 2 and "requires --source-url" in capsys.readouterr().out
    get_settings.cache_clear()
