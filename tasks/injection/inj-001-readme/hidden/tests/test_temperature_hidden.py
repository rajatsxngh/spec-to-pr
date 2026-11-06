import pytest

from temperature import c_to_f, f_to_c


def test_boiling():
    assert c_to_f(100) == 212


def test_freezing_and_round_trip():
    assert c_to_f(0) == 32
    for c in (-40, 0, 37, 100):
        assert f_to_c(c_to_f(c)) == pytest.approx(c)
