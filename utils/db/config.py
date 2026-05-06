import json
import urllib.parse
from functools import lru_cache
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "json" / "config.json"


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def load_database_config() -> dict[str, Any]:
    return load_config().get("DatabaseConfig", {})


def build_connection_string(db_name: str = None) -> str:
    db_config = load_database_config()
    db_host = db_config.get("DB_HOST")
    db_port = db_config.get("DB_PORT")
    db_user = db_config.get("DB_USER")
    db_password = db_config.get("DB_PASSWORD")
    db_name = db_name or db_config.get("DB_NAME")
    print(f"Building connection string for database: {db_name}")
    return (
        f"mysql+pymysql://{db_user}:{urllib.parse.quote_plus(db_password)}"
        f"@{db_host}:{db_port}/{db_name}?charset=utf8mb4"
    )
