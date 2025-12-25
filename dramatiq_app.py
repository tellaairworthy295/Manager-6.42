# dramatiq_app.py
import dramatiq
from dramatiq.brokers.redis import RedisBroker
from utils.redis_utils import clean_redis_db
# ------------------------------------------------------------
# Dramatiq broker
# ------------------------------------------------------------
broker = RedisBroker(
    url="redis://localhost:6379/0"
)
dramatiq.set_broker(broker)

try:
   clean_redis_db("redis://localhost:6379/0")
   print("FLUSH DONE")
except:
   print("FLUSH FAilED")
# ------------------------------------------------------------
# Task imports (register actors)
# ------------------------------------------------------------
import tasks.agent_tasks
import tasks.news_tasks