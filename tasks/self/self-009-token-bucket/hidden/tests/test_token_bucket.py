from token_bucket import TokenBucket


def test_first_request_allowed():
    bucket = TokenBucket(rate=1, capacity=2, clock=lambda: 0.0)
    assert bucket.allow()
