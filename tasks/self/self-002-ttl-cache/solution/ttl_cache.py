import time


class TTLCache:
    def __init__(self, ttl: float, clock=None):
        self.ttl = ttl
        self.clock = clock or time.monotonic
        self._data = {}

    def _expired(self, stored):
        return self.clock() - stored >= self.ttl

    def set(self, key, value):
        self._data[key] = (value, self.clock())

    def get(self, key, default=None):
        item = self._data.get(key)
        if item is None:
            return default
        value, stored = item
        if self._expired(stored):
            del self._data[key]
            return default
        return value

    def __len__(self):
        self._data = {k: v for k, v in self._data.items() if not self._expired(v[1])}
        return len(self._data)
