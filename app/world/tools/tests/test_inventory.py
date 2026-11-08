from __future__ import annotations

import pytest

from world.tools.errors import InsufficientInventoryError
from world.tools.inventory import Inventory


def test_credit_then_debit_round_trips() -> None:
    inventory = Inventory()
    inventory.credit("a1", "wheat", 10)
    assert inventory.quantity("a1", "wheat") == 10
    inventory.debit("a1", "wheat", 4)
    assert inventory.quantity("a1", "wheat") == 6


def test_debit_below_available_raises_and_does_not_mutate() -> None:
    inventory = Inventory()
    inventory.credit("a1", "wheat", 3)
    with pytest.raises(InsufficientInventoryError):
        inventory.debit("a1", "wheat", 4)
    assert inventory.quantity("a1", "wheat") == 3


def test_debit_to_zero_removes_the_item_key() -> None:
    inventory = Inventory()
    inventory.credit("a1", "wheat", 5)
    inventory.debit("a1", "wheat", 5)
    assert inventory.holdings("a1") == {}
    assert "a1" not in inventory.balances


def test_move_is_atomic_on_insufficient_source() -> None:
    inventory = Inventory()
    inventory.credit("a1", "wheat", 2)
    with pytest.raises(InsufficientInventoryError):
        inventory.move("a1", "a2", "wheat", 5)
    assert inventory.quantity("a1", "wheat") == 2
    assert inventory.quantity("a2", "wheat") == 0


def test_credit_rejects_non_positive_quantity() -> None:
    inventory = Inventory()
    with pytest.raises(ValueError):
        inventory.credit("a1", "wheat", 0)


def test_holdings_returns_a_copy_not_the_live_dict() -> None:
    inventory = Inventory()
    inventory.credit("a1", "wheat", 5)
    snapshot = inventory.holdings("a1")
    snapshot["wheat"] = 999
    assert inventory.quantity("a1", "wheat") == 5
