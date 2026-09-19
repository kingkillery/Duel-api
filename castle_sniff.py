"""Raw-CDP castle-roulette socket sniffer.

Bypasses Playwright entirely (its browser-level handshake wedges under the
canvas game's load). Talks straight to the page's DevTools target websocket:
Network.enable, then capture webSocketFrame* events from the game's socket.io
connection. Read-only: no clicks, no bets.

Usage:  py -3.13 castle_sniff.py [seconds]
"""
import json, sys, time
from pathlib import Path

import httpx
import websocket

PAGE_MATCH = "castle-roulette"
OUT = Path("castle_ws_frames.json")


def find_page_ws():
    r = httpx.get("http://127.0.0.1:9223/json/list", timeout=8)
    for t in r.json():
        if t.get("type") == "page" and PAGE_MATCH in t.get("url", ""):
            return t["webSocketDebuggerUrl"]
    return None


def main(seconds=150):
    ws_url = find_page_ws()
    if not ws_url:
        sys.exit("castle-roulette page not found")
    print(f"page target: {ws_url}")
    heartbeats = 0
    ws = websocket.create_connection(ws_url, timeout=10, suppress_origin=True)
    mid = [0]

    def cmd(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        # responses arrive interleaved with events; caller drains after

    cmd("Network.enable")
    cmd("Page.enable")
    cmd("Page.reload")

    frames, rest = [], []
    ws.settimeout(2)
    t0 = time.time()
    print(f"sniffing {seconds}s ...", flush=True)
    while time.time() - t0 < seconds:
        try:
            raw = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        except Exception as e:
            print(f"recv error: {e}")
            time.sleep(1)
            continue
        try:
            msg = json.loads(raw)
        except Exception:
            continue
        m, p = msg.get("method", ""), msg.get("params", {})
        if m == "Network.webSocketFrameReceived" or m == "Network.webSocketFrameSent":
            data = p.get("response", {}).get("payloadData", "")
            import re as _re
            if isinstance(data, str):
                # socket.io v4 text frames: "0" open, "40" ns-connect,
                # "42[...]" event, "2"/"3" heartbeat, "451-..." binary
                m2 = _re.match(r"^(\d+)(.*)$", data, _re.S)
                if m2 and m2.group(2):
                    data = m2.group(2)
            try:
                d = json.loads(data)
            except Exception:
                continue
            if isinstance(d, (int, float)) or d is None:  # heartbeats / open frames
                heartbeats += 1
                continue
            frames.append({"dir": "recv" if m.endswith("Received") else "sent",
                           "t": round(time.time() - t0, 2), "data": d})
        elif "id" in msg:  # command response
            rest.append(msg)
    print(f"captured {len(frames)} frames (+{heartbeats} heartbeats) -> {OUT}")
    ws.close()
    OUT.write_text(json.dumps(frames, indent=1))

    # summarize event names
    from collections import Counter
    names = Counter()
    for f in frames:
        d = f["data"]
        if isinstance(d, list):  # socket.io EVENT: [name, ...args]
            names[f"{f['dir']}:{d[0]}"] += 1
        elif isinstance(d, dict):
            names[f"{f['dir']}:{d.get('event', 'dict:' + ','.join(list(d)[:2]))}"] += 1
    print("\nevent types:")
    for k, v in names.most_common(30):
        print(f"  {v:4d}  {k}")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
