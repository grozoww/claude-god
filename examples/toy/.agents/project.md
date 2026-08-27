# Toy store

## Purpose

A deliberately tiny in-memory order store. It exists to exercise the claude-god
pipeline end to end without the noise of a real codebase.

## Glossary

- **Order** — an id plus one or more lines. Identified by a caller-supplied string id.
- **Line** — a sku, a positive integer quantity, and a non-negative unit price in cents.
- **Capture** — the point at which an order is considered paid.

## Architecture map

- `store/models.py` — dataclasses (`Order`, `Line`) and the error hierarchy.
  All errors derive from `OrderError`.
- `store/orders.py` — `OrderStore`, the only stateful component. Dict-backed.
- `tests/test_orders.py` — pytest suite. the gate is `uv run --with pytest python -m pytest -q`.

## Invariants

- Money is always integer cents. Never floats.
- Every caller error raises a subclass of `OrderError`; nothing raises `KeyError`
  or `ValueError` across the API boundary.
- An order is immutable once `captured` is True.
- `OrderStore` has no I/O and no global state.
