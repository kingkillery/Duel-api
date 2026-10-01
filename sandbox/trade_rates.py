"""USD 0.01 stake floor from a verified public rate.

``quote_minimum`` converts USD 0.01 into the selected coin and rounds that
amount UP to the stake quantum the dice form actually accepts. It never
lowers a stake to fit a server maximum, and it does not import
``sandbox.trade``.

Duel's public ``GET /api/v2/metadata/exchange-rates`` is the only rate
contract verified for this module. ``crypto_rates[<balance-type id>].rate``
is coins per one EUR (a value of ``0.008`` means one EUR buys 0.008 of that
coin), and ``rates["USD"]`` is USD per one EUR. USD per coin is therefore
``rates["USD"] / crypto rate``. Every numeric field is parsed as ``Decimal``;
JSON's binary float is never used.

A direct httpx GET is challenged by Cloudflare. When that GET returns 403 or
a non-JSON body, and no test transport was injected, the same public GET is
issued from an already-open exact ``https://duel.com`` tab on the configured
loopback DevTools endpoint. That fallback never navigates, mints, bets, or
reads cookies. An injected transport never falls through to the browser or
to any other network. DuckDice's Bot API publishes no rate or bet-minimum
field in this repository, so a DuckDice quote uses that same Duel price only
as a labelled reference. It is not DuckDice's advertised minimum.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

import httpx

from sandbox.bet_token import CDP_ENV, assert_loopback_cdp, cdp_url

USD_FLOOR = Decimal("0.01")
RATE_PATH = "/api/v2/metadata/exchange-rates"
RATE_URL = "https://duel.com" + RATE_PATH
TIMEOUT = 10.0
CONNECT_TIMEOUT_MS = 5000

# Loaded bundle index-C0-aqrfD.js: q.SOL = 109, and the other balance-type
# ids it pairs with symbols. crypto_rates uses these ids, not the symbols.
BALANCE_TYPES = {
    "BTC": "101",
    "BCH": "102",
    "ETH": "103",
    "LTC": "104",
    "USDT": "105",
    "USDC": "106",
    "BNB": "107",
    "TRX": "108",
    "SOL": "109",
    "XRP": "110",
    "DOGE": "111",
    "ADA": "112",
    "LINK": "113",
    "AVAX": "114",
    "XLM": "115",
    "TON": "116",
    "HBAR": "117",
    "DOT": "118",
}

# BetInput uses precision 8 for a crypto amount. MoneyInput's step is
# 10 ** -precision, so one stake quantum is 1e-8 of the coin.
CRYPTO_QUANTUM = Decimal("0.00000001")
FIAT_PEGGED = frozenset({"USDT", "USDC"})


class QuoteError(Exception):
    """A rate or currency could not be quoted without inventing a price."""


def quote_minimum(
    provider: str, currency: str, *, transport: httpx.BaseTransport | None = None
) -> dict[str, str]:
    """Smallest accepted stake that is worth at least USD 0.01.

    ``transport`` is an optional httpx transport. End-to-end tests pass a
    mock and this function never opens a second connection when one is set.
    """
    name = _provider(provider)
    symbol = _symbol(currency)
    payload = _fetch(transport)
    usd_per_coin = _usd_per_coin(payload, symbol)
    quantum = _quantum(symbol)
    stake = _ceil(USD_FLOOR / usd_per_coin, quantum)
    if name == "duel":
        source = "duel:/api/v2/metadata/exchange-rates"
    else:
        source = f"duel-usd-reference:{symbol}"
    return {
        "min_stake": format(stake, "f"),
        "usd_per_coin": format(usd_per_coin, "f"),
        "quantum": format(quantum, "f"),
        "source": source,
    }


def _provider(value: str) -> str:
    name = str(value).strip().lower()
    if name in {"duel", "duckdice"}:
        return name
    raise QuoteError(f"no verified rate source for provider {value!r}")


def _symbol(value: str) -> str:
    symbol = str(value).strip().upper()
    if symbol not in BALANCE_TYPES:
        raise QuoteError(f"no verified rate mapping for currency {value!r}")
    return symbol


def _fetch(transport: httpx.BaseTransport | None) -> dict[str, Any]:
    # An injected transport is the whole network. Tests and the desk mock
    # must never leak into the operator's browser or a second real request.
    if transport is not None:
        return _fetch_http(transport)
    try:
        return _fetch_http(None)
    except QuoteError as exc:
        if not _cloudflare_blocked(exc):
            raise
    return _fetch_cdp()


def _cloudflare_blocked(exc: QuoteError) -> bool:
    text = str(exc)
    return text == "exchange-rate response was not JSON" or "HTTP 403" in text


def _fetch_http(transport: httpx.BaseTransport | None) -> dict[str, Any]:
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            transport=transport,
            follow_redirects=False,
            headers={"Accept": "application/json", "x-env-class": "main"},
        ) as client:
            response = client.get(RATE_URL)
    except httpx.HTTPError as exc:
        raise QuoteError("exchange-rate request failed") from exc
    if response.status_code != 200:
        raise QuoteError(f"exchange-rate request returned HTTP {response.status_code}")
    return _parse_rates(response.text)


def _fetch_cdp() -> dict[str, Any]:
    """Read-only GET from an already-open https://duel.com tab. Never navigates."""
    raw = os.environ.get(CDP_ENV, "").strip()
    if not raw:
        raise QuoteError(f"{CDP_ENV} is not configured; the public rate GET was challenged")
    try:
        url = assert_loopback_cdp(cdp_url())
    except Exception as exc:
        raise QuoteError(f"{CDP_ENV} is not a loopback DevTools URL") from exc
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise QuoteError("playwright is required to read the public rate from the open Duel tab") from exc
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(url, timeout=CONNECT_TIMEOUT_MS)
            page = _exact_duel_page(browser)
            text = page.evaluate(
                """async (path) => {
                    const controller = new AbortController();
                    const timer = setTimeout(() => controller.abort(), 10000);
                    try {
                        const response = await fetch(path, {
                            method: "GET",
                            credentials: "same-origin",
                            redirect: "error",
                            signal: controller.signal,
                            headers: {"Accept": "application/json"},
                        });
                        if (response.status !== 200) throw new Error("rate-http-" + response.status);
                        return await response.text();
                    } finally {
                        clearTimeout(timer);
                    }
                }""",
                RATE_PATH,
            )
    except QuoteError:
        raise
    except Exception as exc:
        raise QuoteError("could not read exchange rates from the open Duel tab") from exc
    if not isinstance(text, str) or not text.strip():
        raise QuoteError("the open Duel tab returned no exchange-rate text")
    return _parse_rates(text)


