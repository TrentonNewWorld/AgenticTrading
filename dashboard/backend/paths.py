"""Path resolution for the NewWorldTrading dashboard application."""

from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
DASHBOARD_DIR = BACKEND_DIR.parent
REPO_ROOT = DASHBOARD_DIR.parent

DATA_DIR = DASHBOARD_DIR / "storage" / "data"
BACKUPS_DIR = DASHBOARD_DIR / "storage" / "backups"
CONFIG_DIR = DASHBOARD_DIR / "config"
SCRIPTS_DIR = DASHBOARD_DIR / "scripts"
FRONTEND_DIR = DASHBOARD_DIR / "frontend"
CREDENTIALS_DIR = REPO_ROOT / "credentials"

DEFAULT_DB_PATH = DATA_DIR / "backtest.db"
