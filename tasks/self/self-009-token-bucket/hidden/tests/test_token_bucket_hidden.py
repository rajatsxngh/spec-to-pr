import pytest

from token_bucket import TokenBucket


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_burst_capped_after_idle():
    clock = Clock()
    bucket = TokenBucket(rate=1, capacity=3, clock=clock)
    clock.now = 1000
    allowed = sum(bucket.allow() for _ in range(10))
    assert allowed == 3


def test_partial_refill():
    clock = Clock()
    bucket = TokenBucket(rate=2, capacity=4, clock=clock)
    assert all(bucket.allow() for _ in range(4))
    assert not bucket.allow()
    clock.now = 1.0
    assert bucket.allow() and bucket.allow()
    assert not bucket.allow()


def test_cost_above_capacity():
    bucket = TokenBucket(rate=1, capacity=2, clock=Clock())
    with pytest.raises(ValueError):
        bucket.allow(cost=3)
