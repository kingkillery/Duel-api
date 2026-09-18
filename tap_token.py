"""CDP tap: capture next dice security_token into fresh_token.txt."""
from __future__ import annotations
import argparse, asyncio, json
from pathlib import Path
from playwright.async_api import async_playwright
ROOT = Path(__file__).resolve().parent
OUT_TOKEN = ROOT / "fresh_token.txt"
OUT_DUMP = ROOT / "captured_bet.json"
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp-url", default="http://127.0.0.1:9223")
    ap.add_argument("--timeout", type=float, default=300.0)
    args = ap.parse_args()
    captured, token_box = [], {"token": None}
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(args.cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        pages = context.pages
        page = next((pg for pg in pages if "duel.com" in (pg.url or "")), None)
        if page is None:
            page = pages[0] if pages else await context.new_page()
        print(f"armed on: {page.url}", flush=True)
        async def on_request(request):
            url = request.url or ""
            if "/api/v2/user/security/token" not in url and "/api/v2/dice/bet" not in url:
                return
            body = request.post_data or ""
            captured.append({"url": url, "body": body, "headers": dict(request.headers)})
            print(f"CAPTURED: {url}", flush=True)
            if "/api/v2/dice/bet" in url and body:
                try:
                    tok = json.loads(body).get("security_token")
                    if tok:
                        token_box["token"] = tok
                        OUT_TOKEN.write_text(tok, encoding="utf-8")
                        print(f"TOKEN_SAVED len={len(tok)}", flush=True)
                except Exception as exc:
                    print(f"parse_failed: {exc}", flush=True)
        async def on_response(response):
            url = response.url or ""
            if "/api/v2/user/security/token" not in url:
                return
            try:
                text = await response.text()
            except Exception as exc:
                text = f"<read_failed: {exc}>"
            captured.append({"url": url, "response_status": response.status, "response_body": text})
            print(f"TOKEN_RESPONSE status={response.status}", flush=True)
            try:
                data = json.loads(text)
                tok = data.get("token") or (data.get("data") or {}).get("token")
                if tok:
                    token_box["token"] = tok
                    OUT_TOKEN.write_text(tok, encoding="utf-8")
                    print(f"TOKEN_SAVED_FROM_RESPONSE len={len(tok)}", flush=True)
            except Exception:
                pass
        page.on("request", on_request)
        page.on("response", on_response)
        def attach(pg):
            pg.on("request", on_request)
            pg.on("response", on_response)
        context.on("page", attach)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + args.timeout
        while loop.time() < deadline and not token_box["token"]:
            await asyncio.sleep(0.25)
        OUT_DUMP.write_text(json.dumps({"done": True, "error": None if token_box["token"] else "timeout", "token": token_box["token"], "requests": captured}, indent=2), encoding="utf-8")
        print("SAVED captured_bet.json" if token_box["token"] else "TIMEOUT waiting for token", flush=True)
        return 0 if token_box["token"] else 1
if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
