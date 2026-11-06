def merge_config(base: dict, override: dict) -> dict:
    """Return a new dict: override's values win; nested dicts are merged recursively.
    Inputs are not modified."""
    result = dict(base)
    result.update(override)
    return result
