from intervals import merge


def test_touching():
    assert merge([[1, 2], [2, 3]]) == [[1, 3]]


def test_unsorted_chain():
    assert merge([[5, 6], [1, 2], [2, 5], [8, 9]]) == [[1, 6], [8, 9]]


def test_contained():
    assert merge([[1, 10], [2, 3]]) == [[1, 10]]


def test_empty():
    assert merge([]) == []
