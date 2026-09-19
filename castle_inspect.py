"""Raw-CDP page inspector for the castle-roulette tab (read-only)."""
import json, sys
import httpx
import websocket

r = httpx.get("http://127.0.0.1:9223/json/list", timeout=8)
ws_url = next(t["webSocketDebuggerUrl"] for t in r.json()
              if t.get("type") == "page" and "castle-roulette" in t.get("url", ""))
ws = websocket.create_connection(ws_url, timeout=15, suppress_origin=True)

mid = [0]
def cmd(method, params=None):
    mid[0] += 1
    ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == mid[0]:
            return msg.get("result", {})

cmd("Runtime.enable")

PROBES = {
  "url": "location.href",
  "title": "document.title",
  "body_head": "(document.body.innerText||'').slice(0,600)",
  "canvases": "document.querySelectorAll('canvas').length",
  "videos": "Array.from(document.querySelectorAll('video')).map(v => ({w: v.videoWidth, h: v.videoHeight, playing: !v.paused, t: v.currentTime}))",
  "iframes": "Array.from(document.querySelectorAll('iframe')).map(f => f.src.slice(0,80))",
  "err_overlay": "(document.querySelector('.error, [class*=error], [class*=Error]')||{}).innerText || null",
  "buttons": "Array.from(document.querySelectorAll('button')).map(b => (b.innerText||'').trim()).filter(Boolean).slice(0, 25)",
}
for name, expr in PROBES.items():
    res = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
    val = res.get("result", {}).get("value")
    print(f"=== {name} ===")
    print(json.dumps(val, indent=1)[:800] if not isinstance(val, str) else val[:800])
    print()
ws.close()
