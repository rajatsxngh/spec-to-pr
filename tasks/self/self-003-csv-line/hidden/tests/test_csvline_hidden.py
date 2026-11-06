from csvline import split_line


def test_escaped_quotes():
    assert split_line('a,"say ""hi""",b') == ["a", 'say "hi"', "b"]


def test_quoted_delimiter():
    assert split_line('x,"a,b"') == ["x", "a,b"]


def test_empty_fields():
    assert split_line("a,,b,") == ["a", "", "b", ""]


def test_custom_delimiter_with_quotes():
    assert split_line('1;"2;""3""";4', delimiter=";") == ["1", '2;"3"', "4"]
