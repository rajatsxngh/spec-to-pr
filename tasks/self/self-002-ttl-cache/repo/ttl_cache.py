import time


class TTLCache:
    def __init__(self, ttl: float, clock=None):
        self.ttl = ttl
        self.clock = clock or time.monotonic
        self._data = {}

    def set(self, key, value):
        self._data[key] = (value, self.clock())

    def get(self, key, default=None):
        item = self._data.get(key)
        if item is None:
            return default
        value, stored = item
        if self.clock() - stored > self.ttl:
            return default
        return value

    def __len__(self):
        return len(self._data)
