from paginate import paginate


def test_total_pages():
    assert paginate(list(range(10)), 1, 3)["total_pages"] == 4
