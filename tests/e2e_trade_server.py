"""Test-only local server: real provider adapters, mocked HTTP, no external sockets."""
from __future__ import annotations

import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

if os.environ.get("DUEL_TRADE_MOCK") != "1":
    raise SystemExit("This fixture requires DUEL_TRADE_MOCK=1")

LOG = Path(os.environ["E2E_CALL_LOG"])


def record(value: dict) -> None:
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")


def forbid_external_sockets(event, args):
    if event == "socket.getaddrinfo":
        host = args[0]
    elif event in {"socket.connect", "socket.connect_ex"}:
        address = args[1]
        host = address[0] if isinstance(address, tuple) else None
    else:
        return
    if host not in {"127.0.0.1", "localhost", "::1", None}:
        record({"outbound_blocked": True})
        raise RuntimeError("External sockets forbidden by E2E fixture")


sys.addaudithook(forbid_external_sockets)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi import Request
import sandbox.trade as trade

TOKEN = "synthetic-browser-minted-token"
PROFILE = os.environ[trade.PROFILE_ENV]


def handler(provider: str, request: httpx.Request) -> httpx.Response:
    expected_host = "duel.com" if provider == "duel" or request.url.path.endswith("/exchange-rates") else "duckdice.io"
    assert request.url.host == expected_host
    body = json.loads(request.content) if request.content else {}
    wire = dict(body)
    if "security_token" in wire:
        wire["security_token_matches"] = wire.pop("security_token") == TOKEN
    record({"provider": provider, "path": request.url.path, "body": wire,
            "has_api_key": request.url.params.get("api_key") == "synthetic-e2e-key"})
    if request.url.path.endswith("/user"):
        return httpx.Response(200, json={"user": {"balances": [
            {"balance_type": 101, "balance_type_name": "BTC", "balance": "1.0"},
            {"balance_type": 109, "balance_type_name": "SOL", "balance": "1.0"}
        ]}})
    if request.url.path.endswith("/user-info"):
        return httpx.Response(200, json={"balances": [{"currency": "LTC", "main": "1.25"}, {"currency": "SOL", "main": "1.25"}]})
    if request.url.path == "/api/v2/metadata/exchange-rates":
        if os.environ.get("E2E_RATES") == "down":
            return httpx.Response(503, json={"error": "rates-unavailable"})
        return httpx.Response(200, json={
            "base": "EUR",
            "rates": {"USD": 1.13, "EUR": 1},
            "crypto_rates": {"109": {"rate": os.environ.get("E2E_SOL_RATE", "0.008000000000000000"), "unreliable": False, "updated_at": 1},
                             "104": {"rate": "0.011300000000000000", "unreliable": False, "updated_at": 1},
                             "101": {"rate": "0.000014125000000000", "unreliable": False, "updated_at": 1}},
        })
    if request.url.path not in {"/api/v2/dice/bet", "/api/dice/play"}:
        raise AssertionError("Unexpected provider route")
    if os.environ.get("E2E_MODE") == "error":
        return httpx.Response(500, text="sensitive-fixture-error synthetic-e2e-key")
    if os.environ.get("E2E_MODE") == "slow":
        time.sleep(1.5)
    stake = Decimal(body["amount"])
    payout = stake * 2
    if provider == "duel":
        if stake in {Decimal("0.77"), Decimal("0.78")}:
            bad = {"amount_currency": str(stake), "amount_won": "NaN" if stake == Decimal("0.77") else "0.5",
                   "number": float("nan") if stake == Decimal("0.78") else 1234}
            return httpx.Response(200, content=json.dumps({"data": {"round": bad}}))
        return httpx.Response(200, json={"data": {"round": {
            "won": True, "amount_currency": str(stake), "amount_won": str(payout),
            "number": 1234, "currency": {101: "BTC", 109: "SOL"}[body["currency"]], "security_token": TOKEN,
            "unreviewed_metadata": "must-not-reach-browser",
        }}})
    return httpx.Response(200, json={
        "bet": {"hash": "slow-result" if os.environ.get("E2E_MODE") == "slow" else "duck-result",
                "number": 1234, "result": True, "betAmount": str(stake),
                "winAmount": str(payout), "profit": str(payout - stake)},
        "user": {"balance": "1.25"},
    })


def factory(provider: str):
    return httpx.MockTransport(lambda request: handler(provider, request))


trade.install_mock_transport(factory)


@trade.app.get("/__fixture/identity")
def identity():
    return {"id": os.environ["E2E_INSTANCE"]}


@trade.app.post("/__fixture/config")
async def configure(request: Request):
    """Only this test harness exposes configuration; production has no such route."""
    data = await request.json()
    if "live" in data:
        os.environ[trade.LIVE_ENV] = "1" if data["live"] else "0"
    if "token" in data:
        os.environ[trade.TOKEN_ENV] = TOKEN if data["token"] else ""
    if "profile" in data:
        os.environ[trade.PROFILE_ENV] = PROFILE if data["profile"] else PROFILE + ".missing"
    if "cap" in data:
        os.environ[trade.MAX_STAKE_ENV] = "1" if data["cap"] else ""
    if "floor" in data:
        os.environ[trade.MIN_STAKE_ENV] = str(data["floor"])
    if "rates" in data:
        os.environ["E2E_RATES"] = "down" if data["rates"] is False else "up"
    if "mode" in data:
        os.environ["E2E_MODE"] = data["mode"]
    if "cap_value" in data:
        os.environ[trade.MAX_STAKE_ENV] = str(data["cap_value"])
    if "usd_cap" in data:
        os.environ[trade.MAX_USD_ENV] = str(data["usd_cap"])
    if "sol_rate" in data:
        os.environ["E2E_SOL_RATE"] = str(data["sol_rate"])
    if "listen_seconds" in data:
        from sandbox import bet_token
        bet_token.MAX_LISTEN_SECONDS = float(data["listen_seconds"])
    if data.get("reset") is True:
        trade.reset_bet_guard()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(trade.app, host="127.0.0.1", port=int(sys.argv[1]),
                log_level="warning", access_log=False)
