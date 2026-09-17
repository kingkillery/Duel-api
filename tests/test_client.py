"""Client behaviour tests, driven by a mocked HTTP transport (no network)."""

from __future__ import annotations

import json
import re
import tempfile
import time
from pathlib import Path

import httpx
import pytest

from automation_client import (
    AuthRequired,
    CaptchaRequired,
    CloudflareChallenge,
    CF_BM_TTL_SECONDS,
    DEFAULT_RETRY_AFTER_CEILING,
    DuelClient,
    DuelError,
    RateLimited,
    Session,
    UnsupportedAction,
    WriteNotAllowed,
)

SAMPLE_METADATA = {
    "session_id": 123,
    "user": None,
    "features": {"captcha_on_login": {"enabled": True}},
    "turnstile_sitekey": "0xABC",
    "socket_token": "jwt",
}


def make_client(
    handler,
    session: Session | None = None,
    tmp_path: Path | None = None,
    **kwargs,
) -> DuelClient:
    # Never default to the repo root: login() persists a session file, and a
    # stray session.json in the working tree would leak real credentials.
    base = Path(tmp_path) if tmp_path is not None else Path(tempfile.mkdtemp())
    return DuelClient(
        session,
        profile=base / "session.json",
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_metadata_sends_device_identifier_and_uuid_param() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["device"] = request.headers.get("x-duel-device-identifier")
        seen["env"] = request.headers.get("x-env-class")
        return httpx.Response(200, json=SAMPLE_METADATA, headers={"set-cookie": "duel=sess123; Path=/"})

    with make_client(handler) as client:
        client.session.device_uuid = "11111111-2222-4333-8444-555555555555"
        doc = client.metadata()

    assert doc["session_id"] == 123
    assert "uuid=11111111-2222-4333-8444-555555555555" in seen["url"]
    assert seen["device"] == "11111111-2222-4333-8444-555555555555"
    assert seen["env"] == "main"


def test_metadata_is_what_issues_the_session_cookie() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_METADATA, headers={"set-cookie": "duel=sess123; Path=/"})

    with make_client(handler) as client:
        assert client.session.cookies == {}
        client.metadata()
        assert client.session.cookies["duel"] == "sess123"


def test_login_refuses_without_a_captcha_token() -> None:
    """captcha_on_login is on; the client must not pretend it can log in alone."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        calls.append(request)
        return httpx.Response(200, json={})

    with make_client(handler) as client:
        with pytest.raises(CaptchaRequired):
            client.login("testuser", "pw", captcha_token="")
    assert calls == [], "no request should be attempted without a captcha token"


def test_login_uses_username_field_for_non_email_identifier() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json=SAMPLE_METADATA, headers={"set-cookie": "duel=s; Path=/"})
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"user": {"id": 7, "username": "testuser"}})

    with make_client(handler) as client:
        client.login("testuser", "pw", captcha_token="TOK")

    payload = bodies[-1]
    assert payload["username"] == "testuser"
    assert "email" not in payload
    assert payload["password"] == "pw"
    # The SPA sends the captcha as a {type, token} pair, not {turnstile_token}.
    assert payload["type"] == "turnstile_token"
    assert payload["token"] == "TOK"


def test_login_uses_email_field_for_email_identifier() -> None:
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json=SAMPLE_METADATA, headers={"set-cookie": "duel=s; Path=/"})
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"user": {"id": 1, "username": "x"}})

    with make_client(handler) as client:
        client.login("me@example.com", "pw", captcha_token="TOK")

    assert bodies[-1]["email"] == "me@example.com"
    assert "username" not in bodies[-1]


def test_login_bootstraps_metadata_first() -> None:
    """The cookie-jar session must exist before login binds to it."""
    order = []

    def handler(request: httpx.Request) -> httpx.Response:
        order.append(request.url.path)
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json=SAMPLE_METADATA, headers={"set-cookie": "duel=s; Path=/"})
        return httpx.Response(200, json={"user": {"id": 1, "username": "x"}})

    with make_client(handler) as client:
        client.login("x", "pw", captcha_token="TOK")

    assert order[0].endswith("/api/v2/metadata")
    assert order[1].endswith("/api/v2/auth/login")


def test_login_records_username_and_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json=SAMPLE_METADATA, headers={"set-cookie": "duel=s; Path=/"})
        return httpx.Response(200, json={"user": {"id": 42, "username": "testuser"}})

    with make_client(handler) as client:
        client.login("testuser", "pw", captcha_token="TOK")
        assert client.session.username == "testuser"
        assert client.session.user_id == 42


def test_403_with_cf_mitigated_header_and_json_body_is_a_challenge() -> None:
    """Header *presence* alone must trigger the recovery path.

    Cloudflare's real value is "challenge", which does not contain the substring
    "cf" - so a substring check on the value silently fails whenever the body is
    not HTML, which is exactly the case the recovery ladder has to handle.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "Just a moment..."},
            headers={"cf-mitigated": "challenge"},
        )

    with make_client(handler) as client:
        with pytest.raises(CloudflareChallenge):
            client.profile()


