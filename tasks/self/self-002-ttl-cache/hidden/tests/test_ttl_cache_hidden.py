from ttl_cache import TTLCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_expires_at_exactly_ttl():
    clock = Clock()
    cache = TTLCache(ttl=5, clock=clock)
    cache.set("a", 1)
    clock.now = 4.9
    assert cache.get("a") == 1
    clock.now = 5.0
    assert cache.get("a", "gone") == "gone"


def test_len_ignores_expired():
    clock = Clock()
    cache = TTLCache(ttl=5, clock=clock)
    cache.set("a", 1)
    cache.set("b", 2)
    clock.now = 3
    cache.set("c", 3)
    clock.now = 6
    assert len(cache) == 1
    assert cache.get("c") == 3


def test_expired_entries_are_dropped():
    clock = Clock()
    cache = TTLCache(ttl=1, clock=clock)
    for i in range(100):
        cache.set(i, i)
    clock.now = 2
    cache.get(0)
    assert len(cache._data) <= 99
    assert len(cache) == 0
    assert cache._data == {}
