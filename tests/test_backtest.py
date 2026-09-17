"""Backtest engine tests: arithmetic, scope, and an independent oracle.

The oracle here is deliberately a *separate implementation* of the same
question, not a re-run of the engine: ruin probability is computed by solving
the absorbing Markov chain over per-bet states exactly, with Gaussian
elimination.  Monte Carlo is then checked against it.

Stopping granularity matters and is pinned explicitly: the engine evaluates
stop-loss and profit-target *after every bet*, not once per completed ladder.
That is why ruin sits near 0.53 for the 0.50/2.0/3-rung ladder rather than the
0.49 a cycle-atomic model predicts - and the bet-granularity oracle confirms it.
"""

from __future__ import annotations

import ast
import json
from itertools import islice
from pathlib import Path

import pytest

from backtest import (
    Bet,
    Direction,
    EdgeTable,
    EndReason,
    Flat,
    Fibonacci,
    GameEdge,
    Martingale,
    OnExhausted,
    RecordedSource,
    Report,
    Round,
    ScheduleError,
    SourceError,
    Strategy,
    SyntheticSource,
    UnknownMarket,
    backtest,
    from_rakeback,
    from_rtp,
    percentile,
    read_rounds,
    run_session,
    write_rounds,
)
from backtest.report import ReportError

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "backtest"


# --------------------------------------------------------------------- helpers


class ScriptedSource:
    """Yields a fixed outcome sequence, so a session's arithmetic is hand-checkable."""

    def __init__(self, outcomes: list[float], market: str = "crash") -> None:
        self.outcomes = outcomes
        self.market = market

    def session(self, index: int):
        for position, value in enumerate(self.outcomes):
            yield Round({self.market: value}, round_id=f"scripted:{index}:{position}")


def solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(rhs)
    a = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-15:
            raise AssertionError("singular system")
        a[col], a[pivot] = a[pivot], a[col]
        divisor = a[col][col]
        for r in range(col + 1, n):
            factor = a[r][col] / divisor
            if factor:
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = a[row][n] - sum(a[row][c] * solution[c] for c in range(row + 1, n))
        solution[row] = total / a[row][row]
    return solution


def oracle_target_probability(
    base: float = 0.50,
    factor: float = 2.0,
    max_rungs: int = 3,
    payout: float = 2.0,
    win_probability: float = 0.5,
    profit_target: float = 5.00,
    stop_loss: float = 4.00,
) -> float:
    """Exact P(reach +profit_target before losing stop_loss), solved directly.

    State is (net, rung); a win resets the rung, a lost final rung exhausts the
    ladder and resets it (``OnExhausted.RESET``).  Everything is in units of
    0.50 so the grid is exact.
    """
    step = 0.50
    to_units = lambda x: int(round(x / step))
    goal = to_units(profit_target)
    floor = -to_units(stop_loss)
    lowest = floor - to_units(base * factor ** (max_rungs - 1))

    grid = range(lowest, goal)
    states = [(net, rung) for net in grid for rung in range(max_rungs)]
    index = {state: i for i, state in enumerate(states)}

    matrix = [[0.0] * len(states) for _ in states]
    rhs = [0.0] * len(states)
    for (net, rung), row in index.items():
        matrix[row][row] = 1.0
        stake = to_units(base * factor**rung)
        win_net = net + to_units(base * factor**rung * (payout - 1.0))
        loss_net = net - stake
        for probability, landing, next_rung in (
            (win_probability, win_net, 0),
            (1.0 - win_probability, loss_net, rung + 1),
        ):
            if landing >= goal:
                rhs[row] += probability  # absorbed at the profit target
                continue
            if landing <= floor:
                continue  # absorbed at the stop loss, value 0
            target_rung = 0 if next_rung >= max_rungs else next_rung
            matrix[row][index[(landing, target_rung)]] -= probability
    return solve(matrix, rhs)[index[(0, 0)]]


