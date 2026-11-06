from csvline import split_line


def test_plain():
    assert split_line("a,b,c") == ["a", "b", "c"]