def test_403_with_html_block_page_is_a_challenge_even_without_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="<html><body>Attention Required</body></html>")

    with make_client(handler) as client:
        with pytest.raises(CloudflareChallenge):
            client.profile()


def test_403_without_cloudflare_markers_is_a_plain_error() -> None:
    """A genuine 403 must not be misreported as a stale bot cookie."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden"})

    with make_client(handler) as client:
        with pytest.raises(DuelError) as exc:
            client.profile()
    assert not isinstance(exc.value, CloudflareChallenge)


def test_401_is_reported_as_auth_required() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthenticated"})

    with make_client(handler) as client:
        with pytest.raises(AuthRequired):
            client.profile()


def test_generic_4xx_is_a_plain_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"message": "bad"})

    with make_client(handler) as client:
        with pytest.raises(DuelError) as exc:
            client.profile()
    assert not isinstance(exc.value, (AuthRequired, CloudflareChallenge))


def test_games_uses_start_and_filter_not_page_and_per_page() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"games": []})

    with make_client(handler) as client:
        client.games(start=20, game_filter="popular")

    assert seen["params"]["start"] == "20"
    assert seen["params"]["filter"] == "popular"
    assert "page" not in seen["params"]
    assert "per_page" not in seen["params"]


def test_restored_cookies_are_attached_to_the_first_request() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json=SAMPLE_METADATA)

    session = Session(
        device_uuid="11111111-2222-4333-8444-555555555555",
        cookies={"duel": "restored", "__cf_bm": "bm"},
    )
    with make_client(handler, session) as client:
        client.metadata()

    assert "duel=restored" in seen["cookie"]
    assert "__cf_bm=bm" in seen["cookie"]


def test_is_authenticated_reflects_the_server_user_object() -> None:
    state = {"user": None}

    def handler(request: httpx.Request) -> httpx.Response:
        body = dict(SAMPLE_METADATA)
        body["user"] = state["user"]
        return httpx.Response(200, json=body)

    with make_client(handler) as client:
        assert client.is_authenticated is False
        state["user"] = {"id": 1, "username": "testuser"}
        assert client.is_authenticated is True


def test_session_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    original = Session(
        device_uuid="11111111-2222-4333-8444-555555555555",
        cookies={"duel": "abc"},
        local_storage={"security:uuid": "11111111-2222-4333-8444-555555555555"},
        username="testuser",
        user_id=42,
    )
    original.save(path)

    restored = Session.load(path)
    assert restored == original


def test_save_absorbs_rotated_cookies(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_METADATA, headers={"set-cookie": "duel=rotated; Path=/"})

    path = tmp_path / "session.json"
    with make_client(handler, tmp_path=tmp_path) as client:
        client.metadata()
        client.save()

    assert Session.load(path).cookies["duel"] == "rotated"


def test_unexpected_session_file_keys_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"cookies": {"duel": "x"}, "surprise": 1}), encoding="utf-8")
    assert Session.load(path).cookies == {"duel": "x"}


# --------------------------------------------------------------------- actions
#
# State-changing calls are opt-in, and money-moving paths are refused outright.


def _recording_client(seen: list, *, allow_writes: bool) -> DuelClient:
    """Record (method, path, parsed JSON body) for each outgoing request."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"ok": True})

    return DuelClient(
        profile=Path(tempfile.mkdtemp()) / "session.json",
        transport=httpx.MockTransport(handler),
        allow_writes=allow_writes,
    )


def test_writes_are_refused_by_default() -> None:
    seen: list = []
    with _recording_client(seen, allow_writes=False) as client:
        with pytest.raises(WriteNotAllowed):
            client.update_settings({"volume": 0})
    assert seen == [], "no request may be sent when writes are not opted into"


