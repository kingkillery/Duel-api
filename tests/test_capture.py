"""Capture-side regressions.

Gives a red-capable loop for the class of failure where the CDP port is owned by
a process that answers HTTP but is not a DevTools endpoint: Playwright reports it
as a generic attach failure, which sends the operator to restart Chrome instead of
move to a free port.
"""
from __future__ import annotations

import http.server
import threading

from capture_session import _devtools_note, _port_of


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
