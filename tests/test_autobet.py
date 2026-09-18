"""Tests for autobet strategy module and CLI command."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backtest.autobet_strategies import (
    CustomStepsStrategy,
    DAlembertStrategy,
    FibonacciStrategy,
    FlatStrategy,
    MartingaleStrategy,
    load_strategy,
)


class TestFlatStrategy:
    """Test FlatStrategy implementation."""
    
    def test_constant_stake_ignores_outcome(self) -> None:
        stake = Decimal("0.50")
        strategy = FlatStrategy(stake)
        
        assert strategy.next_stake(Decimal("0.50"), False) == stake
        assert strategy.next_stake(Decimal("0.50"), True) == stake
    
    def test_rejects_non_positive_stake(self) -> None:
        with pytest.raises(ValueError):
            FlatStrategy(Decimal(0))
        with pytest.raises(ValueError):
            FlatStrategy(Decimal("-0.01"))


class TestMartingaleStrategy:
    """Test MartingaleStrategy implementation."""
    
    def test_doubles_after_loss(self) -> None:
        strategy = MartingaleStrategy(Decimal("0.50"))
        
        assert strategy.next_stake(Decimal("0.50"), False) == Decimal("1.00")
        assert strategy.next_stake(Decimal("1.00"), False) == Decimal("2.00")
    
    def test_resets_on_win(self) -> None:
        strategy = MartingaleStrategy(Decimal("0.50"))
        strategy.next_stake(Decimal("0.50"), False)  # Lose, stake becomes 1.00
        assert strategy.next_stake(Decimal("1.00"), True) == Decimal("0.50")
    
    def test_custom_factor(self) -> None:
        strategy = MartingaleStrategy(Decimal("0.50"), Decimal("1.5"))
        assert strategy.next_stake(Decimal("0.50"), False) == Decimal("0.75")
    
    def test_rejects_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            MartingaleStrategy(Decimal(0))
        with pytest.raises(ValueError):
            MartingaleStrategy(Decimal("0.50"), Decimal("1.0"))
        with pytest.raises(ValueError):
            MartingaleStrategy(Decimal("0.50"), Decimal("0.5"))


class TestFibonacciStrategy:
    """Test FibonacciStrategy implementation."""
    
    def test_increases_after_loss(self) -> None:
        strategy = FibonacciStrategy(Decimal("0.50"))
        # First bet (simulated loss -> next stake increases)
        next_stake = strategy.next_stake(Decimal("0.50"), False)
        assert next_stake > Decimal("0.50")
    
    def test_resets_on_win(self) -> None:
        strategy = FibonacciStrategy(Decimal("0.50"))
        strategy.next_stake(Decimal("0.50"), False)  # Loss
        assert strategy.next_stake(Decimal("0.80"), True) == Decimal("0.50")
    
    def test_rejects_non_positive_stake(self) -> None:
        with pytest.raises(ValueError):
            FibonacciStrategy(Decimal(0))


class TestDAlembertStrategy:
    """Test DAlembertStrategy implementation."""
    
    def test_increases_by_unit_after_loss(self) -> None:
        strategy = DAlembertStrategy(Decimal("0.50"), Decimal("0.10"))
        assert strategy.next_stake(Decimal("0.50"), False) == Decimal("0.60")
    
    def test_decreases_by_unit_after_win(self) -> None:
        strategy = DAlembertStrategy(Decimal("0.50"), Decimal("0.10"))
        strategy.next_stake(Decimal("0.50"), False)  # Loss -> 0.60
        assert strategy.next_stake(Decimal("0.60"), True) == Decimal("0.50")
    
    def test_does_not_go_below_base(self) -> None:
        strategy = DAlembertStrategy(Decimal("0.50"), Decimal("0.10"))
        # Lose three times to get to 0.80
        strategy.next_stake(Decimal("0.50"), False)  # 0.60
        strategy.next_stake(Decimal("0.60"), False)  # 0.70
        strategy.next_stake(Decimal("0.70"), False)  # 0.80
        # Win three times back to 0.50
        strategy.next_stake(Decimal("0.80"), True)   # 0.70
        strategy.next_stake(Decimal("0.70"), True)   # 0.60
        strategy.next_stake(Decimal("0.60"), True)   # 0.50
        # Try to decrease below base
        assert strategy.next_stake(Decimal("0.50"), True) == Decimal("0.50")
    
    def test_rejects_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            DAlembertStrategy(Decimal(0), Decimal("0.10"))
        with pytest.raises(ValueError):
            DAlembertStrategy(Decimal("0.50"), Decimal(0))
        with pytest.raises(ValueError):
            DAlembertStrategy(Decimal("0.50"), Decimal("0.60"))


class TestCustomStepsStrategy:
    """Test CustomStepsStrategy implementation."""
    
    def test_follows_step_sequence(self) -> None:
        strategy = CustomStepsStrategy(Decimal("0.50"), [1.0, 2.0, 3.0])
        # First bet (loss -> advance to step 1, multiply by 2.0)
        assert strategy.next_stake(Decimal("0.50"), False) == Decimal("1.00")
        # Second bet (loss -> advance to step 2, multiply by 3.0)
        assert strategy.next_stake(Decimal("1.00"), False) == Decimal("1.50")
        # Third bet (loss -> exhausted)
        assert strategy.next_stake(Decimal("1.50"), False) is None
        strategy = CustomStepsStrategy(Decimal("0.50"), [1.0])
        strategy.next_stake(Decimal("0.50"), False)  # Advance to end
        assert strategy.next_stake(Decimal("0.50"), False) is None
    
    def test_resets_on_win(self) -> None:
        strategy = CustomStepsStrategy(Decimal("0.50"), [1.0, 2.0])
        strategy.next_stake(Decimal("0.50"), False)  # Advance to step 1
        assert strategy.next_stake(Decimal("0.50"), True) == Decimal("0.50")
    
    def test_rejects_invalid_params(self) -> None:
        with pytest.raises(ValueError):
            CustomStepsStrategy(Decimal(0), [1.0])
        with pytest.raises(ValueError):
            CustomStepsStrategy(Decimal("0.50"), [])
        with pytest.raises(ValueError):
            CustomStepsStrategy(Decimal("0.50"), [1.0, 0.0])


class TestLoadStrategy:
    """Test load_strategy factory function."""
    
    def test_loads_flat(self) -> None:
        config = {"type": "flat", "base_stake": "0.50"}
        strategy = load_strategy(config)
        assert isinstance(strategy, FlatStrategy)
        assert strategy.next_stake(Decimal("0.50"), False) == Decimal("0.50")
    
    def test_loads_martingale(self) -> None:
        config = {"type": "martingale", "base_stake": "0.50", "factor": "3"}
        strategy = load_strategy(config)
        assert isinstance(strategy, MartingaleStrategy)
        assert strategy.next_stake(Decimal("0.50"), False) == Decimal("1.50")
    
    def test_loads_fibonacci(self) -> None:
        config = {"type": "fibonacci", "base_stake": "0.50"}
        strategy = load_strategy(config)
        assert isinstance(strategy, FibonacciStrategy)
    
    def test_loads_dalembert(self) -> None:
        config = {"type": "dalembert", "base_stake": "0.50", "unit": "0.20"}
        strategy = load_strategy(config)
        assert isinstance(strategy, DAlembertStrategy)
        assert strategy.next_stake(Decimal("0.50"), False) == Decimal("0.70")
    
    def test_loads_custom_steps(self) -> None:
        config = {"type": "custom_steps", "base_stake": "0.50", "custom_steps": [1.0, 1.5, 2.0]}
        strategy = load_strategy(config)
        assert isinstance(strategy, CustomStepsStrategy)
    
    def test_rejects_unknown_type(self) -> None:
        config = {"type": "unknown", "base_stake": "0.50"}
        with pytest.raises(ValueError, match="Unknown strategy type"):
            load_strategy(config)
    
    def test_defaults_to_flat(self) -> None:
        config = {"base_stake": "0.50"}
        strategy = load_strategy(config)
        assert isinstance(strategy, FlatStrategy)




    def test_loads_from_mode_key(self) -> None:
        config = {"mode": "martingale", "base_stake": "0.50", "factor": "3"}
        strategy = load_strategy(config)
        assert isinstance(strategy, MartingaleStrategy)
        assert strategy.next_stake(Decimal("0.50"), False) == Decimal("1.50")

    def test_loads_dalembert_alias_and_default_unit(self) -> None:
        config = {"mode": "dAlembert", "base_stake": "0.00000100"}
        strategy = load_strategy(config)
        assert isinstance(strategy, DAlembertStrategy)
        assert strategy.next_stake(Decimal("0.00000100"), False) == Decimal("0.00000200")

def test_strategy_transitions():
    """Test full sequence of strategy transitions."""
    # Martingale sequence
    strategy = MartingaleStrategy(Decimal("0.50"))
    stakes = []
    
    # First bet loses
    stakes.append(strategy.next_stake(Decimal("0.50"), False))  # 1.00
    # Second bet loses
    stakes.append(strategy.next_stake(Decimal("1.00"), False))  # 2.00
    # Third bet wins
    stakes.append(strategy.next_stake(Decimal("2.00"), True))   # 0.50
    
    assert stakes == [Decimal("1.00"), Decimal("2.00"), Decimal("0.50")]


def test_strategy_respects_custom_steps():
    """Test custom steps strategy."""
    strategy = CustomStepsStrategy(Decimal("1.00"), [1.0, 2.0, 4.0])
    # Loss -> advance
    assert strategy.next_stake(Decimal("1.00"), False) == Decimal("2.00")
    # Loss -> advance
    assert strategy.next_stake(Decimal("2.00"), False) == Decimal("4.00")
    # Loss -> exhausted
    assert strategy.next_stake(Decimal("4.00"), False) is None