def test_writes_allowed_with_allow_writes() -> None:
    seen: list = []
    with _recording_client(seen, allow_writes=True) as client:
        client.update_settings({"volume": 0})
    assert seen == [("PATCH", "/api/v2/user/settings", {"settings": {"volume": 0}})]


def test_writes_allowed_with_per_call_confirm() -> None:
    seen: list = []
    with _recording_client(seen, allow_writes=False) as client:
        client.set_client_seed("abc", confirm=True)
    assert seen == [("POST", "/api/v2/client-seed", {"client_seed": "abc"})]


def test_seed_rotate_uses_the_rotate_path() -> None:
    seen: list = []
    with _recording_client(seen, allow_writes=True) as client:
        client.rotate_client_seed("xyz")
    assert seen == [("POST", "/api/v2/client-seed/rotate", {"client_seed": "xyz"})]


def test_security_token_sends_the_device_uuid() -> None:
    seen: list = []
    with _recording_client(seen, allow_writes=True) as client:
        client.session.device_uuid = "11111111-2222-4333-8444-555555555555"
        client.security_token(token_type="standard")
    method, path, body = seen[0]
    assert (method, path) == ("POST", "/api/v2/user/security/token")
    assert body == {
        "uuid": "11111111-2222-4333-8444-555555555555",
        "type": "standard",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/games/bet",
        "/api/v2/trade/crypto/withdraw",
        "/api/v2/trade/deposit",
        "/api/v2/roulette/wager",
    ],
)
def test_money_moving_paths_are_refused_even_with_writes_enabled(path: str) -> None:
    """Defence in depth behind site_spec.json metadata.out_of_scope."""
    seen: list = []
    with _recording_client(seen, allow_writes=True) as client:
        with pytest.raises(UnsupportedAction):
            client.request("POST", path, json_body={})
    assert seen == []


