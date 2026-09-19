"""Diagnose castle-roulette page: reload + capture console errors, failed
requests, socket frames. Read-only."""
import json, re, time
from pathlib import Path

import httpx
import websocket

r = httpx.get("http://127.0.0.1:9223/json/list", timeout=8)
ws_url = next(t["webSocketDebuggerUrl"] for t in r.json()
              if t.get("type") == "page" and "castle-roulette" in t.get("url", ""))
ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
mid = [0]

def cmd(method, params=None):
    mid[0] += 1
    ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == mid[0]:
            return m

cmd("Runtime.enable")
cmd("Log.enable")
cmd("Network.enable")
cmd("Page.enable")
cmd("Page.reload")
ws.settimeout(1.0)

events = []
t0 = time.time()
while time.time() - t0 < 90:
    try:
        raw = ws.recv()
    except websocket.WebSocketTimeoutException:
        continue
    except Exception:
        break
    try:
        msg = json.loads(raw)
    except Exception:
        continue
    m, p = msg.get("method", ""), msg.get("params", {})
    if m == "Runtime.consoleAPICalled":
        args = " ".join(str(a.get("value", a.get("description", "")))[:150] for a in p.get("args", []))
        events.append(("console", p.get("type", "?"), args[:400]))
    elif m == "Log.entryAdded":
        e = p.get("entry", {})
        events.append(("log", e.get("level", "?"), f"{e.get('source','')}: {e.get('text','')[:300]} url={e.get('url','')[:100]}"))
    elif m == "Network.loadingFailed":
        events.append(("loadfail", p.get("type", "?"), f"{p.get('errorText','')} {p.get('blockedReason','') or ''}"))
    elif m == "Network.webSocketCreated":
        events.append(("ws-created", "", p.get("url", "")[:100]))
    elif m in ("Network.webSocketFrameSent", "Network.webSocketFrameReceived"):
        data = p.get("response", {}).get("payloadData", "")
        if isinstance(data, str):
            m2 = re.match(r"^(\d+)(.*)$", data, re.S)
            if m2 and m2.group(2):
                data = m2.group(2)
        try:
            d = json.loads(data)
        except Exception:
            continue
        if isinstance(d, (int, float)) or d is None:
            continue
        events.append(("ws-" + ("sent" if m.endswith("Sent") else "recv"), "", json.dumps(d)[:300]))

Path("castle_diag.json").write_text(json.dumps(events, indent=1))
print(f"{len(events)} events")
for kind, lvl, txt in events[:60]:
    print(f"{kind:10s} {lvl:8s} {txt[:220]}")
ws.close()
