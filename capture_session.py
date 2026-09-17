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

   Any free port works - pass it to this script with --cdp-url. If the chosen
   port is already owned by something that is not a debuggable Chrome, the
   attach fails with an HTTP 404 instead of a refused connection; the error
   says so explicitly, so move to a free port rather than restarting Chrome.

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
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from automation_client import DEFAULT_PROFILE, SESSION_COOKIES, Session

CDP_URL = "http://127.0.0.1:9222"
SITE = "duel.com"

# localStorage keys the client needs. security:uuid is the device identifier;
# the rest are captured for completeness/debugging.
LS_KEYS = ("security:uuid", "auth", "lastSelectedCurrency")


def identity_from_storage(storage: dict[str, str]) -> tuple[str | None, int | None]:
    """Pull (username, user_id) out of a captured ``auth`` blob, or (None, None).

    Best-effort only, and normally empty: verified live 2026-09-17 against a
    signed-in profile that duel.com writes no identity blob to localStorage or
    sessionStorage on either ``duel.com`` or ``www.duel.com``, and the ``duel``
    cookie is an opaque 40-character token rather than a readable JWT. Identity
    therefore has to come from the API (``automation_cli.py whoami``).

    Kept anyway: storage layouts differ between site versions, and a wrong guess
    here costs nothing, whereas *claiming* an identity is the dangerous failure.
    Any parse failure, wrong shape or missing key yields (None, None) rather than
    an exception - a capture must not fail because a page changed its layout.
    """
    # A malformed --from-json dump is imported verbatim (``null`` decodes to
    # None, a number stays a number), so storage is not necessarily a mapping.
    # Identity is never load-bearing: return unknown rather than raise.
    if not isinstance(storage, dict):
        return None, None
    try:
        blob = json.loads(storage.get("auth") or "")
    except ValueError:
        return None, None
    if not isinstance(blob, dict):
        return None, None
    nested = blob.get("user")
    source = nested if isinstance(nested, dict) else blob
    username = source.get("username")
    user_id = source.get("user_id")
    # Both keys must be present and well-typed; anything else is "unknown"
    # rather than a half-identity that would look authoritative in reports.
    if not isinstance(username, str) or not username:
        return None, None
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        return None, None
    return username, user_id


def build_session(
    cookies: dict[str, str],
    storage: dict[str, str],
    *,
    keep: tuple[str, ...] = SESSION_COOKIES,
    observed_at: float | None = None,
) -> Session:
    """Assemble a Session from raw browser cookies and localStorage.

    Split out from :func:`capture` so the parts that matter for replay - cookie
    filtering, device uuid, identity, and the timestamps staleness is measured
    against - are testable without a browser attached.

    ``observed_at`` is stamped into both ``captured_at`` and ``cf_bm_at``. Note
    this is when the session was *observed*, not when ``__cf_bm`` was issued;
    that is unknowable from the cookie jar. So it is a lower bound on the bot
    cookie's true age: ``age > TTL`` means definitively expired, while
    ``age <= TTL`` means "not provably expired", not "fresh". See
    ``Session.is_stale()``.
    """
    stamp = time.time() if observed_at is None else observed_at
    # Imported dumps can carry any JSON type where a mapping was expected, and
    # main() already reports a session with no `duel` cookie as a failed
    # capture, which is more informative than a traceback from in here.
    storage = storage if isinstance(storage, dict) else {}
    cookies = cookies if isinstance(cookies, dict) else {}
    username, user_id = identity_from_storage(storage)
    return Session(
        device_uuid=storage.get("security:uuid") or Session().device_uuid,
        cookies={k: v for k, v in cookies.items() if k in keep or k in SESSION_COOKIES},
        local_storage=storage,
        username=username,
        user_id=user_id,
        captured_at=stamp,
        cf_bm_at=stamp,
    )