def _betting_client(seen: list, **kwargs) -> DuelClient:
    """Recording client with betting explicitly enabled (max_stake 10)."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.method, request.url.path, body))
        return httpx.Response(200, json={"ok": True})

    params = {"allow_writes": True, "betting_enabled": True, "max_stake": 10.0}
    params.update(kwargs)
    return DuelClient(
        profile=Path(tempfile.mkdtemp()) / "session.json",
        transport=httpx.MockTransport(handler),
        **params,
    )


def test_dice_bet_dry_run_validates_and_sends_nothing() -> None:
    """The default is zero-risk: validate, return the payload, send nothing."""
    seen: list = []
    with _recording_client(seen, allow_writes=False) as client:
        result = client.place_dice_bet(
            "0.5", bet_type="under", currency="USDT", target="5005"
        )
    assert seen == []
    assert result["dry_run"] is True
    assert result["method"] == "POST"
    assert result["path"] == "/api/v2/dice/bet"
    assert result["payload"] == {
        "amount": "0.5",
        "bet_type": "UNDER",
        "currency": "USDT",
        "security_token": "",
        "target": "5005",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": "0", "bet_type": "OVER", "currency": "USDT", "target": "5005"},
        {"amount": "-1", "bet_type": "OVER", "currency": "USDT", "target": "5005"},
        {"amount": "abc", "bet_type": "OVER", "currency": "USDT", "target": "5005"},
        {"amount": "0.5", "bet_type": "SIDEWAYS", "currency": "USDT", "target": "5005"},
        {"amount": "0.5", "bet_type": "OVER", "currency": "", "target": "5005"},
        {"amount": "0.5", "bet_type": "OVER", "currency": "USDT", "target": "50.05"},
        {"amount": "0.5", "bet_type": "OVER", "currency": "USDT", "target": "99"},
        {"amount": "0.5", "bet_type": "OVER", "currency": "USDT", "target": "9900"},
    ],
)
def test_dice_bet_rejects_bad_parameters_before_anything_else(kwargs) -> None:
    """Validation runs first: even dry runs reject bad parameters."""
    seen: list = []
    with _recording_client(seen, allow_writes=False) as client:
        with pytest.raises(ValueError):
            client.place_dice_bet(**kwargs)
    assert seen == []


def test_dice_bet_live_needs_betting_enabled_and_confirm() -> None:
    """Double opt-in: betting_enabled on the client AND confirm per call."""
    seen: list = []
    with _recording_client(seen, allow_writes=True) as client:
        with pytest.raises(WriteNotAllowed):
            client.place_dice_bet(
                "0.5", bet_type="OVER", currency="USDT", target="5005", dry_run=False
            )
    with _recording_client(seen, allow_writes=True) as client:
        client.betting_enabled = True
        with pytest.raises(WriteNotAllowed):
            client.place_dice_bet(
                "0.5", bet_type="OVER", currency="USDT", target="5005", dry_run=False
            )
    assert seen == []


def test_dice_bet_live_enforces_max_stake() -> None:
    seen: list = []
    with _betting_client(seen) as client:
        with pytest.raises(ValueError, match="max_stake"):
            client.place_dice_bet(
                "50", bet_type="OVER", currency="USDT", target="5005",
                confirm=True, dry_run=False,
            )
    assert seen == []


def test_dice_bet_live_sends_the_bundle_verified_payload() -> None:
    seen: list = []
    with _betting_client(seen) as client:
        result = client.place_dice_bet(
            "0.5", bet_type="OVER", currency="USDT", target="5005",
            security_token="tok", confirm=True, dry_run=False,
        )
    assert seen == [
        (
            "POST",
            "/api/v2/dice/bet",
            {
                "amount": "0.5",
                "bet_type": "OVER",
                "currency": "USDT",
                "security_token": "tok",
                "target": "5005",
            },
        )
    ]
    assert result == {"ok": True}


def test_generic_request_still_refuses_the_dice_bet_path() -> None:
    """The escape hatch stays shut: only place_dice_bet() may bet."""
    seen: list = []
    with _recording_client(seen, allow_writes=True) as client:
        client.betting_enabled = True
        with pytest.raises(UnsupportedAction):
            client.request("POST", "/api/v2/dice/bet", json_body={})
    assert seen == []


def test_dice_config_is_read_only() -> None:
    seen: list = []
    with _recording_client(seen, allow_writes=False) as client:
        client.dice_config()
    assert seen == [("GET", "/api/v2/dice/config", None)]


def test_socket_token_post_is_allowed_without_opt_in() -> None:
    """Spec recovery_ladder.refresh_tokens must work on a default (read-only) client.

    POST /api/v2/metadata/socket-token fetches a short-lived realtime credential;
    it is token lifecycle, not an account mutation, so it must not require the
    write opt-in.
    """
    seen: list = []
    with _recording_client(seen, allow_writes=False) as client:
        client.socket_token()
    assert seen == [("POST", "/api/v2/metadata/socket-token", {})]


def test_xsrf_cookie_is_mirrored_into_header_only_on_writes() -> None:
    """Mirror axios: copy XSRF-TOKEN -> X-XSRF-TOKEN on non-GET requests."""
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.headers.get("x-xsrf-token")))
        return httpx.Response(200, json={"ok": True})

    session = Session(cookies={"duel": "s", "XSRF-TOKEN": "tok123"})
    client = DuelClient(
        session,
        profile=Path(tempfile.mkdtemp()) / "session.json",
        transport=httpx.MockTransport(handler),
        allow_writes=True,
    )
    with client:
        client.profile()                       # GET  -> no header
        client.update_settings({"volume": 0})  # PATCH -> header

    assert seen == [("GET", None), ("PATCH", "tok123")]


# ----------------------------------------------------------------- session age


def test_staleness_is_tri_state_and_never_guesses_fresh() -> None:
    """An untimestamped profile reports unknown, not fresh.

    Every profile captured before timestamps were stamped has neither field.
    Reading that as "fresh" would hide exactly the 403 this exists to explain.
    """
    assert Session().is_stale() is None
    assert Session().bot_cookie_age_seconds() is None

    now = 1_000_000.0
    fresh = Session(captured_at=now, cf_bm_at=now)
    assert fresh.is_stale(now=now + 60) is False
    assert fresh.bot_cookie_age_seconds(now=now + 60) == pytest.approx(60.0)

    old = Session(captured_at=now, cf_bm_at=now)
    assert old.is_stale(now=now + CF_BM_TTL_SECONDS + 1) is True


def test_bot_cookie_clock_prefers_cf_bm_over_captured_at() -> None:
    """A refreshed bot cookie makes an old capture usable again."""
    now = 1_000_000.0
    session = Session(captured_at=now, cf_bm_at=now + 3600)
    # 10 min after the refresh: captured_at would say 70 min, cf_bm_at says 10.
    assert session.bot_cookie_age_seconds(now=now + 4200) == pytest.approx(600.0)
    assert session.is_stale(now=now + 4200) is False
    assert session.age_seconds(now=now + 4200) == pytest.approx(4200.0)


def test_captured_at_alone_is_enough_to_date_a_legacy_profile() -> None:
    """Pre-cf_bm_at profiles fall back to capture time rather than reporting unknown."""
    now = 1_000_000.0
    legacy = Session(captured_at=now, cf_bm_at=None)
    assert legacy.bot_cookie_age_seconds(now=now + 100) == pytest.approx(100.0)
    assert legacy.is_stale(now=now + 100) is False


def test_session_timestamps_survive_a_json_round_trip() -> None:
    session = Session(
        cookies={"duel": "s", "__cf_bm": "bm"}, captured_at=123.5, cf_bm_at=456.5
    )
    restored = Session.from_json(session.to_json())
    assert restored.captured_at == 123.5
    assert restored.cf_bm_at == 456.5
    assert restored.is_stale(now=456.5 + 60) is False


def test_rotated_bot_cookie_restarts_the_ttl_clock() -> None:
    """A server-issued __cf_bm must stamp cf_bm_at, or staleness never resets."""
    before = time.time()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=SAMPLE_METADATA, headers={"set-cookie": "__cf_bm=fresh; Path=/"}
        )

    with make_client(handler) as client:
        assert client.session.cf_bm_at is None
        client.metadata()
        assert client.session.cookies["__cf_bm"] == "fresh"
        assert client.session.cf_bm_at is not None
        assert client.session.cf_bm_at >= before
        assert client.session.is_stale() is False


def test_unchanged_bot_cookie_does_not_restart_the_clock() -> None:
    """Only a *rotation* refreshes the TTL, not every response that echoes it."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SAMPLE_METADATA)

    session = Session(cookies={"__cf_bm": "same"}, captured_at=100.0)
    with make_client(handler, session) as client:
        client.metadata()
        assert client.session.cookies["__cf_bm"] == "same"
        assert client.session.cf_bm_at is None
        assert client.session.bot_cookie_age_seconds(now=100.0 + CF_BM_TTL_SECONDS + 5) is not None
        assert client.session.is_stale(now=100.0 + CF_BM_TTL_SECONDS + 5) is True


