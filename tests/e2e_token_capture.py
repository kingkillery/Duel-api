"""Real Chromium/CDP -> production capture endpoint -> real adapter, offline only.

Run: py -3.13 tests/e2e_token_capture.py
No capture function/cache injection: only upstream HTTP responses are synthetic.
This proves source-bound response metadata handling, not provider validation of a
real token, live minting, account identity, or settlement. Never attaches to an
existing browser. Requires public token_state='listening' after subscription.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "synthetic-browser-minted-token"
AUTH_COOKIE = "do_not_share_this_with_anyone_not_even_staff"
SITE = "https://duel.com/dice"
MINT = "https://duel.com/api/v2/user/security/token"
HTML = """<!doctype html><title>Offline token fixture</title>
<button id="mock">MOCK-TOKEN-RESPONSE</button><output id="done"></output>
<script>
window.fixtureType = 'standard';
document.querySelector('#mock').onclick = async () => {
  const response = await fetch('/api/v2/user/security/token', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({type: window.fixtureType})
  });
  await response.json();
  document.querySelector('#done').textContent = 'fulfilled';
};
</script>"""


def free_port(excluded=()):
    while True:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in {51537, *excluded}:
            return port


def records(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def wagers(log):
    return [row for row in records(log) if row.get("path") in {"/api/v2/dice/bet", "/api/dice/play"}]


def drive(context, url, log):
    minted = []
    blocked = []
    errors = []
    console = []
    desk_responses = []
    response_body = {"success": True, "token": TOKEN, "expires_in": 120, "token_type": "standard"}
    origin = urlsplit(url)

    def route_request(route):
        request = route.request
        target = urlsplit(request.url)
        if (target.scheme, target.netloc) == (origin.scheme, origin.netloc):
            route.continue_()
        elif request.url == SITE and request.method == "GET":
            route.fulfill(status=200, content_type="text/html", body=HTML)
        elif request.url == MINT and request.method == "POST":
            minted.append(request.post_data_json)
            route.fulfill(status=200, content_type="application/json", body=json.dumps(response_body))
        else:
            blocked.append(request.url)
            route.abort()

    context.route("**/*", route_request)
    context.route_web_socket("**/*", lambda ws: ws.close())
    context.add_cookies([
        {"name": "duel", "value": "synthetic", "url": "https://duel.com"},
        {"name": AUTH_COOKIE, "value": "synthetic-auth", "url": "https://duel.com"},
    ])
    site = context.new_page()
    site.on("pageerror", lambda error: errors.append(str(error)))
    site.goto(SITE, wait_until="load")
    desk = context.new_page()
    desk.on("pageerror", lambda error: errors.append(str(error)))
    desk.on("console", lambda message: console.append(message.text))
    desk.on("response", lambda response: desk_responses.append(response))
    desk.goto(url, wait_until="networkidle")

    def api(path, body):
        result = desk.evaluate("""async ({path, body}) => {
          const r = await fetch(path, {method: 'POST',
            headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
          return {status: r.status, body: await r.json()};
        }""", {"path": path, "body": body})
        assert TOKEN not in json.dumps(result), "Token leaked through desk JSON"
        return result

    def status():
        result = api("/api/status", {"provider": "duel", "currency": "BTC"})
        assert result["status"] == 200
        return result["body"]["status"]

    def listen_then_emit(token_type="standard", ttl=120, delay_ms=0):
        response_body.update(expires_in=ttl, token_type=token_type)
        site.evaluate("type => { window.fixtureType = type; document.querySelector('#done').textContent = ''; }", token_type)
        with desk.expect_response(url + "api/token/capture", timeout=40000) as pending:
            desk.locator("#capture").click()
            deadline = time.monotonic() + 10
            while True:
                current = api("/api/token/status", {})["body"]["token"]
                state = current["token_state"]
                if state == "listening":
                    break
                assert time.monotonic() < deadline, f"Capture listener never became ready: {current.get('token_detail', state)}"
                desk.wait_for_timeout(50)
            expect(desk.locator("#capture_signal")).to_contain_text("LISTENING — waiting for a new token")
            expect(desk.locator("#capture_signal")).to_have_class("signal green")
            expect(desk.locator("#capture")).to_be_disabled()
            expect(desk.locator("#bet")).to_be_disabled()
            if delay_ms:
                assert 290 <= current["listen_seconds_remaining"] <= 300
                busy = context.request.post(url + "api/token/capture", data={}, headers={"Origin": url.rstrip("/")})
                assert busy.status == 409 and busy.json()["code"] == "busy"
                assert TOKEN not in busy.text()
                desk.wait_for_timeout(delay_ms)
                assert api("/api/token/status", {})["body"]["token"]["token_state"] == "listening"
                assert not minted and not wagers(log)
            # This click is never issued until the production listener reports ready.
            site.get_by_role("button", name="MOCK-TOKEN-RESPONSE").click()
            expect(site.locator("#done")).to_have_text("fulfilled")
        response = pending.value
        body = response.json()
        assert TOKEN not in response.text()
        expect(desk.locator("#capture")).to_be_enabled()
        return response.status, body

    assert status()["token_state"] == "missing", "Environment/cache injected a token"
    assert not minted and not wagers(log)
    desk.locator("#currency").fill("BTC")
    desk.locator("#amount").fill("0.04")
    desk.locator("#refresh").click()
    expect(desk.locator("#status")).to_contain_text("1.0 BTC")

    code, captured = listen_then_emit(delay_ms=22000)
    assert code == 200 and captured["ok"] is True
    assert captured["token"]["token_state"] == "held"
    expect(desk.locator("#capture_signal")).to_contain_text("TOKEN CAPTURED")
    expect(desk.locator("#status")).to_contain_text("security token: held")
    assert status()["token_state"] == "held"
    assert minted == [{"type": "standard"}]
    assert not wagers(log), "Capture placed a wager"
    print("PASS five-minute listener, capture beyond 20 seconds, visible readiness/countdown, busy lock, and session binding")

    with desk.expect_response(url + "api/bet") as pending:
        desk.locator("#bet").click()
    result = pending.value.json()
    assert pending.value.status == 200 and result["ok"] is True
    expect(desk.locator("#result")).to_contain_text('"amount_won"')
    expect(desk.locator("#confirm")).to_have_count(0)
    wire = wagers(log)
    assert len(wire) == 1
    assert wire[0]["provider"] == "duel"
    assert wire[0]["body"]["security_token_matches"] is True
    assert Decimal(wire[0]["body"]["amount"]) == Decimal("0.00000050")
    assert "E" not in wire[0]["body"]["amount"].upper(), "wire stake must use fixed-point notation"
    assert wire[0]["body"]["currency"] == 101
    print("PASS GUI mocked Duel wager uses captured token with token environment absent")

    # Real cookie mismatch is refused before observing any mint, dropping old token.
    context.add_cookies([{"name": AUTH_COOKIE, "value": "synthetic-other-session", "url": "https://duel.com"}])
    with desk.expect_response(url + "api/token/capture", timeout=10000) as pending:
        desk.locator("#capture").click()
    assert pending.value.status == 409 and pending.value.json()["code"] == "session"
    assert status()["token_state"] == "session"
    assert len(minted) == 1 and len(wagers(log)) == 1
    context.add_cookies([{"name": AUTH_COOKIE, "value": "synthetic-auth", "url": "https://duel.com"}])
    print("PASS profile/browser mismatch refuses capture and invalidates previous token")

    # Production rejects these mint responses and finishes its real bounded listen.
    api("/__fixture/config", {"listen_seconds": 2})  # Shorten only the offline fixture's negative timeout checks.
    for token_type, ttl in [("onetime", 120), ("standard", 0)]:
        code, refused = listen_then_emit(token_type, ttl)
        assert code == 409 and refused["code"] == "operator-action-required"
        assert status()["token_state"] == "operator-action-required"
        expect(desk.locator("#capture_signal")).to_have_class("signal red")
        expect(desk.locator("#capture_signal")).to_contain_text("NOT LISTENING")
        assert len(wagers(log)) == 1
        print(f"PASS unusable response rejected: type={token_type}, expires_in={ttl}")

    assert minted == [{"type": "standard"}, {"type": "onetime"}, {"type": "standard"}]
    assert TOKEN not in desk.content() and TOKEN not in desk.locator("body").inner_text()
    assert all(TOKEN not in item for item in console)
    for response in desk_responses:
        assert TOKEN not in response.text(), "Token leaked through desk HTTP response"
        assert TOKEN not in (response.request.post_data or ""), "Token leaked through desk HTTP request"
    assert TOKEN not in log.read_text(encoding="utf-8")
    assert not errors, errors
    assert not blocked, "Unexpected external browser request (aborted)"
    assert not any(row.get("outbound_blocked") for row in records(log))
    print("PASS desk HTTP/DOM/console/log token containment; zero external browser requests or server socket attempts")


def main():
    with tempfile.TemporaryDirectory(prefix="duel-token-e2e-") as temporary:
        root = Path(temporary)
        profile = root / "session.json"
        profile.write_text(json.dumps({"device_uuid": "synthetic-device", "cookies": {
            "duel": "synthetic", AUTH_COOKIE: "synthetic-auth"}, "local_storage": {}}), encoding="utf-8")
        log = root / "calls.jsonl"
        port = free_port()
        cdp_port = free_port((port,))
        url = f"http://127.0.0.1:{port}/"
        identity = uuid.uuid4().hex
        safe_keys = {"SYSTEMROOT", "WINDIR", "PATH", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in safe_keys}
        environment.update({"PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
                            "DUEL_TRADE_LIVE": "1", "DUEL_TRADE_MOCK": "1", "DUEL_TRADE_MAX_STAKE": "1",
                            "DUEL_TRADE_PROFILE": str(profile), "DUCKDICE_API_KEY": "synthetic-e2e-key",
                            "DUEL_TRADE_CDP_URL": f"http://127.0.0.1:{cdp_port}",
                            "E2E_CALL_LOG": str(log), "E2E_INSTANCE": identity})
        assert "DUEL_TRADE_SECURITY_TOKEN" not in environment
        with (root / "server.log").open("w+", encoding="utf-8") as output:
            process = subprocess.Popen([sys.executable, str(ROOT / "tests/e2e_trade_server.py"), str(port)],
                                       cwd=root, env=environment, stdout=output, stderr=subprocess.STDOUT)
            try:
                with sync_playwright() as pw:
                    request = pw.request.new_context()
                    try:
                        deadline = time.monotonic() + 20
                        while True:
                            if process.poll() is not None:
                                output.seek(0)
                                raise RuntimeError(output.read())
                            try:
                                response = request.get(url + "__fixture/identity", timeout=500)
                            except Exception:
                                if time.monotonic() >= deadline:
                                    raise
                                time.sleep(0.1)
                                continue
                            assert response.json() == {"id": identity}, "Port is not our fixture"
                            break
                    finally:
                        request.dispose()
                    # CDP attaches to the default context. Keep its cookie store
                    # in this disposable profile rather than an incognito context.
                    context = pw.chromium.launch_persistent_context(
                        str(root / "chromium"), headless=True, env=environment,
                        service_workers="block", args=[
                        f"--remote-debugging-port={cdp_port}", "--remote-debugging-address=127.0.0.1",
                        "--disable-background-networking", "--disable-component-update",
                        "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost",
                    ])
                    try:
                        drive(context, url, log)
                    finally:
                        context.close()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
