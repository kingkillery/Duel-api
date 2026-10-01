"""Server-side holder for a Duel bet token captured from the operator's own Chrome.

This module never mints a token and never places a wager. It has no hardcoded
authorization code and never calls the mint endpoint. The live Dice bundle mints
a bet token lazily; login alone does not produce one. Passive capture can only
hold a token while the operator's page is obtaining it; it cannot recover old responses.

Capture listens to ONE chosen mainframe response: an exact HTTPS
``https://duel.com/api/v2/user/security/token`` POST that succeeded. It reads the
token and its real expiry from that response and the token type from the
originating request body or explicit response type. Only ``standard`` and
``silent`` with a verified finite time-to-live are accepted; ``onetime``,
``session-auth``, and unknown types are rejected. Bet-request observation is not
used: a token seen only inside a bet has no verified expiry and must not be held.

The CDP URL is server configuration only (``DUEL_TRADE_CDP_URL``), must be an
HTTPS-less loopback http URL with no path, and can never be supplied by a
request. Nothing prints a token, writes one to disk, or returns one to a browser.
"""

from __future__ import annotations

import hashlib
import math
import os
import threading
import time
from typing import Any
from urllib.parse import urlsplit

CDP_ENV = "DUEL_TRADE_CDP_URL"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
SITE = "duel.com"
TOKEN_POST_MARK = "/api/v2/user/security/token"
MIN_TOKEN_LEN = 20
MIN_TTL_SECONDS = 30
MAX_TTL_SECONDS = 7 * 24 * 60 * 60
CONNECT_TIMEOUT_MS = 5000
MAX_LISTEN_SECONDS = 300.0
ACCEPTED_TYPES = {"standard", "silent"}

_lock = threading.Lock()
_capture_lock = threading.Lock()
_held: dict[str, Any] | None = None
_state = "missing"
_detail = "no bet token is held; login alone does not mint one"
_listen_deadline = 0.0


class TokenCaptureError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def cdp_url() -> str:
    return os.environ.get(CDP_ENV, "").strip() or DEFAULT_CDP_URL


