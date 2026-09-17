"""The replay loop: one session at a time, one bet at a time.

A session walks a round iterator and tracks a single bankroll starting at zero.
``net`` is therefore always relative P/L, never an account balance: the engine
has no notion of a funded account, because a backtest should not either.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator

from .model import Bet, Round
from .strategies import OnExhausted, Strategy


class EndReason(str, Enum):
    """Why a session stopped."""

    PROFIT_TARGET = "profit_target"
    STOP_LOSS = "stop_loss"
    MAX_BETS = "max_bets"
    LADDER_EXHAUSTED = "ladder_exhausted"
    SOURCE_EXHAUSTED = "source_exhausted"


@dataclass(frozen=True)
class SessionResult:
    """Outcome of one session."""

    net: float
    bets: int
    cycles: int
    wagered: float
    ended: EndReason
    peak: float
    max_drawdown: float
    largest_stake: float
    curve: tuple[float, ...] | None = None

    @property
    def ruined(self) -> bool:
        return self.ended is EndReason.STOP_LOSS


def run_session(
    strategy: Strategy,
    rounds: Iterator[Round],
    *,
    trace: bool = False,
) -> SessionResult:
    """Play one session against ``rounds`` until a terminal condition fires.

    With ``trace=True`` the bankroll after each bet is kept on the result; that
    is off by default because a sweep of 100k sessions would otherwise hold
    100k curves.
    """
    net = 0.0
    losses = 0
    bets = 0
    cycles = 0
    wagered = 0.0
    peak = 0.0
    max_drawdown = 0.0
    largest_stake = 0.0
    ended = EndReason.SOURCE_EXHAUSTED
    curve: list[float] | None = [0.0] if trace else None

    while True:
        # Terminal conditions are checked before staking, so a session that has
        # already met its target or stop never places one bet too many.
        if strategy.max_bets is not None and bets >= strategy.max_bets:
            ended = EndReason.MAX_BETS
            break
        if strategy.profit_target is not None and net >= strategy.profit_target:
            ended = EndReason.PROFIT_TARGET
            break
        if strategy.stop_loss is not None and net <= -strategy.stop_loss:
            ended = EndReason.STOP_LOSS
            break

        # Resolve the stake BEFORE pulling a round, so an exhausted ladder does
        # not silently consume an outcome the next cycle would have used.
        stake = strategy.schedule.stake(strategy.base_stake, losses)
        if stake is None:
            cycles += 1
            losses = 0
            if strategy.on_exhausted is OnExhausted.STOP:
                ended = EndReason.LADDER_EXHAUSTED
                break
            continue

        try:
            round_ = next(rounds)
        except StopIteration:
            ended = EndReason.SOURCE_EXHAUSTED
            break

        bet = Bet(
            market=strategy.market,
            stake=stake,
            threshold=strategy.threshold,
            payout=strategy.payout,
            direction=strategy.direction,
        )
        net += bet.net(round_)
        wagered += stake
        bets += 1
        largest_stake = max(largest_stake, stake)

        if bet.wins(round_):
            losses = 0
            cycles += 1
        else:
            losses += 1

        peak = max(peak, net)
        max_drawdown = max(max_drawdown, peak - net)
        if curve is not None:
            curve.append(net)

    return SessionResult(
        net=net,
        bets=bets,
        cycles=cycles,
        wagered=wagered,
        ended=ended,
        peak=peak,
        max_drawdown=max_drawdown,
        largest_stake=largest_stake,
        curve=None if curve is None else tuple(curve),
    )
