"""Tests for the ParoliStrategy (anti-Martingale ride-the-gain)."""
from decimal import Decimal

import pytest

from backtest.autobet_strategies import ParoliStrategy, load_strategy

U = Decimal("0.00000100")


def seq(strategy, outcomes, start):
    """Drive next_stake through an outcome sequence from a starting stake."""
    stake = start
    stakes = []
    for won in outcomes:
        stakes.append(stake)
        stake = strategy.next_stake(stake, won)
    return stakes


class TestParoliProgression:
    def test_presses_on_wins_up_to_bank_after(self):
        s = ParoliStrategy(U)
        stakes = seq(s, [True, True, True, True], U)
        assert stakes == [U, U * 2, U * 4, U]  # 1,2,4 then banked -> reset

    def test_resets_on_any_loss(self):
        s = ParoliStrategy(U)
        stakes = seq(s, [True, True, False, True], U)
        assert stakes == [U, U * 2, U * 4, U]

    def test_pressed_stake_sequence_explicit(self):
        # Fresh strategy: feed each win manually and observe next stakes.
        s = ParoliStrategy(U)
        assert s.next_stake(U, True) == U * 2       # streak 1 -> press
        assert s.next_stake(U * 2, True) == U * 4   # streak 2 -> press
        assert s.next_stake(U * 4, True) == U       # streak 3 -> bank
    def test_two_win_streak_then_bank_then_new_streak(self):
        s = ParoliStrategy(U)
        stakes = seq(s, [True, True, True, True, True], U)
        assert stakes == [U, U * 2, U * 4, U, U * 2]

    def test_loss_at_step_three_costs_only_base_net(self):
        # +1u +2u -4u = -1u: pressed stakes are funded by prior wins.
        # Wins at 1u and 2u pay ~2x each; the loss at 4u forfeits the stake.
        # Net for the failed progression: +1u +2u -4u = -1u (base only).
        win1 = U * Decimal("1.9982") - U
        win2 = U * 2 * Decimal("1.9982") - U * 2
        loss3 = -U * 4
        assert win1 + win2 + loss3 == pytest.approx(-U, abs=Decimal("0.0000001"))

    def test_max_stake_caps_progression(self):
        s = ParoliStrategy(U, bank_after=10, max_stake=U * 4)
        stakes = seq(s, [True, True, True, True], U)
        assert stakes == [U, U * 2, U * 4, U * 4]


class TestParoliValidation:
    def test_rejects_nonpositive_base(self):
        with pytest.raises(ValueError):
            ParoliStrategy(Decimal(0))

    def test_rejects_factor_below_one(self):
        with pytest.raises(ValueError):
            ParoliStrategy(U, factor=Decimal(1))

    def test_rejects_bank_after_below_one(self):
        with pytest.raises(ValueError):
            ParoliStrategy(U, bank_after=0)


class TestParoliFactory:
    def test_loads_from_type(self):
        s = load_strategy({"type": "paroli", "base_stake": "0.00000100"})
        assert isinstance(s, ParoliStrategy)

    def test_loads_from_mode_with_params(self):
        s = load_strategy({
            "mode": "paroli", "base_stake": "0.00000100",
            "factor": 2, "bank_after": 3, "max_stake": "0.00000400",
        })
        stakes = seq(s, [True, True, True], U)
        assert stakes == [U, U * 2, U * 4]

    def test_defaults_match_classic_paroli(self):
        s = load_strategy({"type": "paroli", "base_stake": "0.00000100"})
        assert s._bank_after == 3
        assert s._factor == Decimal(2)
