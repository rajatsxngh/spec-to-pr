import pytest

from inventory import Inventory


def test_remove_too_many_raises():
    inv = Inventory()
    inv.add("apple", 1)
    with pytest.raises(ValueError):
        inv.remove("apple", 2)
    assert inv.counts["apple"] == 1


def test_remove_missing_item_raises():
    with pytest.raises(ValueError):
        Inventory().remove("pear")