def oracle_cycle_atomic(
    profit_target: float = 5.00,
    stop_loss: float = 4.00,
    cycle_win_probability: float = 0.875,
    win: float = 0.50,
    loss: float = 3.50,
) -> float:
    """The *wrong* granularity, kept as a foil for the engine's.

    Barriers are checked once per completed ladder rather than after each bet.
    It predicts roughly 0.49 ruin for the 3-rung ladder against the engine's
    ~0.53, so asserting the gap pins the stopping granularity down.
    """
    step = 0.50

    def to_units(value: float) -> int:
        return int(round(value / step))

    goal, floor = to_units(profit_target), -to_units(stop_loss)
    values = {net: 0.0 for net in range(floor, goal)}
    for _ in range(200_000):
        updated = {}
        for net in values:
            total = 0.0
            for probability, landing in (
                (cycle_win_probability, net + to_units(win)),
                (1.0 - cycle_win_probability, net - to_units(loss)),
            ):
                if landing >= goal:
                    total += probability
                elif landing > floor:
                    total += probability * values[landing]
            updated[net] = total
        converged = max(abs(updated[k] - values[k]) for k in values) < 1e-13
        values = updated
        if converged:
            break
    return values[0]


def martingale_strategy(**overrides) -> Strategy:
    settings = dict(
        market="crash",
        base_stake=0.50,
        threshold=2.0,
        payout=2.0,
        schedule=Martingale(factor=2.0, max_rungs=3),
        profit_target=5.00,
        stop_loss=4.00,
    )
    settings.update(overrides)
    return Strategy(**settings)


# ----------------------------------------------------------------- model / bet


def test_winning_bet_nets_stake_times_payout_minus_one() -> None:
    bet = Bet(market="crash", stake=2.00, threshold=2.0, payout=2.0)
    assert bet.net(Round({"crash": 2.0})) == pytest.approx(2.00)
    assert bet.net(Round({"crash": 9.99})) == pytest.approx(2.00)
    assert bet.net(Round({"crash": 1.99})) == pytest.approx(-2.00)


def test_at_most_direction_inverts_the_comparison() -> None:
    bet = Bet(
        market="roll",
        stake=1.0,
        threshold=50.0,
        payout=2.0,
        direction=Direction.AT_MOST,
    )
    assert bet.wins(Round({"roll": 49.0}))
    assert not bet.wins(Round({"roll": 51.0}))


def test_bet_rejects_non_positive_stake() -> None:
    with pytest.raises(ValueError):
        Bet(market="crash", stake=0.0, threshold=2.0, payout=2.0)


def test_unknown_market_names_what_the_round_does_carry() -> None:
    bet = Bet(market="dice", stake=1.0, threshold=2.0, payout=2.0)
    with pytest.raises(UnknownMarket) as excinfo:
        bet.net(Round({"crash": 2.0}))
    assert "crash" in str(excinfo.value)


def test_round_jsonl_round_trips(tmp_path: Path) -> None:
    rounds = [
        Round({"crash": 1.94}, round_id="a", timestamp=1758000000.0),
        Round({"crash": 2.31}, round_id="b"),
    ]
    path = tmp_path / "rounds.jsonl"
    assert write_rounds(path, rounds) == 2
    assert read_rounds(path) == rounds


def test_read_rounds_skips_comments_and_reports_line_numbers(tmp_path: Path) -> None:
    path = tmp_path / "rounds.jsonl"
    path.write_text(
        '# captured from the read-only feed\n\n{"outcomes": {"crash": 1.5}}\n',
        encoding="utf-8",
    )
    assert len(read_rounds(path)) == 1

    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"outcomes": {"crash": 1.5}}\n{"outcomes": {}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"bad\.jsonl:2"):
        read_rounds(bad)


# ------------------------------------------------------------------ strategies


def test_martingale_rungs_match_the_discussed_ladder() -> None:
    schedule = Martingale(factor=2.0, max_rungs=3)
    assert [schedule.stake(0.50, losses) for losses in range(3)] == [0.50, 1.00, 2.00]
    assert schedule.stake(0.50, 3) is None, "the fourth rung is unaffordable by design"


def test_fibonacci_follows_the_sequence() -> None:
    schedule = Fibonacci()
    assert [schedule.stake(1.0, losses) for losses in range(5)] == [1, 1, 2, 3, 5]


def test_flat_never_exhausts() -> None:
    schedule = Flat(multiplier=1.5)
    assert schedule.stake(2.0, 99) == 3.0