def _exact_duel_page(browser: Any) -> Any:
    for context in browser.contexts:
        for page in context.pages:
            parts = urlsplit(page.url or "")
            if parts.scheme == "https" and (parts.hostname or "").lower() == "duel.com":
                return page
    raise QuoteError("no exact https://duel.com tab is open on the configured DevTools endpoint")


def _parse_rates(text: str) -> dict[str, Any]:
    try:
        payload = json.loads(text, parse_float=Decimal, parse_int=Decimal)
    except (ValueError, json.JSONDecodeError) as exc:
        raise QuoteError("exchange-rate response was not JSON") from exc
    if not isinstance(payload, dict):
        raise QuoteError("exchange-rate response had an unexpected shape")
    return payload


def _usd_per_coin(payload: dict[str, Any], symbol: str) -> Decimal:
    if payload.get("base") != "EUR":
        raise QuoteError("exchange-rate base is not the verified EUR contract")
    rates = payload.get("rates")
    crypto = payload.get("crypto_rates")
    if not isinstance(rates, dict) or not isinstance(crypto, dict):
        raise QuoteError("exchange-rate response is missing rates")
    usd_per_eur = _positive(rates.get("USD"), "USD per EUR")
    row = crypto.get(BALANCE_TYPES[symbol])
    if not isinstance(row, dict):
        raise QuoteError(f"exchange rates have no entry for {symbol}")
    if row.get("unreliable") is not False:
        raise QuoteError(f"exchange rate for {symbol} is unreliable")
    coins_per_eur = _positive(row.get("rate"), f"{symbol} per EUR")
    usd_per_coin = usd_per_eur / coins_per_eur
    if not usd_per_coin.is_finite() or usd_per_coin <= 0:
        raise QuoteError(f"exchange rate for {symbol} is not positive")
    return usd_per_coin


def _positive(value: Any, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise QuoteError(f"{label} is not a decimal") from exc
    if not number.is_finite() or number <= 0:
        raise QuoteError(f"{label} is not positive")
    return number


def _quantum(symbol: str) -> Decimal:
    if symbol in FIAT_PEGGED:
        # Their price can move off USD 1. A 2-decimal stake step is not
        # published for these coins, so a cent-sized quantum would be invented.
        raise QuoteError(f"no verified stake quantum for {symbol}")
    return CRYPTO_QUANTUM


def _ceil(amount: Decimal, quantum: Decimal) -> Decimal:
    units = amount / quantum
    whole = units.to_integral_value()
    if whole < units:
        whole += 1
    stake = whole * quantum
    if not stake.is_finite() or stake <= 0:
        raise QuoteError("computed minimum stake is not positive")
    return stake
