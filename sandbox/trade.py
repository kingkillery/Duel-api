"""Localhost-only manual dice desk.

This is a separate process from ``sandbox.app``. The hosted backtester stays
offline; this module is the only sandbox entrypoint that may construct
``DuelClient`` or ``DuckDiceProvider``, and only when a human starts it with
``DUEL_TRADE_LIVE=1``.

A browser cannot enable live mode or supply provider credentials. Duel tokens
are read from the server environment. Nothing here auto-bets, retries, or
invents a roll. Run one localhost process; never expose this app through a proxy.
"""

from __future__ import annotations

import os
import threading
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool

from automation_client import (
    DEFAULT_PROFILE,
    DuelClient,
    DuelError,
    Session,
)
from duckdice_provider import API_KEY_ENV, DuckDiceError, DuckDiceProvider

LIVE_ENV = "DUEL_TRADE_LIVE"
PROFILE_ENV = "DUEL_TRADE_PROFILE"
MAX_STAKE_ENV = "DUEL_TRADE_MAX_STAKE"
MOCK_ENV = "DUEL_TRADE_MOCK"

STATIC = Path(__file__).resolve().parent / "static"
_SECRET_KEYS = frozenset(
    {
        "security_token",
        "token",
        "api_key",
        "password",
        "cookie",
        "cookies",
        "authorization",
        "secret",
        "session",
    }
)


class TradeError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class StatusRequest(BaseModel):
    provider: str
    currency: str = ""


class BetRequest(BaseModel):
    provider: str
    currency: str
    amount: str
    target: str
    side: str
    confirm: StrictBool = False
    security_token: str | None = None
    client_nonce: str = Field(min_length=8, max_length=80)


def live_enabled() -> bool:
    return os.environ.get(LIVE_ENV, "").strip() == "1"


TOKEN_ENV = "DUEL_TRADE_SECURITY_TOKEN"


def profile_path() -> Path:
    raw = os.environ.get(PROFILE_ENV, "").strip()
    return Path(raw) if raw else Path(DEFAULT_PROFILE)


def max_stake() -> Decimal:
    raw = os.environ.get(MAX_STAKE_ENV, "").strip()
    if not raw:
        raise TradeError(
            403,
            "config",
            f"refusing to bet: set {MAX_STAKE_ENV} to an explicit positive decimal before live mode",
        )
    try:
        stake = Decimal(raw)
    except InvalidOperation as exc:
        raise TradeError(500, "config", "server max stake is not a decimal") from exc
    if not stake.is_finite() or stake <= 0:
        raise TradeError(500, "config", "server max stake must be positive and finite")
    return stake


def server_token() -> str:
    return os.environ.get(TOKEN_ENV, "").strip()


def _authority(value: str) -> tuple[str, int]:
    try:
        parsed = urlsplit("//" + value)
        if (not value or any(c.isspace() for c in value)
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}):
            raise ValueError("invalid authority")
        return parsed.hostname, parsed.port or 80
    except ValueError as exc:
        raise TradeError(403, "host", "only a valid localhost authority is allowed") from exc


def assert_localhost(request: Request) -> None:
    authority = _authority(request.headers.get("host", ""))
    peer = request.client.host if request.client else ""
    if peer not in {"127.0.0.1", "::1"}:
        raise TradeError(403, "host", "only loopback clients are allowed")
    if request.method == "POST":
        origin = request.headers.get("origin", "")
        try:
            parsed = urlsplit(origin)
            if (parsed.scheme != "http" or parsed.path or parsed.query or parsed.fragment
                    or _authority(parsed.netloc) != authority):
                raise ValueError("cross origin")
        except (ValueError, TradeError) as exc:
            raise TradeError(403, "origin", "a matching localhost Origin is required") from exc
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            raise TradeError(415, "content-type", "application/json is required")
    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site and fetch_site not in {"same-origin", "none"}:
        raise TradeError(403, "origin", "cross-site requests are refused")


def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _scrub(item) for key, item in value.items()
                if str(key).lower() not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return str(value) if isinstance(value, Decimal) else value


def _provider_name(value: str) -> str:
    name = value.strip().lower()
    if name not in {"duel", "duckdice"}:
        raise TradeError(400, "provider", "provider must be duel or duckdice")
    return name


def _duckdice_key_configured() -> bool:
    """Whether the existing resolver can see a key. The value is never returned."""
    probe = DuckDiceProvider(api_key=None, betting_enabled=False)
    try:
        return bool(probe._api_key)
    finally:
        probe.close()


