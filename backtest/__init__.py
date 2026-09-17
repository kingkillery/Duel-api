"""Strategy backtesting over recorded or synthetic round data.

This package evaluates staking strategies against *round histories*.  It holds
no HTTP client and has no write path: rounds come from a JSONL capture or a
generator, so it cannot place a bet even by accident.  That separation is the
point - strategy evaluation stays a read-only exercise.

Entry points
------------
    from backtest import Martingale, Strategy, SyntheticSource, backtest

    strategy = Strategy(
        market="crash",
        base_stake=0.50,
        threshold=2.0,
        payout=2.0,
        schedule=Martingale(factor=2.0, max_rungs=3),
        profit_target=5.00,
        stop_loss=4.00,
    )
    report = backtest(strategy, SyntheticSource(edge=0.0, seed=1), sessions=50_000)
    print(report.summary())
"""

from __future__ import annotations

from .edges import EdgeTable, GameEdge, from_rakeback, from_rtp, table_from_json
from .engine import EndReason, SessionResult, run_session
from .model import (
    BacktestError,
    Bet,
    Direction,
    Round,
    RoundSource,
    UnknownMarket,
    read_rounds,
    write_rounds,
)
from .report import Report, ReportError, backtest, percentile, sample_sessions
from .sources import RecordedSource, SourceError, SyntheticSource
from .strategies import (
    Fibonacci,
    Flat,
    Martingale,
    OnExhausted,
    ScheduleError,
    StakingSchedule,
    Strategy,
)

__all__ = [
    "BacktestError",
    "Bet",
    "Direction",
    "EdgeTable",
    "EndReason",
    "Fibonacci",
    "Flat",
    "GameEdge",
    "Martingale",
    "OnExhausted",
    "RecordedSource",
    "Report",
    "ReportError",
    "Round",
    "RoundSource",
    "ScheduleError",
    "SessionResult",
    "SourceError",
    "StakingSchedule",
    "Strategy",
    "SyntheticSource",
    "UnknownMarket",
    "backtest",
    "from_rakeback",
    "from_rtp",
    "percentile",
    "read_rounds",
    "run_session",
    "sample_sessions",
    "table_from_json",
    "write_rounds",
]
