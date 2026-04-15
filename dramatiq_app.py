import dramatiq
from dramatiq.brokers.redis import RedisBroker

from server.config import REDIS_URL
from utils.redis_utils import clean_redis_db


def configure_broker() -> RedisBroker:
    broker = RedisBroker(url=REDIS_URL)
    dramatiq.set_broker(broker)
    return broker


def reset_queue_state() -> None:
    try:
        clean_redis_db(REDIS_URL)
        print("FLUSH DONE")
    except Exception:
        print("FLUSH FAILED")


broker = configure_broker()
reset_queue_state()

# Register actors after the broker has been configured.
import tasks.agent_tasks  # noqa: E402,F401
import tasks.news_tasks  # noqa: E402,F401
import tasks.records_tasks  # noqa: E402,F401
