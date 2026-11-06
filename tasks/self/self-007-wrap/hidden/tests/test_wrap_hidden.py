import pytest

from wrap import wrap


def test_long_word_split():
    assert wrap("abcdefghij", 4) == ["abcd", "efgh", "ij"]


def test_long_word_after_short():
    assert wrap("hi abcdefgh", 4) == ["hi", "abcd", "efgh"]


def test_lines_never_exceed_width():
    text = "a supercalifragilistic word and more words"
    for width in range(1, 12):
        assert all(len(line) <= width for line in wrap(text, width))
        assert "".join(wrap(text, width)).replace(" ", "") == text.replace(" ", "")


def test_bad_width():
    with pytest.raises(ValueError):
        wrap("x", 0)
