import pytest

from roman import from_roman, to_roman


def test_round_trip():
    for n in range(1, 4000):
        assert from_roman(to_roman(n)) == n


def test_lowercase():
    assert from_roman("mcmxciv") == 1994


@pytest.mark.parametrize("bad", ["IIII", "VX", "", "IC", "MMMM", "ABC"])
def test_invalid(bad):
    with pytest.raises(ValueError):
        from_roman(bad)
