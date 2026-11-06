from semver import compare


def test_core_versions():
    assert compare("1.10.0", "1.9.0") == 1
    assert compare("2.0.0", "2.0.0") == 0
