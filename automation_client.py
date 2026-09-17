"""Duel.com private-API client.

Reverse-engineered from the live SPA bundle (``/assets/index-Cs9Avd2r.js``) and
validated against captured browser traffic.  See ``site_spec.json`` for the
captured endpoint catalogue and ``README.md`` for provenance and scope.

Design notes
------------
* The SPA is same-origin: every call is relative to ``https://duel.com`` and
  targets the ``/api/v2`` prefix.  There is no separate API hostname.
* Session state lives in **cookies**, not a bearer token.  ``GET /api/v2/metadata``
  *issues* the ``duel`` session cookie, so the cookie jar must be attached from the
  very first request or login will fail against a session the server never saw.
* Every request carries ``x-duel-device-identifier`` (a client-generated, persistent
  v4 UUID mirrored in ``localStorage["security:uuid"]``) and ``x-env-class: main``.
* ``captcha_on_login`` is enabled server-side (Cloudflare Turnstile).  This client
  therefore **cannot** mint a session on its own: login accepts an externally
  obtained captcha token.  Obtain it from a real browser session, or capture the
  cookies wholesale with ``capture_session.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid as _uuid
from decimal import Decimal, InvalidOperation
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

ORIGIN = "https://duel.com"
API_PREFIX = "/api/v2"

DEFAULT_PROFILE = Path(".private-api-automation/profiles/default/session.json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# Cookies worth persisting. ``__cf_bm`` is Cloudflare bot-management and has a
# ~30 minute TTL, so it is stored but expected to go stale between runs.
SESSION_COOKIES = ("duel", "__cf_bm", "CookieConsent", "_sp_id", "_sp_ses", "XSRF-TOKEN")

# The bot cookie whose TTL actually governs how long a replayed session lives.
CF_BM_COOKIE = "__cf_bm"

# XSRF pair configured by the bundle's axios defaults (kO). The cookie is
# mirrored into the header on non-GET requests, matching axios.
XSRF_COOKIE = "XSRF-TOKEN"
XSRF_HEADER = "X-XSRF-TOKEN"

# Cloudflare bot-management cookie lifetime, per site_spec.json token_sources:
# "__cf_bm ... ~30 minute TTL. A 403 on replay almost always means this went
# stale, not that the path is wrong."
CF_BM_TTL_SECONDS = 30 * 60

# Warn before the TTL expires rather than after a surprise 403.
STALE_WARNING_SECONDS = int(CF_BM_TTL_SECONDS * 0.8)

# HTTP 429 handling: how many times to retry before giving up, and the largest
# Retry-After delay we are willing to honour (longer waits fall back to capped
# exponential backoff) so a hostile or confused server cannot park us for minutes.
DEFAULT_MAX_429_RETRIES = 2
DEFAULT_RETRY_AFTER_CEILING = 30.0


class DuelError(RuntimeError):
    """Base class for client errors."""


class CloudflareChallenge(DuelError):
    """Cloudflare bot-management rejected the request.

    Almost always a stale ``__cf_bm`` cookie -> re-capture the session rather
    than second-guessing the endpoint path.
    """


class AuthRequired(DuelError):
    """No valid session; call ``login()`` or restore a captured session."""


class CaptchaRequired(DuelError):
    """Login needs a fresh captcha token that this client cannot mint."""


class WriteNotAllowed(DuelError):
    """A state-changing call was attempted without an explicit opt-in."""


class UnsupportedAction(DuelError):
    """Requested action is outside this client's deliberate scope."""


