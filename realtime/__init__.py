"""Read-only realtime listeners for duel.com's socket.io server.

The first (and so far only) consumer is the BetFeed namespace: the live
settled-bets stream shown in the site's lobby. Everything here is
receive-only - the one outbound event is the protocol-mandated
``identify`` - so a listener cannot place a bet, by design or by accident.
"""

from .betfeed import (
    BETFEED_NAMESPACE,
    IDENTIFY_EVENT,
    DEFAULT_SOCKET_ADDRESS,
    RECONNECT_EVENTS,
    SOCKET_PATH,
    BetFeedClient,
    JsonlEventSink,
    SocketAuth,
    auth_from_client,
)

__all__ = [
    "BETFEED_NAMESPACE",
    "IDENTIFY_EVENT",
    "DEFAULT_SOCKET_ADDRESS",
    "RECONNECT_EVENTS",
    "SOCKET_PATH",
    "BetFeedClient",
    "JsonlEventSink",
    "SocketAuth",
    "auth_from_client",
]