def _duel_profile_state() -> dict[str, Any]:
    path = profile_path()
    if not path.is_file():
        return {
            "profile_present": False,
            "profile_label": "missing",
            "username": None,
            "stale": None,
            "advice": "no captured Duel profile at the server-configured path",
        }
    try:
        session = Session.load(path)
    except (OSError, ValueError) as exc:
        return {
            "profile_present": False,
            "profile_label": "unreadable",
            "username": None,
            "stale": None,
            "advice": f"captured profile could not be read: {type(exc).__name__}",
        }
    with DuelClient(session, profile=path, betting_enabled=False) as client:
        status = client.session_status()
    return {
        "profile_present": bool(status.get("has_duel_cookie")),
        "profile_label": "present" if status.get("has_duel_cookie") else "no-session-cookie",
        "username": status.get("username"),
        "stale": status.get("stale"),
        "advice": status.get("advice"),
    }


def describe_status(provider: str, currency: str) -> dict[str, Any]:
    name = _provider_name(provider)
    ceiling = os.environ.get(MAX_STAKE_ENV, "").strip() or "unset"
    body: dict[str, Any] = {
        "provider": name,
        "live_enabled": live_enabled(),
        "blocked": _blocked,
        "max_stake": ceiling,
        "currency": currency.strip().upper(),
        "balance": None,
        "balance_state": "not-requested",
    }
    if name == "duel":
        body.update(_duel_profile_state())
        body["credential"] = "server environment token, not accepted from the browser"
        body["token_state"] = "configured" if server_token() else "missing"
        body["key_state"] = "not-applicable"
    else:
        configured = _duckdice_key_configured()
        body.update(
            {
                "profile_present": None,
                "profile_label": "not-applicable",
                "username": None,
                "stale": None,
                "advice": (
                    f"{API_KEY_ENV} is visible to the existing resolver"
                    if configured
                    else f"{API_KEY_ENV} is not configured on the server"
                ),
                "credential": API_KEY_ENV,
                "key_state": "configured" if configured else "missing",
            }
        )
    if not live_enabled():
        body["balance_state"] = "live-disabled"
        body["advice"] = (
            f"live mode is off. Start this process with {LIVE_ENV}=1 to allow "
            "one confirmed wager at a time. No balance was requested."
        )
        return body
    symbol = body["currency"]
    if not symbol:
        body["balance_state"] = "currency-required"
        return body
    try:
        if name == "duel":
            if not body["profile_present"]:
                body["balance_state"] = "credentials-missing"
                return body
            with _open_client("duel", betting=False) as client:
                body["balance"] = str(client.balance_for(symbol))
        else:
            if body["key_state"] != "configured":
                body["balance_state"] = "credentials-missing"
                return body
            with _open_client("duckdice", betting=False) as provider_client:
                body["balance"] = str(provider_client.balance_for(symbol))
        body["balance_state"] = "ok"
    except (DuelError, DuckDiceError, ValueError, OSError) as exc:
        body["balance"] = None
        body["balance_state"] = "provider-error"
        body["advice"] = type(exc).__name__
    return body


def _require_live_bet(req: BetRequest) -> str:
    provider = _provider_name(req.provider)
    if not live_enabled():
        raise TradeError(
            403,
            "live-disabled",
            f"refusing to bet: set {LIVE_ENV}=1 on the server process before starting it",
        )
    if req.confirm is not True:
        raise TradeError(403, "confirm", "refusing to bet: confirm must be exactly true")
    try:
        stake = Decimal(req.amount.strip())
    except InvalidOperation as exc:
        raise TradeError(400, "invalid", "amount") from exc
    ceiling = max_stake()
    if not stake.is_finite() or stake <= 0 or stake > ceiling:
        raise TradeError(400, "invalid", "amount")
    if provider == "duel":
        if (req.security_token or "").strip():
            raise TradeError(
                400,
                "token",
                "refusing to bet: the Duel token is read from the server environment, not the request",
            )
        if not server_token():
            raise TradeError(
                403,
                "token",
                f"refusing to bet: set {TOKEN_ENV} on the server from a browser-minted token",
            )
        if not profile_path().is_file():
            raise TradeError(
                403,
                "credentials-missing",
                "refusing to bet: no captured Duel profile is configured on the server",
            )
    return provider
def _open_client(provider: str, *, betting: bool = True) -> Any:
    transport = None
    if os.environ.get(MOCK_ENV, "").strip() == "1":
        if _installed_mock is None:
            raise TradeError(500, "mock", "mock mode was requested but no test transport is installed")
        transport = _installed_mock(provider)
    if provider == "duel":
        return DuelClient.from_profile(
            profile_path(),
            betting_enabled=betting,
            allow_writes=False,
            max_stake=max_stake() if betting else Decimal(0),
            max_429_retries=0,
            auto_refresh=False,
            follow_redirects=False,
            transport=transport,
        )
    return DuckDiceProvider(
        betting_enabled=betting,
        max_stake=max_stake() if betting else Decimal(0),
        transport=transport,
    )


