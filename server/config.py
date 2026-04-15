from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = PROJECT_ROOT / "snapshoots"
SELECTORS_PATH = PROJECT_ROOT / "json" / "selectors.json"
LOCATORS_PATH = PROJECT_ROOT / "json" / "locators.json"

REDIS_URL = "redis://localhost:6379/0"
MCP_NAME = "News MCP"
MCP_PATH = "/tools"
APP_TITLE = "My Local Dify Server"
APP_HOST = "0.0.0.0"
APP_PORT = 5000