# ---------------------------------------------------------------- auto-refresh


def _challenge() -> httpx.Response:
    return httpx.Response(
        403, json={"message": "Just a moment..."}, headers={"cf-mitigated": "challenge"}
    )


def test_auto_refresh_recovers_a_stale_bot_cookie_and_retries_once() -> None:
    """The recovery ladder's reload_cache step, automated: 403 -> metadata -> retry."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/metadata") and len(calls) == 2:
            return httpx.Response(
                200, json=SAMPLE_METADATA, headers={"set-cookie": "__cf_bm=rotated; Path=/"}
            )
        if len(calls) == 1:
            return _challenge()
        return httpx.Response(200, json={"id": 7})

    with make_client(handler) as client:
        assert client.profile() == {"id": 7}

    assert calls == [
        "/api/v2/user",        # original request, challenged
        "/api/v2/metadata",    # refresh: re-mint __cf_bm
        "/api/v2/user",        # retry, now succeeds
    ]


def test_auto_refresh_retries_exactly_once_and_never_loops() -> None:
    """A persistent challenge must terminate, not recurse."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _challenge()

    with make_client(handler) as client:
        with pytest.raises(CloudflareChallenge):
            client.profile()

    assert len(calls) == 2, f"expected one retry, got {calls}"


def test_a_challenge_during_refresh_does_not_recurse() -> None:
    """metadata() is itself a request; its own 403 must not start another refresh.

    Bounded at exactly two calls: the original plus one refresh attempt. Without
    the ``_refreshing`` re-entrancy guard this recurses until the stack blows,
    since every refresh is itself a request that can be challenged.
    """
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _challenge()

    with make_client(handler) as client:
        with pytest.raises(CloudflareChallenge):
            client.metadata()
    assert calls == ["/api/v2/metadata"] * 2, "one refresh attempt, never recursed"


def test_auto_refresh_can_be_disabled_per_client_and_per_call() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _challenge()

    calls: list[str] = []
    with make_client(handler, auto_refresh=False) as client:
        with pytest.raises(CloudflareChallenge):
            client.profile()
    assert calls == ["/api/v2/user"]

    calls.clear()
    with make_client(handler) as client:
        with pytest.raises(CloudflareChallenge):
            client.request("GET", "/api/v2/user/profile", auto_refresh=False)
    assert calls == ["/api/v2/user/profile"]


