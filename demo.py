"""``duel-api demo`` - the offline, zero-setup first run.

Runs three staking schedules through the real backtest engine at the most
favorable edge tier duel.com offers, then prints the only conclusion the math
supports. It exists so a visitor's first experience of this tool is a result,
not an installation.

Importantly, this module is offline like the engine it drives: it performs no
network access, and a test enforces that (``tests/test_demo.py``) the same way
``tests/test_backtest.py`` guards the backtest package.
"""

from __future__ import annotations

from dataclasses import dataclass

import theme
from backtest.report import Report, backtest
from backtest.sources import SyntheticSource
from backtest.strategies import Fibonacci, Flat, Martingale, Strategy

__all__ = ["BEST_OBSERVED_EDGE", "DemoResult", "run", "render"]

# duel.com's observed dice edge tiers run 0.001 -> 0.0099. The demo uses the
# most favorable one: if the best tier loses, the worst tier needs no demo.
BEST_OBSERVED_EDGE = 0.001


@dataclass(frozen=True)
class DemoPlan:
    label: str
    strategy: Strategy


def _plans(base: float) -> list[DemoPlan]:
    common = dict(
        market="dice",
        base_stake=base,
        threshold=2.0,
        payout=2.0,  # under-5000 style: even money at the observed tier
        profit_target=base * 10.0,
        stop_loss=base * 8.0,
    )
    return [
        DemoPlan("flat", Strategy(schedule=Flat(multiplier=1.0), **common)),
        DemoPlan("martingale-3", Strategy(schedule=Martingale(factor=2.0, max_rungs=3), **common)),
        DemoPlan("fibonacci-5", Strategy(schedule=Fibonacci(max_rungs=5), **common)),
    ]


@dataclass(frozen=True)
class DemoResult:
    edge: float
    sessions: int
    base: float
    rows: list[dict]
    kelly_fraction: float
    verdict: str
    pooled_implied_edge: float


def run(
    *,
    edge: float = BEST_OBSERVED_EDGE,
    sessions: int = 10_000,
    base: float = 0.50,
    seed: int = 0,
) -> DemoResult:
    """Simulate the demo strategies. Deterministic for a given seed."""
    source = SyntheticSource(edge=edge, market="dice", seed=seed)
    rows = []
    for plan in _plans(base):
        report: Report = backtest(plan.strategy, source, sessions)
        rows.append({"label": plan.label, **report.to_json()})

    pooled_net = sum(r["mean_net"] for r in rows) / len(rows)
    pooled_wagered = sum(r["mean_wagered"] for r in rows) / len(rows)
    pooled_edge = -pooled_net / pooled_wagered if pooled_wagered else 0.0

    # At 1:1 payout (threshold == payout), p = (1 - edge) / 2, so the Kelly
    # fraction collapses to p - q = -edge: negative at every tier ever observed.
    kelly = -edge
    verdict = (
        "growth-optimal stake is exactly zero - BankrollPolicy fails closed"
        if kelly <= 0
        else f"growth-optimal fraction f* = {kelly:+.6f} of bankroll"
    )
    return DemoResult(
        edge=edge,
        sessions=sessions,
        base=base,
        rows=rows,
        kelly_fraction=kelly,
        verdict=verdict,
        pooled_implied_edge=pooled_edge,
    )


def render(result: DemoResult) -> str:
    out: list[str] = [
        theme.bold("duel-api demo"),
        theme.dim(theme.rule(72)),
        "",
        theme.kv("game", "dice-style, 1:1 payout"),
        theme.kv("edge", f"{result.edge:.4%} (the most favorable tier duel.com offers)"),
        theme.kv("sessions", f"{result.sessions:,} per schedule, deterministic seed"),
        theme.kv("base stake", f"{result.base:.2f}"),
        "",
        theme.bold("every staking schedule, same destination"),
    ]
    rows = []
    for r in result.rows:
        stderr_edge = r["stderr_net"] / r["mean_wagered"] if r["mean_wagered"] else 0.0
        rows.append(
            [
                r["label"],
                f"{r['mean_net']:+.4f}",
                f"{r['implied_edge'] * 100:+.3f}% ±{stderr_edge * 100:.3f}%"
                if r["implied_edge"] is not None
                else "n/a",
                f"{r['target_probability']:.3f}",
                f"{r['ruin_probability']:.3f}",
                f"{r['mean_wagered']:.2f}",
            ]
        )
    out.append(
        theme.table(
            ["schedule", "EV/session", "implied edge", "P(target)", "P(stop)", "mean wagered"],
            rows,
            aligns=("left", "right", "right", "right", "right", "right"),
        )
    )
    out.append("")
    out.append(
        theme.kv(
            "pooled implied edge",
            f"{result.pooled_implied_edge * 100:+.3f}% across every schedule "
            f"(true edge {result.edge:.3%})",
        )
    )
    out.append(
        theme.dim("  per-schedule estimates carry sampling noise; the pool converges to the truth")
    )
    out.append("")
    out.append(theme.bold("the invariant"))
    out.append(f"  E[net] = {theme.red('-edge')} x E[total wagered]")
    out.append(f"  Sizing changes variance, never sign. Kelly f* = {result.kelly_fraction:+.6f}.")
    out.append("")
    out.append(theme.bold("verdict"))
    out.append(f"  {theme.cyan(result.verdict)}.")
    out.append(f"  {theme.dim('The most useful thing this tool can print.')}")
    return "\n".join(out)
