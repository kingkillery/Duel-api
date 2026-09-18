"""Bankroll policy: turn a *measured* edge into an executable stake rule.

In a positive-EV game, stake sizing is a genuine optimisation: Kelly maximises
long-run growth, fractional Kelly trades some growth for variance, and the
choice of fraction is a real decision. In a negative-EV game the optimisation
is degenerate -- Kelly's fraction is negative, so the growth-optimal stake is
exactly zero and *every* positive stake has strictly lower expected utility
than not betting. There is no frontier to trade along; a "system" that bets
anyway is not optimising, it is choosing a speed of loss.

This module exists so that rule is executed rather than remembered. It is pure
arithmetic: no network, no session, no side effects.

The rules, in order of precedence:

1. Stake is a function of measured edge. ``EV >= 0`` permits
   ``min(kelly_fraction * f*, cap)``; ``EV < 0`` permits exactly zero.
2. The edge must be *measured*, never assumed from configuration.
3. Never increase stake after a loss (anti-martingale). Under negative EV,
   chasing multiplies expected loss -- see :meth:`BankrollPolicy.stake_for`,
   which is a pure function of the edge and cannot see a loss streak at all.
4. A session loss cap is checked *before* dispatch, not after.
5. No batch or autobet: volume is what converts a small edge into a
   near-certain loss.
6. The stake never exceeds the fractional-Kelly bound, even when a bet is
   permitted.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from typing import ClassVar


class EdgeRefused(Exception):
    """The bankroll policy declined to authorise a stake.

    Separate from ``ValueError`` because this is not a malformed request --
    the request is well-formed and the *rule* says no.
    """


def implied_edge(win_chance: Decimal | str | float, multiplier: Decimal | str | float) -> Decimal:
    """EV per unit staked: ``p * multiplier - 1``.

    A fair game returns exactly zero here. Duel's dice returns about
    ``-0.001`` at every tier -- neither ``p`` (0.49995) nor ``multiplier``
    (1.9982) looks wrong on its own; only their product reveals the edge.
    """
    p = Decimal(str(win_chance))
    m = Decimal(str(multiplier))
    return p * m - 1


def kelly_fraction(win_chance, multiplier) -> Decimal:
    """Kelly fraction ``f* = (p*b - (1-p)) / b`` with net odds ``b = m - 1``.

    Negative when the edge is negative. Callers must read a non-positive
    result as "stake nothing" -- never take its modulus.
    """
    p = Decimal(str(win_chance))
    b = Decimal(str(multiplier)) - 1
    if b <= 0:
        return Decimal(0)
    return (p * b - (1 - p)) / b


@dataclass(frozen=True)
class BankrollPolicy:
    """The rules above, as an object the client can enforce.

    ``kelly_fraction`` is the fractional-Kelly multiplier (``k`` in rule 1, at
    most 0.25 by convention). ``min_edge`` is the EV floor required to bet at
    all. ``session_loss_cap`` is rule 4. ``quantum`` is the smallest stakable
    increment for the currency in play.
    """

    kelly_fraction: Decimal = Decimal("0.25")
    min_edge: Decimal = Decimal(0)
    session_loss_cap: Decimal | None = None
    quantum: Decimal = Decimal("0.00000001")

    #: Edge-based sizing cannot proceed without a measured edge (rule 2); the
    #: client reads this to decide whether to demand one before dispatching.
    requires_edge: ClassVar[bool] = True

    def stake_for(self, *, bankroll, win_chance, multiplier) -> Decimal:
        """Largest stake these rules permit, floored to ``quantum``.

        Returns ``Decimal(0)`` when any rule forbids betting -- which is the
        normal case for a negative-EV game, not an error path.
        """
        edge = implied_edge(win_chance, multiplier)
        fraction = kelly_fraction(win_chance, multiplier)
        if edge < self.min_edge or fraction <= 0:
            return Decimal(0)
        raw = Decimal(str(bankroll)) * fraction * self.kelly_fraction
        if raw <= self.quantum:
            return Decimal(0)
        return raw.quantize(self.quantum, rounding=ROUND_DOWN)

    def check(
        self,
        *,
        stake,
        bankroll,
        win_chance,
        multiplier,
        session_realized: Decimal | int | str = 0,
    ) -> None:
        """Raise :class:`EdgeRefused` unless ``stake`` satisfies every rule.

        ``session_realized`` is the running realised PnL of the session (a
        negative number is a loss). It is passed in rather than tracked here so
        this stays pure and testable.
        """
        amount = Decimal(str(stake))
        if amount <= 0:
            raise EdgeRefused(f"stake must be positive, got {amount}")

        cap = self.session_loss_cap
        if cap is not None:
            loss = -Decimal(str(session_realized))
            if loss >= cap:
                raise EdgeRefused(
                    f"session loss {loss} has reached the cap {cap}; "
                    "rule 4 stops play here rather than after the next bet."
                )

        allowed = self.stake_for(
            bankroll=bankroll, win_chance=win_chance, multiplier=multiplier
        )
        if allowed <= 0:
            raise EdgeRefused(
                "rule 1: measured EV is "
                f"{implied_edge(win_chance, multiplier) * 100:.4f}% per unit staked, so the "
                "Kelly-optimal stake is zero. No positive stake is authorised for a "
                "negative-edge game."
            )
        if amount > allowed:
            raise EdgeRefused(
                f"stake {amount} exceeds the fractional-Kelly bound {allowed} "
                f"({self.kelly_fraction} x f* = {kelly_fraction(win_chance, multiplier)}) "
                "for a bankroll of "
                f"{bankroll}"
            )


@dataclass(frozen=True)
class PlayPolicy:
    """Loss-capped play for a game with no edge to capture.

    This is deliberately **not** a strategy. With ``EV < 0`` no stake is
    growth-optimal, so there is nothing to optimise; what remains is a budget
    decision. The policy exists so that choosing to play anyway is *labelled*
    and *bounded* rather than dressed up as edge capture:

    * a **fixed stake** every round -- with negative EV the way to maximise
      rounds played for a given budget is to bet the minimum, and never raise
      it in response to a loss;
    * a **hard stop** once the realised loss reaches the cap, checked before
      dispatch so the cap is never overshot by one bet.

    ``stake_for`` ignores the edge entirely, which is the honest statement:
    the stake here is not derived from a probability, it is a budget line.
    """

    stake: Decimal
    session_loss_cap: Decimal
    quantum: Decimal = Decimal("0.00000001")

    #: A budget line needs no probability: play mode is sized by the budget.
    requires_edge: ClassVar[bool] = False

    def stake_for(self, **_ignored) -> Decimal:
        """The fixed stake. Present so the client can treat both policies alike."""
        return Decimal(str(self.stake)).quantize(self.quantum, rounding=ROUND_DOWN)

    def check(
        self,
        *,
        stake,
        bankroll=None,
        win_chance=None,
        multiplier=None,
        session_realized: Decimal | int | str = 0,
    ) -> None:
        """Raise :class:`EdgeRefused` if the budget is spent or the stake moved."""
        amount = Decimal(str(stake))
        if amount <= 0:
            raise EdgeRefused(f"stake must be positive, got {amount}")

        loss = -Decimal(str(session_realized))
        if loss >= self.session_loss_cap:
            raise EdgeRefused(
                f"entertainment budget spent: realised loss {loss} has reached the cap "
                f"{self.session_loss_cap}. Stopping here, not after the next bet."
            )

        fixed = self.stake_for()
        if amount > fixed:
            raise EdgeRefused(
                f"stake {amount} exceeds the fixed play stake {fixed}; play mode bets the "
                "same minimum every round and does not raise it after a loss"
            )