class RateLimited(DuelError):
    """The server asked us to slow down (HTTP 429)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


_MONEY_MOVING = ("bet", "wager", "stake", "deposit", "withdraw", "buy", "sell")

# POSTs that are token/session lifecycle rather than account mutation, so they
# do not require the write opt-in. Exempting metadata/socket-token keeps the
# spec's recovery_ladder.refresh_tokens step usable on a default client.
_READ_LIKE_POSTS = (
    "/api/v2/auth/login",
    "/api/v2/auth/logout",
    "/api/v2/auth/logout/all",
    "/api/v2/metadata/socket-token",
)

# Betting safety: dice wagers are only possible through the reviewed
# place_dice_bet() path (never the generic request()/call escape hatch),
# default to a dry run, and are capped per bet. This is a client-side
# tripwire, not a substitute for the server's own limits or for checking
# the operator's terms before automating real-money play.
DEFAULT_MAX_STAKE = 1.0
DICE_BET_PATH = "/api/v2/dice/bet"
DICE_CONFIG_PATH = "/api/v2/dice/config"
DICE_BET_TYPES = ("OVER", "UNDER")


@dataclass
class Session:
    """Persistable session state."""

    device_uuid: str = field(default_factory=lambda: str(_uuid.uuid4()))
    cookies: dict[str, str] = field(default_factory=dict)
    local_storage: dict[str, str] = field(default_factory=dict)
    username: str | None = None
    user_id: int | None = None
    captured_at: float | None = None
    # When __cf_bm was last issued - by capture, or by any response that rotated
    # it.  This, not captured_at, is what staleness is measured against.
    cf_bm_at: float | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Session":
        raw = json.loads(text)
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self, path: Path | str = DEFAULT_PROFILE) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PROFILE) -> "Session":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def age_seconds(self, now: float | None = None) -> float | None:
        """Seconds since capture, or ``None`` when the capture time is unknown."""
        if self.captured_at is None:
            return None
        reference = time.time() if now is None else now
        return max(0.0, reference - self.captured_at)

    def bot_cookie_age_seconds(self, now: float | None = None) -> float | None:
        """Age of ``__cf_bm``, falling back to capture time when never stamped.

        A successful ``metadata()`` re-issues the bot cookie and restarts this
        clock, which is what lets a session that looked stale minutes ago be
        rescued without a browser.
        """
        stamp = self.cf_bm_at if self.cf_bm_at is not None else self.captured_at
        if stamp is None:
            return None
        reference = time.time() if now is None else now
        return max(0.0, reference - stamp)

    def is_stale(
        self, *, ttl: float = CF_BM_TTL_SECONDS, now: float | None = None
    ) -> bool | None:
        """Tri-state staleness: ``True`` past TTL, ``False`` inside it, ``None`` unknown.

        ``None`` means neither timestamp is present, which is the state of every
        profile captured before this was stamped. Callers must not read ``None``
        as "fresh": an untimestamped session can be arbitrarily old, and guessing
        would hide exactly the failure this exists to explain.
        """
        age = self.bot_cookie_age_seconds(now)
        return None if age is None else age > ttl


class DuelClient:
    """Thin, read-oriented client for the Duel.com private API."""

    def __init__(
        self,
        session: Session | None = None,
        *,
        profile: Path | str = DEFAULT_PROFILE,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        allow_writes: bool = False,
        betting_enabled: bool = False,
        max_stake: float = DEFAULT_MAX_STAKE,
        auto_refresh: bool = True,
        max_429_retries: int = DEFAULT_MAX_429_RETRIES,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        # State-changing calls are opt-in. Read-only is the default posture.
        self.allow_writes = allow_writes
        # Real-money betting is a further explicit opt-in with a per-bet cap.
        self.betting_enabled = betting_enabled
        self.max_stake = max_stake
        # Re-mint __cf_bm once on a Cloudflare challenge before failing.
        self.auto_refresh = auto_refresh
        self._refreshing = False
        # 429 retries before RateLimited; the injectable sleep keeps tests off the clock.
        self.max_429_retries = max_429_retries
        self._sleep = sleep if sleep is not None else time.sleep
        self.profile_path = Path(profile)
        self.session = session or Session()
        self._client = httpx.Client(
            base_url=ORIGIN,
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": ORIGIN,
                "Referer": ORIGIN + "/",
                "x-env-class": "main",
            },
        )
        # Attach any restored cookies before the first request goes out.
        for name, value in self.session.cookies.items():
            self._client.cookies.set(name, value, domain="duel.com")

    # ------------------------------------------------------------------ plumbing

    def _headers(self, method: str = "GET", extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"x-duel-device-identifier": self.session.device_uuid}

        # Mirror axios: on non-GET requests, copy the XSRF cookie into the header
        # pair the bundle configures (xsrfCookieName `XSRF-TOKEN` ->
        # xsrfHeaderName `X-XSRF-TOKEN`).  Duel does not appear to issue that
        # cookie today, so this is normally a no-op - but replaying the exact
        # client behaviour keeps writes working if it ever does.
        if method.upper() not in ("GET", "HEAD", "OPTIONS"):
            # Name-only lookup, mirroring axios/document.cookie: a cookie issued
            # with `Domain=.duel.com` is stored under the dotted host and an
            # exact `domain="duel.com"` match would silently miss it. It also
            # tolerates the same name appearing under several domains, which
            # `Cookies.get` refuses to resolve (see `_cookie_value`).
            token = self._cookie_value(XSRF_COOKIE)
            if token:
                headers[XSRF_HEADER] = token

        if extra:
            headers.update(extra)
        return headers

    def _cookie_value(self, name: str) -> str | None:
        """Read a cookie by name without tripping httpx's ``CookieConflict``.

        ``Cookies.get(name)`` raises when the same name is present under more
        than one domain, and that is the normal state here rather than an edge
        case: ``__init__`` restores persisted cookies under ``duel.com`` while
        Cloudflare re-issues ``__cf_bm`` under ``.duel.com``. Every request then
        died in ``_absorb_cookies`` before this was resolved.

        Browsers pick the most specific applicable cookie, so do the same:
        filter to domains that actually match our origin host, then rank by
        specificity. A bare ``(path, domain)`` rank is not enough - ``duel.com``
        and ``.duel.com`` normalise to the same string and would tie, leaving the
        winner dependent on jar insertion order. Host-only cookies outrank domain
        cookies, and a longer path outranks a shorter one.
        """
        host = httpx.URL(ORIGIN).host
        best: tuple[int, bool, int, str] | None = None
        for cookie in self._client.cookies.jar:
            if cookie.name != name:
                continue
            raw_domain = cookie.domain or ""
            domain = raw_domain.lstrip(".")
            if domain != host and not host.endswith("." + domain):
                continue
            rank = (
                len(cookie.path or "/"),
                not raw_domain.startswith("."),
                len(domain),
            )
            candidate = rank + (cookie.value,)
            if best is None or candidate[:3] > best[:3]:
                best = candidate
        return None if best is None else best[3]

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        """Parse a numeric ``Retry-After``. HTTP-dates return None (not worth a clock)."""
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return max(0.0, float(raw.strip()))
        except ValueError:
            return None

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
        confirm: bool = False,
        auto_refresh: bool | None = None,
        _attempt: int = 0,
        _allow_money: bool = False,
    ) -> Any:
        """Perform one API call and return the decoded JSON body.

        Raises :class:`CloudflareChallenge` on 403 (stale bot cookie) and
        :class:`AuthRequired` on 401/419.

        ``auto_refresh`` overrides the client default: on a Cloudflare challenge
        the client makes one ``metadata()`` call to re-mint ``__cf_bm`` and
        retries the original request once. The retry passes
        ``auto_refresh=False``, so this can never loop.
        """
        attempt = _attempt
        allow_money = _allow_money
        url = path if path.startswith("/") else f"{API_PREFIX}/{path}"
        # The guard lives here, not only in the named action methods, so that no
        # entry point (including the CLI `call` escape hatch) can bypass it.
        self._guard_write(url, method, confirm, allow_money)
        response = self._client.request(
            method, url, json=json_body, params=params, headers=self._headers(method)
        )

        if response.status_code == 403:
            # Cloudflare signals a managed challenge by the PRESENCE of the
            # cf-mitigated header; its value is typically "challenge", which does
            # not contain the substring "cf". Test presence, not the value.
            body = response.text[:200]
            mitigated = response.headers.get("cf-mitigated") is not None
            if mitigated or "<html" in body[:80].lower():
                if self._refresh_bot_cookie(auto_refresh):
                    return self.request(
                        method,
                        path,
                        json_body=json_body,
                        params=params,
                        confirm=confirm,
                        auto_refresh=False,
                        _allow_money=allow_money,
                        _attempt=attempt,
                    )
                raise CloudflareChallenge(self._cloudflare_message(method, url))
            raise DuelError(f"403 from {method} {url}: {body}")
        if response.status_code in (401, 419):
            raise AuthRequired(f"session not authenticated ({response.status_code} {method} {url})")
        if response.status_code == 429:
            if attempt < self.max_429_retries:
                delay = self._retry_after_seconds(response)
                if delay is None or delay > DEFAULT_RETRY_AFTER_CEILING:
                    delay = min(delay or 2.0 * (2**attempt), DEFAULT_RETRY_AFTER_CEILING)
                self._sleep(delay)
                return self.request(
                    method,
                    path,
                    json_body=json_body,
                    params=params,
                    confirm=confirm,
                    auto_refresh=auto_refresh,
                    _allow_money=allow_money,
                    _attempt=attempt + 1,
                )
            raise RateLimited(
                f"429 from {method} {url} after {attempt + 1} attempt(s): {response.text[:200]}",
                self._retry_after_seconds(response),
            )
        if response.status_code >= 400:
            raise DuelError(f"{response.status_code} from {method} {url}: {response.text[:300]}")

        # Mirror server cookie rotation back into the persisted session.
        self._absorb_cookies(response)

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def _refresh_bot_cookie(self, override: bool | None) -> bool:
        """Try once to re-mint ``__cf_bm`` via metadata. True if it worked.

        ``self._refreshing`` guards re-entrancy: metadata() is itself a request,
        so a challenge during the refresh must not trigger another refresh.
        """
        enabled = self.auto_refresh if override is None else override
        if not enabled or self._refreshing:
            return False
        self._refreshing = True
        try:
            self.metadata()
        except DuelError:
            return False
        finally:
            self._refreshing = False
        return True

    def _cloudflare_message(self, method: str, url: str) -> str:
        """Make the 403 self-diagnosing: report how old the session actually is."""
        age = self.session.bot_cookie_age_seconds()
        ttl_minutes = CF_BM_TTL_SECONDS / 60.0
        if age is None:
            detail = (
                "the session carries no timestamp so its age is unknown - "
                "re-capture to make future 403s diagnosable"
            )
        else:
            verdict = "past" if age > CF_BM_TTL_SECONDS else "within"
            detail = (
                f"the bot cookie is {age / 60.0:.1f} min old, {verdict} its "
                f"~{ttl_minutes:.0f} min TTL"
            )
        return (
            f"Cloudflare challenged {method} {url}; the __cf_bm cookie is likely "
            f"stale. {detail}. Re-capture the session (capture_session.py)."
        )

    def _absorb_cookies(self, response: httpx.Response | None = None) -> None:
        """Mirror server cookie rotation back into the persisted session.

        ``response`` is optional: ``request()`` passes the response it just got,
        while ``save()`` and ``login()`` only have the accumulated jar to work
        from. When present, the response's own ``Set-Cookie`` values are
        authoritative *and* unambiguous, so they win. The jar is consulted only
        for names the response did not mention, and even there a name held under
        several domains is resolved by specificity - ``Cookies.get`` raises
        ``CookieConflict`` instead, which previously killed every request once
        ``__cf_bm`` existed under both ``duel.com`` and ``.duel.com``.
        """
        previous = self.session.cookies.get(CF_BM_COOKIE)
        issued = (
            {c.name: c.value for c in response.cookies.jar} if response is not None else {}
        )
        for name in SESSION_COOKIES:
            value = issued.get(name)
            if value is None:
                value = self._cookie_value(name)
            if value is not None:
                self.session.cookies[name] = value
            else:
                # Server cleared the cookie (or it was never issued): do not
                # resurrect a stale value from disk on the next save().
                self.session.cookies.pop(name, None)
        # A rotated bot cookie restarts the TTL clock that is_stale() reads.
        current = self.session.cookies.get(CF_BM_COOKIE)
        if current is not None and current != previous:
            self.session.cf_bm_at = time.time()

    # -------------------------------------------------------------- bootstrap/auth

    def metadata(self, *, refresh_session: bool = False) -> dict[str, Any]:
        """``GET /api/v2/metadata?uuid=`` - the app's bootstrap document.

        Also the call that *issues* the ``duel`` session cookie, so it is
        normally the first request a client makes.
        """
        if refresh_session:
            self.session.device_uuid = str(_uuid.uuid4())
        return self.request("GET", "/api/v2/metadata", params={"uuid": self.session.device_uuid})

    def socket_token(self) -> dict[str, Any]:
        """``POST /api/v2/metadata/socket-token`` - short-lived realtime credential."""
        return self.request("POST", "/api/v2/metadata/socket-token", json_body={})

    def fetch_text(self, url: str) -> str:
        """GET an absolute URL and return the body as text (used for spec drift).

        Absorbs cookie rotation like any other response. Does not use request(),
        because bundle assets are neither same-origin ``/api/v2`` nor JSON.
        """
        response = self._client.request("GET", url, headers=self._headers("GET"))
        self._absorb_cookies(response)
        response.raise_for_status()
        return response.text

    def check_spec_drift(self, spec: dict) -> dict:
        """Compare the live bundle hash to ``spec['metadata']['provenance']``.

        The spec records the SPA bundle path and the first 8 hex digits of its
        sha256. When the live bundle no longer matches, every endpoint captured
        from that bundle is suspect: the SPA shipped a new build, and paths,
        payloads or auth may have moved under it.
        """
        provenance = (spec.get("metadata") or {}).get("provenance") or {}
        bundle = provenance.get("bundle") or ""
        expected = provenance.get("bundle_sha256_prefix") or ""
        if not bundle or not expected:
            return {"drifted": None, "reason": "spec records no bundle hash"}
        body = self.fetch_text(bundle if bundle.startswith("/") else f"/{bundle}")
        actual = hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]
        return {"expected": expected, "actual": actual, "drifted": actual != expected}

    @property
    def is_authenticated(self) -> bool:
        """Re-read metadata and report whether the server recognises the session."""
        try:
            return bool((self.metadata() or {}).get("user"))
        except DuelError:
            return False

    def login(
        self,
        identifier: str,
        password: str,
        *,
        captcha_token: str,
        captcha_type: str = "turnstile_token",
        remember: bool = True,
    ) -> dict[str, Any]:
        """``POST /api/v2/auth/login``.

        ``identifier`` is an email or a username - the SPA sends ``email`` when the
        value contains ``@`` and ``username`` otherwise, and so do we.

        ``captcha_token`` must come from a real browser: ``captcha_on_login`` is
        enabled and the server rejects submissions without a fresh Turnstile token.
        There is deliberately no attempt to solve or bypass the challenge here.
        """
        if not captcha_token:
            raise CaptchaRequired(
                "login requires a fresh captcha token; obtain one from a real "
                "browser session (see README 'Authenticating')."
            )

        # Metadata first: this is what creates the `duel` cookie the login binds to.
        self.metadata()

        payload: dict[str, Any] = {"password": password, "type": captcha_type, "token": captcha_token}
        if "@" in identifier:
            payload["email"] = identifier
        else:
            payload["username"] = identifier

        result = self.request("POST", "/api/v2/auth/login", json_body=payload)
        self._absorb_cookies()

        user = (result or {}).get("user") or {}
        if user:
            self.session.username = user.get("username")
            self.session.user_id = user.get("id")
        self.save()
        return result

    def logout(self) -> Any:
        """``POST /api/v2/auth/logout``."""
        return self.request(
            "POST",
            "/api/v2/auth/logout",
            json_body={"uuid": self.session.device_uuid, "end_all_sessions": False},
        )

    # ------------------------------------------------------------- read endpoints

    def games(
        self,
        *,
        start: int = 0,
        search: str = "",
        game_filter: str = "popular",
        provider: str = "",
        sort: str = "recommended",
    ) -> Any:
        """``GET /api/v2/games`` - catalogue listing (public)."""
        return self.request(
            "GET",
            "/api/v2/games",
            params={
                "start": start,
                "search": search,
                "filter": game_filter,
                "provider": provider,
                "sort": sort,
            },
        )

    def slots(self) -> Any:
        """``GET /api/v2/slots`` - slot catalogue (public)."""
        return self.request("GET", "/api/v2/slots")

    def exchange_rates(self) -> Any:
        """``GET /api/v2/metadata/exchange-rates`` (public)."""
        return self.request("GET", "/api/v2/metadata/exchange-rates")

    def payment_methods(self) -> Any:
        """``GET /api/v2/trade/metadata/payment-methods`` (public)."""
        return self.request("GET", "/api/v2/trade/metadata/payment-methods")

    def withdraw_methods(self) -> Any:
        """``GET /api/v2/trade/crypto/withdraw/methods`` (public)."""
        return self.request("GET", "/api/v2/trade/crypto/withdraw/methods")

    def rakeback_status(self) -> Any:
        """``GET /api/v2/user/zero-edge-rakeback/status`` (public)."""
        return self.request("GET", "/api/v2/user/zero-edge-rakeback/status")

    def profile(self) -> Any:
        """``GET /api/v2/user`` - requires an authenticated session."""
        return self.request("GET", "/api/v2/user")

    def settings(self) -> Any:
        """``GET /api/v2/user/settings`` - requires an authenticated session."""
        return self.request("GET", "/api/v2/user/settings")

    def kyc(self) -> Any:
        """``GET /api/v2/user/kyc`` - requires an authenticated session."""
        return self.request("GET", "/api/v2/user/kyc")

    def client_seed(self) -> Any:
        """``GET /api/v2/client-seed`` - provably-fair client seed."""
        return self.request("GET", "/api/v2/client-seed")

    # ---------------------------------------------------------------- actions
    #
    # State-changing calls. All paths below were read out of the live bundle
    # (verified against the shipped service definitions). Account management
    # calls need the write opt-in; the dice betting methods at the end of this
    # section need the further betting opt-in plus per-call confirmation.

    def _guard_write(
        self, path: str, method: str, confirm: bool, allow_money: bool = False
    ) -> None:
        """Enforce the write policy for every outgoing call.

        Policy:

        * reads (GET/HEAD/OPTIONS) are always allowed;
        * money-moving paths are refused outright, regardless of opt-in -
          except through ``allow_money``, which is private and may only be set
          by reviewed betting methods (``place_dice_bet``). The generic
          ``request()`` path and the CLI ``call`` escape hatch stay blocked;
        * token/session lifecycle POSTs listed in ``_READ_LIKE_POSTS`` are
          allowed, since login is already gated by :class:`CaptchaRequired`;
        * every other state-changing call needs an explicit opt-in.
        """
        bare = path.split("?")[0].lower()
        # Reads are always allowed, including the read-only method listings
        # (e.g. GET withdraw/methods); money-moving state changes are refused
        # below regardless of opt-in, unless this is the reviewed betting path.
        if method.upper() in ("GET", "HEAD", "OPTIONS"):
            return
        for verb in _MONEY_MOVING:
            if verb in bare and not allow_money:
                raise UnsupportedAction(
                    f"'{verb}' endpoints are blocked on the generic path "
                    "(real-money site; wagering is only available via the "
                    "reviewed place_dice_bet() method - see site_spec.json "
                    "metadata.wagering)."
                )
        if bare in _READ_LIKE_POSTS:
            return
        if not (confirm or self.allow_writes):
            raise WriteNotAllowed(
                f"refusing to call {method} {path!r}: state-changing calls need "
                "confirm=True or DuelClient(allow_writes=True)."
            )

    def update_settings(self, settings: dict[str, Any], *, confirm: bool = False) -> Any:
        """``PATCH /api/v2/user/settings`` - body is ``{"settings": {...}}``."""
        return self.request(
            "PATCH",
            "/api/v2/user/settings",
            json_body={"settings": settings},
            confirm=confirm,
        )

    def set_client_seed(self, client_seed: str, *, confirm: bool = False) -> Any:
        """``POST /api/v2/client-seed`` - initialise/change the provably-fair seed."""
        return self.request(
            "POST",
            "/api/v2/client-seed",
            json_body={"client_seed": client_seed},
            confirm=confirm,
        )

    def rotate_client_seed(self, client_seed: str, *, confirm: bool = False) -> Any:
        """``POST /api/v2/client-seed/rotate`` - rotate the provably-fair seed."""
        return self.request(
            "POST",
            "/api/v2/client-seed/rotate",
            json_body={"client_seed": client_seed},
            confirm=confirm,
        )

    def security_token(
        self,
        *,
        token_type: str = "standard",
        confirm: bool = False,
    ) -> Any:
        """``POST /api/v2/user/security/token`` - request a 2FA/security challenge.

        ``token_type`` is one of ``silent`` / ``standard`` / ``onetime`` /
        ``session-auth``. Sends the caller's device uuid, as the SPA does.
        """
        return self.request(
            "POST",
            "/api/v2/user/security/token",
            json_body={"uuid": self.session.device_uuid, "type": token_type},
            confirm=confirm,
        )

    def two_factor_setup(self, *, confirm: bool = False) -> Any:
        """``POST /api/v2/user/security/two-factor-setup``."""
        return self.request(
            "POST", "/api/v2/user/security/two-factor-setup", json_body={}, confirm=confirm
        )

    # ------------------------------------------------------- dice wagering
    #
    # REAL-MONEY betting. This is the only path in the client that can move
    # money, and it is gated four deep: validated parameters, dry_run=True by
    # default, DuelClient(betting_enabled=True), and per-call confirm=True,
    # plus a client-side per-bet cap (max_stake). Automated wagering almost
    # certainly violates the operator's terms and can lose real money fast -
    # check the terms first, never stake more than you can afford to lose.

    def dice_config(self) -> Any:
        """``GET /api/v2/dice/config`` - dice limits and rules (read-only)."""
        return self.request("GET", DICE_CONFIG_PATH)

    def place_dice_bet(
        self,
        amount: str,
        *,
        bet_type: str,
        currency: str,
        target: str,
        security_token: str = "",
        confirm: bool = False,
        dry_run: bool = True,
    ) -> Any:
        """``POST /api/v2/dice/bet`` - place one real-money dice bet.

        Body (bundle-verified from the ``useDice`` chunk): ``amount`` is the
        stake as a crypto amount string, ``bet_type`` is ``OVER`` or ``UNDER``,
        ``currency`` is the currency code, ``security_token`` is ``""`` when
        no extra security is required (else a token from
        ``security_token()``, which may need a 2FA code), and ``target`` is
        the roll target x100 as an integer string (e.g. ``"5005"`` for 50.05).
        The response carries ``{data: {round: ...}}`` with nonce, seeds and
        the settled result.

        Gates, in order: parameters are validated before anything is sent;
        ``dry_run=True`` (the default) validates and returns the would-be
        payload without sending anything; a live bet additionally requires
        ``DuelClient(betting_enabled=True)``, ``confirm=True``, and
        ``amount <= max_stake``.
        """
        side = (bet_type or "").upper()
        if side not in DICE_BET_TYPES:
            raise ValueError(f"bet_type must be one of {DICE_BET_TYPES}, got {bet_type!r}")
        if not currency or not str(currency).strip():
            raise ValueError("currency must be a non-empty currency code")
        try:
            stake = Decimal(str(amount))
        except InvalidOperation:
            raise ValueError(f"amount must be a decimal stake string, got {amount!r}") from None
        if stake <= 0:
            raise ValueError(f"amount must be positive, got {amount!r}")
        target_s = str(target)
        if not target_s.isdigit() or not 200 <= int(target_s) <= 9800:
            raise ValueError(
                "target must be the roll target x100 as an integer string "
                f"(200-9800), got {target!r}"
            )
        payload = {
            "amount": str(amount),
            "bet_type": side,
            "currency": currency,
            "security_token": security_token,
            "target": target_s,
        }
        if dry_run:
            return {
                "dry_run": True,
                "method": "POST",
                "path": DICE_BET_PATH,
                "payload": payload,
            }
        if not self.betting_enabled:
            raise WriteNotAllowed(
                "refusing to place a dice bet: real-money betting needs "
                "DuelClient(betting_enabled=True)."
            )
        if not confirm:
            raise WriteNotAllowed("refusing to place a dice bet: live bets need confirm=True.")
        if stake > Decimal(str(self.max_stake)):
            raise ValueError(
                f"stake {stake} exceeds this client's max_stake {self.max_stake}; "
                "raise max_stake explicitly if you mean it."
            )
        return self.request(
            "POST", DICE_BET_PATH, json_body=payload, confirm=True, _allow_money=True
        )

    # ----------------------------------------------------------- session health

    def session_status(self) -> dict[str, Any]:
        """Offline health summary of the loaded session. Makes no request.

        ``stale`` is tri-state: ``None`` means the session carries no timestamp
        at all, which is reported as "age unknown" rather than "fresh".
        """
        age = self.session.bot_cookie_age_seconds()
        stale = self.session.is_stale()
        cookies = self.session.cookies
        status: dict[str, Any] = {
            "device_uuid": self.session.device_uuid,
            "username": self.session.username,
            "user_id": self.session.user_id,
            "captured_at": self.session.captured_at,
            "cf_bm_at": self.session.cf_bm_at,
            "age_seconds": None if age is None else round(age, 1),
            "age_minutes": None if age is None else round(age / 60.0, 2),
            "ttl_minutes": round(CF_BM_TTL_SECONDS / 60.0, 1),
            "stale": stale,
            "has_bot_cookie": CF_BM_COOKIE in cookies,
            "has_duel_cookie": "duel" in cookies,
            # Evidence of *identity*, not merely of a cookie. A live probe shows
            # `duel` can be present with `user: null` - an issued-but-anonymous
            # session - so cookie presence must not be read as authenticated.
            "has_identity": self.session.username is not None
            or self.session.user_id is not None,
            "cookies_present": sorted(cookies),
        }
        if not status["has_duel_cookie"]:
            status["advice"] = (
                "no `duel` session cookie, so this session cannot authenticate. "
                "Capture a logged-in session with capture_session.py; login() "
                "cannot help without a Turnstile token from a real browser."
            )
        elif stale:
            # A definite expiry outranks the identity note: it is actionable now,
            # and reporting "unverified" here would hide an imminent 403.
            status["advice"] = (
                "bot cookie is past its TTL, so a 403 is likely. Re-capture with "
                "capture_session.py, or call metadata() to attempt a refresh."
            )
        elif stale is None:
            status["advice"] = (
                "session carries no timestamp, so its age is unknown - treat it "
                "as possibly expired. Re-capture to make future 403s diagnosable."
            )
        elif not status["has_identity"]:
            # build_session() records cookies, device uuid and timestamps but not
            # identity, and `auth` is not present in captured localStorage, so
            # this fires for every captured profile. Say what is actually known
            # rather than implying the session is anonymous.
            status["advice"] = (
                "profile holds a `duel` cookie within TTL, but capture does not "
                "record identity, so authentication cannot be confirmed offline. "
                "Run `whoami` - it is authoritative."
            )
        else:
            status["advice"] = "bot cookie is within TTL"
        return status

    # ---------------------------------------------------------------- persistence

    def save(self, path: Path | str | None = None) -> Path:
        """Persist cookies + device uuid + localStorage mirror to disk."""
        self._absorb_cookies()
        return self.session.save(path or self.profile_path)

    @classmethod
    def from_profile(
        cls, path: Path | str = DEFAULT_PROFILE, **kwargs: Any
    ) -> "DuelClient":
        """Build a client from a previously captured session file."""
        return cls(Session.load(path), profile=path, **kwargs)

    @classmethod
    def from_env(cls, **kwargs: Any) -> "DuelClient":
        """Build a client from a captured session, or an empty one if absent.

        ``DUEL_SESSION_FILE`` overrides the profile path.
        """
        profile = Path(os.environ.get("DUEL_SESSION_FILE", DEFAULT_PROFILE))
        session = Session.load(profile) if profile.exists() else Session()
        return cls(session, profile=profile, **kwargs)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "DuelClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()