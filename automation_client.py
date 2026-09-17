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

import json
import os
import uuid as _uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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

# XSRF pair configured by the bundle's axios defaults (kO). The cookie is
# mirrored into the header on non-GET requests, matching axios.
XSRF_COOKIE = "XSRF-TOKEN"
XSRF_HEADER = "X-XSRF-TOKEN"


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


@dataclass
class Session:
    """Persistable session state."""

    device_uuid: str = field(default_factory=lambda: str(_uuid.uuid4()))
    cookies: dict[str, str] = field(default_factory=dict)
    local_storage: dict[str, str] = field(default_factory=dict)
    username: str | None = None
    user_id: int | None = None
    captured_at: float | None = None

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
    ) -> None:
        # State-changing calls are opt-in. Read-only is the default posture.
        self.allow_writes = allow_writes
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
            # exact `domain="duel.com"` match would silently miss it.
            token = self._client.cookies.get(XSRF_COOKIE)
            if token:
                headers[XSRF_HEADER] = token

        if extra:
            headers.update(extra)
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
        confirm: bool = False,
    ) -> Any:
        """Perform one API call and return the decoded JSON body.

        Raises :class:`CloudflareChallenge` on 403 (stale bot cookie) and
        :class:`AuthRequired` on 401/419.
        """
        url = path if path.startswith("/") else f"{API_PREFIX}/{path}"
        # The guard lives here, not only in the named action methods, so that no
        # entry point (including the CLI `call` escape hatch) can bypass it.
        self._guard_write(url, method, confirm)
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
                raise CloudflareChallenge(
                    "Cloudflare challenged this request; the __cf_bm cookie is "
                    "likely stale. Re-capture the session (capture_session.py)."
                )
            raise DuelError(f"403 from {method} {url}: {body}")
        if response.status_code in (401, 419):
            raise AuthRequired(f"session not authenticated ({response.status_code} {method} {url})")
        if response.status_code >= 400:
            raise DuelError(f"{response.status_code} from {method} {url}: {response.text[:300]}")

        # Mirror server cookie rotation back into the persisted session.
        self._absorb_cookies()

        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def _absorb_cookies(self) -> None:
        for name in SESSION_COOKIES:
            # Name-only, for the same reason as the XSRF lookup above.
            value = self._client.cookies.get(name)
            if value:
                self.session.cookies[name] = value

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
    # (verified against the shipped service definitions) and are account
    # management only - deliberately *not* wagering or money movement.

    def _guard_write(self, path: str, method: str, confirm: bool) -> None:
        """Enforce the write policy for every outgoing call.

        Policy:

        * reads (GET/HEAD/OPTIONS) are always allowed;
        * money-moving paths are refused outright, regardless of opt-in;
        * token/session lifecycle POSTs listed in ``_READ_LIKE_POSTS`` are
          allowed, since login is already gated by :class:`CaptchaRequired`;
        * every other state-changing call needs an explicit opt-in.
        """
        bare = path.split("?")[0].lower()
        for verb in _MONEY_MOVING:
            if verb in bare:
                raise UnsupportedAction(
                    f"'{verb}' endpoints are deliberately out of scope for this client "
                    "(real-money site; see site_spec.json metadata.out_of_scope)."
                )

        if method.upper() in ("GET", "HEAD", "OPTIONS"):
            return
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
        self._guard_write("/api/v2/user/security/two-factor-setup", confirm)
        return self.request("POST", "/api/v2/user/security/two-factor-setup", json_body={})

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