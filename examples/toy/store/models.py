"""Domain objects for the toy store."""

from __future__ import annotations

from dataclasses import dataclass, field


class OrderError(Exception):
    """Base class for anything the caller did wrong."""


class ValidationError(OrderError):
    pass


class DuplicateOrder(OrderError):
    pass


class UnknownOrder(OrderError):
    pass


@dataclass(frozen=True)
class Line:
    sku: str
    qty: int
    unit_price_cents: int

    @property
    def total_cents(self) -> int:
        return self.qty * self.unit_price_cents


@dataclass
class Order:
    id: str
    lines: list[Line] = field(default_factory=list)
    captured: bool = False

    @property
    def total_cents(self) -> int:
        return sum(line.total_cents for line in self.lines)
