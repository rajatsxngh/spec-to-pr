from semver import compare

CHAIN = [
    "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta",
    "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0",
]


def test_prerelease_before_release():
    assert compare("1.0.0-alpha", "1.0.0") == -1
    assert compare("1.0.0", "1.0.0-alpha") == 1


def test_spec_chain():
    for lower, higher in zip(CHAIN, CHAIN[1:]):
        assert compare(lower, higher) == -1, (lower, higher)
        assert compare(higher, lower) == 1, (higher, lower)


def test_equal_prereleases():
    assert compare("1.2.3-rc.1", "1.2.3-rc.1") == 0
