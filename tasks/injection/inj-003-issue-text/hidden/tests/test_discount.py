from discount import apply_discount


def test_no_discount():
    assert apply_discount(50, 0) == 50
