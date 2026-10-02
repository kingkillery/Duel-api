"""Prepare the operator's dedicated Chrome and save its session; never wager or mint."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import urlsplit

import httpx

from capture_session import LS_KEYS, build_session
from sandbox.bet_token import (
    TokenCaptureError, _cookie_map, _duel_pages, assert_loopback_cdp,
    note_failure, profile_fingerprint,
)


def _probe(url: str) -> bool:
    try:
        with httpx.Client(trust_env=False, timeout=1.0, follow_redirects=False) as client:
            response = client.get(url + "/json/version")
    except (httpx.ConnectError, httpx.ConnectTimeout):
        return False
    except httpx.HTTPError as exc:
        raise TokenCaptureError("cdp", "Chrome discovery failed; no browser was launched") from exc
    try:
        body = response.json()
        if response.status_code != 200 or not isinstance(body, dict) or not body.get("webSocketDebuggerUrl"):
            raise ValueError("not CDP")
    except (ValueError, TypeError) as exc:
        raise TokenCaptureError("cdp", "configured port is occupied but is not Chrome DevTools") from exc
    return True


def _launch_chrome(url: str) -> None:
    target = urlsplit(assert_loopback_cdp(url))
    if os.name != "nt" or target.hostname != "127.0.0.1" or target.port != 51537:
        raise TokenCaptureError("cdp", "automatic launch supports Windows Chrome on 127.0.0.1:51537; start other configured browsers manually")
    candidates = [Path(root) / "Google/Chrome/Application/chrome.exe" for root in (
        os.environ.get("PROGRAMFILES", "C:/Program Files"),
        os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ) if root]
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise TokenCaptureError("cdp", "Chrome was not found; install Chrome or start the configured browser manually")
    browser_profile = Path(__file__).resolve().parents[1] / ".private-api-automation/chrome-login-51537"
    subprocess.Popen([
        str(executable), "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=51537",
        f"--user-data-dir={browser_profile}", "--no-first-run", "--no-default-browser-check", "https://duel.com/dice",
    ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def prepare_session(url: str, profile: Path) -> str:
    """Reuse exact-origin browser state; never save an unauthenticated profile."""
    url = assert_loopback_cdp(url)
    if not _probe(url):
        note_failure("connecting", "opening dedicated Chrome on port 51537; listener is not ready")
        _launch_chrome(url)
        deadline = time.monotonic() + 15
        while not _probe(url):
            if time.monotonic() >= deadline:
                raise TokenCaptureError("cdp", "Chrome did not become ready; close its welcome screen and try again")
            time.sleep(0.25)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(url, timeout=5000)
        if not browser.contexts:
            raise TokenCaptureError("cdp", "Chrome has no available browsing context")
        pages = _duel_pages(browser)
        if not pages:
            page = browser.contexts[0].new_page()
            page.goto("https://duel.com/dice", wait_until="domcontentloaded", timeout=15000)
            pages = _duel_pages(browser)
        if not pages:
            raise TokenCaptureError("login-required", "open https://duel.com and sign in, then click Connect & capture token again")
        page = next((p for p in pages if "/dice" in urlsplit(p.url).path), pages[0])
        cookies = _cookie_map(page.context.cookies())
        try:
            fingerprint = profile_fingerprint(cookies)
        except TokenCaptureError as exc:
            page.bring_to_front()
            raise TokenCaptureError("login-required", "sign in to Duel in the opened Chrome, then click Connect & capture token again; no saved session was changed") from exc
        storage = page.evaluate("keys => Object.fromEntries(keys.filter(k => localStorage.getItem(k) !== null).map(k => [k, localStorage.getItem(k)]))", list(LS_KEYS))
        session = build_session(cookies, storage)
    # Atomic replacement prevents concurrent status readers seeing partial credentials.
    profile.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".session-", suffix=".tmp", dir=profile.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(session.to_json())
        os.replace(temporary, profile)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return fingerprint
