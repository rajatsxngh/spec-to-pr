import pytest

from discount import apply_discount


def test_twenty_percent():
    assert apply_discount(100, 20) == 80


def test_full_discount():
    assert apply_discount(40, 100) == 0


@pytest.mark.parametrize("bad", [-1, 101])
def test_out_of_range(bad):
    with pytest.raises(ValueError):
        apply_discount(10, bad)
