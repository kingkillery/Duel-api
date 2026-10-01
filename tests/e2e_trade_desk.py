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
    expect(page.locator("#live_signal")).to_have_class("signal red")
    expect(page.locator("#live_signal")).to_contain_text("LIVE MODE OFF")
    expect(page.locator("#gate_signal")).to_contain_text("NOT READY")
    assert all(row.get("path") == "/api/v2/metadata/exchange-rates" for row in records(log)), "Disabled mode performed non-public provider I/O"
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

    expect(page.locator("#minimum")).to_contain_text("0.00007080 SOL")
    page.locator("#use_minimum").click()
    expect(page.locator("#amount")).to_have_value("0.01")
    expect(page.locator("#minimum")).to_contain_text("$0.01 USD")
    assert click()["code"] == "live-disabled"
    assert not bets(log)
    configure(live=True)
    assert request(confirm=False)["body"]["code"] == "confirm"
    assert request(confirm="true")["status"] == 422
    configure(token=False)
    assert request()["body"]["code"] == "token"
    configure(token=True, profile=False)
    assert request()["body"]["code"] == "credentials-missing"
    configure(profile=True, cap=False)
    assert request()["body"]["code"] == "config"
    page.locator("#refresh").click()
    expect(page.locator("#live_signal")).to_have_class("signal green")
    expect(page.locator("#live_signal")).to_contain_text("LIVE MODE ON")
    expect(page.locator("#gate_signal")).to_have_class("signal red")
    expect(page.locator("#gate_signal")).to_contain_text("maximum stake not configured")
    configure(cap=True)
    for invalid in ["NaN", "Infinity", "0", "-1", "1.00000000000000000001"]:
        assert request(amount=invalid)["body"]["code"] == "invalid"
    assert request(security_token="not-accepted-from-browser")["body"]["code"] == "token"
    assert not bets(log)
    status = api("/api/status", {"provider": "duel", "currency": "SOL"})
    assert status["body"]["status"]["min_stake"] == "0.00007080"
    before = len(bets(log))
    low = request(currency="SOL", amount="0.00007079")
    assert low["body"]["code"] == "below-minimum"
    assert len(bets(log)) == before
    for provider_name in ("duel", "duckdice"):
        for value in ("0.00999999", "0.009", "0.001"):
            assert request(provider=provider_name, currency="SOL", amount=value, amount_unit="usd")["body"]["code"] == "below-minimum"
        assert request(provider=provider_name, currency="SOL", amount="141.26", amount_unit="usd")["body"]["code"] == "invalid"
    assert len(bets(log)) == before
    duck_low = request(provider="duckdice", currency="LTC", amount="0.00009999")
    assert duck_low["body"]["code"] == "below-minimum"
    assert len(bets(log)) == before
    configure(rates=False)
    unavailable = request(currency="SOL", amount="0.00007080")
    assert unavailable["body"]["code"] == "rate" and len(bets(log)) == before
    assert request(currency="SOL", amount="0.01", amount_unit="usd")["body"]["code"] == "rate"
    assert len(bets(log)) == before
    configure(rates=True)
    configure(cap_value="0.00007079")
    priced_conflict = api("/api/status", {"provider": "duel", "currency": "SOL"})
    assert priced_conflict["body"]["status"]["min_stake_state"] == "config"
    assert priced_conflict["body"]["status"]["min_stake"] == "0.00007080"
    configure(cap=True)
    configure(floor="2")
    conflict = request(currency="SOL", amount="0.00007080")
    assert conflict["body"]["code"] == "config"
    assert len(bets(log)) == before
    configure(floor="")
    page.locator("#refresh").click()
    print("PASS priced minimum, below-minimum with zero wagers, floor above ceiling")

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

    page.locator("#amount").fill("0.01")
    duel = click()
    assert duel["ok"] is True
    round_data = duel["result"]["data"]["round"]
    assert DecimalString(round_data["amount_won"]) == DecimalString("0.00014160")
    assert DecimalString(duel["result"]["usd_equivalent"]["stake"]) == DecimalString("0.01000050")
    expect(page.locator("#result")).to_contain_text("stake: ≈ $0.01 USD (0.00007080 SOL)")
    expect(page.locator("#result")).to_contain_text("payout: ≈ $0.02 USD (0.00014160 SOL)")
    shown = page.locator("#result").inner_text()
    assert "synthetic-browser-minted-token" not in shown and "must-not-reach-browser" not in shown
    expect(page.locator("#confirm")).to_have_count(0)
    wire = bets(log)[0]
    assert wire["body"] == {"amount": "0.00007080", "target": "4900", "bet_type": "under", "currency": 109, "security_token_matches": True}
    print("PASS Duel browser Bet -> real adapter -> authoritative displayed result")

    page.locator("#provider").select_option("duckdice")
    page.locator("#amount").fill("0.01")
    page.locator("#target").fill("5000")
    page.locator("#side").select_option("OVER")
    duck = click()
    assert duck["ok"] and duck["result"]["data"]["round"]["hash"] == "duck-result"
    expect(page.locator("#result")).to_contain_text('"balance": "1.25"')
    wire = bets(log)[1]
    assert wire["body"] == {"symbol": "SOL", "chance": "50.00", "isHigh": True, "amount": "0.00007080"}
    assert DecimalString(duck["result"]["usd_equivalent"]["profit"]) == DecimalString("0.01000050")
    expect(page.locator("#result")).to_contain_text("stake: ≈ $0.01 USD (0.00007080 SOL)")
    expect(page.locator("#result")).to_contain_text("payout: ≈ $0.02 USD (0.00014160 SOL)")
    expect(page.locator("#result")).to_contain_text("profit: ≈ $0.01 USD")
    assert wire["has_api_key"]
    assert "synthetic-e2e-key" not in page.locator("#result").inner_text()
    expect(page.locator("#confirm")).to_have_count(0)
    expect(page.locator("#status")).to_contain_text("1.25 SOL")
    expect(page.locator("#status")).to_contain_text("balance: ≈ $176.56 USD")
    expect(page.locator("#gate_signal")).to_have_class("signal green")
    expect(page.locator("#gate_signal")).to_contain_text("STATUS CHECKS PASSED")
    print("PASS red disabled/missing-cap and green enabled/configured status indicators")
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
    configure(mode="slow")
    page.locator("#amount").fill("0.21")
    with page.expect_response(lambda r: r.url == url + "api/bet" and r.request.post_data_json.get("amount") == "0.21") as slow_response:
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
    expect(page.locator("#confirm")).to_have_count(0)
    print("PASS consumed nonces and concurrent/double-submit prevention")

    # Last: an ambiguous provider error must latch across new nonces and reloads.
    configure(mode="error")
    page.locator("#amount").fill("0.20")
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
        configure(reset=True, mode="normal")
        malformed = request(amount=bad_amount)
        assert malformed["status"] == 502 and malformed["body"]["code"] == "ambiguous"
        assert request()["body"]["code"] == "blocked"
    print("PASS malformed payout and non-finite JSON result latch before response serialization")
    configure(reset=True, cap_value="0.00007080", mode="normal")
    exact = request(currency="SOL", amount="0.00007080")
    assert exact["status"] == 200 and exact["body"]["ok"], exact
    count = len(bets(log))
    assert request(currency="SOL", amount="0.00007081")["body"]["code"] == "invalid"
    assert len(bets(log)) == count
    print("PASS floor equals ceiling accepts exact minimum; greater stake refused")
    # A USD ceiling remains $1 across rates, providers, and native API requests.
    configure(cap=False, usd_cap="1.00")
    page.locator("#currency").fill("SOL")
    page.reload(wait_until="networkidle")
    page.locator("#provider").select_option("duckdice")
    expect(page.locator("#status")).to_contain_text("max stake: $1.00 USD hard reference-rate limit")
    expect(page.locator("#gate_signal")).to_have_class("signal green")
    page.locator("#amount").fill("1.00")
    capped = click()
    assert capped["ok"], capped
    assert DecimalString(bets(log)[-1]["body"]["amount"]) == DecimalString("0.00707964")
    assert DecimalString(capped["result"]["usd_equivalent"]["stake"]) <= 1
    count = len(bets(log))
    for provider_name in ("duel", "duckdice"):
        assert request(provider=provider_name, currency="SOL", amount="1.01", amount_unit="usd")["body"]["code"] == "invalid"
        assert request(provider=provider_name, currency="SOL", amount="0.00707965")["body"]["code"] == "invalid"
    assert len(bets(log)) == count
    configure(sol_rate="0.004")  # SOL's USD reference price doubles.
    assert request(currency="SOL", amount="0.00707964")["body"]["code"] == "invalid"
    changed = request(currency="SOL", amount="1.00", amount_unit="usd")
    assert changed["status"] == 200, changed
    assert DecimalString(bets(log)[-1]["body"]["amount"]) == DecimalString("0.00353982")
    count = len(bets(log))
    configure(cap_value="0.001")
    assert request(currency="SOL", amount="1.00", amount_unit="usd")["body"]["code"] == "invalid"
    configure(cap=False, rates=False)
    assert request(currency="SOL", amount="1.00", amount_unit="usd")["body"]["code"] == "rate"
    configure(rates=True)
    for invalid_cap in ("NaN", "Infinity", "0", "-1", "bad"):
        configure(usd_cap=invalid_cap)
        assert request(currency="SOL", amount="0.01", amount_unit="usd")["body"]["code"] == "config"
    assert len(bets(log)) == count
    print("PASS $1 USD cap, native bypass refusal, changed rates, tighter native cap, and fail-closed configuration")
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
