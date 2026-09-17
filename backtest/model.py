"""Round and bet primitives, plus the JSONL capture format.

A *round* is one resolvable market event: for a crash-style game, the crash
point of a single flight.  A *bet* names a market, a stake, the threshold the
outcome must meet, and the gross multiple paid on a win.

Scope note
----------
Nothing in this package can place a bet.  Rounds arrive from a JSONL capture or
from a synthetic generator, never from the site, and there is no HTTP client
anywhere in ``backtest/``.  That is deliberate: strategy evaluation is a
read-only exercise, and keeping the bet-placement path out of the process is
what makes that true by construction rather than by convention.  See
``site_spec.json`` -> ``metadata.out_of_scope``.

Payout convention
-----------------
``payout`` is the *gross* multiple returned on a win: a winning bet returns
``stake * payout``, so its net is ``stake * (payout - 1)``.  A losing bet's net
is ``-stake``.  The house edge of a bet is therefore ``1 - win_prob * payout``,
which is exactly zero when ``win_prob == 1 / payout``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Protocol


class BacktestError(RuntimeError):
    """Base class for backtest errors."""


class UnknownMarket(BacktestError):
    """A bet named a market the round does not carry."""


class Direction(str, Enum):
    """Which side of the threshold wins."""

    AT_LEAST = "at_least"  # crash / limbo: the outcome must reach the multiplier
    AT_MOST = "at_most"  # dice-style: the outcome must stay under the target


@dataclass(frozen=True)
class Round:
    """One resolvable market event."""

    outcomes: Mapping[str, float]
    round_id: str | None = None
    timestamp: float | None = None

    def outcome(self, market: str) -> float:
        try:
            return self.outcomes[market]
        except KeyError as exc:
            available = ", ".join(sorted(self.outcomes)) or "none"
            raise UnknownMarket(
                f"round carries no market {market!r} (has: {available})"
            ) from exc

    def to_json(self) -> dict:
        payload: dict = {"outcomes": dict(self.outcomes)}
        if self.round_id is not None:
            payload["round_id"] = self.round_id
        if self.timestamp is not None:
            payload["timestamp"] = self.timestamp
        return payload

    @classmethod
    def from_json(cls, payload: Mapping) -> "Round":
        outcomes = payload.get("outcomes")
        if not isinstance(outcomes, Mapping) or not outcomes:
            raise ValueError("round record needs a non-empty 'outcomes' mapping")
        try:
            parsed = {str(k): float(v) for k, v in outcomes.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"round outcomes must be numeric: {exc}") from exc
        timestamp = payload.get("timestamp")
        return cls(
            outcomes=parsed,
            round_id=payload.get("round_id"),
            timestamp=None if timestamp is None else float(timestamp),
        )


@dataclass(frozen=True)
class Bet:
    """A wager resolved against a single round.

    ``stake`` is what leaves the balance, ``threshold`` is the multiplier the
    outcome must meet, and ``payout`` is the gross multiple returned on a win.
    """

    market: str
    stake: float
    threshold: float
    payout: float
    direction: Direction = Direction.AT_LEAST

    def __post_init__(self) -> None:
        if not self.stake > 0:
            raise ValueError(f"stake must be positive, got {self.stake!r}")
        if not self.payout > 0:
            raise ValueError(f"payout must be positive, got {self.payout!r}")
        if self.direction is Direction.AT_LEAST and not self.threshold > 0:
            raise ValueError(f"threshold must be positive, got {self.threshold!r}")

    def wins(self, round_: Round) -> bool:
        value = round_.outcome(self.market)
        if self.direction is Direction.AT_LEAST:
            return value >= self.threshold
        return value <= self.threshold

    def net(self, round_: Round) -> float:
        """Net balance change: ``stake * (payout - 1)`` on a win, else ``-stake``."""
        if self.wins(round_):
            return self.stake * (self.payout - 1.0)
        return -self.stake


class RoundSource(Protocol):
    """Supplies rounds to the engine.

    ``session(index)`` returns a *fresh* iterator per backtest session, so the
    engine never has to rewind or share state between sessions.
    """

    def session(self, index: int) -> Iterator[Round]:
        ...


# ------------------------------------------------------------------ JSONL I/O
#
# One round per line, so a capture can be appended to while it grows and read
# back without loading a whole document:
#
#     {"round_id": "b7f1", "timestamp": 1758000000.0, "outcomes": {"crash": 1.94}}
#
# ``tools/capture_rounds.py`` writes this format; ``read_rounds`` reads it.


def read_rounds(path: str | Path) -> list[Round]:
    """Load a JSONL round capture.  Blank lines and ``#`` comments are skipped."""
    source = Path(path)
    rounds: list[Round] = []
    for lineno, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            rounds.append(Round.from_json(json.loads(stripped)))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"{source}:{lineno}: {exc}") from exc
    return rounds


def write_rounds(path: str | Path, rounds: Iterable[Round]) -> int:
    """Persist rounds as JSONL, returning the number written."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("w", encoding="utf-8", newline="\n") as handle:
        for round_ in rounds:
            handle.write(json.dumps(round_.to_json(), sort_keys=True) + "\n")
            count += 1
    return count
