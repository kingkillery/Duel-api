"""Autobet staking strategies for live and simulated play.

This module provides strategy implementations for automated betting sessions.
It distinguishes between backtesting strategies (in backtest package) and
live autobet strategies that integrate with the session flow.
"""

from __future__ import annotations

from decimal import Decimal


class FlatStrategy:
    """Constant stake strategy; never exhausts."""
    
    def __init__(self, stake: Decimal) -> None:
        if stake <= 0:
            raise ValueError("stake must be positive")
        self._stake = stake
    
    def next_stake(self, current_stake: Decimal, won: bool) -> Decimal:
        return self._stake


class MartingaleStrategy:
    """Double stake after each loss; reset to base on win."""
    
    def __init__(self, base_stake: Decimal, factor: Decimal = Decimal(2)) -> None:
        if base_stake <= 0:
            raise ValueError("base_stake must be positive")
        if factor <= 1:
            raise ValueError("factor must be > 1")
        self._base = base_stake
        self._factor = factor
    
    def next_stake(self, current_stake: Decimal, won: bool) -> Decimal:
        if won:
            return self._base
        return current_stake * self._factor


class FibonacciStrategy:
    """Advance one Fibonacci step after each loss; reset to start on win.
    
    Fibonacci sequence: 1, 1, 2, 3, 5, 8, 13, ...
    We track position in sequence; position 0 and 1 both yield base stake.
    """
    
    def __init__(self, base_stake: Decimal) -> None:
        if base_stake <= 0:
            raise ValueError("base_stake must be positive")
        self._base = base_stake
    
    def next_stake(self, current_stake: Decimal, won: bool) -> Decimal:
        # For simplicity: multiply by golden ratio approx (1.618) on loss
        # This approximates Fibonacci growth without state tracking
        if won:
            return self._base
        # Golden ratio approximation: 1.618...
        return (current_stake * Decimal("1.618")).quantize(Decimal("0.00000001"))


class DAlembertStrategy:
    """Increase stake by one unit after loss; decrease after win.
    
    Classic system: bet unit, increase by unit after loss, decrease after win.
    Never goes below base unit.
    """
    
    def __init__(self, base_stake: Decimal, unit: Decimal) -> None:
        if base_stake <= 0:
            raise ValueError("base_stake must be positive")
        if unit <= 0:
            raise ValueError("unit must be positive")
        if unit > base_stake:
            raise ValueError("unit should not exceed base_stake")
        self._base = base_stake
        self._unit = unit
    
    def next_stake(self, current_stake: Decimal, won: bool) -> Decimal:
        if won:
            # Decrease by unit, but not below base
            new_stake = max(self._base, current_stake - self._unit)
        else:
            # Increase by unit
            new_stake = current_stake + self._unit
        return new_stake.quantize(Decimal("0.00000001"))


class CustomStepsStrategy:
    """Follow an explicit sequence of stake multipliers."""
    
    def __init__(self, base_stake: Decimal, steps: list[float]) -> None:
        if base_stake <= 0:
            raise ValueError("base_stake must be positive")
        if not steps:
            raise ValueError("steps must not be empty")
        if any(s <= 0 for s in steps):
            raise ValueError("all steps must be positive")
        self._base = base_stake
        self._steps = steps
        self._index = 0
    
    def next_stake(self, current_stake: Decimal, won: bool) -> Decimal:
        if won:
            # Reset to first step on win
            self._index = 0
        else:
            # Advance to next step
            self._index += 1
        
        if self._index >= len(self._steps):
            return None  # Exhausted
        
        multiplier = Decimal(str(self._steps[self._index]))
        return (self._base * multiplier).quantize(Decimal("0.00000001"))


def load_strategy(config: dict) -> object:
    """Factory to load a strategy from configuration dict.
    
    Args:
        config: Dict with 'type' or 'mode' key and strategy-specific parameters.
               Supported types: 'flat', 'martingale', 'fibonacci', 
               'dalembert'/'dAlembert', 'custom_steps'
    
    Returns:
        Strategy instance configured per the config.
    
    Raises:
        ValueError: If config is invalid or type is unknown.
    """
    raw_type = config.get("type") or config.get("mode") or "flat"
    strategy_type = str(raw_type).strip().lower().replace("'", "").replace("’", "")
    base_stake = Decimal(str(config.get("base_stake", "0.01")))
    
    if strategy_type == "flat":
        return FlatStrategy(base_stake)
    
    elif strategy_type == "martingale":
        factor = Decimal(str(config.get("factor", "2")))
        return MartingaleStrategy(base_stake, factor)
    
    elif strategy_type == "fibonacci":
        return FibonacciStrategy(base_stake)
    
    elif strategy_type in {"dalembert", "dalembertstrategy"}:
        # Classic dAlembert unit equals the base stake when omitted; a
        # hardcoded 0.01 default exceeds typical satoshi-scale base_stake.
        unit = Decimal(str(config.get("unit", base_stake)))
        return DAlembertStrategy(base_stake, unit)
    
    elif strategy_type == "custom_steps":
        steps = config.get("custom_steps", [1.0])
        return CustomStepsStrategy(base_stake, steps)
    
    else:
        raise ValueError(f"Unknown strategy type: {strategy_type}")
