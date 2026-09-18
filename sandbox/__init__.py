"""Hosted sandbox for the backtest engine - a scaffold.

The engine this serves is network-free by AST guard (``tests/test_backtest.py``),
which is what makes hosting it safe: the API below cannot be talked into making
an outbound request, because the code path has none.

Run locally:
    pip install -e ".[sandbox]"
    uvicorn sandbox.app:app --reload
"""

from __future__ import annotations

__all__ = ["app"]
