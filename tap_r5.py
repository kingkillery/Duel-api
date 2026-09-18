"""Round 5 passive tap: capture browser-minted security_token from POST /api/v2/dice/bet."""
import asyncio, json, time
from pathlib import Path
from playwright.async_api import async_playwright

WINDOW = 2700  # 45 min

async def main():
    done = {"hit": False}
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        pg = next((x for x in b.contexts[0].pages if "duel.com/dice" in x.url), None)
        if pg is None:
            print("NO DICE TAB", flush=True)
            return
        print(f"armed on {pg.url}", flush=True)

        def on_req(req):
            if done["hit"] or req.method != "POST" or "/api/v2/dice/bet" not in req.url:
                return
            try:
                body = json.loads(req.post_data or "{}")
            except Exception:
                return
            tok = body.get("security_token", "")
            if len(tok) >= 20:
                done["hit"] = True
                Path("fresh_token.txt").write_text(tok)
                Path("captured_bet.json").write_text(json.dumps({
                    "source": "browser_dice_bet",
                    "captured_at": time.strftime("%H:%M:%S"),
                    "payload": body,
                }, indent=1))
                print("TOKEN SAVED", flush=True)

        pg.on("request", on_req)
        deadline = time.time() + WINDOW
        while time.time() < deadline and not done["hit"]:
            await asyncio.sleep(2)
        print("CAPTURED" if done["hit"] else "TIMEOUT", flush=True)

asyncio.run(main())
