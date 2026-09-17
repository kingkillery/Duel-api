#!/usr/bin/env python3
"""Command-line front end for the strategy backtester.

Examples
--------
    # Zero-edge martingale: the ladder from README discussion.
    python -m backtest run --schedule martingale --base 0.50 --max-rungs 3 \
        --threshold 2.0 --payout 2.0 --profit-target 5.00 --stop-loss 4.00 \
        --edge 0.0 --sessions 50000

    # Same plan against a recorded capture instead of a generator.
    python -m backtest run --capture captures/crash.jsonl --schedule martingale \
        --base 0.50 --max-rungs 3 --threshold 2.0 --payout 2.0 \
        --profit-target 5.00 --stop-loss 4.00 --sessions 5000

    python -m backtest rounds captures/crash.jsonl
    python -m backtest sessions --schedule martingale --base 0.50 --max-rungs 3 \
        --profit-target 5.00 --stop-loss 4.00 --count 3

Read-only by design: there is no wagering command, and this package cannot place
a bet.  See README.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .edges import table_from_json
from .model import BacktestError, read_rounds
from .report import Report, backtest, sample_sessions
from .sources import RecordedSource, SyntheticSource
from .strategies import (
    Fibonacci,
    Flat,
    Martingale,
    OnExhausted,
    StakingSchedule,
    Strategy,
)


def _schedule(args: argparse.Namespace) -> StakingSchedule:
    if args.schedule == "flat":
        return Flat(multiplier=args.factor)
    if args.schedule == "martingale":
        return Martingale(factor=args.factor, max_rungs=args.max_rungs)
    if args.schedule == "fibonacci":
        return Fibonacci(max_rungs=args.max_rungs)
    raise BacktestError(f"unknown schedule {args.schedule!r}")


def _strategy(args: argparse.Namespace) -> Strategy:
    return Strategy(
        market=args.market,
        base_stake=args.base,
        threshold=args.threshold,
        payout=args.payout,
        schedule=_schedule(args),
        profit_target=args.profit_target,
        stop_loss=args.stop_loss,
        max_bets=args.max_bets,
        on_exhausted=OnExhausted(args.on_exhausted),
    )


def _resolved_edge(args: argparse.Namespace) -> float:
    """The house edge to simulate: ``--edge``, or the published edge of ``--game``.

    ``--edges-from`` replaces the guessed edge with the site's published number
    for one game. Combining it with a non-default ``--edge`` is an error - both
    answer the same question, and letting one silently win would hide which
    number a report is actually about.
    """
    if args.edges_from is None:
        return args.edge
    if args.edge != 0.0:
        raise BacktestError(
            "--edges-from and --edge are mutually exclusive; pick the published "
            "table or the manual guess, not both"
        )
    try:
        table = table_from_json(args.edges_from)
    except (OSError, ValueError) as exc:
        raise BacktestError(f"--edges-from {args.edges_from}: {exc}") from exc
    try:
        entry = table.lookup(args.game)
    except KeyError:
        raise BacktestError(
            f"--game {args.game!r} is not in {args.edges_from}; known: {', '.join(table.games)}"
        ) from None
    if not args.quiet:
        print(
            f"edge: using published house edge for {entry.game!r}: "
            f"{entry.house_edge * 100:.4g}% (source {entry.source})",
            file=sys.stderr,
        )
    return entry.house_edge


def _source(args: argparse.Namespace) -> RecordedSource | SyntheticSource:
    if args.capture:
        if args.edges_from is not None or args.edge != 0.0:
            # A capture carries real recorded outcomes, so its edge is already in
            # the data and applying another would double-count. This branch used
            # to return before resolving the edge at all, so both flags were
            # dropped without a word and the report showed a number the operator
            # never asked for.
            print(
                "note: --capture supplies recorded outcomes, so the configured edge "
                "is NOT applied; drop --edge/--edges-from, or drop --capture to "
                "simulate a synthetic edge.",
                file=sys.stderr,
            )
        return RecordedSource.from_jsonl(args.capture, cycle=not args.no_cycle)
    edge = _resolved_edge(args)
    if not args.quiet and edge == 0.0:
        print(
            "note: --edge 0.0 assumes a perfectly fair game. Expect EV 0.000 and "
            "no strategy to beat it; pass a real edge to see the realistic case.",
            file=sys.stderr,
        )
    return SyntheticSource(edge=edge, market=args.market, seed=args.seed)


def _emit(report: Report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_json(), indent=1, sort_keys=True))
    else:
        print(report.summary())


def _warn_if_capture_too_short(source, sessions: int) -> None:
    """A short capture cycled over many sessions repeats outcomes exactly.

    Every session then replays the same path, which reads as a suspiciously
    clean result - a 5-round capture can show a 100% win rate.  Say so rather
    than let the number stand unqualified.
    """
    if not isinstance(source, RecordedSource) or not source.cycle:
        return
    held = len(source.rounds)
    if held < sessions:
        print(
            f"note: capture holds {held} rounds for {sessions} sessions, so "
            "outcomes repeat and sessions are not independent. Use a longer "
            "capture, --no-cycle, or a synthetic --edge instead.",
            file=sys.stderr,
        )


def cmd_run(args: argparse.Namespace) -> int:
    source = _source(args)
    _warn_if_capture_too_short(source, args.sessions)
    _emit(backtest(_strategy(args), source, args.sessions), args.json)
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    results = sample_sessions(_strategy(args), _source(args), args.count)
    for index, result in enumerate(results):
        curve = ", ".join(f"{v:+.2f}" for v in (result.curve or ()))
        print(
            f"session {index}: net {result.net:+.2f}  bets {result.bets}  "
            f"cycles {result.cycles}  ended {result.ended.value}  "
            f"maxdd {result.max_drawdown:.2f}"
        )
        print(f"  path: {curve}")
    return 0


def cmd_rounds(args: argparse.Namespace) -> int:
    rounds = read_rounds(args.path)
    markets: dict[str, list[float]] = {}
    for round_ in rounds:
        for market, value in round_.outcomes.items():
            markets.setdefault(market, []).append(value)
    print(f"{args.path}: {len(rounds)} rounds")
    for market, values in sorted(markets.items()):
        print(
            f"  {market:<16} n={len(values)}  min={min(values):.4f}  "
            f"max={max(values):.4f}  mean={sum(values) / len(values):.4f}"
        )
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--market", default="crash", help="market key in each round")
    parser.add_argument("--base", type=float, default=0.50, help="base stake")
    parser.add_argument(
        "--schedule", choices=("flat", "martingale", "fibonacci"), default="martingale"
    )
    parser.add_argument(
        "--factor", type=float, default=2.0, help="progression factor / flat multiplier"
    )
    parser.add_argument(
        "--max-rungs", type=int, default=3, help="ladder depth cap (None for unbounded)"
    )
    parser.add_argument(
        "--threshold", type=float, default=2.0, help="multiplier the outcome must reach"
    )
    parser.add_argument(
        "--payout", type=float, default=2.0, help="gross multiple returned on a win"
    )
    parser.add_argument("--profit-target", type=float, default=5.00)
    parser.add_argument("--stop-loss", type=float, default=4.00)
    parser.add_argument("--max-bets", type=int, default=None)
    parser.add_argument(
        "--on-exhausted", choices=("reset", "stop"), default="reset",
        help="reset the ladder and continue, or end the session",
    )
    parser.add_argument("--capture", type=Path, default=None, help="JSONL round capture")
    parser.add_argument("--no-cycle", action="store_true", help="do not wrap the capture")
    parser.add_argument("--edge", type=float, default=0.0, help="synthetic house edge")
    parser.add_argument(
        "--edges-from",
        type=Path,
        default=None,
        help="JSON file of published edges (rakeback payload or game catalogue); overrides --edge",
    )
    parser.add_argument("--game", default="crash", help="game key to look up in --edges-from")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--quiet", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backtest",
        description="Backtest staking strategies over round histories. Read-only.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="aggregate statistics over many sessions")
    _add_common(run)
    run.add_argument("--sessions", type=int, default=50_000)
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    sessions = sub.add_parser("sessions", help="print traced bankroll paths")
    _add_common(sessions)
    sessions.add_argument("--count", type=int, default=3)
    sessions.set_defaults(func=cmd_sessions)

    rounds = sub.add_parser("rounds", help="summarise a JSONL capture")
    rounds.add_argument("path", type=Path)
    rounds.set_defaults(func=cmd_rounds)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except BacktestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
