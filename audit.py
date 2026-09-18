"""``duel-api audit`` - your own numbers, honestly computed.

Reads the round logs the tool itself writes (``live_*.jsonl``, one JSON object
per round) and reports what actually happened: total wagered, net, win rate,
and the implied edge recovered from your own results via

    edge = -net / wagered

The same identity the backtester verifies on simulated data, applied to the
only data that ever mattered - yours. Offline: it reads local files and makes
no request.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import theme

__all__ = ["FileAudit", "AuditResult", "audit", "render"]


@dataclass
class FileAudit:
    path: Path
    rounds: int = 0
    wagered: float = 0.0
    net: float = 0.0
    wins: int = 0
    worst_round: float = 0.0
    best_round: float = 0.0
    max_cumulative_loss: float = 0.0
    errors: list[str] = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        return self.wins / self.rounds if self.rounds else 0.0

    @property
    def implied_edge(self) -> float | None:
        if self.wagered <= 0:
            return None
        return -self.net / self.wagered

    def to_dict(self) -> dict:
        return {
            "file": str(self.path),
            "rounds": self.rounds,
            "wagered": round(self.wagered, 12),
            "net": round(self.net, 12),
            "wins": self.wins,
            "win_rate": round(self.win_rate, 6),
            "implied_edge": None if self.implied_edge is None else round(self.implied_edge, 8),
            "worst_round": round(self.worst_round, 12),
            "best_round": round(self.best_round, 12),
            "max_cumulative_loss": round(self.max_cumulative_loss, 12),
            "errors": self.errors,
        }


@dataclass(frozen=True)
class AuditResult:
    files: list[FileAudit]
    wagered: float
    net: float
    rounds: int

    @property
    def implied_edge(self) -> float | None:
        if self.wagered <= 0:
            return None
        return -self.net / self.wagered


def _audit_file(path: Path) -> FileAudit:
    fa = FileAudit(path=path)
    if not path.is_file():
        fa.errors.append("not found")
        return fa
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            stake = float(row["stake"])
            net = float(row["net"])
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            fa.errors.append(f"line {lineno}: {exc}")
            continue
        fa.rounds += 1
        fa.wagered += stake
        fa.net += net
        if bool(row.get("won")):
            fa.wins += 1
        fa.worst_round = min(fa.worst_round, net)
        fa.best_round = max(fa.best_round, net)
        try:
            fa.max_cumulative_loss = max(fa.max_cumulative_loss, float(row.get("cumulative_loss", 0.0)))
        except (TypeError, ValueError):
            pass
    return fa


def audit(paths: list[Path]) -> AuditResult:
    files = [_audit_file(Path(p)) for p in paths]
    wagered = sum(f.wagered for f in files)
    net = sum(f.net for f in files)
    rounds = sum(f.rounds for f in files)
    return AuditResult(files=files, wagered=wagered, net=net, rounds=rounds)


def render(result: AuditResult) -> str:
    out: list[str] = [
        theme.bold("duel-api audit"),
        theme.dim(theme.rule(72)),
        "",
        theme.bold("your own numbers"),
    ]
    rows = []
    for f in result.files:
        edge = "n/a" if f.implied_edge is None else f"{f.implied_edge * 100:+.4f}%"
        note = f"  ({len(f.errors)} unreadable)" if f.errors else ""
        rows.append(
            [
                f.path.name + note,
                str(f.rounds),
                f"{f.win_rate:.3f}" if f.rounds else "-",
                f"{f.wagered:.8f}",
                f"{f.net:+.8f}",
                edge,
            ]
        )
    out.append(
        theme.table(
            ["file", "rounds", "win rate", "wagered", "net", "implied edge"],
            rows,
            aligns=("left", "right", "right", "right", "right", "right"),
        )
    )
    out.append("")
    out.append(theme.bold("combined"))
    edge = result.implied_edge
    if edge is None:
        out.append(theme.kv("implied edge", "n/a - nothing wagered"))
    else:
        sign = theme.red("you are paying") if edge > 0 else theme.green("you are being paid")
        out.append(theme.kv("implied edge", f"{edge * 100:+.4f}%  ({sign} {abs(edge) * 100:.4f}% of every unit wagered)"))
        out.append(theme.kv("kelly f*", f"{-edge:+.6f}"))
        if result.rounds < 1000:
            verdict = (
                f"{result.rounds} rounds is a gallery of coin flips, not an edge - "
                "no sample this small should change what you do"
            )
        elif edge > 0:
            verdict = "your own results price this game above zero cost - the growth-optimal stake is zero"
        else:
            verdict = "your sample prices the game non-negative; that does not survive the long run, but nice run"
        out.append(theme.kv("verdict", theme.cyan(verdict)))
    out.append("")
    out.append(theme.dim("  identity: edge = -net / wagered - the same one the backtester verifies."))
    return "\n".join(out)
