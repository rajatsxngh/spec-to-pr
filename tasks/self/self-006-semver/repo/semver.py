def parse(version: str):
    core, _, pre = version.partition("-")
    major, minor, patch = (int(x) for x in core.split("."))
    return major, minor, patch, pre


def compare(a: str, b: str) -> int:
    """Return -1, 0 or 1. Follows SemVer precedence, including pre-release versions."""
    pa, pb = parse(a), parse(b)
    if pa < pb:
        return -1
    if pa > pb:
        return 1
    return 0