def _devtools_note(cdp_url: str) -> str:
    """Explain *why* an attach failed: nothing listening, or a non-DevTools squatter.

    Playwright reports both cases with the same generic error, and the second is a
    real trap: a process that merely owns the port answers every DevTools path with
    404, so the attach fails even though "Chrome is running" looks true.
    """
    url = cdp_url.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return (
            f"note: {cdp_url} answers HTTP but is not a DevTools endpoint "
            f"(GET /json/version -> {exc.code}); another process owns that port"
        )
    except OSError as exc:
        return f"note: nothing is serving DevTools at {cdp_url} ({exc})"
    except Exception as exc:
        # Never let the diagnostic mask the error it exists to explain. A
        # malformed port raises InvalidURL, which is not an OSError.
        return f"note: could not probe {cdp_url} ({type(exc).__name__}: {exc})"
    return f"note: {cdp_url} responded outside the attach path: {body[:120]!r}"


def _port_of(cdp_url: str) -> int | None:
    """Port embedded in a CDP URL, so error text names the port actually in use."""
    try:
        return urllib.parse.urlsplit(cdp_url).port
    except ValueError:
        return None


def _start_hint(cdp_url: str) -> str:
    """Remediation line naming the port in use, or a generic one when absent.

    ``http://127.0.0.1`` carries no port, and interpolating that directly
    rendered the literal ``--remote-debugging-port=None`` - not a thing an
    operator can run.
    """
    port = _port_of(cdp_url)
    if port is None:
        return (
            "Start (or restart) Chrome with --remote-debugging-port=<free port>, "
            "pass that same URL back via --cdp-url, then log in by hand."
        )
    return (
        f"Start (or restart) Chrome with --remote-debugging-port={port}, "
        "then log in by hand and re-run this command."
    )


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
                f"{_devtools_note(cdp_url)}\n"
                f"{_start_hint(cdp_url)}"
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

    return build_session(cookies, storage, keep=keep)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cdp-url", default=CDP_URL)
    parser.add_argument("--out", default=str(DEFAULT_PROFILE))
    parser.add_argument(
        "--from-json",
        default=None,
        help="instead of CDP, import a JSON dump {cookies, local_storage} produced elsewhere",
    )
    parser.add_argument(
        "--no-stamp",
        action="store_true",
        help=(
            "leave captured_at/cf_bm_at unset so status reports 'age unknown' "
            "(only meaningful with --from-json, when the file mtime is known wrong)"
        ),
    )
    args = parser.parse_args(argv)

    if args.from_json:
        raw = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SystemExit(
                f"{args.from_json} is not a captured-session object (found "
                f'{type(raw).__name__}); expected a JSON object holding "cookies" '
                'and "local_storage".'
            )
        # A dump is imported verbatim, so any field can hold the wrong JSON type
        # (`null` decodes to None, a number stays a number). Coerce once, here,
        # rather than trusting every use below: build_session can tolerate a bad
        # storage blob, but this branch reads it directly too.
        storage = raw.get("local_storage") or raw.get("localStorage") or {}
        if not isinstance(storage, dict):
            storage = {}
        cookies = raw.get("cookies") or {}
        # build_session derives identity; here the file's mtime is the best
        # capture-time proxy available - a lower bound, since copying the file
        # refreshes mtime. That matches is_stale() semantics: past TTL is
        # definitive, within TTL only means "not provably expired".
        session = build_session(
            cookies,
            storage,
            observed_at=None if args.no_stamp else Path(args.from_json).stat().st_mtime,
        )
        # The one thing build_session cannot see is `raw` itself: fall back to
        # a dumped device_uuid when the storage blob carries no security:uuid.
        session.device_uuid = (
            storage.get("security:uuid") or raw.get("device_uuid") or session.device_uuid
        )
        if args.no_stamp:
            session.captured_at = None
            session.cf_bm_at = None
    else:
        session = capture(args.cdp_url)

    path = session.save(args.out)
    print(f"captured {len(session.cookies)} cookie(s): {sorted(session.cookies)}")
    print(f"device uuid: {session.device_uuid}")
    if session.username is not None:
        print(f"username: {session.username}")
        print(f"user id: {session.user_id}")
    else:
        # Not evidence of being signed out: see identity_from_storage. Only the
        # API can answer this, and it does, from the captured `duel` cookie.
        print("username: unknown (duel.com stores no client-side identity blob)")
        print("user id: unknown  (resolve with: automation_cli.py whoami)")
    print(f"wrote {path}")
    if not any(k in session.cookies for k in SESSION_COOKIES[:1]):
        print("warning: no 'duel' cookie captured - you are probably not logged in yet", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())