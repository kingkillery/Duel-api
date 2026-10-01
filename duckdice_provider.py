"""DuckDice provider for Duel-API's dice/autobet surfaces.

DuckDice uses its verified Bot API directly rather than Duel's private /api/v2
routes. Authentication is the official Bot API key query parameter; dry runs
never require or expose a key.
"""
from __future__ import annotations

import os
import re
import time
from decimal import Decimal, Inexact, InvalidOperation, Rounded, localcontext
from pathlib import Path
from typing import Any, Callable

import httpx

from automation_client import DuelError, WriteNotAllowed
from bankroll import EdgeRefused

ORIGIN = "https://duckdice.io"
API_KEY_ENV = "DUCKDICE_API_KEY"
USER_AGENT = "DuckDiceBot/1.0.0"
PLAY_PATH = "/api/dice/play"
USER_INFO_PATH = "/api/bot/user-info"
DEFAULT_MAX_STAKE = Decimal("1")
# DuckDice's advertised dice edge: the multiplier paid on a win is 99% of the
# fair odds implied by the win chance, i.e. a flat 1% house edge on every bet.
# Unlike Duel (whose edge is read from the live scaling_edge config), DuckDice's
# published bot API exposes no edge table, so this is a site-wide constant and
# the single source of truth for it. Ride the actual constant: it is what makes
# E[net] = -0.01 x total_wagered, so a sizing policy refuses rather than
# authorising a stake off an assumed edge.
HOUSE_EDGE = Decimal("0.01")
DEFAULT_RATE_INTERVAL = 0.35
_MAX_HASH_LENGTH = 256
_MAX_SETTLEMENT_PRECISION = 100


class DuckDiceError(DuelError):
    """DuckDice transport, authentication, or response error."""


