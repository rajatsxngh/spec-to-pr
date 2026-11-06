def parse(version: str):
    core, _, pre = version.partition("-")
    major, minor, patch = (int(x) for x in core.split("."))
    return major, minor, patch, pre


def _pre_key(pre: str):
    if not pre:
        return (1,)  # a release sorts after any of its pre-releases
    parts = []
    for ident in pre.split("."):
        if ident.isdigit():
            parts.append((0, int(ident), ""))
        else:
            parts.append((1, 0, ident))
    return (0, tuple(parts))


def _key(version: str):
    major, minor, patch, pre = parse(version)
    return major, minor, patch, _pre_key(pre)


def compare(a: str, b: str) -> int:
    """Return -1, 0 or 1. Follows SemVer precedence, including pre-release versions."""
    ka, kb = _key(a), _key(b)
    if ka < kb:
        return -1
    if ka > kb:
        return 1
    return 0
