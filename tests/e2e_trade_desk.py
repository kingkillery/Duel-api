"""Browser E2E: real local app and provider adapters; upstream HTTP is mocked.

Run: py -3.13 tests/e2e_trade_desk.py
No real credentials, accounts or wagers are used. External sockets are denied
inside the fixture server, including status/balance reads.
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
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


def records(log):
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()] if log.exists() else []


def bets(log):
    return [row for row in records(log) if row.get("path") in {"/api/v2/dice/bet", "/api/dice/play"}]


def drive(page, url, log, screenshot):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(url) else route.abort())
    page.goto(url, wait_until="networkidle")
    expect(page.locator("#status")).to_contain_text("live: disabled")
    assert records(log) == [], "Disabled mode performed provider I/O"
    assert page.locator("#security_token").count() == 0

    def api(path, body):
        return page.evaluate("""async ({path, body}) => {
            const r = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
            return {status: r.status, body: await r.json()};
        }""", {"path": path, "body": body})

    def configure(**kwargs):
        assert api("/__fixture/config", kwargs)["status"] == 200

    def request(**kwargs):
        payload = {"provider": "duel", "currency": "BTC", "amount": "0.00000050",
                   "target": "4900", "side": "UNDER", "confirm": True,
                   "client_nonce": uuid.uuid4().hex}
        payload.update(kwargs)
        return api("/api/bet", payload)

    def click():
        with page.expect_response(lambda r: r.url == url + "api/bet") as response:
            page.locator("#bet").click()
        result = response.value.json()
        expect(page.locator("#result")).not_to_have_text("sending one wager…")
        return result

    page.locator("#confirm").check()
    assert click()["code"] == "live-disabled"
    assert not bets(log)
    configure(live=True)
    assert click()["code"] == "confirm"
    assert request(confirm="true")["status"] == 422
    configure(token=False)
    assert request()["body"]["code"] == "token"
    configure(token=True, profile=False)
    assert request()["body"]["code"] == "credentials-missing"
    configure(profile=True, cap=False)
    assert request()["body"]["code"] == "config"
    configure(cap=True)
    for invalid in ["NaN", "Infinity", "0", "-1", "1.00000000000000000001"]:
        assert request(amount=invalid)["body"]["code"] == "invalid"
    assert request(security_token="not-accepted-from-browser")["body"]["code"] == "token"
    assert not bets(log)
    print("PASS disabled mode, strict confirmation, token/profile/cap checks")

    # An external page or another local port cannot submit a wager.
    for origin in ["https://evil.invalid", "http://127.0.0.1:1", "null"]:
        response = page.request.post(url + "api/bet", data={}, headers={"Origin": origin})
        assert response.status == 403
    for host in ["evil.invalid", "localhost:8765@evil.invalid", "localhost:8765 garbage"]:
        response = page.request.get(url, headers={"Host": host})
        assert response.status == 403
    assert not bets(log)
    print("PASS cross-origin and malformed Host refusal")

    page.locator("#confirm").check()
    duel = click()
    assert duel["ok"] is True
    round_data = duel["result"]["data"]["round"]
    assert DecimalString(round_data["amount_won"]) == DecimalString("0.00000100")
    shown = page.locator("#result").inner_text()
    assert "synthetic-browser-minted-token" not in shown and "must-not-reach-browser" not in shown
    expect(page.locator("#confirm")).not_to_be_checked()
    wire = bets(log)[0]
    assert wire["body"]["amount"] == "0.00000050"
    assert wire["body"]["target"] == "4900" and wire["body"]["bet_type"] == "under"
    assert wire["body"]["currency"] == 101 and wire["body"]["security_token_matches"]
    print("PASS Duel browser Bet -> real adapter -> authoritative displayed result")

    page.locator("#provider").select_option("duckdice")
    page.locator("#currency").fill("LTC")
    page.locator("#amount").fill("0.20")
    page.locator("#target").fill("5000")
    page.locator("#side").select_option("OVER")
    page.locator("#confirm").check()
    duck = click()
    assert duck["ok"] and duck["result"]["data"]["round"]["hash"] == "duck-result"
    expect(page.locator("#result")).to_contain_text('"balance": "1.25"')
    wire = bets(log)[1]
    assert wire["body"] == {"symbol": "LTC", "chance": "50.00", "isHigh": True, "amount": "0.20"}
    assert wire["has_api_key"]
    assert "synthetic-e2e-key" not in page.locator("#result").inner_text()
    expect(page.locator("#confirm")).not_to_be_checked()
    expect(page.locator("#status")).to_contain_text("1.25 LTC")
    page.screenshot(path=str(screenshot), full_page=True)
    print("PASS DuckDice browser Bet -> real adapter -> authoritative displayed result")

    # A successful request nonce cannot be replayed.
    nonce = uuid.uuid4().hex
    first = request(provider="duckdice", currency="LTC", amount="0.20", client_nonce=nonce)
    assert first["status"] == 200
    count = len(bets(log))
    assert request(provider="duckdice", currency="LTC", amount="0.20", client_nonce=nonce)["body"]["code"] == "duplicate"
    assert len(bets(log)) == count

    # Race a second request while the first real adapter is inside MockTransport.
    page.locator("#currency").fill("SLOW")
    page.locator("#confirm").check()
    with page.expect_response(lambda r: r.url == url + "api/bet" and r.request.post_data_json.get("currency") == "SLOW") as slow_response:
        page.locator("#bet").click()
        expect(page.locator("#bet")).to_be_disabled()
        deadline = time.monotonic() + 5
        while len(bets(log)) == count and time.monotonic() < deadline:
            page.wait_for_timeout(25)
        assert len(bets(log)) == count + 1
        raced = request(provider="duckdice", currency="LTC", amount="0.20")
        assert raced["status"] == 409 and raced["body"]["code"] == "inflight"
    assert slow_response.value.json()["ok"]
    expect(page.locator("#result")).to_contain_text("slow-result")
    assert len(bets(log)) == count + 1
    expect(page.locator("#confirm")).not_to_be_checked()
    print("PASS consumed nonces and concurrent/double-submit prevention")

    # Last: an ambiguous provider error must latch across new nonces and reloads.
    page.locator("#currency").fill("ERR")
    page.locator("#confirm").check()
    failed = click()
    assert failed["code"] == "ambiguous"
    assert "sensitive-fixture-error" not in json.dumps(failed)
    count = len(bets(log))
    assert request()["body"]["code"] == "blocked"
    page.reload(wait_until="networkidle")
    expect(page.locator("#bet")).to_be_disabled()
    assert request()["body"]["code"] == "blocked"
    assert len(bets(log)) == count
    for bad_amount in ("0.77", "0.78"):
        configure(reset=True)
        malformed = request(amount=bad_amount)
        assert malformed["status"] == 502 and malformed["body"]["code"] == "ambiguous"
        assert request()["body"]["code"] == "blocked"
    print("PASS malformed payout and non-finite JSON result latch before response serialization")
    assert not errors, errors
    assert not any(row.get("outbound_blocked") for row in records(log)), "A path attempted external networking"
    print("PASS ambiguous-outcome latch, reload protection, secret-safe errors, zero browser errors")
    print(f"PASS {len(bets(log))} mocked wager dispatches; zero external socket attempts")


def main():
    with tempfile.TemporaryDirectory(prefix="duel-desk-e2e-") as temporary:
        root = Path(temporary)
        profile = root / "session.json"
        profile.write_text(json.dumps({"device_uuid": "e2e-device", "cookies": {"duel": "synthetic"}, "local_storage": {}}), encoding="utf-8")
        log = root / "calls.jsonl"
        identity = uuid.uuid4().hex
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}/"
        env = {key: value for key, value in os.environ.items()
               if key in {"SYSTEMROOT", "WINDIR", "PATH", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "TEMP", "TMP"}}
        env.update({"PYTHONPATH": str(ROOT), "DUEL_TRADE_LIVE": "0", "DUEL_TRADE_MOCK": "1",
                    "DUEL_TRADE_MAX_STAKE": "1", "DUEL_TRADE_SECURITY_TOKEN": "synthetic-browser-minted-token",
                    "DUEL_TRADE_PROFILE": str(profile), "DUCKDICE_API_KEY": "synthetic-e2e-key",
                    "E2E_CALL_LOG": str(log), "E2E_INSTANCE": identity})
        screenshot = Path(tempfile.gettempdir()) / f"duel-desk-{identity}.png"
        with (root / "server.log").open("w+", encoding="utf-8") as output:
            proc = subprocess.Popen([sys.executable, str(ROOT / "tests/e2e_trade_server.py"), str(port)],
                                    cwd=root, env=env, stdout=output, stderr=subprocess.STDOUT)
            try:
                with sync_playwright() as pw:
                    request = pw.request.new_context()
                    deadline = time.monotonic() + 20
                    while True:
                        if proc.poll() is not None:
                            output.seek(0)
                            raise RuntimeError(output.read())
                        try:
                            response = request.get(url + "__fixture/identity", timeout=500)
                            assert response.json() == {"id": identity}, "Port belongs to another process"
                            break
                        except Exception:
                            if time.monotonic() >= deadline:
                                raise
                            time.sleep(0.1)
                    request.dispose()
                    browser = pw.chromium.launch(headless=True)
                    try:
                        drive(browser.new_page(), url, log, screenshot)
                    finally:
                        browser.close()
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        print(f"SCREENSHOT {screenshot}")
    return 0


if __name__ == "__main__":
    from decimal import Decimal as DecimalString
    raise SystemExit(main())
