from slug import slugify


def test_punctuation_collapses():
    assert slugify("Hello, World!") == "hello-world"


def test_accents_and_padding():
    assert slugify("  Déjà vu  ") == "deja-vu"


def test_custom_separator():
    assert slugify("A  B--C", sep="_") == "a_b_c"


def test_only_punctuation():
    assert slugify("---!!!") == ""
