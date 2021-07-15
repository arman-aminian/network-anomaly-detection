from redis import StrictRedis
import json

redis_cli = StrictRedis(host='localhost', port=6379, db=0)

redis_cli.set('armin', 'mazloomi')

print(redis_cli.get('armin'))


def add_list_to_redis(key, value):
    redis_cli.set(key, json.dumps(value))


def get_list_from_redis(key, default=None):
    value = redis_cli.get(key)
    if value is None:
        return default
    return json.loads(value)



