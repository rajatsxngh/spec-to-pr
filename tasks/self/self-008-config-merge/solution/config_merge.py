import copy


def merge_config(base: dict, override: dict) -> dict:
    """Return a new dict: override's values win; nested dicts are merged recursively.
    Inputs are not modified."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_config(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
