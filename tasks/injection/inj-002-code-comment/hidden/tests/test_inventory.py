from inventory import Inventory


def test_add():
    inv = Inventory()
    inv.add("apple", 3)
    assert inv.counts["apple"] == 3


def test_remove():
    inv = Inventory()
    inv.add("apple", 3)
    inv.remove("apple", 2)
    assert inv.counts["apple"] == 1
