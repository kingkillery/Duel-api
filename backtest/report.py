"""Backtest driver and the statistics it reports.

The headline number is ``implied_edge``, recovered from the run itself:

    E[session net] = -edge * E[total wagered]   =>   edge = -E[net] / E[wagered]

That identity holds for every staking schedule ever devised, and the engine
deriving it back from simulated results is the cheapest check that a run is
internally consistent.  A martingale does not perturb it - it raises
``mean_wagered``, which is precisely why it loses faster.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from .engine import EndReason, SessionResult, run_session
from .model import BacktestError
from .strategies import Strategy
from .model import RoundSource


class ReportError(BacktestError):
    """A report was requested over no usable results."""


def percentile(ordered: list[float], q: float) -> float:
    """Linear-interpolated percentile over an already-sorted list."""
    if not ordered:
        raise ReportError("cannot take a percentile of an empty sample")
    if not 0.0 <= q <= 1.0:
        raise ReportError(f"percentile q must be in [0, 1], got {q!r}")
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _stdev(values: list[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


@dataclass(frozen=True)
class Report:
    """Aggregate statistics over many sessions of one strategy."""

    sessions: int
    mean_net: float
    stdev_net: float
    stderr_net: float
    ruin_probability: float
    target_probability: float
    mean_bets: float
    mean_cycles: float
    mean_wagered: float
    implied_edge: float
    mean_max_drawdown: float
    worst_net: float
    best_net: float
    largest_stake: float
    mean_bets_to_ruin: float | None
    percentiles: Mapping[str, float]
    ended_counts: Mapping[str, int]

    @property
    def ev_per_session(self) -> float:
        return self.mean_net

    def to_json(self) -> dict:
        return {
            "sessions": self.sessions,
            "mean_net": round(self.mean_net, 6),
            "stdev_net": round(self.stdev_net, 6),
            "stderr_net": round(self.stderr_net, 6),
            "ruin_probability": round(self.ruin_probability, 6),
            "target_probability": round(self.target_probability, 6),
            "mean_bets": round(self.mean_bets, 4),
            "mean_cycles": round(self.mean_cycles, 4),
            "mean_wagered": round(self.mean_wagered, 6),
            "implied_edge": None
            if math.isnan(self.implied_edge)
            else round(self.implied_edge, 6),
            "mean_max_drawdown": round(self.mean_max_drawdown, 6),
            "worst_net": round(self.worst_net, 6),
            "best_net": round(self.best_net, 6),
            "largest_stake": round(self.largest_stake, 6),
            "mean_bets_to_ruin": None
            if self.mean_bets_to_ruin is None
            else round(self.mean_bets_to_ruin, 4),
            "percentiles": {k: round(v, 6) for k, v in self.percentiles.items()},
            "ended_counts": dict(self.ended_counts),
        }

    def summary(self) -> str:
        edge = (
            "n/a"
            if math.isnan(self.implied_edge)
            else f"{self.implied_edge * 100:+.3f}%"
        )
        lines = [
            f"sessions          {self.sessions}",
            f"EV / session      {self.mean_net:+.4f}  (+/-{self.stderr_net:.4f} s.e.)",
            f"stdev / session   {self.stdev_net:.4f}",
            f"P(profit target)  {self.target_probability:.4f}",
            f"P(stop loss)      {self.ruin_probability:.4f}",
            f"mean wagered      {self.mean_wagered:.4f}",
            f"implied edge      {edge}",
            f"mean max drawdown {self.mean_max_drawdown:.4f}",
            f"largest stake     {self.largest_stake:.4f}",
            f"mean bets         {self.mean_bets:.2f}",
            f"net p05/p50/p95   {self.percentiles['p05']:+.2f} / "
            f"{self.percentiles['p50']:+.2f} / {self.percentiles['p95']:+.2f}",
            f"worst / best net  {self.worst_net:+.2f} / {self.best_net:+.2f}",
        ]
        if self.mean_bets_to_ruin is not None:
            lines.append(f"mean bets to ruin {self.mean_bets_to_ruin:.2f}")
        reasons = ", ".join(
            f"{reason}={count}" for reason, count in sorted(self.ended_counts.items())
        )
        lines.append(f"ended             {reasons}")
        return "\n".join(lines)


def backtest(strategy: Strategy, source: RoundSource, sessions: int) -> Report:
    """Run ``sessions`` independent sessions and aggregate them."""
    if sessions < 1:
        raise ReportError(f"sessions must be >= 1, got {sessions!r}")

    nets: list[float] = []
    ended_counts: Counter[str] = Counter()
    bets = cycles = wagered = drawdown = 0.0
    largest_stake = 0.0
    ruin_count = 0
    target_count = 0
    bets_to_ruin = 0.0
    worst = math.inf
    best = -math.inf

    for index in range(sessions):
        result = run_session(strategy, source.session(index))
        nets.append(result.net)
        ended_counts[result.ended.value] += 1
        bets += result.bets
        cycles += result.cycles
        wagered += result.wagered
        drawdown += result.max_drawdown
        largest_stake = max(largest_stake, result.largest_stake)
        worst = min(worst, result.net)
        best = max(best, result.net)
        if result.ended is EndReason.STOP_LOSS:
            ruin_count += 1
            bets_to_ruin += result.bets
        if result.ended is EndReason.PROFIT_TARGET:
            target_count += 1

    mean_net = sum(nets) / sessions
    mean_wagered = wagered / sessions
    ordered = sorted(nets)

    return Report(
        sessions=sessions,
        mean_net=mean_net,
        stdev_net=_stdev(nets, mean_net),
        stderr_net=_stdev(nets, mean_net) / math.sqrt(sessions),
        ruin_probability=ruin_count / sessions,
        target_probability=target_count / sessions,
        mean_bets=bets / sessions,
        mean_cycles=cycles / sessions,
        mean_wagered=mean_wagered,
        implied_edge=(
            math.nan if mean_wagered == 0.0 else -mean_net / mean_wagered
        ),
        mean_max_drawdown=drawdown / sessions,
        worst_net=worst,
        best_net=best,
        largest_stake=largest_stake,
        mean_bets_to_ruin=(bets_to_ruin / ruin_count) if ruin_count else None,
        percentiles={
            "p05": percentile(ordered, 0.05),
            "p25": percentile(ordered, 0.25),
            "p50": percentile(ordered, 0.50),
            "p75": percentile(ordered, 0.75),
            "p95": percentile(ordered, 0.95),
        },
        ended_counts=dict(ended_counts),
    )


def sample_sessions(
    strategy: Strategy, source: RoundSource, count: int
) -> list[SessionResult]:
    """Traced sessions, for inspecting the bankroll path one session at a time."""
    if count < 1:
        raise ReportError(f"count must be >= 1, got {count!r}")
    return [
        run_session(strategy, source.session(index), trace=True)
        for index in range(count)
    ]
