"""Tests for the bankroll stake-sizing rules (``bankroll.py``).

The reference numbers are taken from a *real settled round* (round id
131290672) rather than invented, so the tests pin the arithmetic to the
measured edge the game actually charges.
"""

from __future__ import annotations

from decimal import Decimal as D

import httpx
import pytest

from automation_client import DuelClient
from bankroll import BankrollPolicy, EdgeRefused, PlayPolicy, implied_edge, kelly_fraction

# Measured live: win chance, payout multiplier and the resulting edge.
P = D("0.499950004999500050")
MULT = D("1.998199800000000000")


# ----------------------------------------------------------------- the arithmetic


def test_implied_edge_of_a_fair_game_is_exactly_zero() -> None:
    assert implied_edge(D("0.5"), D("2")) == 0


def test_implied_edge_reproduces_the_measured_house_take() -> None:
    """p x multiplier is 0.999, not 1 - that shortfall IS the 0.1% edge."""
    assert implied_edge(P, MULT).quantize(D("0.0001")) == D("-0.0010")


def test_kelly_fraction_is_negative_when_the_edge_is_negative() -> None:
    assert kelly_fraction(P, MULT) < 0


def test_kelly_fraction_of_a_fair_game_is_zero() -> None:
    assert kelly_fraction(D("0.5"), D("2")) == 0


# ------------------------------------------------------------------ stake_for


def test_stake_for_is_zero_on_a_negative_edge() -> None:
    """Rule 1: no positive stake is authorised, at any bankroll."""
    policy = BankrollPolicy()
    assert policy.stake_for(bankroll=D("11"), win_chance=P, multiplier=MULT) == 0
    assert policy.stake_for(bankroll=D("1000000"), win_chance=P, multiplier=MULT) == 0


def test_stake_for_is_bounded_by_fractional_kelly_on_a_positive_edge() -> None:
    # Hypothetical +5% edge at even odds: b=1.1, f* = 1/22, quarter-Kelly = 1/88.
    policy = BankrollPolicy(kelly_fraction=D("0.25"))
    stake = policy.stake_for(bankroll=D("100"), win_chance=D("0.5"), multiplier=D("2.1"))
    assert stake == D("1.13636363")
    assert stake < D("2")


# --------------------------------------------------------------------- check


def test_check_refuses_a_negative_edge_game() -> None:
    policy = BankrollPolicy()
    with pytest.raises(EdgeRefused, match="Kelly-optimal stake is zero"):
        policy.check(stake=D("0.00000012"), bankroll=D("11"), win_chance=P, multiplier=MULT)


def test_check_refuses_a_stake_above_the_kelly_bound() -> None:
    policy = BankrollPolicy(kelly_fraction=D("0.25"))
    with pytest.raises(EdgeRefused, match="exceeds the fractional-Kelly bound"):
        policy.check(stake=D("20"), bankroll=D("100"), win_chance=D("0.5"), multiplier=D("2.1"))


def test_check_allows_a_stake_within_the_kelly_bound() -> None:
    policy = BankrollPolicy(kelly_fraction=D("0.25"))
    policy.check(stake=D("1"), bankroll=D("100"), win_chance=D("0.5"), multiplier=D("2.1"))


def test_check_stops_at_the_session_loss_cap_before_dispatch() -> None:
    """Rule 4 is checked before the bet, not after it."""
    policy = BankrollPolicy(kelly_fraction=D("0.25"), session_loss_cap=D("0.05"))
    with pytest.raises(EdgeRefused, match="cap"):
        policy.check(
            stake=D("1"), bankroll=D("100"), win_chance=D("0.5"), multiplier=D("2.1"),
            session_realized=D("-0.05"),
        )


def test_check_allows_play_while_under_the_cap() -> None:
    policy = BankrollPolicy(kelly_fraction=D("0.25"), session_loss_cap=D("0.05"))
    policy.check(
        stake=D("1"), bankroll=D("100"), win_chance=D("0.5"), multiplier=D("2.1"),
        session_realized=D("-0.049"),
    )


def test_check_rejects_a_non_positive_stake() -> None:
    policy = BankrollPolicy()
    with pytest.raises(EdgeRefused, match="must be positive"):
        policy.check(stake=D("0"), bankroll=D("100"), win_chance=D("0.5"), multiplier=D("2.1"))


# --------------------------------------------------------- client integration

_ROUND = {
    "success": True,
    "data": {
        "round": {
            "amount_currency": "0.000000120000000000",
            "amount_won": "0",
            "win_chance": "0.499950004999500050",
            "multiplier": "1.998199800000000000",
        }
    },
}


def _client(tmp_path, seen: list, payload: dict) -> DuelClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json=payload)

    return DuelClient(
        profile=tmp_path / "session.json",
        transport=httpx.MockTransport(handler),
        allow_writes=True,
        betting_enabled=True,
        max_stake=1.0,
    )