def assert_loopback_cdp(url: str) -> str:
    """Accept only a bare loopback DevTools http URL: no path, query, or userinfo."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise TokenCaptureError("cdp", f"{CDP_ENV} is not a URL the desk can attach to") from exc
    if parts.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost", "::1"}:
        raise TokenCaptureError("cdp", f"{CDP_ENV} must be loopback")
    if port is None:
        raise TokenCaptureError("cdp", f"{CDP_ENV} must name the DevTools port")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise TokenCaptureError("cdp", f"{CDP_ENV} must not carry credentials, a query, or a fragment")
    if parts.path not in {"", "/"}:
        raise TokenCaptureError("cdp", f"{CDP_ENV} must not carry a path")
    literal = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{literal}:{port}"


def public_state() -> dict[str, Any]:
    """Status safe to return to the browser. Never includes the token."""
    with _lock:
        held = _held
        state = _state
        detail = _detail
        deadline = _listen_deadline
    if held is None:
        result: dict[str, Any] = {"token_state": state, "token_detail": detail}
        if state == "listening":
            result["listen_seconds_remaining"] = max(0, math.ceil(deadline - time.monotonic()))
        return result
    expires = held.get("expires_at")
    if isinstance(expires, (int, float)) and time.time() >= expires - MIN_TTL_SECONDS:
        return {
            "token_state": "expired",
            "token_detail": "the held bet token is expired or inside the safety margin; capture another after the page mints one",
        }
    return {
        "token_state": "held",
        "token_detail": "a bet token is held in server memory for the configured profile; it is not returned to the browser",
    }


def current_token(fingerprint: str) -> str:
    """Return the held token only when it matches this profile. Never log it."""
    with _lock:
        held = dict(_held) if _held else None
    if not held:
        raise TokenCaptureError("token", _detail)
    if held.get("fingerprint") != fingerprint:
        raise TokenCaptureError("token", "the held bet token belongs to a different Duel session")
    if held.get("token_type") not in ACCEPTED_TYPES:
        raise TokenCaptureError("token", "the held bet token is not a reusable type for this desk")
    expires = held.get("expires_at")
    if not _valid_expiry(expires):
        raise TokenCaptureError("token", "the held bet token has no verified expiry")
    if time.time() >= float(expires) - MIN_TTL_SECONDS:
        raise TokenCaptureError("token", "the held bet token is expired or inside the safety margin")
    token = held.get("token")
    if not isinstance(token, str) or len(token) < MIN_TOKEN_LEN:
        raise TokenCaptureError("token", "the held bet token is not usable")
    return token


def remember(
    token: str,
    *,
    fingerprint: str,
    expires_at: float | None,
    token_type: str,
    source: str,
) -> None:
    """Hold one captured token. Unknown type or expiry is refused."""
    if not isinstance(token, str) or len(token) < MIN_TOKEN_LEN:
        raise TokenCaptureError("token", "captured value is not a bet token")
    if token_type not in ACCEPTED_TYPES:
        raise TokenCaptureError("token", "captured token type is not accepted for reuse")
    if not _valid_expiry(expires_at):
        raise TokenCaptureError("token", "captured token has no verified usable expiry")
    if time.time() >= float(expires_at) - MIN_TTL_SECONDS:
        raise TokenCaptureError("token", "captured token is expired or inside the safety margin")
    if not fingerprint:
        raise TokenCaptureError("token", "captured token is not bound to a Duel session")
    held = {
        "token": token,
        "fingerprint": fingerprint,
        "expires_at": float(expires_at),
        "token_type": token_type,
        "source": source,
    }
    global _held, _state, _detail
    with _lock:
        _held = held
        _state = "held"
        _detail = "held in server memory"
    del token


def note_failure(code: str, message: str, *, listen_seconds: float = 0) -> None:
    """Record capture progress/failure and drop any older token."""
    global _held, _state, _detail, _listen_deadline
    with _lock:
        _held = None
        _state = code
        _detail = message
        _listen_deadline = time.monotonic() + listen_seconds if code == "listening" else 0.0


def clear() -> None:
    global _held, _state, _detail
    with _lock:
        _held = None
        _state = "missing"
        _detail = "no bet token is held; login alone does not mint one"


def profile_fingerprint(cookies: dict[str, str]) -> str:
    """SHA-256 of the configured profile's auth cookie, so no cookie value is compared raw."""
    value = cookies.get("do_not_share_this_with_anyone_not_even_staff", "").strip()
    if not value:
        raise TokenCaptureError("session", "the configured profile has no Duel auth cookie")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_expiry(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if not math.isfinite(float(value)):
        return False
    return float(value) > 0


def _https_duel(url: str) -> bool:
    parts = urlsplit(url or "")
    return parts.scheme == "https" and (parts.hostname or "").lower() in {SITE, f"www.{SITE}"}


def _duel_pages(browser: Any) -> list[Any]:
    pages = []
    for context in browser.contexts:
        for page in context.pages:
            if _https_duel(page.url or ""):
                pages.append(page)
    return pages


def _cookie_map(cookies: list[dict[str, Any]]) -> dict[str, str]:
    found: dict[str, str] = {}
    for cookie in cookies:
        domain = str(cookie.get("domain") or "").lstrip(".").lower()
        if domain == SITE or domain.endswith(f".{SITE}"):
            found[str(cookie.get("name") or "")] = str(cookie.get("value") or "")
    return found


def _parse_response(response: Any) -> dict[str, Any] | None:
    """Read one successful mint response into a validated token record, or None."""
    try:
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict) or body.get("success") is not True:
        return None
    token = body.get("token")
    if not isinstance(token, str) or len(token) < MIN_TOKEN_LEN:
        return None
    ttl = body.get("expires_in")
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(float(ttl)):
        return None
    ttl = float(ttl)
    if not (MIN_TTL_SECONDS < ttl <= MAX_TTL_SECONDS):
        return None
    token_type = ""
    try:
        request = response.request
        if request.method == "POST":
            payload = request.post_data_json
            if isinstance(payload, dict):
                token_type = str(payload.get("type") or "")
    except Exception:
        token_type = ""
    if not token_type:
        token_type = str(body.get("token_type") or body.get("type") or "")
    if token_type not in ACCEPTED_TYPES:
        return None
    return {"token": token, "expires_at": time.time() + ttl, "token_type": token_type}


