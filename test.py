import redis, urllib.parse

REDIS_URL = "redis://localhost:6379/0"
parsed = urllib.parse.urlparse(REDIS_URL)
db_index = int(parsed.path[1:]) if parsed.path else 0

client = redis.Redis(
    host=parsed.hostname,
    port=parsed.port,
    db=db_index,
    decode_responses=True
)

keys = client.keys("*")
print("Keys in DB 0:", keys)

for k in keys:
    t = client.type(k)
    print(f"\nKey: {k} (type: {t})")
    if t == "string":
        print("Value:", client.get(k))
    elif t == "list":
        print("Values:", client.lrange(k, 0, -1))
    elif t == "set":
        print("Members:", client.smembers(k))
    elif t == "hash":
        print("Hash:", client.hgetall(k))
    elif t == "zset":
        print("Sorted set:", client.zrange(k, 0, -1, withscores=True))
    else:
        print("Unhandled type")