class DuckDiceProvider:
    """Provider-compatible client backed by DuckDice's verified Bot API."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
        betting_enabled: bool = False,
        max_stake: str | Decimal = DEFAULT_MAX_STAKE,
        rate_interval: float = DEFAULT_RATE_INTERVAL,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._api_key = self._load_api_key(api_key)
        self.betting_enabled = betting_enabled
        self.max_stake = Decimal(str(max_stake))
        self.bankroll: Decimal | str | None = None
        self.session_realized = Decimal(0)
        self.last_measured_edge: tuple[Decimal, Decimal] | None = None
        self._rate_interval = max(0.0, float(rate_interval))
        self._last_request = 0.0
        self._sleep = sleep if sleep is not None else time.sleep
        self._client = httpx.Client(
            base_url=ORIGIN,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
        )

    @staticmethod
    def _load_api_key(explicit: str | None) -> str:
        if explicit is not None:
            return explicit.strip()
        env_key = os.environ.get(API_KEY_ENV, "").strip()
        if env_key:
            return env_key
        module_dir = Path(__file__).resolve().parent

        # Project-local .env is the preferred persistent store for Duel-API.
        # Parse only DUCKDICE_API_KEY and never echo the value.
        for env_path in (Path(".env"), module_dir / ".env"):
            try:
                lines = env_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                name, value = stripped.split("=", 1)
                if name.strip().removeprefix("export ").strip() != API_KEY_ENV:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if value:
                    return value

        # Keep Ducky's existing .duckdice_key convention as a compatibility fallback.
        for path in (Path(".duckdice_key"), Path("..") / ".duckdice_key", module_dir / ".duckdice_key"):
            try:
                key = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if key:
                return key
        return ""

    def _require_key(self) -> str:
        if not self._api_key:
            raise DuckDiceError(
                f"DuckDice requires {API_KEY_ENV} for API reads or live bets"
            )
        return self._api_key

    def _redact(self, value: str) -> str:
        text = str(value)
        if self._api_key:
            text = text.replace(self._api_key, "REDACTED")
        return re.sub(r"(api_key=)[^&\s]+", r"\1REDACTED", text)

    def _throttle(self) -> None:
        if not self._last_request or not self._rate_interval:
            self._last_request = time.monotonic()
            return
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._rate_interval:
            self._sleep(self._rate_interval - elapsed)
        self._last_request = time.monotonic()

    def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        key = self._require_key()
        self._throttle()
        try:
            response = self._client.request(
                method,
                path,
                params={"api_key": key},
                json=json_body,
            )
        except httpx.TimeoutException as exc:
            raise DuckDiceError(f"DuckDice request timed out: {self._redact(exc)}") from exc
        except httpx.HTTPError as exc:
            raise DuckDiceError(f"DuckDice request failed: {self._redact(exc)}") from exc

        if response.status_code == 403:
            raise DuckDiceError("DuckDice request was blocked (HTTP 403)")
        if response.status_code == 429:
            raise DuckDiceError("DuckDice rate limit reached (HTTP 429)")
        if response.status_code >= 400:
            body = self._redact(response.text[:300])
            raise DuckDiceError(
                f"DuckDice API returned HTTP {response.status_code}: {body}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise DuckDiceError("DuckDice returned a non-JSON response") from exc
        if not isinstance(payload, dict):
            raise DuckDiceError("DuckDice returned an unexpected response shape")
        return payload

    @staticmethod
    def _validated_stake(amount: str | Decimal) -> Decimal:
        try:
            stake = Decimal(str(amount))
        except InvalidOperation:
            raise ValueError(f"amount must be a decimal stake string, got {amount!r}") from None
        if not stake.is_finite() or stake <= 0:
            raise ValueError(f"amount must be a positive finite decimal, got {amount!r}")
        return stake

    @staticmethod
    def _chance_for(target: str | int, side: str) -> Decimal:
        target_s = str(target).strip()
        if not target_s.isdigit() or not 200 <= int(target_s) <= 9800:
            raise ValueError(
                "target must be the roll target x100 as an integer string "
                f"(200-9800), got {target!r}"
            )
        side_value = str(side).upper()
        if side_value not in ("OVER", "UNDER"):
            raise ValueError(f"side must be OVER or UNDER, got {side!r}")
        t = Decimal(int(target_s))
        chance = t / Decimal(100) if side_value == "UNDER" else (Decimal(10000) - t) / Decimal(100)
        return chance.quantize(Decimal("0.01"))

    def user_info(self) -> dict[str, Any]:
        """GET /api/bot/user-info (read-only)."""
        return self._request("GET", USER_INFO_PATH)

    def save(self) -> None:
        """No-op session persist.

        ``automation_cli`` calls ``client.save()`` wherever a ``DuelClient`` may
        still hold mutable session/cookie state. DuckDice authenticates per
        request with its Bot API key and keeps no local session to persist, so
        this exists purely so the shared CLI lifecycle works with this provider
        instead of raising ``AttributeError`` *after* a real-money bet has
        already been accepted by the server.
        """

    def balance_for(self, currency: str | int) -> Decimal:
        wanted = str(currency).strip().upper()
        if not wanted:
            raise ValueError("currency must be non-empty")
        payload = self.user_info()
        for entry in payload.get("balances") or []:
            if isinstance(entry, dict) and str(entry.get("currency", "")).upper() == wanted:
                try:
                    return Decimal(str(entry["main"]))
                except (KeyError, InvalidOperation):
                    raise DuckDiceError(f"DuckDice returned an invalid {wanted} balance") from None
        raise DuckDiceError(f"DuckDice user-info has no balance for {wanted}")

    @staticmethod
    def _decimal_field(value: Any, *, field: str) -> Decimal:
        """Parse an authoritative DuckDice money field, rejecting junk.

        The play response's ``betAmount``/``winAmount``/``profit`` drive stake
        accounting and the autobet outcome, so a missing, empty, non-numeric, or
        non-finite value must raise here rather than reach downstream arithmetic.
        """
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise DuckDiceError(
                f"DuckDice returned an invalid decimal for {field}: {value!r}"
            ) from None
        if not parsed.is_finite():
            raise DuckDiceError(
                f"DuckDice returned a non-finite decimal for {field}: {value!r}"
            )
        return parsed

    def dice_edge(
        self, *, target: str | int, side: str = "UNDER"
    ) -> tuple[Decimal, Decimal]:
        """Return DuckDice's probability/multiplier pair at its flat house edge.

        The payout multiplier is the fair-odds multiplier scaled by
        ``(1 - HOUSE_EDGE)``. Implied EV is therefore ``-HOUSE_EDGE`` up to
        ``Decimal`` context rounding (dividing by a win chance can leave a
        last-place residue), and is negative at every valid target — which is
        what ``--edge-guard`` and ``BankrollPolicy`` size against. The sign is
        the guarantee; the last digit is not.
        """
        chance_percent = self._chance_for(target, side)
        probability = chance_percent / Decimal(100)
        multiplier = (Decimal(1) - HOUSE_EDGE) / probability
        return probability, multiplier


    @staticmethod
    def _validated_result(value: Any) -> bool:
        """Require the documented boolean win flag.

        ``bool("false")`` is True, so a string must not be coerced into a win.
        Only a real bool is an authoritative result.
        """
        if type(value) is not bool:
            raise DuckDiceError(
                f"DuckDice bet.result must be a boolean, got {value!r}"
            )
        return value

    @staticmethod
    def _validated_hash(value: Any) -> str:
        """Require a nonempty bounded bet identifier.

        Published examples are short hex strings, but the docs do not define
        an alphabet or a fixed length. A hex requirement would reject valid
        identifiers, so only empty, non-string, and oversized values fail.
        The length cap is a local bound, not a published hash contract.
        """
        if not isinstance(value, str) or not value.strip() or len(value) > _MAX_HASH_LENGTH:
            raise DuckDiceError(
                f"DuckDice bet.hash must be a nonempty bounded string, got {value!r}"
            )
        return value

    @staticmethod
    def _validated_roll(value: Any) -> int:
        """Require an integer roll.

        DuckDice documents ``number`` as an integer. The published example is
        6559. Inclusive 0-9999 is a local sanity bound for that four-digit
        example, not a second limit in the published schema. Bools are
        rejected because they are ints in Python.
        """
        if type(value) is bool or not isinstance(value, int):
            raise DuckDiceError(
                f"DuckDice bet.number must be an integer, got {value!r}"
            )
        if not 0 <= value <= 9999:
            raise DuckDiceError(
                "DuckDice bet.number is outside the local 0-9999 sanity "
                f"range, got {value!r}"
            )
        return value

    @staticmethod
    def _decimal_span(value: Decimal) -> tuple[int, int]:
        exponent = value.as_tuple().exponent
        if not isinstance(exponent, int) or not value.is_finite():
            raise DuckDiceError(
                f"DuckDice settlement amount is not a finite decimal: {value!r}"
            )
        return value.adjusted(), exponent

    @staticmethod
    def _exact_apply(left: Decimal, right: Decimal, op: Callable[[Decimal, Decimal], Decimal]) -> Decimal:
        """Apply an arithmetic op without ambient rounding.

        The default context precision is 28, so a wider response can lose
        digits. Precision covers the highest adjusted exponent through the
        lowest exponent, plus one digit of carry or borrow. Extreme spans
        fail closed instead of rounding.
        """
        left_high, left_low = DuckDiceProvider._decimal_span(left)
        right_high, right_low = DuckDiceProvider._decimal_span(right)
        precision = max(left_high, right_high) - min(left_low, right_low) + 2
        if precision > _MAX_SETTLEMENT_PRECISION:
            raise DuckDiceError(
                "DuckDice settlement precision exceeds the exact-compare cap "
                f"of {_MAX_SETTLEMENT_PRECISION} digits"
            )
        with localcontext() as ctx:
            ctx.prec = max(precision, 1)
            ctx.traps[Inexact] = True
            ctx.traps[Rounded] = True
            try:
                return op(left, right)
            except (Inexact, Rounded) as exc:
                raise DuckDiceError(
                    "DuckDice settlement arithmetic could not be computed exactly"
                ) from exc

    @staticmethod
    def _exact_difference(payout: Decimal, stake: Decimal) -> Decimal:
        return DuckDiceProvider._exact_apply(payout, stake, lambda left, right: left - right)

    @staticmethod
    def _require_settlement(
        *,
        submitted: Decimal,
        stake_returned: Decimal,
        payout: Decimal,
        profit: Decimal,
        balance: Decimal,
    ) -> None:
        # Refuse a response that cannot be booked without inventing money.
        # Stake must be positive, payout and balance non-negative, the returned
        # stake must be the stake that was submitted, and profit must equal
        # payout minus stake by exact subtraction.
        if stake_returned <= 0:
            raise DuckDiceError(
                f"DuckDice returned a non-positive stake: {stake_returned}"
            )
        if payout < 0:
            raise DuckDiceError(f"DuckDice returned a negative payout: {payout}")
        if balance < 0:
            raise DuckDiceError(f"DuckDice returned a negative balance: {balance}")
        for label, amount in (
            ("stake", stake_returned),
            ("payout", payout),
            ("profit", profit),
            ("balance", balance),
        ):
            high, low = DuckDiceProvider._decimal_span(amount)
            if high - low + 1 > _MAX_SETTLEMENT_PRECISION:
                raise DuckDiceError(
                    f"DuckDice {label} precision exceeds the exact-compare cap "
                    f"of {_MAX_SETTLEMENT_PRECISION} digits"
                )
        if stake_returned != submitted:
            raise DuckDiceError(
                "DuckDice returned stake "
                f"{stake_returned} does not match submitted stake {submitted}"
            )
        expected = DuckDiceProvider._exact_difference(payout, stake_returned)
        if profit != expected:
            raise DuckDiceError(
                "DuckDice profit "
                f"{profit} does not match payout {payout} minus stake "
                f"{stake_returned}"
            )

    def place_dice_bet(
        self,
        amount: str | Decimal,
        *,
        side: str | None = None,
        bet_type: str | None = None,
        currency: str = "",
        target: str | int = "",
        security_token: str | None = None,
        confirm: bool = False,
        dry_run: bool = True,
        policy: Any | None = None,
        edge: tuple[Any, Any] | None = None,
    ) -> dict[str, Any]:
        if side is None and bet_type is None:
            raise ValueError("dice bet needs side='OVER' or 'UNDER'")
        if side is not None and bet_type is not None and str(side).upper() != str(bet_type).upper():
            raise ValueError(f"side and bet_type conflict: {side!r} vs {bet_type!r}")
        side_value = str(side if side is not None else bet_type).upper()
        chance = self._chance_for(target, side_value)
        stake = self._validated_stake(amount)
        symbol = str(currency).strip().upper()
        if not symbol:
            raise ValueError("currency must be a non-empty currency symbol")

        payload = {
            "symbol": symbol,
            "chance": f"{chance:.2f}",
            "isHigh": side_value == "OVER",
            "amount": str(amount),
        }
        if dry_run:
            return {
                "dry_run": True,
                "provider": "duckdice",
                "method": "POST",
                "path": PLAY_PATH,
                "payload": payload,
            }
        if not self.betting_enabled:
            raise WriteNotAllowed(
                "refusing to place a DuckDice bet: betting_enabled=True is required"
            )
        if not confirm:
            raise WriteNotAllowed("refusing to place a DuckDice bet: confirm=True is required")
        if stake > self.max_stake:
            raise ValueError(
                f"stake {stake} exceeds this client's max_stake {self.max_stake}"
            )
        if policy is not None:
            gate: dict[str, Any] = {
                "stake": stake,
                "session_realized": self.session_realized,
            }
            if getattr(policy, "requires_edge", True):
                measured = edge if edge is not None else self.last_measured_edge
                if measured is None:
                    raise EdgeRefused("no measured edge is available")
                if self.bankroll is None:
                    raise EdgeRefused("bankroll is required for edge-based sizing")
                gate.update(
                    bankroll=self.bankroll,
                    win_chance=measured[0],
                    multiplier=measured[1],
                )
            policy.check(**gate)

        raw = self._request("POST", PLAY_PATH, json_body=payload)
        bet = raw.get("bet")
        user = raw.get("user")
        if not isinstance(bet, dict) or not isinstance(user, dict):
            raise DuckDiceError("DuckDice play response is missing bet/user objects")
        required = ("hash", "number", "result", "betAmount", "winAmount", "profit")
        missing = [name for name in required if name not in bet]
        if "balance" not in user:
            missing.append("user.balance")
        if missing:
            raise DuckDiceError(
                "DuckDice play response is missing authoritative fields: "
                + ", ".join(missing)
            )
        # Parse every settlement field before touching session accounting. A
        # later invalid field must not leave session_realized or bankroll
        # advanced from a response we then refuse.
        stake_returned = self._decimal_field(bet["betAmount"], field="bet.betAmount")
        payout = self._decimal_field(bet["winAmount"], field="bet.winAmount")
        profit = self._decimal_field(bet["profit"], field="bet.profit")
        balance = self._decimal_field(user["balance"], field="user.balance")
        won = self._validated_result(bet["result"])
        bet_hash = self._validated_hash(bet["hash"])
        roll = self._validated_roll(bet["number"])
        self._require_settlement(
            submitted=stake,
            stake_returned=stake_returned,
            payout=payout,
            profit=profit,
            balance=balance,
        )
        booked = self._exact_apply(
            self.session_realized, profit, lambda left, right: left + right
        )
        self.session_realized = booked
        self.bankroll = balance
        probability, multiplier = self.dice_edge(target=target, side=side_value)
        self.last_measured_edge = (probability, multiplier)

        return {
            "provider": "duckdice",
            "data": {
                "round": {
                    "hash": bet_hash,
                    "number": roll,
                    "won": won,
                    "amount_currency": str(stake_returned),
                    "stake": str(stake_returned),
                    "amount_won": str(payout),
                    "profit": str(profit),
                    "win_chance": str(probability),
                    "multiplier": str(multiplier),
                    "balance": str(balance),
                    "currency": symbol,
                }
            },
        }

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DuckDiceProvider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
