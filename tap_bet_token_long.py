"""Passive CDP tap: capture security_token from a real browser POST /api/v2/dice/bet.

NEVER mints via /api/v2/user/security/token.
NEVER places a bet.
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "fresh_token.txt"
CAP = ROOT / "captured_bet.json"
CDP = "http://127.0.0.1:9223"
WAIT_S = 420
MIN_TOKEN_LEN = 20
BET_MARK = "/api/v2/dice/bet"
MINT_MARK = "/api/v2/user/security/token"


def _extract_token(post_data: str | None) -> str | None:
    if not post_data:
        return None
    try:
        body = json.loads(post_data)
    except Exception:
        return None
    tok = body.get("security_token")
    if isinstance(tok, str) and len(tok) >= MIN_TOKEN_LEN:
        return tok
    return None


def _post_data(req) -> str | None:
    data = getattr(req, "post_data", None)
    if data:
        return data
    try:
        buf = req.post_data_buffer
        if buf:
            return buf.decode("utf-8", errors="replace")
    except Exception:
        pass
    return None


def pick_page(browser):
    pages = [pg for ctx in browser.contexts for pg in ctx.pages]
    duel = [pg for pg in pages if "duel.com" in (pg.url or "")]
    if not duel:
        return None
    for pg in duel:
        if "/dice" in (pg.url or ""):
            return pg
    return duel[0]


def report(status: str, token_len: int, page_url: str, elapsed_s: float) -> None:
    print(
        json.dumps(
            {
                "status": status,
                "token_len": token_len,
                "page_url": page_url,
                "elapsed_s": round(elapsed_s, 3),
            }
        ),
        flush=True,
    )


async def main() -> int:
    started = time.monotonic()
    page_url = ""
    OUT.write_text("", encoding="utf-8")
    print(f"armed on CDP {CDP}; waiting up to {WAIT_S}s for POST {BET_MARK}", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP)
        page = pick_page(browser)
        if page is None:
            print("NO_DUEL_PAGE", flush=True)
            report("NO_DUEL_PAGE", 0, "", time.monotonic() - started)
            return 2

        page_url = page.url or ""
        print(f"watching: {page_url}", flush=True)

        done = asyncio.Event()
        box: dict = {}

        def on_request(req) -> None:
            try:
                url = req.url or ""
                method = (req.method or "").upper()
                if MINT_MARK in url:
                    return
                if BET_MARK not in url or method != "POST":
                    return
                raw = _post_data(req)
                tok = _extract_token(raw)
                if not tok:
                    print(f"bet_seen_no_token url={url[:120]}", flush=True)
                    return
                try:
                    body = json.loads(raw or "{}")
                except Exception:
                    body = {"raw": raw}
                CAP.write_text(
                    json.dumps(
                        {
                            "url": url,
                            "method": method,
                            "request_body": body,
                            "security_token": tok,
                            "captured_at": time.time(),
                            "page_url": page_url,
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                OUT.write_bytes(tok.encode("utf-8"))
                box["token"] = tok
                print(f"SAVED token_len={len(tok)} -> {OUT.name}", flush=True)
                done.set()
            except Exception as e:
                print(f"handler_err: {e}", flush=True)

        for ctx in browser.contexts:
            ctx.on("request", on_request)
        page.on("request", on_request)

        print("ROLL_NOW", flush=True)
        try:
            await asyncio.wait_for(done.wait(), timeout=WAIT_S)
        except asyncio.TimeoutError:
            print("TIMEOUT", flush=True)
            report("TIMEOUT", 0, page_url, time.monotonic() - started)
            return 1

        tok = box.get("token", "")
        print(f"OK token_len={len(tok)}", flush=True)
        report("CAPTURED", len(tok), page_url, time.monotonic() - started)
        return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except SystemExit:
        raise
    except Exception as exc:
        traceback.print_exc()
        print(f"FATAL: {exc}", flush=True)
        raise SystemExit(1)
