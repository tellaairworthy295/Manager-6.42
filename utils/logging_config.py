import logging
import sys
import queue
from logging.handlers import TimedRotatingFileHandler, QueueHandler, QueueListener
from pathlib import Path

LOG_ROOT = Path("logs")
_log_queue = queue.Queue()
_listener = None


def create_logger(name: str, log_file: Path) -> logging.Logger:
    global _listener

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if logger.handlers:
        return logger

    log_file.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(process)d | %(threadName)s | %(name)s | %(message)s"
    )

    # File handler (ONLY listener touches this)
    file_handler = TimedRotatingFileHandler(
        filename=str(log_file),
        when="midnight",
        backupCount=15,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    # Start listener ONCE per process
    if _listener is None:
        _listener = QueueListener(
            _log_queue,
            file_handler,
            console_handler,
            respect_handler_level=True,
        )
        _listener.start()

    # Logger uses queue only
    logger.addHandler(QueueHandler(_log_queue))

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