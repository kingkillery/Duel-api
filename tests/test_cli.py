"""CLI-surface regressions: argument validation that used to pass silently.

The CLI had no test module of its own; these live here rather than in the client
tests because they exercise argument handling, not transport.
"""
from __future__ import annotations

import argparse

import pytest

import automation_cli


def test_non_negative_accepts_zero_and_positive_values() -> None:
    assert automation_cli._non_negative("0") == 0
    assert automation_cli._non_negative("10") == 10


@pytest.mark.parametrize("value", ["-5", "-1", "abc", "1.5", ""])
def test_non_negative_rejects_anything_else(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        automation_cli._non_negative(value)


def test_games_limit_rejects_a_negative_count_at_parse_time() -> None:
    """Regression: `--limit -5` sliced items[:-5] and silently returned a
    smaller, arbitrary subset instead of refusing the argument."""
    parser = automation_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["games", "--limit", "-5"])
    assert parser.parse_args(["games", "--limit", "5"]).limit == 5


def test_games_start_rejects_a_negative_offset() -> None:
    """`--start` was passed straight through to the API, so a negative offset
    was even less defensible than a negative limit."""
    parser = automation_cli.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["games", "--start", "-1"])
    assert parser.parse_args(["games", "--start", "0"]).start == 0

def test_dice_bet_parses_security_token_and_dry_run_flags() -> None:
    parser = automation_cli.build_parser()
    args = parser.parse_args(
        ["dice-bet", "--amount", "0.5", "--side", "OVER", "--currency", "USDT",
         "--target", "5005", "--security-token", "tok", "--dry-run"]
    )
    assert args.security_token == "tok"
    assert args.dry_run is True


def test_dice_bet_security_token_defaults_to_none() -> None:
    parser = automation_cli.build_parser()
    args = parser.parse_args(
        ["dice-bet", "--amount", "0.5", "--side", "OVER", "--currency", "USDT",
         "--target", "5005"]
    )
    assert args.security_token is None


def test_dice_bet_live_without_token_raises_argument_error() -> None:
    """--live with no --security-token is refused before any client exists."""
    args = automation_cli.build_parser().parse_args(
        ["--yes", "dice-bet", "--amount", "0.5", "--side", "OVER", "--currency",
         "USDT", "--target", "5005", "--enable-betting", "--confirm-bet", "--live"]
    )
    with pytest.raises(argparse.ArgumentError):
        automation_cli.cmd_dice_bet(args)


def test_live_dice_bet_without_token_reports_a_friendly_error(capsys) -> None:
    """main() turns the gate into a plain message and exit code 2, no traceback."""
    rc = automation_cli.main(
        ["--yes", "dice-bet", "--amount", "0.5", "--side", "OVER", "--currency",
         "USDT", "--target", "5005", "--enable-betting", "--confirm-bet", "--live"]
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "Live dice bet requires --security-token" in err