# toy-store

Run the tests with `uv run --with pytest python -m pytest -q` from the repo root.
There is no bare `python` on PATH; always go through `uv run`.

Style: type hints everywhere, `from __future__ import annotations` at the top of
every module, dataclasses for domain objects, no external dependencies beyond
pytest.
