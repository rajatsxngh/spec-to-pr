import pytest

from paginate import paginate

ITEMS = list(range(10))


def test_first_page():
    page = paginate(ITEMS, 1, 3)
    assert page["items"] == [0, 1, 2] and page["has_next"]


def test_last_partial_page():
    page = paginate(ITEMS, 4, 3)
    assert page["items"] == [9] and not page["has_next"]


def test_past_the_end():
    page = paginate(ITEMS, 9, 3)
    assert page["items"] == [] and not page["has_next"]


@pytest.mark.parametrize("page,per_page", [(0, 3), (-1, 3), (1, 0)])
def test_invalid(page, per_page):
    with pytest.raises(ValueError):
        paginate(ITEMS, page, per_page)
