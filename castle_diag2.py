"""Correlate failed network requests with their URLs + capture all duel/roulette
API calls during a castle-roulette reload. Read-only."""
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
    global mid
    mid[0] += 1
    ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            m = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        if m.get("id") == mid[0]:
            return m
    raise RuntimeError(f"no ack for {method} in 60s")
cmd("Page.enable")
cmd("Page.reload")
ws.settimeout(1.0)

pending = {}   # requestId -> url
fails, api_calls, ws_frames = [], [], []
t0 = time.time()
while time.time() - t0 < 75:
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
    if m == "Network.requestWillBeSent":
        url = p.get("request", {}).get("url", "")
        pending[p.get("requestId")] = url
        if re.search(r"duel\.com/(api|assets)", url) and re.search(r"roulette|bet|game|config|state", url, re.I):
            api_calls.append(("req", p.get("request", {}).get("method"), url[:130],
                              (p.get("request", {}).get("postData") or "")[:200]))
    elif m == "Network.loadingFailed":
        url = pending.get(p.get("requestId"), "?")
        fails.append((p.get("errorText", ""), url[:140], p.get("type", "")))
    elif m == "Network.responseReceived":
        url = p.get("response", {}).get("url", "")
        if re.search(r"roulette|bet", url, re.I) and "api" in url:
            api_calls.append(("resp", p.get("response", {}).get("status"), url[:130], ""))
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
        ws_frames.append((("sent" if m.endswith("Sent") else "recv"), json.dumps(d)[:250]))

Path("castle_diag2.json").write_text(json.dumps({
    "fails": fails, "api_calls": api_calls, "ws_frames": ws_frames}, indent=1))

print(f"=== {len(fails)} failed loads ===")
for e, u, t in fails:
    print(f"  {e:24s} {t:8s} {u}")
print(f"\n=== {len(api_calls)} duel api/asset calls (roulette|bet|game|config) ===")
for k, s, u, post in api_calls[:30]:
    print(f"  {k:5s} {s} {u} {post[:120]}")
print(f"\n=== {len(ws_frames)} game ws frames ===")
for d, s in ws_frames[:20]:
    print(f"  {d:4s} {s}")
ws.close()