def test_cloudflare_error_reports_how_old_the_session_actually_is() -> None:
    """The 403 message should be self-diagnosing rather than generic."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _challenge()

    session = Session(captured_at=time.time() - 90 * 60, cf_bm_at=time.time() - 90 * 60)
    with make_client(handler, session, auto_refresh=False) as client:
        with pytest.raises(CloudflareChallenge) as exc:
            client.profile()
    message = str(exc.value)
    assert "past" in message and "~30 min TTL" in message
    # Format is "<n>.n min old"; assert the number is present and sane rather
    # than matching a literal that drifts with the clock.
    reported = re.search(r"(\d+\.\d) min old", message)
    assert reported, f"age missing from: {message}"
    assert 89.0 <= float(reported.group(1)) <= 91.0


def test_cloudflare_error_says_age_unknown_rather_than_guessing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _challenge()

    with make_client(handler, Session(), auto_refresh=False) as client:
        with pytest.raises(CloudflareChallenge) as exc:
            client.profile()
    assert "no timestamp" in str(exc.value)


# -------------------------------------------------------------- rate limiting


def test_429_honours_retry_after_and_stays_bounded(tmp_path: Path) -> None:
    """Retry-After is obeyed; the request count is asserted exactly, not assumed."""
    responses = iter(
        [
            httpx.Response(429, json={"message": "slow down"}, headers={"Retry-After": "0"}),
            httpx.Response(429, json={"message": "slow down"}, headers={"Retry-After": "0"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return next(responses)

    sleeps: list[float] = []
    client = make_client(handler, tmp_path=tmp_path, sleep=sleeps.append)
    assert client.request("GET", "/api/v2/user/profile") == {"ok": True}
    assert len(seen) == 3, f"expected exactly 3 requests, got {len(seen)}"
    assert sleeps == [0.0, 0.0]


def test_persistent_429_raises_rate_limited_with_the_delay_capped(tmp_path: Path) -> None:
    """Above the ceiling the client backs off capped, then raises with the reason."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(429, json={"message": "no"}, headers={"Retry-After": "99"})

    sleeps: list[float] = []
    client = make_client(handler, tmp_path=tmp_path, sleep=sleeps.append)
    with pytest.raises(RateLimited) as excinfo:
        client.request("GET", "/api/v2/user/profile")
    assert len(calls) == 3, f"expected exactly 3 requests, got {len(calls)}"
    assert sleeps == [DEFAULT_RETRY_AFTER_CEILING] * 2
    assert excinfo.value.retry_after == 99.0


# -------------------------------------------------------------- session status


