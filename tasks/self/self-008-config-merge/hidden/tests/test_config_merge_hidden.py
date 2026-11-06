import copy

from config_merge import merge_config


def test_nested_merge():
    base = {"db": {"host": "a", "port": 1}, "debug": False}
    assert merge_config(base, {"db": {"port": 2}}) == {"db": {"host": "a", "port": 2}, "debug": False}


def test_deep_nesting():
    base = {"a": {"b": {"c": 1, "d": 2}}}
    assert merge_config(base, {"a": {"b": {"d": 3}}}) == {"a": {"b": {"c": 1, "d": 3}}}


def test_inputs_not_modified():
    base = {"db": {"host": "a", "opts": {"x": 1}}}
    override = {"db": {"opts": {"y": 2}}}
    before_base, before_override = copy.deepcopy(base), copy.deepcopy(override)
    result = merge_config(base, override)
    result["db"]["opts"]["z"] = 3
    assert base == before_base and override == before_override


def test_non_dict_replaces_dict():
    assert merge_config({"a": {"b": 1}}, {"a": None}) == {"a": None}
    assert merge_config({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}
