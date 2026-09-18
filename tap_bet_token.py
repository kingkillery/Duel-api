"""Passive CDP tap: capture security_token from a real browser POST /api/v2/dice/bet.

NEVER mints via /api/v2/user/security/token.
NEVER places a bet.
NEVER recaptures the session / navigates the open tab.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent
TOKEN = ROOT / "fresh_token.txt"
CAP = ROOT / "captured_bet.json"
CDP_HTTP = "http://127.0.0.1:9223"
WAIT_S = 420
MIN_TOKEN_LEN = 20
BET_MARK = "/api/v2/dice/bet"
MINT_MARK = "/api/v2/user/security/token"

HOOK_JS = r"""
(() => {
  const push = (url, body) => {
    try {
      const raw = typeof body === "string" ? body : (body == null ? "" : String(body));
      if (typeof window.__duelTapPush === "function") {
        window.__duelTapPush(JSON.stringify({url: String(url || ""), body: raw}));
      }
    } catch (e) {}
  };
  const consider = (url, method, body) => {
    const u = String(url || "");
    const m = String(method || "GET").toUpperCase();
    if (u.includes("/api/v2/dice/bet") && m === "POST") push(u, body);
  };
  const origFetch = window.__duelTapOrigFetch || window.fetch;
  window.__duelTapOrigFetch = origFetch;
  window.fetch = function(input, init) {
    try {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      const method = (init && init.method) || (input && input.method) || "GET";
      const body = (init && init.body) || (input && input.body);
      consider(url, method, body);
    } catch (e) {}
    return origFetch.apply(this, arguments);
  };
  if (!window.__duelTapXhrWrapped) {
    window.__duelTapXhrWrapped = true;
    const origOpen = XMLHttpRequest.prototype.open;
    const origSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url) {
      this.__duelMethod = method;
      this.__duelUrl = url;
      return origOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function(body) {
      try { consider(this.__duelUrl, this.__duelMethod, body); } catch (e) {}
      return origSend.apply(this, arguments);
    };
  }
  window.__duelTapInstalled = true;
  return "installed";
})()
"""


def _parse_payload(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _token_of(payload: dict) -> str | None:
    tok = payload.get("security_token")
    if isinstance(tok, str) and len(tok) >= MIN_TOKEN_LEN:
        return tok
    return None


def _is_bet_url(url: str, method: str) -> bool:
    if (method or "").upper() != "POST":
        return False
    u = url or ""
    if MINT_MARK in u:
        return False
    return BET_MARK in u


def _write_capture(url: str, payload: dict, tok: str) -> None:
    TOKEN.write_bytes(tok.encode("utf-8"))
    rec = {
        "source": "browser_dice_bet",
        "url": url,
        "payload": payload,
    }
    CAP.write_text(json.dumps(rec, indent=2), encoding="utf-8")


def _report(status: str, token_len: int, page_url: str, elapsed_s: float) -> None:
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


class CdpSession:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self.ws = ws
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}

    async def send(
        self,
        method: str,
        params: dict | None = None,
        timeout: float = 8.0,
        session_id: str | None = None,
    ):
        self._id += 1
        nid = self._id
        fut = asyncio.get_running_loop().create_future()
        self._pending[nid] = fut
        msg: dict = {"id": nid, "method": method}
        if params is not None:
            msg["params"] = params
        if session_id:
            msg["sessionId"] = session_id
        await self.ws.send_str(json.dumps(msg))
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(nid, None)
            raise

    def handle_message(self, data: dict) -> dict | None:
        mid = data.get("id")
        if mid in self._pending:
            fut = self._pending.pop(mid)
            if not fut.done():
                fut.set_result(data)
            return None
        if "method" in data:
            return data
        return None


async def _browser_ws_url(http: aiohttp.ClientSession) -> str:
    async with http.get(f"{CDP_HTTP}/json/version") as resp:
        data = await resp.json()
    return data["webSocketDebuggerUrl"]


async def _find_dice_page(http: aiohttp.ClientSession) -> dict | None:
    async with http.get(f"{CDP_HTTP}/json/list") as resp:
        tabs = await resp.json()
    for t in tabs:
        if t.get("type") == "page" and "duel.com/dice" in (t.get("url") or ""):
            return t
    return None


async def main() -> int:
    t0 = time.time()
    TOKEN.write_bytes(b"")
    page_url = ""
    captured: dict = {}
    done = asyncio.Event()

    def consider(url: str, raw: str | None, via: str) -> None:
        if done.is_set() or captured.get("token"):
            return
        if MINT_MARK in (url or ""):
            print(f"ignore_mint via={via} url={(url or '')[:160]}", flush=True)
            return
        payload = _parse_payload(raw)
        if payload is None:
            print(f"bet_seen_no_json via={via} url={(url or '')[:160]}", flush=True)
            return
        tok = _token_of(payload)
        if not tok:
            print(f"bet_seen_no_token via={via} url={(url or '')[:160]}", flush=True)
            return
        _write_capture(url, payload, tok)
        captured["token"] = tok
        captured["url"] = url
        print(f"CAPTURED len={len(tok)} via={via}", flush=True)
        done.set()

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=None)
    async with aiohttp.ClientSession(timeout=timeout) as http:
        target = await _find_dice_page(http)
        if target is None:
            print("NO_DICE_PAGE", flush=True)
            _report("NO_DICE_PAGE", 0, "", time.time() - t0)
            return 2
        page_url = target.get("url") or ""
        target_id = target.get("id")
        ws_url = await _browser_ws_url(http)
        if not ws_url or not target_id:
            print("NO_WS", flush=True)
            _report("NO_DICE_PAGE", 0, page_url, time.time() - t0)
            return 2

        async with http.ws_connect(ws_url, heartbeat=20, max_msg_size=8 * 1024 * 1024) as ws:
            cdp = CdpSession(ws)
            sessions: set[str] = set()

            async def get_post_data_for(url: str, request_id: str, session_id: str | None) -> None:
                try:
                    extra = await cdp.send(
                        "Network.getRequestPostData",
                        {"requestId": request_id},
                        timeout=3,
                        session_id=session_id,
                    )
                    result = extra.get("result") or {}
                    consider(url, result.get("postData"), "network")
                except Exception as exc:
                    print(f"postdata_fail {exc}", flush=True)

            async def setup_session(session_id: str | None) -> None:
                await cdp.send(
                    "Network.enable",
                    {"maxPostDataSize": 1048576},
                    session_id=session_id,
                )
                await cdp.send("Runtime.enable", session_id=session_id)
                try:
                    await cdp.send(
                        "Runtime.addBinding",
                        {"name": "__duelTapPush"},
                        session_id=session_id,
                    )
                except Exception as exc:
                    print(f"binding_fail {exc}", flush=True)
                hook = await cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": HOOK_JS,
                        "returnByValue": True,
                        "awaitPromise": False,
                    },
                    session_id=session_id,
                )
                print(
                    f"hook session={session_id} {json.dumps((hook.get('result') or {}).get('result'))}",
                    flush=True,
                )

            def handle_event(method: str, params: dict, session_id: str | None) -> None:
                if method == "Target.attachedToTarget":
                    info = params.get("targetInfo") or {}
                    sid = params.get("sessionId")
                    ttype = info.get("type")
                    print(
                        f"attached type={ttype} url={(info.get('url') or '')[:160]} sid={sid}",
                        flush=True,
                    )
                    if sid and ttype in ("iframe", "worker", "service_worker", "page"):
                        sessions.add(sid)
                        asyncio.create_task(_safe_setup(sid))
                    return
                if method == "Runtime.bindingCalled":
                    if params.get("name") != "__duelTapPush":
                        return
                    payload = params.get("payload") or ""
                    try:
                        item = json.loads(payload)
                    except Exception:
                        return
                    consider(item.get("url") or page_url, item.get("body"), "hook")
                    return
                if method == "Network.requestWillBeSent":
                    req = params.get("request") or {}
                    u = req.get("url") or ""
                    m = req.get("method") or ""
                    has_post = bool(req.get("postData")) or bool(req.get("hasPostData"))
                    if "/api/v2/" in u:
                        print(
                            f"cdp_seen {m} {u[:180]} hasPost={has_post}",
                            flush=True,
                        )
                    if not _is_bet_url(u, m):
                        return
                    raw = req.get("postData")
                    if raw:
                        consider(u, raw, "network")
                        return
                    net_id = params.get("requestId")
                    if net_id:
                        asyncio.create_task(get_post_data_for(u, net_id, session_id))
                    return

            async def _safe_setup(sid: str | None) -> None:
                try:
                    await setup_session(sid)
                except Exception as exc:
                    print(f"child_setup_fail sid={sid} {exc}", flush=True)

            async def pump() -> None:
                async for msg in ws:
                    if done.is_set():
                        break
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            print(f"ws_end type={msg.type}", flush=True)
                            break
                        continue
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue
                    event = cdp.handle_message(data)
                    if event:
                        try:
                            handle_event(
                                event["method"],
                                event.get("params") or {},
                                event.get("sessionId"),
                            )
                        except Exception as exc:
                            print(f"handle_fail {exc}", flush=True)

            pump_task = asyncio.create_task(pump())
            try:
                await cdp.send(
                    "Target.setAutoAttach",
                    {
                        "autoAttach": True,
                        "waitForDebuggerOnStart": False,
                        "flatten": True,
                    },
                )
                attached = await cdp.send(
                    "Target.attachToTarget",
                    {"targetId": target_id, "flatten": True},
                )
                sid = (attached.get("result") or {}).get("sessionId")
                print(f"attached_page sid={sid} url={page_url}", flush=True)
                if sid:
                    sessions.add(sid)
                    await setup_session(sid)
                else:
                    await setup_session(None)
            except Exception as exc:
                print(f"cdp_setup_fail {exc}", flush=True)

            print("ROLL_NOW", flush=True)
            print(
                f"armed on CDP {CDP_HTTP}; watching {page_url}; waiting {WAIT_S}s for POST {BET_MARK}",
                flush=True,
            )

            deadline = t0 + WAIT_S
            last_hb = 0.0
            while time.time() < deadline and not done.is_set():
                remaining = deadline - time.time()
                try:
                    await asyncio.wait_for(
                        done.wait(), timeout=min(1.0, max(0.05, remaining))
                    )
                except asyncio.TimeoutError:
                    pass
                now = time.time()
                if now - last_hb >= 30:
                    last_hb = now
                    print(
                        f"still_waiting remaining={int(deadline - now)}s page={page_url} pump={not pump_task.done()}",
                        flush=True,
                    )

            pump_task.cancel()
            try:
                await pump_task
            except (asyncio.CancelledError, Exception):
                pass

    elapsed = time.time() - t0
    tok = captured.get("token") or ""
    if tok:
        _report("CAPTURED", len(tok), page_url, elapsed)
        return 0

    TOKEN.write_bytes(b"")
    print("TIMEOUT", flush=True)
    _report("TIMEOUT", 0, page_url, elapsed)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
