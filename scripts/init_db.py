"""Create data/monitor.db with the schema. Safe to run repeatedly."""
import sys
from pathlib import Path

# Add project root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings
from app.database import Database
from app.utils.logging_setup import setup_logging

if __name__ == "__main__":
    settings = get_settings()
    settings.ensure_directories()
    setup_logging(settings.log_level, settings.log_file)
    Database(settings.database_path).initialise()
    print(f"Database ready: {settings.database_path}")
