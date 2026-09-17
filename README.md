# Duel-api

A **private-API** client for `duel.com`, built with the
[private-api-automation](https://github.com/kingkillery/private-api-automation)
methodology: **Capture → Spec → Replay → Refresh**.

The target is a Vue 3 SPA that talks to a same-origin `/api/v2` API. There is no
documented public API; everything here was recovered from the shipped bundle and
confirmed against live browser traffic.

---

## Scope — read this first

Duel.com is a **real-money gaming site**. Reads are open; writes are gated.

- **No wagering.** Bet/wager placement endpoints were identified during recon and
  deliberately **not** implemented. The write guard refuses any path containing
  `bet`, `wager`, `stake`, `deposit`, `withdraw`, `buy` or `sell` — at the single
  chokepoint in `DuelClient.request()`, so no entry point (including the CLI
  `call` escape hatch) can bypass it.
- **No deposit or withdrawal submission.** Only read-only method *listings* are exposed.
- **Account management is supported, but opt-in.** Settings, provably-fair client
  seed, and 2FA endpoints are wired; they require `DuelClient(allow_writes=True)`
  (or `confirm=True` per call, or `--yes` on the CLI).
- Automated access almost certainly violates the operator's terms of service. The
  practical risks are account closure, forfeiture of funds, and (for wagering
  automation) rapid financial loss.
- Use only against an account **you own**.

`site_spec.json → metadata.out_of_scope` records the exclusion, and
`tests/test_spec_loads.py::test_wagering_is_out_of_scope` + the money-path tests in
`tests/test_client.py` enforce it.

---

## Layout

```
Duel-api/
├── site_spec.json              # captured spec: endpoints, tokens, failure signals, ladder
├── automation_client.py        # DuelClient - cookie jar, bootstrap, auth, replay
├── automation_cli.py           # command-line front end
├── capture_session.py          # lift a session out of a real browser (CDP)
├── captures/                   # sanitized probe output
├── tests/                      # offline tests (mocked transport)
└── .private-api-automation/    # runtime state (gitignored)
    └── profiles/default/session.json
```

## Install

```bash
pip install httpx pytest
pip install playwright      # only needed for capture_session.py
```

## Quick start

```bash
python automation_cli.py spec          # summarise the spec
python automation_cli.py metadata      # public bootstrap; creates the session cookie
python automation_cli.py games --limit 5
python automation_cli.py rates
python automation_cli.py whoami        # are we authenticated?
```

---

## How the site authenticates

Three things must be true for any API call to work:

| Requirement | Detail |
|---|---|
| **Cookie jar from request #1** | `GET /api/v2/metadata` *issues* the `duel` session cookie. Login binds to that session, so metadata must be called first on a fresh client. |
| **`x-duel-device-identifier`** | A client-generated, persistent v4 UUID, mirrored in `localStorage["security:uuid"]`. Sent on every request; also accepted as `?uuid=`. |
| **`x-env-class: main`** | Environment selector for the production site. |

Session state lives in **cookies** (`duel`, plus Cloudflare's `__cf_bm`), not a
bearer token. `__cf_bm` has a ~30 minute TTL — a **403 means "re-capture the
session"**, not "the path is wrong".

### The captcha gate

`POST /api/v2/auth/login` is protected by **Cloudflare Turnstile** — the server's
`captcha_on_login` feature flag is enabled. The body carries the captcha as a
`{type, token}` pair:

```json
{
  "username": "…",            // or "email" when the identifier contains "@"
  "password": "…",
  "type": "turnstile_token",
  "token": "<fresh Turnstile token>"
}
```

A headless client **cannot** mint that token. `DuelClient.login()` therefore
requires an externally obtained token and raises `CaptchaRequired` rather than
pretending otherwise. Unattended re-authentication is unavailable by design, and
`recovery_ladder.headless_reauth` is marked disabled in the spec.

**Do not retry login in a loop** — repeated captcha-gated attempts can trip
anti-bot flags on the account.

### Recommended: capture a real session

This is the methodology's *Capture* step and the reliable path.

1. Launch Chrome against an isolated profile:

   ```bash
   chrome.exe --remote-debugging-port=9222 \
              --user-data-dir=.private-api-automation/chrome-profile \
              --no-first-run
   ```

2. Log in at <https://duel.com> **by hand** in that window, captcha included.
3. Lift the session out:

   ```bash
   python capture_session.py
   python automation_cli.py whoami
   ```

`capture_session.py` records cookies **and** the authenticated
`localStorage["security:uuid"]` — a cookie-only capture would leave the client
missing a header it needs.

Alternative — inline login from a real browser session:

```bash
python automation_cli.py login your-username \
  --password "$DUEL_PASSWORD" \
  --captcha-token "<token from the browser>"
```

---

## Endpoints

Confirmed **live** during capture (public):

| Endpoint | Notes |
|---|---|
| `GET /api/v2/metadata?uuid=` | bootstrap + session cookie, 169 feature flags, `turnstile_sitekey` |
| `GET /api/v2/games` | uses `start` / `filter` — **not** `page` / `per_page` |
| `GET /api/v2/slots` | slot catalogue |
| `GET /api/v2/metadata/exchange-rates` | FX rates |
| `GET /api/v2/trade/metadata/payment-methods` | deposit methods |
| `GET /api/v2/trade/crypto/withdraw/methods` | withdrawal method listing (read-only) |
| `GET /api/v2/user/zero-edge-rakeback/status` | rakeback status |

Path confirmed in the bundle, requires a session (unverified live, because
unattended login is captcha-blocked):

| Endpoint | Notes |
|---|---|
| `POST /api/v2/metadata/socket-token` | realtime credential (JWT, ~30s TTL) |
| `POST /api/v2/auth/login` · `POST /api/v2/auth/logout` | session lifecycle |
| `GET /api/v2/user` · `GET /api/v2/user/settings` · `GET /api/v2/user/kyc` | profile |
| `GET /api/v2/client-seed` | provably-fair seed |

Realtime is **socket.io** at `https://pvp.duel.com`, authenticated with
`{uid, authorizationToken: socket_token, signature: socket_signature, uuid}`.

## Failure handling

The spec's `auth_failure_signals` and `recovery_ladder` are implemented as typed
exceptions rather than silent retries:

| Signal | Exception | Response |
|---|---|---|
| 401 / 419 | `AuthRequired` | restore a captured session; re-auth needs a browser |
| 403 + `cf-mitigated` | `CloudflareChallenge` | `__cf_bm` went stale → re-capture |
| missing captcha token | `CaptchaRequired` | obtain a token from a real browser |

Ladder steps that *are* automated: `reload_cache` (re-run metadata) and
`refresh_tokens` (`metadata/socket-token`). `headless_reauth` is disabled.

## Actions (opt-in)

Writes go through one guard in `DuelClient.request()`:

| Policy | Behaviour |
|---|---|
| reads (GET/HEAD/OPTIONS) | always allowed |
| token/session lifecycle POSTs | allowed (login is already captcha-gated; `metadata/socket-token` keeps the `refresh_tokens` ladder step alive on a read-only client) |
| money-moving paths | **refused outright** — `UnsupportedAction`, regardless of opt-in |
| everything else state-changing | `WriteNotAllowed` unless `allow_writes=True` / `confirm=True` |

```bash
# refused by default
python automation_cli.py settings-update '{"volume":0}'
📎 write not allowed: ... state-changing calls need confirm=True or ... (hint: pass --yes)

# opts in
python automation_cli.py --yes settings-update '{"volume":0}'
python automation_cli.py --yes seed-set                 "<seed>"
python automation_cli.py --yes seed-rotate              "<seed>"
python automation_cli.py --yes 2fa-setup
```

```python
with DuelClient.from_profile(allow_writes=True) as client:
    client.update_settings({"volume": 0})        # PATCH /api/v2/user/settings
    client.rotate_client_seed("abc")             # POST  /api/v2/client-seed/rotate
```

All action paths are **bundle-derived** (read from the shipped service
definitions) and marked as such in `site_spec.json`; they are unverified live
because unattended login is captcha-blocked.

### XSRF

The bundle's axios config restates `xsrfCookieName: XSRF-TOKEN` /
`xsrfHeaderName: X-XSRF-TOKEN`. Duel does not appear to issue that cookie today,
so the mirroring is normally a no-op — but it is implemented, because replaying
the exact client behaviour is what keeps writes working if it ever does. Cookie
lookups are **name-only** to mirror `document.cookie`; an exact
`domain="duel.com"` match would miss a cookie issued as `Domain=.duel.com`.

## Tests

```bash
python -m pytest tests/ -q
```

All tests run offline against a mocked transport; none touch the network.

---

## Provenance

| | |
|---|---|
| Method | bundle analysis + live browser capture |
| Bundle | `/assets/index-Cs9Avd2r.js` (sha256 `518cc628…`) |
| Client | Vue 3 SPA, axios-based API client class, same-origin `/api/v2` |
| Spec | `site_spec.json` (schema v0.1) |

No credentials are stored in this repository. Sessions live in the gitignored
`.private-api-automation/` directory.