"""Castle roulette one-bet protocol probe (resilient window polling)."""
import json, re, time
from pathlib import Path
import httpx
import websocket

STAKE = "0.0000005"  # 0.5 uBTC
CAPTURE_SECS_AFTER_BET = 90
OUT = Path("castle_probe_capture.json")

def get_tab():
    r = httpx.get("http://127.0.0.1:9223/json/list", timeout=8)
    pages = [t for t in r.json() if t.get("type") == "page" and "castle-roulette" in t.get("url", "")]
    if not pages:
        raise RuntimeError("No castle-roulette tab found")
    return pages[-1]["webSocketDebuggerUrl"]

def main():
    ws_url = get_tab()
    print(f"Connecting to tab target: {ws_url}", flush=True)
    ws = websocket.create_connection(ws_url, timeout=30, suppress_origin=True)
    mid = [0]

    def cmd(method, params=None):
        mid[0] += 1
        ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                m = json.loads(ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if m.get("id") == mid[0]:
                return m
        return {}

    def evaluate(expr):
        res = cmd("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        return res.get("result", {}).get("result", {}).get("value")

    cmd("Runtime.enable")
    cmd("Network.enable")
    ws.settimeout(1.0)
    events = []

    def drain():
        while True:
            try:
                raw = ws.recv()
            except websocket.WebSocketTimeoutException:
                break
            except Exception:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            m, p = msg.get("method", ""), msg.get("params", {})
            if m in ("Network.webSocketFrameSent", "Network.webSocketFrameReceived"):
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
                events.append({"t": round(time.time(), 2),
                               "kind": "ws_" + ("sent" if m.endswith("Sent") else "recv"),
                               "data": d})
            elif m == "Network.requestWillBeSent" and re.search(r"roulette|bet|rakeback", p.get("request", {}).get("url", ""), re.I):
                req = p.get("request", {})
                events.append({"t": round(time.time(), 2), "kind": "rest",
                               "data": {"method": req.get("method"), "url": req.get("url", "")[:120],
                                        "post": (req.get("postData") or "")[:400]}})

    print("Polling for active betting window (buttons enabled)...", flush=True)
    deadline = time.time() + 300
    ready = False
    while time.time() < deadline:
        drain()
        info = evaluate("""(() => {
          const btns = Array.from(document.querySelectorAll('button'))
            .filter(b => /2X/.test(b.innerText||'') && /place bet/i.test(b.innerText||'') && b.offsetParent);
          const enabled = btns.filter(b => !b.disabled);
          const bodyText = (document.body.innerText || '').slice(0, 300);
          return JSON.stringify({total: btns.length, enabled: enabled.length, textSnippet: bodyText});
        })()""")
        if info:
            parsed = json.loads(info)
            if parsed.get("enabled", 0) > 0:
                print(f"Betting window OPEN! Enabled 2X buttons: {parsed['enabled']}", flush=True)
                ready = True
                break
            else:
                # Print status every ~15s
                if int(time.time()) % 15 == 0:
                    print(f"Waiting... (found {parsed.get('total', 0)} 2X buttons, 0 enabled)", flush=True)
        time.sleep(2)

    if not ready:
        ws.close()
        OUT.write_text(json.dumps(events, indent=1))
        print("Timeout waiting for betting window", flush=True)
        return

    # Set stake
    v = evaluate("""(() => {
      const inp = Array.from(document.querySelectorAll('input'))
        .filter(i => i.offsetParent && (i.placeholder === '0.00'))[0];
      if (!inp) return 'NO_INPUT';
      const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      set.call(inp, '%s');
      inp.dispatchEvent(new Event('input', {bubbles: true}));
      inp.dispatchEvent(new Event('change', {bubbles: true}));
      return inp.value;
    })()""" % STAKE)
    print(f"Stake input configured: {v}", flush=True)

    # Click 2X Place bet
    click_res = evaluate("""(() => {
      const btns = Array.from(document.querySelectorAll('button'))
        .filter(b => /2X/.test(b.innerText||'') && /place bet/i.test(b.innerText||'') && b.offsetParent && !b.disabled);
      if (!btns.length) return 'NO_ENABLED_BTN';
      btns[0].click();
      return 'CLICKED';
    })()""")
    print(f"Action trigger: {click_res}", flush=True)
    t_bet = time.time()

    print(f"Capturing settlement traffic for {CAPTURE_SECS_AFTER_BET}s...", flush=True)
    while time.time() - t_bet < CAPTURE_SECS_AFTER_BET:
        drain()
        time.sleep(0.5)

    ws.close()
    OUT.write_text(json.dumps(events, indent=1))
    print(f"Done! Captured {len(events)} events -> {OUT}", flush=True)
    for e in events[-20:]:
        print(f"[{e['t']:.0f}] {e['kind']:8s} {json.dumps(e['data'])[:240]}")

if __name__ == "__main__":
    main()
