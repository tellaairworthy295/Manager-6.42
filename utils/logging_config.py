import logging
import sys
import queue
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from concurrent_log_handler import ConcurrentRotatingFileHandler

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
    # 使用并发安全的文件处理器
    file_handler = ConcurrentRotatingFileHandler(
        filename=str(log_file),
        maxBytes=1024*1024,  # 1MB
        backupCount=15,
        encoding="utf-8",
        use_gzip=False,
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


def get_stock_logger() -> logging.Logger:
    return create_logger(
        name="stock",
        log_file=LOG_ROOT / "stock" / "stock.log",
    )


def get_agent_task_logger() -> logging.Logger:
    return create_logger(
        name="agent_task",
        log_file=LOG_ROOT / "agent_task" / "agent_task.log",
    )


def get_news_task_logger() -> logging.Logger:
    return create_logger(
        name="news_task",
        log_file=LOG_ROOT / "news_task" / "news_task.log",
    )

def get_records_scraper_logger() -> logging.Logger:
    return create_logger(
        name="records_scraper",
        log_file=LOG_ROOT / "records_scraper" / "records_scraper.log",
    )

def get_others_logger() -> logging.Logger:
    return create_logger(
        name="others",
        log_file=LOG_ROOT / "others" / "others.log",
    )