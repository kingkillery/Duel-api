"""Tests for the offline demo and audit commands.

The AST check mirrors ``tests/test_backtest.py``'s network guard: the offline
tier's promise ("no network, ever") is enforced, not asserted.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import audit
import demo
import automation_cli

NETWORK_MODULES = {
    "httpx", "requests", "urllib", "urllib3", "socket", "http", "aiohttp",
    "httpcore", "websocket", "websockets",
}
NETWORK_ATTRS = {"urlopen", "request", "socket"}


def _assert_offline(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in NETWORK_MODULES, f"{path.name} imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            assert root not in NETWORK_MODULES, f"{path.name} imports from {node.module}"


def test_demo_module_is_offline_by_ast() -> None:
    _assert_offline(Path("demo.py"))


def test_audit_module_is_offline_by_ast() -> None:
    _assert_offline(Path("audit.py"))


def test_demo_deterministic_for_seed() -> None:
    a = demo.run(sessions=500, seed=7)
    b = demo.run(sessions=500, seed=7)
    assert [r["mean_net"] for r in a.rows] == [r["mean_net"] for r in b.rows]


def test_demo_zero_edge_is_a_fair_game() -> None:
    # SyntheticSource at edge=0 is the engine's oracle: EV must be ~0 and the
    # implied edge recovered from results must straddle zero.
    result = demo.run(edge=0.0, sessions=2000, seed=1)
    for row in result.rows:
        assert abs(row["implied_edge"]) < 0.02, row


def test_demo_negative_edge_verdict_is_zero_stake() -> None:
    result = demo.run(sessions=200, seed=0)
    assert result.kelly_fraction < 0
    assert "zero" in result.verdict
    assert "pooled implied edge" in demo.render(result)


def test_demo_json_via_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert automation_cli.main(["demo", "--sessions", "200", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["kelly_fraction"] == pytest.approx(-0.001)
    assert len(data["rows"]) == 3


def test_audit_reads_round_logs(tmp_path: Path) -> None:
    log = tmp_path / "live_test.jsonl"
    rows = [
        {"round": 1, "stake": "0.00000100", "won": True, "net": "0.00000100", "cumulative_loss": "0"},
        {"round": 2, "stake": "0.00000100", "won": False, "net": "-0.00000100", "cumulative_loss": "0.00000100"},
        {"round": 3, "stake": "0.00000100", "won": False, "net": "-0.00000100", "cumulative_loss": "0.00000200"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    result = audit.audit([log])
    assert result.rounds == 3
    assert result.wagered == pytest.approx(3e-6)
    assert result.net == pytest.approx(-1e-6)
    assert result.implied_edge == pytest.approx(1e-6 / 3e-6)
    rendered = audit.render(result)
    assert "gallery of coin flips" in rendered  # small-sample verdict


def test_audit_skips_unreadable_lines(tmp_path: Path) -> None:
    log = tmp_path / "broken.jsonl"
    log.write_text(
        '{"round": 1, "stake": "1", "won": true, "net": "1"}\nnot json\n',
        encoding="utf-8",
    )
    result = audit.audit([log])
    assert result.rounds == 1
    assert result.files[0].errors


def test_audit_missing_file_does_not_crash(tmp_path: Path) -> None:
    result = audit.audit([tmp_path / "nope.jsonl"])
    assert result.rounds == 0
    assert "n/a" in audit.render(result)


def test_audit_cli_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = tmp_path / "x.jsonl"
    log.write_text(
        json.dumps({"round": 1, "stake": "2", "won": True, "net": "2"}), encoding="utf-8"
    )
    assert automation_cli.main(["audit", "--json", str(log)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["rounds"] == 1
    assert data["implied_edge"] == pytest.approx(-1.0)
