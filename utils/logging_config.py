import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOG_ROOT = Path("logs")

def _ensure_log_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def create_logger(
    *,
    name: str,
    log_file: Path,
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Create or return a named logger with:
    - console output
    - daily rotating file
    - NO duplicate handlers
    """

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # 🚨 critical to avoid double logs

    if logger.handlers:
        return logger  # already configured

    _ensure_log_dir(log_file.parent)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(process)d | %(threadName)s | %(name)s | %(message)s"
    )

    # Console handler (respects PYTHONUNBUFFERED=1)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)

    # File handler (per-folder)
    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        backupCount=15,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file_handler)

    return logger

from datetime import datetime

def _logfile_with_datetime(folder: Path, basename: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d")
    return folder / f"{basename}_{timestamp}.log"

def get_stock_logger() -> logging.Logger:
    return create_logger(
        name="stock",
        log_file=_logfile_with_datetime(LOG_ROOT / "stock", "stock"),
    )

def get_agent_task_logger() -> logging.Logger:
    return create_logger(
        name="agent_task",
        log_file=_logfile_with_datetime(LOG_ROOT / "agent_task", "agent_task"),
    )

def get_news_task_logger() -> logging.Logger:
    return create_logger(
        name="news_task",
        log_file=_logfile_with_datetime(LOG_ROOT / "news_task", "news_task"),
    )

def get_others_logger() -> logging.Logger:
    return create_logger(
        name="others",
        log_file=_logfile_with_datetime(LOG_ROOT / "others", "others"),
    )