def test_client_refuses_a_negative_edge_bet_before_dispatch(tmp_path) -> None:
    """The guard runs before the request goes out, so nothing is staked."""
    seen: list = []
    with _client(tmp_path, seen, _ROUND) as client:
        client.bankroll = D("11")
        with pytest.raises(EdgeRefused, match="Kelly-optimal stake is zero"):
            client.place_dice_bet(
                "0.00000012", side="UNDER", currency=109, target=5000,
                security_token="tok", confirm=True, dry_run=False,
                policy=BankrollPolicy(), edge=(P, MULT),
            )
    assert seen == []


def test_client_refuses_a_policy_bet_with_no_measured_edge(tmp_path) -> None:
    """Rule 2 fails closed: an assumed edge is no edge."""
    seen: list = []
    with _client(tmp_path, seen, _ROUND) as client:
        client.bankroll = D("11")
        with pytest.raises(EdgeRefused, match="no measured edge"):
            client.place_dice_bet(
                "0.00000012", side="UNDER", currency=109, target=5000,
                security_token="tok", confirm=True, dry_run=False,
                policy=BankrollPolicy(),
            )
    assert seen == []


def test_settled_round_records_edge_and_realised_pnl(tmp_path) -> None:
    """The measured edge and PnL come from the response, not from config."""
    seen: list = []
    with _client(tmp_path, seen, _ROUND) as client:
        client.place_dice_bet(
            "0.00000012", side="UNDER", currency=109, target=5000,
            security_token="tok", confirm=True, dry_run=False,
        )
        assert client.session_realized == D("-0.00000012")
        assert client.last_measured_edge == (P, MULT)
        # a second gated bet now has a measured edge to size against
        client.bankroll = D("11")
        with pytest.raises(EdgeRefused, match="Kelly-optimal stake is zero"):
            client.place_dice_bet(
                "0.00000012", side="UNDER", currency=109, target=5000,
                security_token="tok2", confirm=True, dry_run=False,
                policy=BankrollPolicy(),
            )


def test_dry_run_never_consults_the_policy_gate(tmp_path) -> None:
    """Dry runs are already zero-risk and need no bankroll configured."""
    seen: list = []
    with _client(tmp_path, seen, _ROUND) as client:
        result = client.place_dice_bet(
            "0.00000012", side="UNDER", currency=109, target=5000,
            policy=BankrollPolicy(), edge=(P, MULT),
        )
        assert result["dry_run"] is True
    assert seen == []

# ------------------------------------------------------- entertainment play mode


def test_play_policy_holds_the_stake_fixed() -> None:
    """Play mode bets the same minimum every round - it never raises on a loss."""
    policy = PlayPolicy(stake=D("0.00000012"), session_loss_cap=D("0.0005"))
    assert policy.stake_for() == D("0.00000012")


def test_play_policy_refuses_a_raised_stake() -> None:
    policy = PlayPolicy(stake=D("0.00000012"), session_loss_cap=D("0.0005"))
    with pytest.raises(EdgeRefused, match="fixed play stake"):
        policy.check(stake=D("0.00000120"), session_realized=D("-0.00000012"))


def test_play_policy_stops_once_the_budget_is_spent() -> None:
    policy = PlayPolicy(stake=D("0.00000012"), session_loss_cap=D("0.0005"))
    with pytest.raises(EdgeRefused, match="budget spent"):
        policy.check(stake=D("0.00000012"), session_realized=D("-0.0005"))


def test_play_policy_allows_play_while_under_the_cap() -> None:
    policy = PlayPolicy(stake=D("0.00000012"), session_loss_cap=D("0.0005"))
    policy.check(stake=D("0.00000012"), session_realized=D("-0.00000036"))


def test_play_policy_ignores_the_edge_by_design() -> None:
    """The honest statement: this stake is a budget line, not a probability."""
    policy = PlayPolicy(stake=D("0.00000012"), session_loss_cap=D("0.0005"))
    # called with no edge information at all, it still sizes the same way
    assert policy.stake_for() == policy.stake_for(win_chance=P, multiplier=MULT) == D("0.00000012")


def test_client_accepts_the_play_policy(tmp_path) -> None:
    """Both policies satisfy the same duck-typed contract at the call site."""
    seen: list = []
    with _client(tmp_path, seen, _ROUND) as client:
        client.place_dice_bet(
            "0.00000012", side="UNDER", currency=109, target=5000,
            security_token="tok", confirm=True, dry_run=False,
            policy=PlayPolicy(stake=D("0.00000012"), session_loss_cap=D("0.0005")),
        )
    assert seen and seen[0][1] == "/api/v2/dice/bet"
