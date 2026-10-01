from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

import automation_cli
from duckdice_provider import DuckDiceProvider


def test_duckdice_dry_run_maps_duel_shape_without_network() -> None:
    provider = DuckDiceProvider(api_key="")
    try:
        result = provider.place_dice_bet(
            "0.01", side="OVER", currency="decoy", target="5005"
        )
    finally:
        provider.close()
    assert result["path"] == "/api/dice/play"
    assert result["payload"] == {
        "symbol": "DECOY",
        "chance": "49.95",
        "isHigh": True,
        "amount": "0.01",
    }


def test_duckdice_read_uses_verified_user_info_route_and_key_query() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["key"] = request.url.params.get("api_key")
        return httpx.Response(
            200,
            json={"balances": [{"currency": "SOL", "main": "1.25"}]},
        )

    with DuckDiceProvider(
        api_key="secret-test-key",
        transport=httpx.MockTransport(handler),
        rate_interval=0,
    ) as provider:
        assert provider.balance_for("sol") == Decimal("1.25")

    assert seen == {"path": "/api/bot/user-info", "key": "secret-test-key"}


def test_cli_duckdice_dry_run_does_not_require_duel_security_token(capsys) -> None:
    rc = automation_cli.main(
        [
            "--provider",
            "duckdice",
            "dice-bet",
            "--amount",
            "0.01",
            "--side",
            "UNDER",
            "--currency",
            "DECOY",
            "--target",
            "4995",
        ]
    )
    assert rc == 0
    assert '"provider": "duckdice"' in capsys.readouterr().out

def test_duckdice_live_bet_supports_the_shared_cli_save_lifecycle() -> None:
    """dice-bet's live path calls client.save() after the wager is placed.

    DuckDice keeps no local session, but the CLI's shared lifecycle still calls
    save(); before this existed the command raised AttributeError only *after*
    DuckDice had accepted a real-money bet, inviting a duplicate retry.
    """
    provider = DuckDiceProvider(api_key="secret-test-key", rate_interval=0)
    try:
        assert provider.save() is None
    finally:
        provider.close()


def _play_response(**overrides) -> dict:
    bet = {
        "hash": "abc123",
        "number": 5000,
        "result": True,
        "betAmount": "0.01",
        "winAmount": "0.0198",
        "profit": "0.0098",
    }
    bet.update(overrides)
    return {"bet": bet, "user": {"balance": "1.25"}}


def _live_provider(payload: dict) -> DuckDiceProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return DuckDiceProvider(
        api_key="secret-test-key",
        transport=httpx.MockTransport(handler),
        betting_enabled=True,
        max_stake="1",
        rate_interval=0,
    )


def test_duckdice_live_bet_reports_authoritative_money_fields() -> None:
    with _live_provider(_play_response()) as provider:
        result = provider.place_dice_bet(
            "0.01",
            side="UNDER",
            currency="SOL",
            target="4995",
            security_token=None,
            confirm=True,
            dry_run=False,
        )

    round_data = result["data"]["round"]
    assert round_data["stake"] == "0.01"
    assert round_data["amount_won"] == "0.0198"
    assert round_data["profit"] == "0.0098"
    assert round_data["won"] is True
    assert provider.session_realized == Decimal("0.0098")
    # balance is recorded from the response, not invented locally
    assert provider.bankroll == Decimal("1.25")


def test_duckdice_live_bet_rejects_invalid_money_fields() -> None:
    for field in ("betAmount", "winAmount", "profit"):
        with _live_provider(_play_response(**{field: ""})) as provider:
            try:
                provider.place_dice_bet(
                    "0.01",
                    side="UNDER",
                    currency="SOL",
                    target="4995",
                    security_token=None,
                    confirm=True,
                    dry_run=False,
                )
            except Exception as exc:  # noqa: BLE001 - asserting a refusal
                assert "decimal" in str(exc)
            else:  # pragma: no cover - explicit failure path
                raise AssertionError(f"blank {field} was accepted")


def test_autobet_live_refuses_a_round_without_a_payout(tmp_path, monkeypatch) -> None:
    """A missing payout must fail the round, not be recorded as a loss.

    The autobet loop derives its win/loss outcome from the round payout. When
    the payout was silently defaulted to 0, an accepted wager was booked as a
    loss and the next strategy step (Paroli/Martingale) ran off a wrong outcome.
    """
    config_path = tmp_path / "strategy.json"
    config_path.write_text(
        json.dumps(
            {
                "strategy": "flat",
                "stake": "0.01",
                "max_stake": "1",
                "buffer": "0",
                "max_rounds": 1,
            }
        ),
        encoding="utf-8",
    )

    dispatched = []

    class FakeProvider:
        betting_enabled = False
        max_stake = Decimal("1")
        session_realized = Decimal(0)
        bankroll = None

        def balance_for(self, currency):
            return Decimal("1.00")

        def place_dice_bet(self, *args, **kwargs):
            dispatched.append(kwargs)
            return {"data": {"round": {"stake": "0.01", "profit": "0.0098"}}}

        def save(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        automation_cli, "_betting_client", lambda args: FakeProvider()
    )
    monkeypatch.setattr(
        automation_cli, "next_stake", lambda *args, **kwargs: Decimal("0.01"), raising=False
    )

    args = automation_cli.build_parser().parse_args(
        [
            "--yes",
            "--provider",
            "duckdice",
            "autobet",
            "--config",
            str(config_path),
            "--currency",
            "SOL",
            "--side",
            "UNDER",
            "--target",
            "4995",
            "--confirm",
        ]
    )

    monkeypatch.chdir(tmp_path)
    state_file = Path(args.profile).parent / "autobet_state_duckdice.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps({"first_run_completed": True, "providers": {"duckdice": True}}),
        encoding="utf-8",
    )
    result = automation_cli.cmd_autobet(args)

    # The loop must abort without recording a round, rather than booking the
    # wager as a loss.
    assert result == 1
    assert len(dispatched) == 1
    rows_file = tmp_path / "autobet_rounds.jsonl"
    assert not rows_file.exists() or rows_file.read_text(encoding="utf-8").strip() == ""