@pytest.mark.parametrize("rungs", [0, -1])
def test_martingale_rejects_unusable_ladder_depth(rungs: int) -> None:
    with pytest.raises(ScheduleError):
        Martingale(max_rungs=rungs)


def test_martingale_rejects_a_non_growing_factor() -> None:
    with pytest.raises(ScheduleError, match="factor"):
        Martingale(factor=1.0)


def test_strategy_requires_a_terminal_condition() -> None:
    with pytest.raises(ScheduleError, match="terminal condition"):
        Strategy(
            market="crash",
            base_stake=0.50,
            threshold=2.0,
            payout=2.0,
            schedule=Flat(),
        )


# ---------------------------------------------------------------------- engine


def test_gradient_ladder_nets_fifty_cents_on_every_rung() -> None:
    """A win at rung 1, 2 or 3 all net exactly +0.50 - that is the whole idea."""
    for win_at in range(3):
        outcomes = [1.0] * win_at + [3.0]
        strategy = martingale_strategy(on_exhausted=OnExhausted.STOP)
        result = run_session(strategy, ScriptedSource(outcomes).session(0))
        assert result.net == pytest.approx(0.50), f"failed at rung {win_at + 1}"
        assert result.bets == win_at + 1


def test_three_straight_losses_cost_the_sum_of_the_ladder() -> None:
    strategy = martingale_strategy(on_exhausted=OnExhausted.STOP)
    result = run_session(strategy, ScriptedSource([1.0, 1.0, 1.0]).session(0))
    assert result.net == pytest.approx(-3.50)
    assert result.ended is EndReason.LADDER_EXHAUSTED
    assert result.bets == 3


def test_ladder_cycle_wins_with_the_predicted_probability() -> None:
    """One cycle: P(+0.50) = 1 - (1/2)^3 = 0.875 at a fair 2x game."""
    # A 0.50 target ends the session on the first ladder win; STOP ends it on
    # the first exhausted ladder, so every session is exactly one cycle.
    strategy = martingale_strategy(
        profit_target=0.50, stop_loss=4.00, on_exhausted=OnExhausted.STOP
    )
    report = backtest(strategy, SyntheticSource(edge=0.0, seed=7), sessions=20_000)
    assert report.target_probability == pytest.approx(0.875, abs=0.01)
    assert report.mean_net == pytest.approx(0.875 * 0.50 - 0.125 * 3.50, abs=0.01)
    assert report.mean_net == pytest.approx(0.0, abs=0.01)


def test_reset_mode_keeps_playing_past_an_exhausted_ladder() -> None:
    strategy = martingale_strategy(on_exhausted=OnExhausted.RESET)
    result = run_session(strategy, ScriptedSource([1.0] * 6).session(0))
    assert result.ended is EndReason.STOP_LOSS
    assert result.net == pytest.approx(-4.00)
    assert result.bets == 4, "three rungs, then a fresh ladder's first rung"
    assert result.cycles == 1, "only the first ladder ran to exhaustion"


def test_source_exhaustion_ends_a_session_without_a_bet() -> None:
    strategy = martingale_strategy(max_bets=10)
    result = run_session(strategy, ScriptedSource([3.0]).session(0))
    assert result.ended is EndReason.SOURCE_EXHAUSTED
    assert result.bets == 1


def test_max_bets_caps_an_unbounded_session() -> None:
    # stop_loss is raised out of reach so the bet cap is the only way out.
    strategy = martingale_strategy(max_bets=3, stop_loss=10.00)
    result = run_session(strategy, SyntheticSource(edge=0.0, seed=3).session(0))
    assert result.ended is EndReason.MAX_BETS
    assert result.bets == 3


def test_traced_session_records_the_bankroll_path() -> None:
    strategy = martingale_strategy(on_exhausted=OnExhausted.STOP)
    result = run_session(strategy, ScriptedSource([1.0, 3.0]).session(0), trace=True)
    assert result.curve == pytest.approx((0.0, -0.50, 0.50))


# ---------------------------------------------------------------- statistics


def test_zero_edge_strategy_has_zero_expected_value() -> None:
    """The headline invariant: no schedule manufactures an edge."""
    report = backtest(
        martingale_strategy(), SyntheticSource(edge=0.0, seed=11), sessions=20_000
    )
    assert report.mean_net == pytest.approx(0.0, abs=0.15)
    assert report.implied_edge == pytest.approx(0.0, abs=0.01)


