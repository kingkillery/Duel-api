"""Client behaviour tests, driven by a mocked HTTP transport (no network)."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

import httpx
import pytest

from automation_client import (
    AuthRequired,
    CaptchaRequired,
    CloudflareChallenge,
    DuelClient,
    DuelError,
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


def make_client(handler, session: Session | None = None, tmp_path: Path | None = None) -> DuelClient:
    # Never default to the repo root: login() persists a session file, and a
    # stray session.json in the working tree would leak real credentials.
    base = Path(tmp_path) if tmp_path is not None else Path(tempfile.mkdtemp())
    return DuelClient(
        session,
        profile=base / "session.json",
        transport=httpx.MockTransport(handler),
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