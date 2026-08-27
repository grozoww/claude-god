"""In-memory order store.

Invariant: an order is immutable once captured.
"""

from __future__ import annotations

from .models import DuplicateOrder, Line, Order, UnknownOrder, ValidationError


class OrderStore:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}

    def create(self, order_id: str, lines: list[dict]) -> Order:
        if not order_id:
            raise ValidationError("order_id is required")
        if order_id in self._orders:
            raise DuplicateOrder(f"order {order_id} already exists")
        if not lines:
            raise ValidationError("an order needs at least one line")

        parsed: list[Line] = []
        for raw in lines:
            sku = raw.get("sku")
            qty = raw.get("qty")
            price = raw.get("unit_price_cents")
            if not sku:
                raise ValidationError("each line needs a sku")
            if not isinstance(qty, int) or qty <= 0:
                raise ValidationError(f"qty for {sku} must be a positive integer")
            if not isinstance(price, int) or price < 0:
                raise ValidationError(f"unit_price_cents for {sku} must be a non-negative integer")
            parsed.append(Line(sku=sku, qty=qty, unit_price_cents=price))

        order = Order(id=order_id, lines=parsed)
        self._orders[order_id] = order
        return order

    def get(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise UnknownOrder(f"no order {order_id}") from None

    def capture(self, order_id: str) -> Order:
        order = self.get(order_id)
        order.captured = True
        return order

    def list_ids(self) -> list[str]:
        return sorted(self._orders)