def capture_from_cdp(fingerprint: str, *, timeout_s: float | None = None) -> dict[str, Any]:
    """Attach to the operator's Chrome and hold a token only from a real mint response.

    This does not mint, does not click, and does not place a wager. It cannot
    create a token on its own; the operator's own logged-in page must have run
    its token action. When nothing is observed the result says the operator must
    use that page. A later verified non-wager prepare action can be added only
    once its payload is proven.
    """
    url = assert_loopback_cdp(cdp_url())
    if not fingerprint:
        raise TokenCaptureError("session", "no profile session is available to bind a token to")
    if not _capture_lock.acquire(blocking=False):
        raise TokenCaptureError("busy", "a token capture is already running")
    try:
        note_failure("connecting", "connecting to the configured Chrome window; no wager will be sent")
        return _capture_locked(url, fingerprint, MAX_LISTEN_SECONDS if timeout_s is None else min(timeout_s, MAX_LISTEN_SECONDS))
    finally:
        _capture_lock.release()


def _capture_locked(url: str, fingerprint: str, timeout_s: float) -> dict[str, Any]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        note_failure("cdp", "playwright is not installed; cannot attach to the operator's Chrome")
        raise TokenCaptureError("cdp", "playwright is required to read the operator's logged-in Chrome") from exc
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(url, timeout=CONNECT_TIMEOUT_MS)
            pages = _duel_pages(browser)
            if not pages:
                raise TokenCaptureError(
                    "cdp",
                    f"no logged-in https://{SITE} tab is open; open that page and log in by hand",
                )
            page = next((item for item in pages if "/dice" in urlsplit(item.url).path), pages[0])
            if profile_fingerprint(_cookie_map(page.context.cookies())) != fingerprint:
                raise TokenCaptureError(
                    "session",
                    "the logged-in Chrome tab is a different Duel session than the configured profile",
                )
            observed = _observe_mint(page, timeout_s)
            if observed is None:
                raise TokenCaptureError(
                    "operator-action-required",
                    "passive listen saw no token response. This desk cannot mint one. "
                    "Capture must be listening while the page obtains a token; old responses cannot be recovered. "
                    "do not place a wager just to test this desk",
                )
            if profile_fingerprint(_cookie_map(page.context.cookies())) != fingerprint:
                raise TokenCaptureError("session", "the Duel session changed during capture; token refused")
    except TokenCaptureError as exc:
        note_failure(exc.code, exc.message)
        raise
    except Exception as exc:
        message = f"could not attach to Chrome: {type(exc).__name__}"
        note_failure("cdp", message)
        raise TokenCaptureError("cdp", message) from exc
    try:
        remember(
            observed["token"],
            fingerprint=fingerprint,
            expires_at=observed["expires_at"],
            token_type=observed["token_type"],
            source="operator-page-mint",
        )
    except TokenCaptureError as exc:
        note_failure(exc.code, exc.message)
        raise
    return public_state()


def _observe_mint(page: Any, timeout_s: float) -> dict[str, Any] | None:
    """Listen on this page's mainframe only for one successful token POST response."""
    found: dict[str, Any] = {}

    def on_response(response: Any) -> None:
        if found:
            return
        if response.request.method != "POST" or not _https_duel(response.url):
            return
        if TOKEN_POST_MARK != urlsplit(response.url).path:
            return
        if response.request.frame != page.main_frame:
            return
        try:
            if response.status < 200 or response.status >= 300:
                return
        except Exception:
            return
        parsed = _parse_response(response)
        if parsed:
            found.update(parsed)

    page.on("response", on_response)
    note_failure("listening", "listening for the site's token response; this desk sends no wager or mint request", listen_seconds=timeout_s)
    try:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not found:
            page.wait_for_timeout(200)
    finally:
        page.remove_listener("response", on_response)
    return found or None
