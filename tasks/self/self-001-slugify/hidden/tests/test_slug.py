from slug import slugify


def test_single_word():
    assert slugify("hello") == "hello"
