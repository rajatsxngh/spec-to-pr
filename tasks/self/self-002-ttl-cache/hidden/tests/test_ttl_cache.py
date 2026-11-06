from ttl_cache import TTLCache


def test_fresh_value():
    cache = TTLCache(ttl=10)
    cache.set("a", 1)
    assert cache.get("a") == 1
