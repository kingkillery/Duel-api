"""Capture-side regressions.

Gives a red-capable loop for the class of failure where the CDP port is owned by
a process that answers HTTP but is not a DevTools endpoint: Playwright reports it
as a generic attach failure, which sends the operator to restart Chrome instead of
move to a free port.
"""
from __future__ import annotations

import http.server
import threading

from capture_session import _devtools_note, _port_of, identity_from_storage


class _Squatter(http.server.BaseHTTPRequestHandler):
    """Behaves like the real-world squatter: any DevTools path answers 404."""

    def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):  # silence the handler
        pass


def _serve():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Squatter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_note_names_a_port_squatting_process_as_not_devtools():
    srv = _serve()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        note = _devtools_note(url)
        assert "not a DevTools endpoint" in note
        assert "404" in note
        assert url in note
    finally:
        srv.shutdown()


def test_note_reports_a_free_port_as_nothing_listening():
    srv = _serve()
    port = srv.server_address[1]
    srv.shutdown()
    note = _devtools_note(f"http://127.0.0.1:{port}")
    assert "nothing is serving DevTools" in note


def test_port_of_reads_the_attached_port_not_a_hardcoded_default():
    assert _port_of("http://127.0.0.1:9223") == 9223
    assert _port_of("http://127.0.0.1") is None


def test_signed_in_storage_exposes_no_client_side_identity():
    """Live-verified 2026-09-17: a signed-in duel.com profile has no auth blob.

    This is the exact localStorage key set captured from a signed-in session, on
    both duel.com and www.duel.com. If it ever starts yielding identity, the site
    changed its storage layout and capture can stop deferring to whoami; if this
    test is deleted, someone has re-introduced the assumption that broke it.
    """
    storage = dict.fromkeys(
        (
            "collapsedAnnouncements", "hideZeroBalances", "security:uuid",
            "currency", "userHasChosenLocale", "displayInFiat", "chatOpen",
            "notificationsOn", "userChosenChatRoom", "localMutedUsers",
            "musicOn", "fxOn", "userChosenLocale",
        ),
        "",
    )
    assert identity_from_storage(storage) == (None, None)


def test_identity_still_read_when_a_site_version_does_write_one():
    blob = '{"user": {"username": "someone", "user_id": 42}}'
    assert identity_from_storage({"auth": blob}) == ("someone", 42)
    assert identity_from_storage({"auth": "not json"}) == (None, None)

