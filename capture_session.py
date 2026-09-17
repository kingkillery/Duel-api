#!/usr/bin/env python3
"""Capture an authenticated Duel.com session from a real browser.

Why this exists
---------------
``captcha_on_login`` is enabled (Cloudflare Turnstile), so a headless client
cannot mint a session on its own. The supported path is the methodology's
*Capture* step: log in by hand in a real browser, then lift the session out of
that browser and replay it over HTTP.

Usage
-----
1. Start Chrome with remote debugging, pointed at the profile directory::

       chrome.exe --remote-debugging-port=9222 \
                  --user-data-dir=.private-api-automation/chrome-profile \
                  --no-first-run

2. Log in to https://duel.com by hand in that window (including the captcha).
3. Run::

       python capture_session.py

Writes ``.private-api-automation/profiles/default/session.json``.

Requires ``playwright`` (``pip install playwright``) - it is only needed for
capture, not for replay.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from automation_client import DEFAULT_PROFILE, SESSION_COOKIES, Session

CDP_URL = "http://127.0.0.1:9222"
SITE = "duel.com"

# localStorage keys the client needs. security:uuid is the device identifier;
# the rest are captured for completeness/debugging.
LS_KEYS = ("security:uuid", "auth", "lastSelectedCurrency")


def capture(cdp_url: str = CDP_URL, *, keep: tuple[str, ...] = SESSION_COOKIES) -> Session:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - environment dependent
        raise SystemExit(
            "playwright is required for capture: pip install playwright && playwright install chromium"
        )

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise SystemExit(
                f"could not attach to Chrome at {cdp_url}: {exc}\n"
                "Is Chrome running with --remote-debugging-port=9222?"
            )

        contexts = browser.contexts
        if not contexts:
            raise SystemExit("attached to Chrome but found no browser context")
        ctx = contexts[0]

        page = next((p for p in ctx.pages if SITE in (p.url or "")), None)
        if page is None:
            page = ctx.new_page()
            page.goto(f"https://{SITE}/", wait_until="domcontentloaded")

        cookies = {c["name"]: c["value"] for c in ctx.cookies() if SITE in (c.get("domain") or "")}
        storage = page.evaluate(
            "(keys) => Object.fromEntries(keys.filter(k => localStorage.getItem(k) !== null)"
            ".map(k => [k, localStorage.getItem(k)]))",
            list(LS_KEYS),
        )

    session = Session(
        device_uuid=storage.get("security:uuid") or Session().device_uuid,
        cookies={k: v for k, v in cookies.items() if k in keep or k in SESSION_COOKIES},
        local_storage=storage,
    )
    return session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cdp-url", default=CDP_URL)
    parser.add_argument("--out", default=str(DEFAULT_PROFILE))
    parser.add_argument(
        "--from-json",
        default=None,
        help="instead of CDP, import a JSON dump {cookies, local_storage} produced elsewhere",
    )
    args = parser.parse_args(argv)

    if args.from_json:
        raw = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        storage = raw.get("local_storage", {}) or raw.get("localStorage", {})
        session = Session(
            device_uuid=storage.get("security:uuid") or raw.get("device_uuid") or Session().device_uuid,
            cookies=raw.get("cookies", {}),
            local_storage=storage,
        )
    else:
        session = capture(args.cdp_url)

    path = session.save(args.out)
    print(f"captured {len(session.cookies)} cookie(s): {sorted(session.cookies)}")
    print(f"device uuid: {session.device_uuid}")
    print(f"wrote {path}")
    if not any(k in session.cookies for k in SESSION_COOKIES[:1]):
        print("warning: no 'duel' cookie captured - you are probably not logged in yet", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())