"""Read castle-roulette stake controls (read-only)."""
import json
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
        m = json.loads(ws.recv())
        if m.get("id") == mid[0]:
            return m

cmd("Runtime.enable")
JS = r"""(() => {
  const inputs = Array.from(document.querySelectorAll('input')).map(i => ({
    type: i.type, val: i.value, vis: !!i.offsetParent,
    ph: i.placeholder, cls: (i.className || '').slice(0, 40)
  }));
  const btns = Array.from(document.querySelectorAll('button'))
    .filter(b => /place bet|rebet|autobet/i.test(b.innerText || ''))
    .map(b => ({txt: (b.innerText || '').replace(/\n/g, ' | ').trim().slice(0, 40),
                disabled: b.disabled, vis: !!b.offsetParent}));
  const bal = (document.querySelector('[class*=balance], [class*=Balance]') || {}).innerText || null;
  return JSON.stringify({inputs, btns, bal});
})()"""
res = cmd("Runtime.evaluate", {"expression": JS, "returnByValue": True})
val = res.get("result", {}).get("result", {}).get("value")
print(json.dumps(json.loads(val), indent=1) if val else json.dumps(res)[:800])
ws.close()
