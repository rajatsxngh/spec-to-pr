from wrap import wrap


def test_simple():
    assert wrap("the quick brown fox", 10) == ["the quick", "brown fox"]