def place(req: BetRequest) -> dict[str, Any]:
    provider = _require_live_bet(req)
    client = _open_client(provider)
    try:
        result = client.place_dice_bet(
            req.amount.strip(),
            side=req.side.strip().upper(),
            currency=req.currency.strip(),
            target=req.target.strip(),
            security_token=server_token() or None,
            confirm=True,
            dry_run=False,
        )
        try:
            client.save()
        except Exception as exc:
            raise TradeError(502, "ambiguous", "session save failed after dispatch; restart to reconcile") from exc
    except (DuckDiceError, DuelError) as exc:
        raise TradeError(502, "ambiguous", type(exc).__name__) from exc
    except Exception as exc:
        raise TradeError(502, "ambiguous", type(exc).__name__) from exc
    finally:
        client.close()
    if not isinstance(result, dict):
        raise TradeError(502, "ambiguous", "provider returned a non-object result; not retrying")
    round_data = result.get("data", {}).get("round")
    if not isinstance(round_data, dict):
        raise TradeError(502, "ambiguous", "provider returned no round; reconcile before restarting")
    try:
        paid = Decimal(str(round_data["amount_won"]))
        staked = Decimal(str(round_data["amount_currency"]))
        if (not paid.is_finite() or paid < 0 or not staked.is_finite()
                or staked <= 0 or staked != Decimal(req.amount)):
            raise ValueError("invalid settled amounts")
        for key in ("stake", "profit", "balance", "multiplier", "win_chance"):
            if key in round_data and not Decimal(str(round_data[key])).is_finite():
                raise ValueError("invalid optional settlement field")
        for value in round_data.values():
            if isinstance(value, float) and not Decimal(str(value)).is_finite():
                raise ValueError("non-finite result")
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise TradeError(502, "ambiguous", "invalid settlement fields; reconcile before restarting") from exc
    fields = {"hash", "number", "won", "amount_currency", "stake", "amount_won",
              "profit", "balance", "currency", "multiplier", "win_chance", "nonce"}
    public_round = {key: value for key, value in round_data.items()
                    if key in fields and isinstance(value, (str, int, float, bool, type(None)))}
    return {"provider": provider, "data": {"round": public_round}}


_lock = threading.Lock()
_inflight = False
_blocked = False
_seen_nonces: set[str] = set()
_installed_mock: Callable[[str], Any] | None = None


def install_mock_transport(factory: Callable[[str], Any] | None) -> None:
    """Test-only hook. Production never calls this, and mock mode is opt-in."""
    global _installed_mock
    _installed_mock = factory


def reset_bet_guard() -> None:
    global _inflight, _blocked
    with _lock:
        _inflight = False
        _blocked = False
        _seen_nonces.clear()


def _error(exc: TradeError) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "code": exc.code, "error": exc.message},
        status_code=exc.status,
    )


app = FastAPI(
    title="duel-api local trade desk",
    description="Localhost manual wager desk. Live mode is a server environment flag, not a page control.",
    version="0.1.0",
)


@app.middleware("http")
async def localhost_only(request: Request, call_next):
    try:
        assert_localhost(request)
    except TradeError as exc:
        return _error(exc)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'"
    return response


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "trade.html")


@app.post("/api/status")
def status(req: StatusRequest) -> JSONResponse:
    try:
        return JSONResponse({"ok": True, "status": _scrub(describe_status(req.provider, req.currency))})
    except TradeError as exc:
        return _error(exc)


@app.post("/api/bet")
def bet(req: BetRequest) -> JSONResponse:
    global _inflight, _blocked
    with _lock:
        if _blocked:
            return _error(TradeError(409, "blocked", "desk is blocked; reconcile the wager at the provider before restarting"))
        if _inflight:
            return _error(TradeError(409, "inflight", "a wager is already in flight; not sending another"))
        if req.client_nonce in _seen_nonces:
            return _error(TradeError(409, "duplicate", "this wager nonce was already submitted; not resending"))
        _inflight = True
        _seen_nonces.add(req.client_nonce)
    try:
        result = place(req)
    except TradeError as exc:
        if exc.code == "ambiguous":
            with _lock:
                _blocked = True
            return _error(exc)
        with _lock:
            _seen_nonces.discard(req.client_nonce)
        return _error(exc)
    except Exception:
        with _lock:
            _blocked = True
        return _error(TradeError(502, "ambiguous", "unexpected failure"))
    finally:
        with _lock:
            _inflight = False
    return JSONResponse({"ok": True, "result": result})


def main() -> None:
    import uvicorn

    uvicorn.run(
        "sandbox.trade:app",
        host="127.0.0.1",
        port=8765,
        reload=False,
    )


if __name__ == "__main__":
    main()
