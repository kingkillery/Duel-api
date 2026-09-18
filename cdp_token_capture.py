"""CDP capture of a browser-minted duel.com security_token.

Attaches to an already-running Chrome over CDP (127.0.0.1:9223), finds the
duel.com page (prefers /dice), and listens for:

  - POST /api/v2/user/security/token responses  (token mint)
  - POST /api/v2/dice/bet request postData      (contains security_token)

If nothing arrives passively within 15s it attempts an in-page mint via
page.evaluate(fetch) — read-only with respect to money (token mint only,
never a bet). If the mint fails it prints ROLL_NOW and stays armed for a
manual roll up to 4 minutes total.

Outputs:
  fresh_token.txt   raw token string only
  captured_bet.json latest dice/bet request (url, body, headers, captured_at)

Usage: py -3.13 cdp_token_capture.py
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
import time

from playwright.sync_api import sync_playwright

CDP_ENDPOINT = "http://127.0.0.1:9223"
TOKEN_PATH = "fresh_token.txt"
BET_PATH = "captured_bet.json"

PASSIVE_WINDOW_S = 15.0
TOTAL_BUDGET_S = 240.0  # 4 minutes armed, overall cap

# Device UUID observed in prior captures (headers + mint body fall back to it).
KNOWN_UUID = "fd0a6773-ffe1-4a0d-ac43-156ec7a309e5"

TOKEN_URL_MARK = "security/token"
BET_URL_MARK = "dice/bet"

MINT_JS = """
async (uuid) => {
  const r = await fetch("/api/v2/user/security/token", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      "x-duel-device-identifier": uuid,
      "x-env-class": "green",
    },
    body: JSON.stringify({ uuid, code: "0000", type: "standard" }),
  });
  const text = await r.text();
  return { status: r.status, body: text.slice(0, 2000) };
}
"""


def log(msg: str) -> None:
    stamp = _dt.datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {msg}", flush=True)


def extract_token(obj) -> str | None:
    """Pull a plausible token string out of a mint response JSON blob."""
    if not isinstance(obj, dict):
        return None
    containers = [obj]
    if isinstance(obj.get("data"), dict):
        containers.append(obj["data"])
    for c in containers:
        for key in ("token", "security_token", "securityToken"):
            v = c.get(key)
            if isinstance(v, str) and len(v) >= 20:
                return v
    return None


def main() -> int:
    started = time.monotonic()
    state = {
        "token": None,
        "source": None,  # "passive_mint" | "manual_roll" | "in_page_mint"
        "bet": None,
        "page_url": None,
        "error": None,
    }
    pending_responses: list = []  # (kind, Response) read bodies later, outside handlers
    seen_requests: list[dict] = []

    def on_request(request):
        try:
            url = request.url
            if TOKEN_URL_MARK not in url and BET_URL_MARK not in url:
                return
            post = request.post_data or ""
            entry = {
                "ts": time.time(),
                "url": url,
                "method": request.method,
                "post_data": post,
                "headers": dict(request.headers),
            }
            seen_requests.append(entry)
            log(f"REQUEST {request.method} {url} post={post[:220]}")
            if BET_URL_MARK in url and post:
                try:
                    body = json.loads(post)
                except ValueError:
                    body = None
                token = None
                if isinstance(body, dict):
                    v = body.get("security_token")
                    if isinstance(v, str) and len(v) >= 20:
                        token = v
                state["bet"] = {**entry, "body": body, "captured_at": _dt.datetime.now().isoformat(timespec="seconds")}
                if token and not state["token"]:
                    state["token"] = token
                    state["source"] = "manual_roll"
                    log(f"TOKEN from dice/bet postData ({len(token)} chars)")
        except Exception as exc:  # handler must never raise into CDP
            log(f"request-handler error: {exc!r}")

    def on_response(response):
        try:
            url = response.url
            if TOKEN_URL_MARK in url:
                pending_responses.append(response)
                log(f"RESPONSE {response.status} {url} (body deferred)")
        except Exception as exc:
            log(f"response-handler error: {exc!r}")

    with sync_playwright() as pw:
        log(f"connecting over CDP to {CDP_ENDPOINT}")
        browser = pw.chromium.connect_over_cdp(CDP_ENDPOINT)
        log(f"attached: {browser.version}")

        page = None
        for ctx in browser.contexts:
            for cand in ctx.pages:
                if "duel.com" in cand.url:
                    if "/dice" in cand.url:
                        page = cand
                        break
                    page = page or cand
            if page is not None and "/dice" in page.url:
                break
        if page is None:
            state["error"] = "no duel.com page found in CDP browser"
            log(state["error"])
            finish(started, state)
            return 2
        state["page_url"] = page.url
        log(f"target page: {page.url!r} title={page.title()!r}")

        ctx = page.context
        ctx.on("request", on_request)
        ctx.on("response", on_response)

        def drain_pending() -> None:
            while pending_responses:
                resp = pending_responses.pop(0)
                try:
                    text = resp.text()
                except Exception as exc:
                    log(f"body read failed for {resp.url}: {exc!r}")
                    continue
                log(f"mint response {resp.status} body[:200]={text[:200]!r}")
                try:
                    blob = json.loads(text)
                except ValueError:
                    blob = None
                tok = extract_token(blob)
                if tok and not state["token"]:
                    state["token"] = tok
                    state["source"] = "passive_mint"
                    log(f"TOKEN from security/token response ({len(tok)} chars)")

        def deadline_left() -> float:
            return TOTAL_BUDGET_S - (time.monotonic() - started)

        # Phase 1: passive listen (15s). Page must stay alive; pump events.
        log(f"passive listen for {PASSIVE_WINDOW_S:.0f}s (security/token, dice/bet)")
        t0 = time.monotonic()
        while time.monotonic() - t0 < PASSIVE_WINDOW_S and not state["token"]:
            page.wait_for_timeout(250)
            drain_pending()
        drain_pending()

        # Phase 2: in-page mint attempt (read-only; never a bet).
        if not state["token"]:
            uuid_val = None
            try:
                uuid_val = page.evaluate("() => localStorage.getItem('security:uuid')")
            except Exception as exc:
                log(f"localStorage read failed: {exc!r}")
            if not uuid_val:
                uuid_val = KNOWN_UUID
                log(f"localStorage security:uuid absent; falling back to known uuid {uuid_val}")
            else:
                log(f"using localStorage security:uuid {uuid_val}")
            try:
                result = page.evaluate(MINT_JS, uuid_val)
                status = result.get("status")
                body_text = result.get("body", "")
                log(f"in-page mint HTTP {status} body[:300]={body_text[:300]!r}")
                try:
                    blob = json.loads(body_text)
                except ValueError:
                    blob = None
                tok = extract_token(blob)
                if tok:
                    state["token"] = tok
                    state["source"] = "in_page_mint"
                    log(f"TOKEN minted in-page ({len(tok)} chars)")
                else:
                    log("in-page mint did not yield a token (geo-block / 2fa rejection)")
            except Exception as exc:
                log(f"in-page mint evaluate failed: {exc!r}")
            drain_pending()

        # Phase 3: armed wait for manual roll, up to the 4-minute total cap.
        if not state["token"]:
            bar = "*" * 66
            print(f"\n{bar}", flush=True)
            print("ROLL_NOW: click ROLL on the dice page.", flush=True)
            print(f"CDP is attached to {page.url} and listening for {BET_URL_MARK}.", flush=True)
            print(f"Armed for another {deadline_left():.0f}s.", flush=True)
            print(f"{bar}\n", flush=True)
            next_banner = time.monotonic() + 30
            while deadline_left() > 0 and not state["token"]:
                page.wait_for_timeout(250)
                drain_pending()
                if time.monotonic() > next_banner:
                    next_banner += 30
                    print("ROLL_NOW (still armed, listening for a manual roll...)", flush=True)
            drain_pending()

        try:
            ctx.remove_listener("request", on_request)
            ctx.remove_listener("response", on_response)
        except Exception:
            pass

    status = finish(started, state)
    return status


def finish(started: float, state: dict) -> int:
    elapsed = time.monotonic() - started
    token = state["token"]
    if token:
        with open(TOKEN_PATH, "w", encoding="utf-8", newline="") as fh:
            fh.write(token)  # raw token string only
        status = "ok"
    else:
        status = "roll_required"
    if state["bet"]:
        with open(BET_PATH, "w", encoding="utf-8") as fh:
            json.dump(state["bet"], fh, indent=1)
    report = {
        "status": status,
        "token_len": len(token) if token else 0,
        "source": state["source"],
        "page_url": state["page_url"],
    }
    if state["error"]:
        report["error"] = state["error"]
    report["elapsed_s"] = round(elapsed, 1)
    print("\nREPORT " + json.dumps(report), flush=True)
    return 0 if token else 3


if __name__ == "__main__":
    sys.exit(main())
