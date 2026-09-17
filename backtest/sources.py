"""Round sources: synthetic generators and recorded JSONL captures.

Two sources, deliberately separated:

* :class:`SyntheticSource` draws crash-style rounds from the distribution
  implied by a declared house edge.  It is exact by construction, which makes it
  the right tool for verifying the engine and for exploring a strategy's shape
  across edges without trusting any capture.
* :class:`RecordedSource` replays a real JSONL capture.  Nothing is assumed
  about the underlying game; the rounds are whatever was observed.

Neither source can place a bet.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

from .model import BacktestError, Round, read_rounds


class SourceError(BacktestError):
    """A round source was configured or sized unusably."""


@dataclass
class SyntheticSource:
    """Crash-style rounds with a configurable house edge.

    For a crash game with edge ``e``, the crash point ``X`` satisfies
    ``P(X >= m) = (1 - e) / m``: cashing out at multiplier ``m`` wins with that
    probability and pays ``m``, so the edge is exactly ``1 - (1 - e) = e``.
    Sampling ``X = (1 - e) / u`` for ``u ~ Uniform(0, 1)`` reproduces it.

    ``edge=0.0`` therefore yields a genuinely fair game, which is what makes it
    a usable oracle: the engine must return an expected value of exactly zero.
    """

    edge: float = 0.0
    market: str = "crash"
    seed: int = 0

    def __post_init__(self) -> None:
        if not 0.0 <= self.edge < 1.0:
            raise SourceError(f"edge must be in [0, 1), got {self.edge!r}")

    def session(self, index: int) -> Iterator[Round]:
        rng = random.Random(f"{self.seed}:{index}")
        n = 0
        while True:
            u = rng.random()
            if u <= 0.0:  # random() is [0, 1); guard the reciprocal
                u = 2.0**-53
            n += 1
            yield Round(
                outcomes={self.market: (1.0 - self.edge) / u},
                round_id=f"synthetic:{index}:{n}",
            )

    def win_probability(self, threshold: float) -> float:
        """Exact ``P(outcome >= threshold)`` for this distribution."""
        if threshold <= 0:
            raise SourceError(f"threshold must be positive, got {threshold!r}")
        return min(1.0, (1.0 - self.edge) / threshold)

    def expected_edge(self, threshold: float, payout: float) -> float:
        """Exact house edge of a bet at ``threshold``/``payout`` on this source."""
        return 1.0 - self.win_probability(threshold) * payout

    def rounds_needed_for(self, threshold: float, confidence: float = 0.999) -> int:
        """Rounds a capture should hold for the observed rate to be meaningful."""
        p = self.win_probability(threshold)
        if p <= 0.0 or p >= 1.0:
            raise SourceError("threshold admits no variance to estimate")
        z = math.sqrt(2.0) * _erfinv(confidence)
        return int(math.ceil((z / 0.01) ** 2 * p * (1.0 - p)))


def _erfinv(y: float) -> float:
    """Inverse error function, used only to size captures."""
    if not -1.0 < y < 1.0:
        raise SourceError(f"erfinv domain is (-1, 1), got {y!r}")
    a = 8.0 * (math.pi - 3.0) / (3.0 * math.pi * (4.0 - math.pi))
    t = 1.0 - y * y
    ln = math.log(t)
    return math.copysign(
        1.0, y
    ) * math.sqrt(
        math.sqrt(
            (2.0 / (math.pi * a) + ln / 2.0) ** 2 - ln / a
        )
        - (2.0 / (math.pi * a) + ln / 2.0)
    )


@dataclass
class RecordedSource:
    """Replays a captured sequence of rounds.

    ``cycle=True`` wraps around when the capture runs out, which keeps long
    backtests supplied from a short capture.  Note the caveat: cycling makes
    separate sessions share the same underlying outcomes, so sessions are not
    independent draws.  For independent sessions use a synthetic source, or a
    capture long enough to slice without repetition.
    """

    rounds: Sequence[Round]
    cycle: bool = True

    def __post_init__(self) -> None:
        if not self.rounds:
            raise SourceError("recorded source needs at least one round")

    @classmethod
    def from_jsonl(cls, path: str | Path, cycle: bool = True) -> "RecordedSource":
        return cls(rounds=read_rounds(path), cycle=cycle)

    def session(self, index: int) -> Iterator[Round]:
        if not self.cycle:
            yield from self.rounds
            return
        while True:
            yield from self.rounds

    @property
    def markets(self) -> set[str]:
        return {market for round_ in self.rounds for market in round_.outcomes}
