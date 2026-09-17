"""Published house edges for Duel games, as a lookup the backtester can trust.

Every ``SyntheticSource --edge`` value until now was a guess. This module turns
the two edges the site actually publishes into one table:

* the rakeback document (``GET /api/v2/rakeback``) carries
  ``house_edge_percentage`` per game - the house's own number for its rakeback
  math;
* the games catalogue (``GET /api/v2/games``) carries ``rtp`` (return to
  player, percent) on most slots/instant games, so the edge is ``100 - rtp``.

Both loaders return plain dicts keyed by lowercased game name; :class:`EdgeTable`
wraps the merged result and *refuses to guess*: an unknown game raises
``KeyError`` rather than quietly reading as edge 0 (a manufactured fair game).

No network here. The fetch happens elsewhere (CLI, notebook) and the already-
decoded payloads are passed in - this package stays unable to open a socket.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .model import BacktestError


@dataclass(frozen=True)
class GameEdge:
    """One game's published edge, and where the number came from."""

    game: str  # lowercased rakeback key, e.g. "crash"
    house_edge: float  # fraction, e.g. 0.001 for 0.1%
    source: str  # "rakeback" | "rakeback:disabled" | "rtp"


def from_rakeback(payload: dict) -> dict[str, GameEdge]:
    """Build edges from the rakeback document's ``data.games`` map.

    ``house_edge_percentage`` is a percent, so it is divided by 100. Games with
    ``enabled: false`` are kept but flagged ``source="rakeback:disabled"`` so
    the caller can filter them out rather than have them silently dropped - the
    published edge is still the house's number for that game. A game that
    publishes no ``house_edge_percentage`` yields no entry: inventing one would
    manufacture an edge the site never stated.
    """
    games = (payload.get("data") or {}).get("games") or {}
    edges: dict[str, GameEdge] = {}
    for key, info in games.items():
        info = info or {}
        pct = info.get("house_edge_percentage")
        if pct is None:
            continue
        enabled = info.get("enabled", True)
        edges[key.lower()] = GameEdge(
            game=key.lower(),
            house_edge=float(pct) / 100.0,
            source="rakeback" if enabled else "rakeback:disabled",
        )
    return edges


def from_rtp(games: list[dict]) -> dict[str, GameEdge]:
    """Build edges from games-catalogue entries carrying ``name`` and ``rtp``.

    ``rtp`` is return-to-player in percent, so the house edge is ``100 - rtp``.
    A ``null`` (or missing) ``rtp`` yields **no entry** - never coerce it to
    edge 0, which would manufacture a fair game that does not exist.
    """
    edges: dict[str, GameEdge] = {}
    for info in games:
        info = info or {}
        rtp = info.get("rtp")
        name = info.get("name")
        if rtp is None or not name:
            continue
        key = str(name).lower()
        edges[key] = GameEdge(
            game=key,
            house_edge=(100.0 - float(rtp)) / 100.0,
            source="rtp",
        )
    return edges


class EdgeTable:
    """Merged edge lookup. Unknown games raise rather than defaulting to 0."""

    def __init__(self, edges: dict[str, GameEdge] | None = None) -> None:
        self._edges: dict[str, GameEdge] = dict(edges or {})

    def add(self, edges: dict[str, GameEdge]) -> "EdgeTable":
        """Merge another loader's output in (later sources win on collision)."""
        self._edges.update(edges)
        return self

    def lookup(self, game: str) -> GameEdge:
        key = game.lower()
        if key not in self._edges:
            raise KeyError(game)
        return self._edges[key]

    def __contains__(self, game: object) -> bool:
        return isinstance(game, str) and game.lower() in self._edges

    def __len__(self) -> int:
        return len(self._edges)

    @property
    def games(self) -> list[str]:
        """Sorted lowercased game keys, for diagnostics."""
        return sorted(self._edges)


def table_from_json(path: str | Path) -> EdgeTable:
    """Load an :class:`EdgeTable` from a JSON dump of either live format.

    Accepts a rakeback payload (``{"data": {"games": ...}}``, i.e. the output
    of ``automation_cli.py call GET /api/v2/rakeback``) or a games catalogue
    (a list of ``{"name": ..., "rtp": ...}`` entries). Reading a file the user
    already fetched keeps this module network-free.
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return EdgeTable(from_rakeback(payload))
    if isinstance(payload, list):
        return EdgeTable(from_rtp(payload))
    raise BacktestError(
        f"{path}: expected a rakeback payload (object) or a games catalogue (list)"
    )
