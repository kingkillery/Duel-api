"""Staking schedules and session rules.

A *schedule* answers one question: given how many bets have been lost in a row
within the current ladder, what is the next stake?  Returning ``None`` means the
progression refuses to continue - the ladder is exhausted.

A *strategy* binds a schedule to a market, a stake base, the session's profit
target and stop-loss, and what to do when a ladder runs out.

Note on what a schedule can and cannot do
-----------------------------------------
Bet sizing is a variance knob.  It redistributes a game's expectation across
outcomes; it cannot change the sign of that expectation.  For any bet,
``E[net] = -edge * stake``, so a session's expectation is ``-edge`` times the
total amount wagered:

    E[session net] = -edge * E[total wagered]

A martingale raises ``E[total wagered]``, which is why doubling progressions
lose faster than flat betting at the same base stake rather than slower.  The
engine reports ``implied_edge`` so this identity is checkable on any run.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .model import BacktestError, Direction


class ScheduleError(BacktestError):
    """A staking schedule was configured with unusable parameters."""


class StakingSchedule(Protocol):
    """Maps consecutive losses to the next stake, or ``None`` when exhausted."""

    def stake(self, base: float, losses: int) -> float | None:
        ...


@dataclass(frozen=True)
class Flat:
    """Constant stake; never exhausts."""

    multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.multiplier <= 0:
            raise ScheduleError(f"multiplier must be positive, got {self.multiplier!r}")

    def stake(self, base: float, losses: int) -> float | None:
        return base * self.multiplier


@dataclass(frozen=True)
class Martingale:
    """Multiply the stake by ``factor`` after each consecutive loss.

    ``max_rungs`` caps the ladder: with ``max_rungs=3`` at base ``0.50`` the
    rungs are 0.50 / 1.00 / 2.00 and three straight losses cost 3.50.  This is
    the parameter that implements a "stop once I am down N" rule, since the
    unaffordable rung is the one that would have recovered the ladder.
    """

    factor: float = 2.0
    max_rungs: int | None = None

    def __post_init__(self) -> None:
        if self.factor <= 1.0:
            raise ScheduleError(f"factor must exceed 1.0, got {self.factor!r}")
        if self.max_rungs is not None and self.max_rungs < 1:
            raise ScheduleError(f"max_rungs must be >= 1, got {self.max_rungs!r}")

    def stake(self, base: float, losses: int) -> float | None:
        if self.max_rungs is not None and losses >= self.max_rungs:
            return None
        return base * self.factor**losses


def _fibonacci(n: int) -> int:
    """1, 1, 2, 3, 5, 8, ... indexed from zero."""
    a, b = 1, 1
    for _ in range(n):
        a, b = b, a + b
    return a


@dataclass(frozen=True)
class Fibonacci:
    """Stake advances one Fibonacci step per consecutive loss, resetting on a win."""

    max_rungs: int | None = None

    def __post_init__(self) -> None:
        if self.max_rungs is not None and self.max_rungs < 1:
            raise ScheduleError(f"max_rungs must be >= 1, got {self.max_rungs!r}")

    def stake(self, base: float, losses: int) -> float | None:
        if self.max_rungs is not None and losses >= self.max_rungs:
            return None
        return base * _fibonacci(losses)


class OnExhausted(str, Enum):
    """What to do when a ladder runs out of rungs."""

    RESET = "reset"  # start a fresh ladder and keep playing (bounded by stop_loss)
    STOP = "stop"  # end the session where the ladder ended


@dataclass(frozen=True)
class Strategy:
    """A complete, runnable betting plan."""

    market: str
    base_stake: float
    threshold: float
    payout: float
    schedule: StakingSchedule
    profit_target: float | None = None
    stop_loss: float | None = None
    direction: Direction = Direction.AT_LEAST
    max_bets: int | None = None
    on_exhausted: OnExhausted = OnExhausted.RESET

    def __post_init__(self) -> None:
        if not self.base_stake > 0:
            raise ScheduleError(f"base_stake must be positive, got {self.base_stake!r}")
        if not self.payout > 0:
            raise ScheduleError(f"payout must be positive, got {self.payout!r}")
        if self.profit_target is None and self.stop_loss is None and self.max_bets is None:
            raise ScheduleError(
                "strategy has no terminal condition: set profit_target, stop_loss "
                "or max_bets, or a session would run until the source is exhausted"
            )
        if self.stop_loss is not None and self.stop_loss <= 0:
            raise ScheduleError(f"stop_loss must be positive, got {self.stop_loss!r}")
        if self.profit_target is not None and self.profit_target <= 0:
            raise ScheduleError(
                f"profit_target must be positive, got {self.profit_target!r}"
            )
        if self.max_bets is not None and self.max_bets < 1:
            raise ScheduleError(f"max_bets must be >= 1, got {self.max_bets!r}")

    @property
    def fair_payout(self) -> bool:
        """True when a win at ``threshold`` is priced exactly at fair odds."""
        return abs(self.payout - self.threshold) < 1e-12
