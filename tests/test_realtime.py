"""Offline tests for the read-only BetFeed listener (realtime/betfeed.py).

The handshake contract pinned here was recovered from SPA bundle 518cc628:
namespace /livebetfeed on https://pvp.duel.com, engine path /s, websocket-only,
uid/token as handshake query params, then an `identify` event carrying
{uid, authorizationToken, signature, uuid}. If a test here fails after a site
update, run `automation_cli.py spec-check` first - the protocol may have moved.
"""

import json

import httpx
import pytest

from automation_client import DuelClient, Session
from realtime.betfeed import (
    BETFEED_NAMESPACE,
    IDENTIFY_EVENT,
    SOCKET_PATH,
    BetFeedClient,
    JsonlEventSink,
    SocketAuth,
    auth_from_client,
)

AUTH = SocketAuth(uid="42", token="TOK", signature="SIG", uuid="u1")


class FakeSio:
    """Duck-typed socketio.Client that records what the wrapper does."""

    def __init__(self) -> None:
        self.handlers: dict[tuple, object] = {}
        self.connects: list[tuple[str, dict]] = []
        self.emitted: list[tuple[str, object, str]] = []
        self.disconnections = 0
        self.wait_calls = 0
        self.connected = True

    def on(self, event, handler=None, namespace=None):
        self.handlers[(namespace, event)] = handler

    def connect(self, url, **kwargs):
        self.connects.append((url, kwargs))
        self.handlers[(BETFEED_NAMESPACE, "connect")]()

    def emit(self, event, data=None, namespace=None):
        self.emitted.append((event, data, namespace))

    def disconnect(self):
        self.disconnections += 1

    def wait(self):
        # Mirrors python-socketio 5.x exactly: Client.wait() takes NO arguments.
        # Accepting a `seconds` keyword here is precisely what let the wrapper
        # pass one to the real library without a single test failing.
        self.wait_calls += 1


def make_feed(provider=None, **kwargs):
    sio = FakeSio()
    feed = BetFeedClient(
        sio,
        provider or (lambda: AUTH),
        on_event=kwargs.pop("on_event", None) or (lambda *a: None),
        clock=kwargs.pop("clock", lambda: 123.0),
        **kwargs,
    )
    return sio, feed


def test_connect_matches_the_bundle_verified_handshake() -> None:
    sio, feed = make_feed()
    feed.connect()
    assert len(sio.connects) == 1
    url, kwargs = sio.connects[0]
    assert url == "https://roulette.duel.com?uid=42&token=TOK"
    assert kwargs["socketio_path"] == SOCKET_PATH == "/s"
    assert kwargs["transports"] == ["websocket"]
    assert kwargs["namespaces"] == ["/livebetfeed"]


def test_connect_identifies_and_identify_is_the_only_outbound_event() -> None:
    sio, feed = make_feed()
    feed.connect()
    assert sio.emitted == [
        (IDENTIFY_EVENT, AUTH.identify_payload(), "/livebetfeed")
    ]
    assert AUTH.identify_payload() == {
        "uid": "42",
        "authorizationToken": "TOK",
        "signature": "SIG",
        "uuid": "u1",
    }


def test_guest_url_quotes_the_uid_and_token() -> None:
    guest = SocketAuth(uid="guest", token="a b/c", signature="S", uuid="u")
    sio, feed = make_feed(provider=lambda: guest)
    feed.connect()
    url, _ = sio.connects[0]
    assert url == "https://roulette.duel.com?uid=guest&token=a%20b/c"


def test_server_events_forward_to_on_event_with_timestamp() -> None:
    seen: list[tuple[str, list, float]] = []
    sio, feed = make_feed(on_event=lambda e, d, t: seen.append((e, d, t)))
    feed.connect()
    sio.handlers[(BETFEED_NAMESPACE, "*")]("house_game_feed_all", {"bet": 1})
    sio.handlers[(BETFEED_NAMESPACE, "*")]("init", {"rows": []}, 7)
    assert seen == [
        ("house_game_feed_all", [{"bet": 1}], 123.0),
        ("init", [{"rows": []}, 7], 123.0),
    ]


def test_lifecycle_events_are_handled_not_forwarded() -> None:
    seen: list = []
    sio, feed = make_feed(on_event=lambda e, d, t: seen.append((e, d, t)))
    feed.connect()
    sio.handlers[(BETFEED_NAMESPACE, "*")]("server_draining", {"reason": "x"})
    sio.handlers[(BETFEED_NAMESPACE, "*")]("force_reconnect")
    # reconnect handler ran (see next test), but nothing leaked into the feed
    assert seen == []


def test_server_draining_reconnects_with_fresh_credentials() -> None:
    tokens = iter(["TOK1", "TOK2"])
    sio, feed = make_feed(
        provider=lambda: SocketAuth(uid="42", token=next(tokens), signature="S", uuid="u")
    )
    feed.connect()
    sio.handlers[(BETFEED_NAMESPACE, "server_draining")]( {"reason": "deploy"})
    assert sio.disconnections == 1
    assert len(sio.connects) == 2
    assert sio.connects[1][0].endswith("token=TOK2")