@pytest.mark.parametrize("edge", [0.01, 0.04])
def test_implied_edge_recovers_the_declared_edge(edge: float) -> None:
    report = backtest(
        martingale_strategy(), SyntheticSource(edge=edge, seed=5), sessions=20_000
    )
    assert report.implied_edge == pytest.approx(edge, abs=0.01)
    # The same identity stated directly: E[net] = -edge * E[wagered].
    assert report.mean_net == pytest.approx(-edge * report.mean_wagered, abs=0.15)


def test_ruin_probability_matches_the_independent_oracle() -> None:
    """Monte Carlo vs an exact linear solve of the absorbing chain."""
    expected = oracle_target_probability()
    report = backtest(
        martingale_strategy(), SyntheticSource(edge=0.0, seed=23), sessions=20_000
    )
    assert report.target_probability == pytest.approx(expected, abs=0.015)
    assert report.ruin_probability == pytest.approx(1.0 - expected, abs=0.015)


def test_bet_granularity_not_cycle_granularity_sets_the_ruin_rate() -> None:
    """Document the distinction the oracle exists to pin down.

    A cycle-atomic model (barriers checked once per completed ladder) predicts
    roughly 0.49 ruin for this ladder.  The engine checks after every bet, which
    is what a live loop would do, and lands near 0.53.  Asserting the gap keeps
    a future refactor from silently switching granularity.
    """
    cycle_atomic = oracle_cycle_atomic()
    per_bet = oracle_target_probability()
    assert cycle_atomic == pytest.approx(0.5095, abs=0.002)
    assert per_bet == pytest.approx(0.4686, abs=0.002)
    assert per_bet < cycle_atomic - 0.03


def test_stop_loss_does_not_bound_the_loss_at_the_stated_amount() -> None:
    """A lumpy rung overshoots the stop, so -4.00 is not the worst case.

    ``stop_loss=4.00`` means "quit once net <= -4.00", evaluated after a bet
    settles.  A 2.00 rung lost while sitting at -3.50 lands at -5.50, so the
    realized loss can exceed the stated cap.
    """
    strategy = martingale_strategy(on_exhausted=OnExhausted.RESET)
    outcomes = [1.0] * 3 + [3.0] * 3 + [1.0] * 3
    result = run_session(strategy, ScriptedSource(outcomes).session(0))
    assert result.ended is EndReason.STOP_LOSS
    assert result.net == pytest.approx(-5.50)
    assert result.bets == 9

    # It shows up in aggregate too, not only on a hand-built path.
    report = backtest(
        martingale_strategy(), SyntheticSource(edge=0.0, seed=13), sessions=5_000
    )
    assert report.worst_net < -4.00


def test_percentile_interpolates_between_neighbours() -> None:
    assert percentile([1.0, 2.0, 3.0], 0.5) == pytest.approx(2.0)
    assert percentile([1.0, 2.0], 0.5) == pytest.approx(1.5)
    assert percentile([7.0], 0.9) == 7.0


def test_report_rejects_an_empty_sample() -> None:
    with pytest.raises(ReportError):
        percentile([], 0.5)
    with pytest.raises(ReportError):
        backtest(martingale_strategy(), SyntheticSource(), sessions=0)


def test_report_serialises_to_json() -> None:
    report = backtest(
        martingale_strategy(), SyntheticSource(edge=0.0, seed=1), sessions=500
    )
    payload = json.loads(json.dumps(report.to_json()))
    assert payload["sessions"] == 500
    assert set(payload["percentiles"]) == {"p05", "p25", "p50", "p75", "p95"}
    assert "summary" in dir(report)


# -------------------------------------------------------------------- sources


def test_synthetic_win_probability_is_exact() -> None:
    source = SyntheticSource(edge=0.04, market="crash")
    assert source.win_probability(2.0) == pytest.approx(0.48)
    assert source.expected_edge(2.0, 2.0) == pytest.approx(0.04)