def test_session_status_makes_no_request() -> None:
    """Offline by design: a status check must not consume bot-cookie TTL."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=SAMPLE_METADATA)

    session = Session(
        cookies={"duel": "s", "__cf_bm": "bm"},
        username="alice",
        user_id=42,
        captured_at=time.time(),
        cf_bm_at=time.time(),
    )
    with make_client(handler, session) as client:
        status = client.session_status()

    assert calls == []
    assert status["has_duel_cookie"] is True
    assert status["has_bot_cookie"] is True
    assert status["stale"] is False
    assert status["username"] == "alice"
    assert status["user_id"] == 42
    assert status["ttl_minutes"] == pytest.approx(30.0)
    assert status["age_minutes"] is not None
    assert set(status["cookies_present"]) == {"duel", "__cf_bm"}


def test_session_status_flags_an_expired_session() -> None:
    old = time.time() - CF_BM_TTL_SECONDS - 60
    session = Session(cookies={"duel": "s"}, captured_at=old, cf_bm_at=old)
    with make_client(lambda r: httpx.Response(200, json={}), session) as client:
        status = client.session_status()
    assert status["stale"] is True
    assert "TTL" in status["advice"]


def test_session_status_explains_a_missing_duel_cookie() -> None:
    """No duel cookie means unauthenticated, and login() cannot fix it headless."""
    session = Session(cookies={"__cf_bm": "bm"}, captured_at=time.time())
    with make_client(lambda r: httpx.Response(200, json={}), session) as client:
        status = client.session_status()
    assert status["has_duel_cookie"] is False
    assert "capture_session.py" in status["advice"]
    assert "Turnstile" in status["advice"]


def test_session_status_reports_unknown_age_for_legacy_profiles() -> None:
    session = Session(cookies={"duel": "s"})
    with make_client(lambda r: httpx.Response(200, json={}), session) as client:
        status = client.session_status()
    assert status["stale"] is None
    assert status["age_minutes"] is None
    assert "unknown" in status["advice"]


# ------------------------------------------------------ two-factor-setup guard


def test_two_factor_setup_passes_the_guard_and_forwards_confirm() -> None:
    """Regression: this once called ``_guard_write(url, confirm)`` positionally.

    ``confirm`` landed in the ``method`` parameter, so the call raised TypeError
    before any request, and the opt-in was never actually consulted.
    """
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"ok": True})

    with make_client(handler) as client:
        with pytest.raises(WriteNotAllowed):
            client.two_factor_setup()
    assert calls == [], "the guard must reject before any request goes out"

    with make_client(handler) as client:
        assert client.two_factor_setup(confirm=True) == {"ok": True}
    assert calls == [("POST", "/api/v2/user/security/two-factor-setup")]


# ------------------------------------------------------- multi-domain cookies


def _multi_domain_handler(value: str = "rotated", domain: str = ".duel.com"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=SAMPLE_METADATA,
            headers={"set-cookie": f"{value}; Path=/; Domain={domain}"},
        )

    return handler


def test_multi_domain_bot_cookie_does_not_raise_cookie_conflict() -> None:
    """Regression: ``__cf_bm`` under two domains killed *every* request.

    ``Cookies.get(name)`` raises ``httpx.CookieConflict`` when a name exists
    under more than one domain. ``__init__`` restores persisted cookies under
    ``duel.com`` while Cloudflare re-issues ``__cf_bm`` under ``.duel.com``, so
    ``_absorb_cookies`` exploded on all calls once that happened - not an edge
    case, but the steady state of any session that survived one response.
    """
    handler = _multi_domain_handler("__cf_bm=rotated")
    session = Session(
        cookies={"duel": "s", "__cf_bm": "persisted"}, captured_at=1000.0
    )
    with make_client(handler, session) as client:
        client.metadata()  # populates the jar with the .duel.com copy
        names = [c.name for c in client._client.cookies.jar]
        assert names.count("__cf_bm") == 2, f"expected both domains, got {names}"

        # The call that used to raise CookieConflict from _absorb_cookies.
        client.metadata()
        assert client.session.cookies["__cf_bm"] == "rotated"


def test_response_set_cookie_beats_the_stale_persisted_copy() -> None:
    """The rotation is authoritative; picking by jar order would keep the old one.

    ``duel.com`` and ``.duel.com`` normalise to the same rank, so a bare
    specificity tie-break leaves the winner dependent on insertion order.
    Reading the response's own ``Set-Cookie`` first removes the ambiguity.
    """
    handler = _multi_domain_handler("__cf_bm=fresh-rotation")
    session = Session(cookies={"__cf_bm": "stale-on-disk"}, captured_at=1000.0)
    with make_client(handler, session) as client:
        before = client.session.cf_bm_at
        client.metadata()
        assert client.session.cookies["__cf_bm"] == "fresh-rotation"
        assert client.session.cf_bm_at is not None
        assert client.session.cf_bm_at != before


def test_cookie_value_prefers_the_longest_path() -> None:
    """RFC 6265 sending precedence: the longer path is sent first."""
    with make_client(lambda r: httpx.Response(200, json={})) as client:
        cookies = client._client.cookies
        cookies.set("probe", "broad", domain=".duel.com", path="/")
        cookies.set("probe", "narrow", domain=".duel.com", path="/api/v2")
        assert client._cookie_value("probe") == "narrow"
        assert client._cookie_value("absent") is None


def test_cookie_value_breaks_a_path_tie_host_only_first() -> None:
    """Same path, two domains: resolved deterministically, not by jar order.

    This is the exact shape of the production conflict - a persisted cookie
    restored under ``duel.com`` beside a server-issued one under ``.duel.com``.
    """
    with make_client(lambda r: httpx.Response(200, json={})) as client:
        cookies = client._client.cookies
        cookies.set("probe", "domain-cookie", domain=".duel.com", path="/")
        cookies.set("probe", "host-cookie", domain="duel.com", path="/")
        assert client._cookie_value("probe") == "host-cookie"


def test_cookie_value_ignores_other_hosts_entirely() -> None:
    """A same-named cookie on an unrelated host must never be picked up."""
    with make_client(lambda r: httpx.Response(200, json={})) as client:
        client._client.cookies.set("probe", "wrong-host", domain="example.com", path="/")
        assert client._cookie_value("probe") is None


def test_multi_domain_xsrf_cookie_does_not_break_a_write() -> None:
    """The same latent conflict existed on the XSRF header lookup."""
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("X-XSRF-TOKEN"))
        return httpx.Response(200, json={"ok": True})

    seen: list[str | None] = []
    session = Session(cookies={"XSRF-TOKEN": "persisted-token"}, captured_at=1000.0)
    with make_client(handler, session, allow_writes=True) as client:
        client._client.cookies.set(
            "XSRF-TOKEN", "server-token", domain=".duel.com", path="/"
        )
        names = [c.name for c in client._client.cookies.jar]
        assert names.count("XSRF-TOKEN") == 2

        client.update_settings({"volume": 0}, confirm=True)

    assert seen == ["persisted-token"], "host-only cookie wins the tie-break"


def test_cookie_cleared_by_the_server_is_not_resurrected() -> None:
    """A name the response omits and the jar no longer holds must be dropped."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=SAMPLE_METADATA, headers={"set-cookie": "__cf_bm=; Max-Age=0; Path=/"}
        )

    session = Session(cookies={"duel": "s", "__cf_bm": "old"}, captured_at=1000.0)
    with make_client(handler, session) as client:
        client.metadata()
        assert "__cf_bm" not in client.session.cookies