def test_auth_from_client_prefers_the_socket_token_endpoint(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/metadata/socket-token"):
            return httpx.Response(200, json={"socket_token": "T", "socket_signature": "S"})
        if request.url.path == "/api/v2/user":
            return httpx.Response(200, json={"id": 42, "username": "t"})
        return httpx.Response(404, json={})

    client = DuelClient(
        Session(username="t", user_id=42, device_uuid="u1"),
        profile=tmp_path / "session.json",
        transport=httpx.MockTransport(handler),
    )
    auth = auth_from_client(client)
    assert auth == SocketAuth(uid="42", token="T", signature="S", uuid="u1")


def test_auth_from_client_falls_back_to_metadata_for_guests(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/metadata/socket-token"):
            return httpx.Response(401, json={"message": "no session"})
        if request.url.path == "/api/v2/metadata":
            return httpx.Response(
                200,
                json={"socket_token": "GT", "socket_signature": "GS", "user": None},
            )
        return httpx.Response(401, json={"message": "no session"})

    client = DuelClient(
        Session(device_uuid="u1"),
        profile=tmp_path / "session.json",
        transport=httpx.MockTransport(handler),
    )
    auth = auth_from_client(client)
    assert auth.uid == "guest"
    assert auth == SocketAuth(uid="guest", token="GT", signature="GS", uuid="u1")


def test_guest_identify_payload_omits_the_signature() -> None:
    """Live-verified 2026-09-17: anonymous credential responses carry no
    socket_signature; the SPA's JSON.stringify drops the undefined field, so
    the identify payload omits it rather than sending null."""
    guest = SocketAuth(uid="guest", token="T", signature=None, uuid="u")
    assert guest.identify_payload() == {
        "uid": "guest",
        "authorizationToken": "T",
        "uuid": "u",
    }


def test_auth_from_client_accepts_signature_less_guest_credentials(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/metadata/socket-token"):
            return httpx.Response(401, json={"message": "no session"})
        if request.url.path == "/api/v2/metadata":
            return httpx.Response(200, json={"socket_token": "GT", "user": None})
        return httpx.Response(401, json={"message": "no session"})

    client = DuelClient(
        Session(device_uuid="u1"),
        profile=tmp_path / "session.json",
        transport=httpx.MockTransport(handler),
    )
    auth = auth_from_client(client)
    assert auth.signature is None
    assert auth.token == "GT"


def test_jsonl_sink_appends_valid_jsonl(tmp_path) -> None:
    path = tmp_path / "feed.jsonl"
    sink = JsonlEventSink(path)
    sink("house_game_feed_all", [{"bet": 1}], 100.0)
    sink("init", [{"rows": []}], 101.5)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first == {"observed_at": 100.0, "event": "house_game_feed_all", "data": [{"bet": 1}]}


def test_module_declares_itself_read_only() -> None:
    """Structural guard: exactly one outbound emit call site exists.

    Counted over the parsed AST rather than by substring. The previous version
    asserted on text left behind by a str.replace, so its `or` fallback was
    unreachable dead code and any emit spelled differently slipped past it.
    """
    import ast
    import inspect

    from realtime import betfeed

    emits = [
        node
        for node in ast.walk(ast.parse(inspect.getsource(betfeed)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "emit"
    ]
    assert len(emits) == 1, f"expected exactly one outbound emit, found {len(emits)}"


def test_bounded_wait_never_hands_a_timeout_to_the_real_socketio_client() -> None:
    """Regression: python-socketio 5.x Client.wait() takes NO arguments.

    The old code passed `seconds` positionally on every call - even wait(None) -
    so it raised TypeError against the real library while the fake accepted it.
    """
    import socketio

    feed = BetFeedClient(socketio.Client(), lambda: AUTH)
    feed.wait(0.01)  # must not raise


def _stepping_clock(step: float = 0.05):
    now = [0.0]

    def clock() -> float:
        now[0] += step
        return now[0]

    return clock


def test_bounded_wait_polls_instead_of_delegating_and_unbounded_delegates() -> None:
    sio, feed = make_feed(clock=_stepping_clock())
    feed.wait(0.2)
    assert sio.wait_calls == 0  # bounded: polled here, never handed a timeout

    feed.wait()
    assert sio.wait_calls == 1  # unbounded: delegated to the library


def test_bounded_wait_stops_early_when_the_transport_drops() -> None:
    sio, feed = make_feed(clock=_stepping_clock())
    sio.connected = False
    feed.wait(60.0)  # would sleep for a minute if it ignored the transport
    assert sio.wait_calls == 0


def test_reconnect_keeps_the_session_headers() -> None:
    """Regression: a server-triggered reconnect dropped the CLI's cookie jar.

    connect() merged the supplied headers for that call only, so the reconnect
    - which the server initiates - reconnected without the session at all.
    """
    sio, feed = make_feed()
    feed.connect(headers={"Cookie": "duel=FAKE", "x-device-uuid": "u1"})

    sio.handlers[(BETFEED_NAMESPACE, "server_draining")]()

    assert len(sio.connects) == 2
    for _url, kwargs in sio.connects:
        assert kwargs["headers"]["Cookie"] == "duel=FAKE"
        assert kwargs["headers"]["x-device-uuid"] == "u1"


def test_jsonl_sink_keeps_every_line_under_concurrent_dispatch(tmp_path) -> None:
    """Regression: unlocked append lost and interleaved recorded events."""
    import threading

    path = tmp_path / "feed.jsonl"
    sink = JsonlEventSink(path)

    def worker(n: int) -> None:
        for i in range(200):
            sink("house_game_feed_all", [{"writer": n, "i": i}], float(i))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1000
    for line in lines:
        json.loads(line)  # intact JSON means no two writes interleaved


def test_jsonl_sink_creates_a_missing_output_directory(tmp_path) -> None:
    """Regression: a missing --out directory recorded nothing, silently."""
    path = tmp_path / "missing" / "nested" / "feed.jsonl"
    JsonlEventSink(path)("init", [], 1.0)
    assert path.exists()
