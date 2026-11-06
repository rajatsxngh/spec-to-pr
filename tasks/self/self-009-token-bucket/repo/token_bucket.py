class TokenBucket:
    """Allows `rate` tokens per second, bursting up to `capacity`."""

    def __init__(self, rate: float, capacity: int, clock):
        self.rate = rate
        self.capacity = capacity
        self.clock = clock
        self.tokens = capacity
        self.last = clock()

    def allow(self, cost: int = 1) -> bool:
        now = self.clock()
        self.tokens += (now - self.last) * self.rate
        self.last = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False