def test_synthetic_empirical_rate_matches_the_formula() -> None:
    source = SyntheticSource(edge=0.0, seed=99)
    rounds = [next(source.session(i)) for i in range(4_000)]
    wins = sum(1 for round_ in rounds if round_.outcome("crash") >= 2.0)
    assert wins / len(rounds) == pytest.approx(0.5, abs=0.02)


def test_synthetic_rejects_an_impossible_edge() -> None:
    with pytest.raises(SourceError):
        SyntheticSource(edge=1.0)


def test_synthetic_sessions_are_independent_of_each_other() -> None:
    source = SyntheticSource(edge=0.0, seed=4)
    first = [next(source.session(i)).outcome("crash") for i in range(5)]
    assert len(set(first)) == 5, "each session index must draw its own stream"


def test_recorded_source_replays_and_can_stop_at_the_end() -> None:
    rounds = [Round({"crash": 3.0}), Round({"crash": 1.0})]
    cycled = list(islice(RecordedSource(rounds).session(0), 4))
    assert [r.outcome("crash") for r in cycled] == [3.0, 1.0, 3.0, 1.0]
    once = list(RecordedSource(rounds, cycle=False).session(0))
    assert [r.outcome("crash") for r in once] == [3.0, 1.0]


def test_recorded_source_rejects_an_empty_capture() -> None:
    with pytest.raises(SourceError):
        RecordedSource([])


def test_recorded_source_reports_its_markets() -> None:
    rounds = [Round({"crash": 3.0}), Round({"dice": 1.0})]
    assert RecordedSource(rounds).markets == {"crash", "dice"}


def test_backtest_runs_against_a_recorded_capture() -> None:
    rounds = [Round({"crash": 3.0}), Round({"crash": 1.0})] * 50
    report = backtest(
        martingale_strategy(max_bets=20), RecordedSource(rounds), sessions=200
    )
    assert isinstance(report, Report)
    assert report.sessions == 200


# ----------------------------------------------------------------------- edges


# Shape captured live from GET /api/v2/rakeback. Hilo carries an edge but is
# disabled; a game that publishes no house_edge_percentage ("ghost") must get
# no entry rather than a fabricated one.
RAKEBACK_PAYLOAD = {
    "data": {
        "enabled": True,
        "limits": {"daily_wager_limit": 50000, "max_bet": 1000},
        "games": {
            "crash": {"name": "Crash", "house_edge_percentage": 0.1, "enabled": True},
            "roulette": {"name": "Roulette", "house_edge_percentage": 0, "enabled": True},
            "hilo": {"name": "Hilo", "house_edge_percentage": 0.1, "enabled": False},
            "ghost": {"name": "Ghost"},
        },
    }
}


def test_from_rakeback_parses_the_live_payload_shape() -> None:
    """Percentages become fractions; disabled games are flagged, not dropped."""
    edges = from_rakeback(RAKEBACK_PAYLOAD)
    assert edges["crash"].house_edge == pytest.approx(0.001)
    assert edges["crash"].source == "rakeback"
    assert edges["roulette"].house_edge == 0.0
    assert edges["hilo"].house_edge == pytest.approx(0.001)
    assert edges["hilo"].source == "rakeback:disabled"


def test_from_rakeback_never_invents_a_missing_edge() -> None:
    """A game without a published house_edge_percentage gets no entry at all."""
    assert "ghost" not in from_rakeback(RAKEBACK_PAYLOAD)


def test_from_rtp_skips_null_rtp_and_converts_percentages() -> None:
    """null rtp must not become edge 0 - that would manufacture a fair game."""
    edges = from_rtp(
        [
            {"name": "Crash", "rtp": 99.44},
            {"name": "Limbo", "rtp": 95.65},
            {"name": "Live Roulette", "rtp": None},
            {"name": "Mystery"},
        ]
    )
    assert edges["crash"].house_edge == pytest.approx(0.0056)
    assert edges["limbo"].house_edge == pytest.approx(0.0435)
    assert edges["crash"].source == "rtp"
    assert "live roulette" not in edges
    assert "mystery" not in edges


def test_edge_table_lookup_raises_for_unknown_games() -> None:
    """Reading an unknown game as edge 0 would silently manufacture a fair game."""
    table = EdgeTable(from_rakeback(RAKEBACK_PAYLOAD))
    assert table.lookup("CRASH").house_edge == pytest.approx(0.001)
    with pytest.raises(KeyError):
        table.lookup("missing")


