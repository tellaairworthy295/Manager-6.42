# dramatiq_app.py
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from utils.redis_utils import clean_redis_db
# ------------------------------------------------------------
# Redis config
# ------------------------------------------------------------
REDIS_URL = "redis://localhost:6379/0"
# Enable only if you REALLY want this
clean_redis_db(REDIS_URL)

# ------------------------------------------------------------
# Dramatiq broker
# ------------------------------------------------------------
broker = RedisBroker(
    url=REDIS_URL
)
dramatiq.set_broker(broker)

# ------------------------------------------------------------
# Task imports (register actors)
# ------------------------------------------------------------
import tasks.agent_tasks
import tasks.news_tasks