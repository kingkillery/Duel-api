# Session Lifetime & Cloudflare

The single most common failure mode in private-API automation is treating a session as permanent. This project treats it as a perishable credential with explicit semantics.

---

## 1. The `__cf_bm` Cookie

| Property | Value |
|---|---|
| Purpose | Cloudflare bot-management marker |
| TTL | ~30 minutes (1800 s) |
| Refresh | Server-issued; not client-mintable |
| Consequence | A captured session goes stale, not invalid |

A stale `__cf_bm` does **not** mean the session is dead — the `duel` cookie may still authenticate. What it means is that replay reliability degrades and a challenge becomes more likely.

---

## 2. Tri-State Staleness

`DuelClient.session_status()` returns `stale` as a **tri-state**, because guessing is worse than admitting ignorance:

| Value | Meaning | Action |
|---|---|---|
| `True` | Timestamped session, past TTL | Re-capture or refresh |
| `False` | Timestamped session, inside TTL | Safe to proceed |
| `None` | No timestamp recorded | Age unknown — do not assume freshness |

Commands surface this as a non-fatal warning (`warning: session bot cookie is N min old (TTL ~30 min)`) rather than blocking, because observed behavior is that stale bot cookies still pass — until they don't.

---

## 3. Bounded Auto-Refresh

When a request fails in a way that looks like session expiry, the client attempts **exactly two** recovery requests (a `metadata` bootstrap to re-mint cookies, then a retry of the original call). No exponential backoff, no retry loops:

> Unbounded retries against a bot-gated endpoint are how a scraper turns a stale cookie into a ban.

---

## 4. Capturing a Session

```
duel-api doctor                     # confirm playwright + spec are available
python capture_session.py --cdp-url http://127.0.0.1:9223
duel-api session-status             # verify the capture + read staleness
```

- **CDP port 9222 is usually squatted** by an existing Chrome DevTools instance; the tooling defaults to **9223**. If attach fails, `_devtools_note()` prints the squatter and the exact relaunch command.
- Chrome must be launched with `--remote-debugging-port` and **detached** (on Windows: `Start-Process`, so the console does not own the process).
- **Identity is API-only.** duel.com writes no `auth` blob to storage; the `duel` cookie is an opaque ~40-character token. Use `duel-api whoami` to see who you are — never guess from cookie contents.

---

## 5. Credential Handling Rules

1. Captured sessions (`captures/session*.json`, `.private-api-automation/`) are gitignored — a session file *is* a credential.
2. A credential pasted into chat or logs is treated as compromised. Rotate it.
3. Bet-body security tokens are browser-minted only; CLI-minted and in-page-minted tokens are rejected with `400 incorrect_2fa`. See `docs/betting-gates.md`.