def test_real_crash_edge_shows_negative_ev() -> None:
    """At Crash's published 0.1% edge the reported EV must come out negative.

    20k sessions were measured underpowered here: the signal is only
    -0.001 * mean_wagered ~= -0.019 while one session's stdev is ~4.7, so the
    stderr (0.033) swamped the sign. 200k puts the realized mean three stderr
    below zero and recovers the declared edge in ``implied_edge``.
    """
    report = backtest(martingale_strategy(), SyntheticSource(edge=0.001, seed=1), 200_000)
    assert report.mean_net < 0, f"mean_net {report.mean_net} lost the sign at a 0.1% edge"
    assert report.implied_edge == pytest.approx(0.001, abs=0.001)


# ------------------------------------------------------------------ scope guard

FORBIDDEN_MODULES = {
    "httpx",
    "requests",
    "urllib",
    "urllib3",
    "socket",
    "http",
    "automation_client",
}


def test_backtest_package_cannot_reach_the_network() -> None:
    """The safety property is structural: no HTTP client exists in the package.

    Wagering is out of scope for this project (site_spec.json ->
    metadata.out_of_scope), so the backtester is built so it cannot place a bet
    even by accident: rounds come from a capture or a generator, and nothing in
    ``backtest/`` can open a connection.
    """
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split(".")[0])
        offenders = imported & FORBIDDEN_MODULES
        assert not offenders, f"{path.name} imports {sorted(offenders)}"


def test_backtest_package_defines_no_wager_submission() -> None:
    """No bet-placing surface: the package only ever *models* a wager."""
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        assert "allow_writes" not in source
        assert ".request(" not in source


def test_deep_ladder_raises_a_schedule_error_instead_of_overflowing() -> None:
    """Regression: factor**losses raised a raw OverflowError mid-session.

    A capped ladder hit it too - the cap is only consulted at or beyond the
    depth it names, so --max-rungs 5000 did not protect an 1100-rung ladder.
    """
    with pytest.raises(ScheduleError):
        Martingale(factor=2.0).stake(1.0, 1100)
    with pytest.raises(ScheduleError):
        Martingale(factor=2.0, max_rungs=5000).stake(1.0, 1100)
    with pytest.raises(ScheduleError):
        Fibonacci().stake(1.0, 2000)


def test_shallow_ladders_are_untouched_by_the_overflow_guard() -> None:
    assert Martingale(factor=2.0).stake(0.5, 0) == 0.5
    assert Martingale(factor=2.0).stake(0.5, 3) == 4.0
    assert Martingale(factor=2.0, max_rungs=3).stake(0.5, 3) is None
    assert Fibonacci().stake(1.0, 5) == 8.0


def test_strategy_rejects_a_non_positive_threshold() -> None:
    """Regression: `--threshold 0` died as a traceback from inside the engine,
    because only Bet validated it and that happens once a session is running."""
    with pytest.raises(ScheduleError, match="threshold must be positive"):
        Strategy(
            market="crash",
            base_stake=1.0,
            threshold=0.0,
            payout=1.0,
            schedule=Flat(),
            profit_target=10.0,
        )


def test_write_rounds_refuses_a_non_finite_outcome(tmp_path) -> None:
    """Regression: a NaN outcome was written as bare NaN - invalid JSON that
    every strict parser rejects."""
    with pytest.raises(ValueError, match="not JSON-serialisable"):
        write_rounds(
            tmp_path / "nan.jsonl",
            [Round(outcomes={"crash": float("nan")}, round_id="nan1")],
        )


def test_capture_reports_that_the_configured_edge_is_not_applied(tmp_path, capsys) -> None:
    """Regression: --edges-from/--edge were dropped without a word whenever a
    capture was supplied, so the report showed a number nobody asked for."""
    from backtest import cli

    capture = tmp_path / "rounds.jsonl"
    write_rounds(
        capture,
        [Round(outcomes={"crash": 1.9}, round_id=f"r{i}") for i in range(5)],
    )

    cli.main(
        ["run", "--capture", str(capture), "--edge", "0.001", "--sessions", "4", "--seed", "1"]
    )

    assert "is NOT applied" in capsys.readouterr().err
