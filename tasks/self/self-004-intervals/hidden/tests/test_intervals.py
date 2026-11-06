from intervals import merge


def test_overlap():
    assert merge([[1, 3], [2, 4]]) == [[1, 4]]