def test_canonical_path_normalises_spelling() -> None:
    from automation_client import canonical_path

    assert canonical_path("/api/v2/dice/b%65t") == "/api/v2/dice/bet"
    assert canonical_path("/api/v2//dice/./bet?x=1") == "/api/v2/dice/bet"
    assert canonical_path("/api/v2/dice/x/../bet") == "/api/v2/dice/bet"
    assert canonical_path("/api/v2/dice/%2562et") == "/api/v2/dice/bet"
    # A plain path must survive unchanged, or every allowlist match breaks.
    assert canonical_path("/api/v2/metadata/socket-token") == "/api/v2/metadata/socket-token"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v2/dice/bet",
        "/api/v2/dice/b%65t",
        "/api/v2/dice/be%74",
        "/api/v2/dice/%2562et",
        "/api/v2/dice//bet",
        "/api/v2/dice/./bet",
        "/api/v2/dice/x/../bet",
        "/api/v2/DICE/BET",
        "/api/v2/dice/bet?x=1",
    ],
)
def test_generic_path_cannot_reach_a_money_endpoint_by_any_spelling(path: str) -> None:
    """The money blocklist must not be evadable by how a path is spelled.

    Regression: ``POST /api/v2/dice/b%65t`` was actually SENT - the guard
    classified the raw string while the server decodes it to the bet endpoint.
    """
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json={"data": {}})

    with make_client(handler) as client:
        with pytest.raises(UnsupportedAction):
            client.request("POST", path, json_body={"amount": "1"}, confirm=True)

    assert sent == []


def test_canonicalisation_does_not_over_block_a_harmless_encoded_path() -> None:
    """The fix must refuse money paths, not everything that looks encoded."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(str(request.url))
        return httpx.Response(200, json={})

    with make_client(handler) as client:
        client.request("POST", "/api/v2/client-se%65d", json_body={}, confirm=True)

    assert len(sent) == 1


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "sNaN"])
def test_place_dice_bet_rejects_a_non_finite_stake(amount: str) -> None:
    """Decimal('NaN') parses, then `stake <= 0` raised decimal.InvalidOperation.

    Reachable as `dice-bet --amount NaN`, which surfaced a raw decimal
    traceback instead of the documented ValueError.
    """
    with make_client(lambda request: httpx.Response(200, json={})) as client:
        with pytest.raises(ValueError, match="finite decimal stake"):
            client.place_dice_bet(amount, bet_type="UNDER", currency="SOL", target="5005")


def test_spec_drift_reports_a_missing_bundle_instead_of_raising() -> None:
    """A renamed bundle IS drift; the tripwire must report it, not crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    spec = {
        "metadata": {
            "provenance": {
                "bundle": "/assets/index-GONE.js",
                "bundle_sha256_prefix": "518cc628",
            }
        }
    }
    with make_client(handler) as client:
        result = client.check_spec_drift(spec)

    assert result["drifted"] is True
    assert "404" in result["reason"]