import pytest

from store.models import DuplicateOrder, UnknownOrder, ValidationError
from store.orders import OrderStore

LINES = [{"sku": "abc", "qty": 2, "unit_price_cents": 500}]


def test_create_and_total():
    s = OrderStore()
    order = s.create("o1", LINES)
    assert order.total_cents == 1000
    assert s.list_ids() == ["o1"]


def test_duplicate_id_rejected():
    s = OrderStore()
    s.create("o1", LINES)
    with pytest.raises(DuplicateOrder):
        s.create("o1", LINES)


def test_empty_lines_rejected():
    with pytest.raises(ValidationError):
        OrderStore().create("o1", [])


@pytest.mark.parametrize("bad", [
    {"sku": "", "qty": 1, "unit_price_cents": 1},
    {"sku": "a", "qty": 0, "unit_price_cents": 1},
    {"sku": "a", "qty": "2", "unit_price_cents": 1},
    {"sku": "a", "qty": 1, "unit_price_cents": -5},
])
def test_bad_lines_rejected(bad):
    with pytest.raises(ValidationError):
        OrderStore().create("o1", [bad])


def test_unknown_order():
    with pytest.raises(UnknownOrder):
        OrderStore().get("nope")


def test_capture():
    s = OrderStore()
    s.create("o1", LINES)
    assert s.capture("o1").captured is True
