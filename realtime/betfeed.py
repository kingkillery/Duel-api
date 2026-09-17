"""Read-only BetFeed listener for duel.com's realtime server.

Protocol recovered from SPA bundle ``518cc628`` (verified 2026-09-17) and
recorded in ``site_spec.json -> metadata.realtime``:

- socket.io v4 server at ``https://roulette.duel.com`` (the SPA resolver's
  default address; the ``pvp.duel.com`` arena URL is dead config), engine
  path ``/s``, websocket-only transport;
- the SPA's ``BetFeed`` enum maps to the wire namespace ``livebetfeed``;
- auth is two-stage: ``uid`` and ``token`` (the socket_token) travel as
  handshake query params, then the client emits ``identify`` carrying
  ``{uid, authorizationToken, signature, uuid}``;
- credentials are short-lived (~30s): the SPA fetches a fresh pair before
  every connect, and treats server events ``server_draining`` /
  ``force_reconnect`` as "refresh and reconnect";
- the server pushes an ``init`` payload with initial state after identify.

Live-verified 2026-09-17 against the production server: anonymous metadata /
socket-token responses carry ``socket_token`` only. ``socket_signature`` is
the authenticated-session binding; guests omit it from the identify payload,
exactly like the SPA, whose ``undefined`` signature field is dropped by
``JSON.stringify``.

Live probe 2026-09-17: the handshake is **gated by an engine.io middleware**.
From a non-browser client the server answers every connect with
``{"code":3,"message":"Bad request"}`` (MIDDLEWARE_FAILURE) - with correct
credentials, session cookies, Origin, Sec-Fetch and device headers alike -
and websocket upgrades are refused even earlier. That is a bot-management
gate of the same class as the Turnstile-gated login: this listener does not
impersonate a browser to pass it. It will connect from any context where
that trust already exists (e.g. a CDP bridge into a real browser session).

Read-only by construction: the only event this module ever emits is the
protocol-mandated ``identify``. There is no send/bet API here and no code
path that could grow one without touching this file's explicit chokepoint.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from automation_client import DuelClient, DuelError

# Live-verified 2026-09-17: the SPA's per-namespace resolver falls back to
# `https://roulette.<host>` (NOT the pvp.duel.com arena URL, which is dead
# config) with engine.io path "/s". Probing roulette.duel.com/s/ returns
# engine.io's own error JSON, confirming the mount.
DEFAULT_SOCKET_ADDRESS = "https://roulette.duel.com"
SOCKET_PATH = "/s"  # engine.io path - not /socket.io (bundle fallback o || '/s')
BETFEED_NAMESPACE = "/livebetfeed"
IDENTIFY_EVENT = "identify"
RECONNECT_EVENTS = ("server_draining", "force_reconnect")

# Bounded listens poll rather than handing a timeout to the library: see wait().
WAIT_POLL_SECONDS = 0.1

# Server-managed events: handled specifically, never forwarded as data.
# (python-socketio's '*' wildcard dispatches regular events only; the guard
# below keeps forwarding honest even if a transport surfaces these too.)
_LIFECYCLE_EVENTS = frozenset({"connect", "disconnect", *RECONNECT_EVENTS})


@dataclass(frozen=True)
class SocketAuth:
    """A short-lived realtime credential pair plus who it belongs to."""

    uid: str  # signed-in user id, or "guest"
    token: str  # socket_token (the authorizationToken)
    signature: str | None  # socket_signature; None for anonymous sessions
    uuid: str  # device uuid from the captured session

    def identify_payload(self) -> dict[str, str]:
        """The exact ``identify`` payload the SPA sends after connecting.

        An absent signature is omitted rather than sent as null, mirroring the
        SPA's ``JSON.stringify`` drop of the guest's ``undefined`` field.
        """
        payload: dict[str, str] = {
            "uid": self.uid,
            "authorizationToken": self.token,
            "uuid": self.uuid,
        }
        if self.signature is not None:
            payload["signature"] = self.signature
        return payload


def _uid_from(client: DuelClient) -> str:
    """The signed-in user's id, or ``guest`` - mirroring the SPA's uid choice."""
    try:
        who = client.profile()
    except DuelError:
        return "guest"
    if not isinstance(who, dict):
        return "guest"
    user = who.get("user") if isinstance(who.get("user"), dict) else who
    uid = user.get("id") if isinstance(user, dict) else None
    return str(uid) if uid is not None else "guest"


def auth_from_client(client: DuelClient) -> SocketAuth:
    """Fetch a fresh realtime credential the way the SPA does.

    The dedicated ``POST /api/v2/metadata/socket-token`` is tried first (it
    answers for both guests and signed-in sessions); the public metadata
    document is the fallback. Called before every connect, because the
    credential is short-lived (~30s).
    """
    try:
        payload = client.socket_token()
    except DuelError:
        payload = client.metadata() or {}
    token = payload.get("socket_token")
    signature = payload.get("socket_signature")
    if not token:
        raise DuelError(
            "no realtime credential in the socket-token or metadata response; "
            "cannot authenticate to pvp.duel.com"
        )
    return SocketAuth(
        uid=_uid_from(client),
        token=str(token),
        signature=str(signature) if signature else None,
        uuid=client.session.device_uuid,
    )


class BetFeedClient:
    """Read-only listener for the ``/livebetfeed`` namespace.

    ``sio`` is a :class:`socketio.Client` (duck-typed in tests). Lifecycle
    mirrors the SPA's namespace wrapper: fresh credentials, connect with
    ``uid``/``token`` query params over websocket-only transport on path
    ``/s``, then ``identify`` once connected. Every other server event is
    forwarded to ``on_event(event, data, observed_at)``; state transitions
    go to ``on_state``.

    The only outbound event is ``identify``. Do not add another: this client
    is a listener, and the replay guarantees of this package depend on it.
    """

    def __init__(
        self,
        sio: Any,
        auth_provider: Callable[[], SocketAuth],
        *,
        url: str = DEFAULT_SOCKET_ADDRESS,
        namespace: str = BETFEED_NAMESPACE,
        on_event: Callable[[str, list, float], None] | None = None,
        on_state: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._sio = sio
        self._auth_provider = auth_provider
        self._url = url.rstrip("/")
        self._headers = {"Origin": "https://duel.com", **(headers or {})}
        # Set by connect(); reconnects are server-triggered, so per-session
        # headers must persist beyond the call that supplied them.
        self._session_headers: dict[str, str] = {}
        self._namespace = namespace
        self._on_event = on_event or (lambda event, data, observed_at: None)
        self._on_state = on_state
        self._clock = clock
        self._auth: SocketAuth | None = None
        self._reconnecting = False

        handlers = {
            "connect": self._handle_connect,
            "disconnect": self._handle_disconnect,
            "server_draining": self._handle_reconnect_request,
            "force_reconnect": self._handle_reconnect_request,
            "*": self._handle_data_event,
        }
        for event, handler in handlers.items():
            sio.on(event, handler, namespace=namespace)

    # -- connection -------------------------------------------------------

    def connect(self, headers: dict[str, str] | None = None) -> None:
        """Fetch fresh credentials and connect + identify on the namespace.

        ``headers`` extends the default browser-parity set (``Origin``); the
        CLI passes the session cookie jar and device identifier here. They are
        remembered for the life of this client so that a server-triggered
        reconnect does not silently drop the session.
        """
        self._state("connecting")
        self._auth = self._auth_provider()
        assert self._auth is not None
        # Note: the namespace travels in the `namespaces` argument, not the
        # URL - python-socketio, unlike the JS client, does not treat a URL
        # path as the namespace. The query carries the handshake credential.
        if headers:
            self._session_headers = dict(headers)
        merged = {**self._headers, **self._session_headers}
        self._sio.connect(
            f"{self._url}?uid={quote(self._auth.uid)}&token={quote(self._auth.token)}",
            socketio_path=SOCKET_PATH,
            transports=["websocket"],
            namespaces=[self._namespace],
            headers=merged,
        )

    def disconnect(self) -> None:
        self._sio.disconnect()

    def wait(self, seconds: float | None = None) -> None:
        """Block while events are processed (``seconds=None``: until disconnect).

        python-socketio 5.x's ``Client.wait()`` accepts **no** arguments, so a
        bounded listen is a sleep loop here rather than a timeout argument. The
        old code passed ``seconds`` positionally and raised TypeError against
        the real library; the duck-typed test double accepted the argument, so
        nothing caught it.
        """
        if seconds is None:
            self._sio.wait()
            return
        deadline = self._clock() + seconds
        while self._clock() < deadline and getattr(self._sio, "connected", True):
            time.sleep(WAIT_POLL_SECONDS)

    # -- server event handlers (mirror the SPA wrapper) --------------------

    def _handle_connect(self) -> None:
        self._state("connected")
        if self._auth is not None:
            self._sio.emit(
                IDENTIFY_EVENT, self._auth.identify_payload(), namespace=self._namespace
            )

    def _handle_disconnect(self, *_: object) -> None:
        self._state("disconnected")

    def _handle_reconnect_request(self, *_: object) -> None:
        """``server_draining`` / ``force_reconnect``: refresh and reconnect."""
        if self._reconnecting:
            return
        self._reconnecting = True
        try:
            self._state("reconnecting")
            self._sio.disconnect()
            self.connect()
        finally:
            self._reconnecting = False

    def _handle_data_event(self, event: str, *data: object) -> None:
        if event in _LIFECYCLE_EVENTS:
            return
        self._on_event(event, list(data), self._clock())

    def _state(self, state: str) -> None:
        if self._on_state is not None:
            self._on_state(state)


class JsonlEventSink:
    """Append every event as one JSON line.

    Same append-as-it-grows shape as the backtest round captures: the file is
    valid JSONL while recording, and safe to read in another process. The
    per-line ``open`` is deliberate - a crash mid-session loses nothing.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        # A lock plus an ensured directory: the sink is called from whichever
        # thread the transport dispatches on, and losing events here would
        # silently corrupt the recording rather than fail loudly.
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, event: str, data: list, observed_at: float) -> None:
        record = {"observed_at": observed_at, "event": event, "data": data}
        line = json.dumps(record, default=str) + "\n"
        with self._lock, self._path.open("a", encoding="utf-8") as fh:
            fh.write(line)
