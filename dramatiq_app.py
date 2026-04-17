import dramatiq
from dramatiq.brokers.redis import RedisBroker
from utils.redis_utils import clean_redis_db


broker = RedisBroker(url="redis://localhost:6379/0")
dramatiq.set_broker(broker)

def reset_queue_state() -> None:
    try:
        clean_redis_db("redis://localhost:6379/0")
        print("FLUSH DONE")
    except Exception:
        print("FLUSH FAILED")


reset_queue_state()

# Register actors after the broker has been configured.
import tasks.agent_tasks  # noqa: E402,F401
import tasks.news_tasks  # noqa: E402,F401
import tasks.records_tasks  # noqa: E402,F401
