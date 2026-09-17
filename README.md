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

- **Real-money betting exists, gated four deep.** Dice betting
  (`place_dice_bet()` / the `dice-bet` CLI command) is the only path that can
  move money: validated parameters, dry-run default, `betting_enabled` plus
  per-call `confirm` plus a `max_stake` cap. The generic `request()` path and
  the CLI `call` escape hatch still refuse every money verb — `bet`, `wager`,
  `stake`, `deposit`, `withdraw`, `buy`, `sell` — unconditionally, at the
  single chokepoint in `DuelClient.request()`.
- **No deposit, withdrawal, or batch/autobet.** Only read-only method
  *listings* and the single manual dice bet are wired.
- **Account management is supported, but opt-in.** Settings, provably-fair client
  seed, and 2FA endpoints are wired; they require `DuelClient(allow_writes=True)`
  (or `confirm=True` per call, or `--yes` on the CLI).
- Automated wagering almost certainly violates the operator's terms of service. The
  practical risks are account closure, forfeiture of funds, and rapid financial
  loss. Never stake more than you can afford to lose, and prefer dry runs while
  developing. If gambling stops being fun, stop - problem-gambling helplines exist
  in most jurisdictions (e.g. 1-800-GAMBLER in the US, GamStop/GamCare in the UK).
- Use only against an account **you own**.

`site_spec.json → metadata.wagering` documents the betting gates,
`metadata.out_of_scope` records the remaining exclusions, and
`tests/test_spec_loads.py::test_wagering_is_explicitly_gated` + the money-path tests in
`tests/test_client.py` enforce them.

---

## Layout

```
Duel-api/
├── site_spec.json              # captured spec: endpoints, tokens, failure signals, ladder
├── automation_client.py        # DuelClient - cookie jar, bootstrap, auth, replay
├── automation_cli.py           # command-line front end
├── capture_session.py          # lift a session out of a real browser (CDP)
├── captures/                   # sanitized probe output
├── backtest/                   # read-only strategy backtester (no HTTP client)
├── realtime/                   # read-only socket.io BetFeed listener (no wagers)
├── tests/                      # offline tests (mocked transport)
└── .private-api-automation/    # runtime state (gitignored)
    └── profiles/default/session.json
```

## Install

```bash
pip install httpx pytest
pip install playwright      # only needed for capture_session.py
pip install "python-socketio[client]"  # only needed for the realtime BetFeed listener
```

## Quick start

```bash
python automation_cli.py spec          # summarise the spec
python automation_cli.py metadata      # public bootstrap; creates the session cookie
python automation_cli.py games --limit 5
python automation_cli.py rates
python automation_cli.py whoami        # are we authenticated?
python automation_cli.py session-status # session age & staleness (makes no request)
python automation_cli.py spec-check     # live bundle hash vs the spec (drift tripwire)
python automation_cli.py betfeed --duration 30   # record live BetFeed events (read-only)
python automation_cli.py dice-bet --amount 0.5 --side UNDER --currency USDT --target 5005  # dry run (default: sends nothing)
python automation_cli.py --yes dice-bet --amount 0.5 --side UNDER --currency USDT --target 5005 --enable-betting --live --confirm-bet  # REAL MONEY
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
bearer token. `__cf_bm` has a ~30 minute TTL, so it — not the login — is what
actually bounds how long a replayed session stays usable. A **403 with
`cf-mitigated` means the bot cookie went stale**, not that the path is wrong.

### Session lifetime

Capture stamps `captured_at` and `cf_bm_at` into the profile, which is what makes
session age knowable at all:

| Behaviour | Detail |
|---|---|
| `session-status` | offline health report — age, staleness, cookies present, next step. Makes **no request**. Exit 0 = usable, 1 = needs attention. |
| `Session.is_stale()` | tri-state: `True` past TTL, `False` inside it, `None` when the profile carries no timestamp. `None` is reported as "age unknown", never guessed as fresh. |
| auto-refresh | on a Cloudflare challenge the client runs the ladder's `reload_cache` step — one `metadata()` call to re-mint `__cf_bm` — then retries the original request once. Off with `auto_refresh=False`. |
| warnings | every CLI command warns on stderr once the session is past TTL, or within 20% of it (`STALE_WARNING_SECONDS`). Suppress with `--quiet`. |

Two caveats worth stating plainly:

- The stamp records when the session was **observed**, not when `__cf_bm` was
  issued; that is unknowable from a cookie jar. So `age > TTL` means definitively
  expired, while `age <= TTL` means "not provably expired", not "fresh".
- Only an actual **rotation** restarts the clock — not every response that echoes
  the cookie. This is what lets a session that looked expired be rescued without
  a browser, and equally why a static cookie never looks artificially fresh.

Auto-refresh is bounded. The retry passes `auto_refresh=False`, and a
`_refreshing` re-entrancy guard stops `metadata()`'s own 403 from starting
another refresh, since every refresh is itself a request that can be challenged.
A persistent challenge costs exactly two requests and then raises.

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

1. Launch a Chromium browser against an isolated profile:

   ```bash
   chrome.exe --remote-debugging-port=9222 \
              --user-data-dir=.private-api-automation/chrome-profile \
              --no-first-run
   ```

   **Any Chromium works** — Chrome, Edge, Brave, Vivaldi. This tool attaches over
   CDP and never launches a browser itself, so Microsoft Edge is a drop-in
   alternative (`msedge.exe` takes the same flags; verified against `Edg/153`).
   Firefox and WebKit do not speak CDP, so they are not options.

   Any free port works — pass it to the next step with `--cdp-url`. If something
   already owns 9222, do **not** kill that process: pick a free port instead.

   A dedicated `--user-data-dir` is required, not optional. Without it the launch
   hands off to an already-running browser instance and the debug port silently
   never binds — which looks like a broken tool rather than a missing flag.

2. Log in at <https://duel.com> **by hand** in that window, captcha included.
3. Lift the session out:

   ```bash
   python capture_session.py
   python automation_cli.py whoami
   ```

   Use `--cdp-url http://127.0.0.1:<port>` if you launched on a port other than
   9222. If the attach fails with an HTTP 404 rather than a refused connection,
   a non-DevTools process owns that port — move to a free one.


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

Realtime is **socket.io v4** at `https://roulette.duel.com` — engine path `/s`,
websocket-only transport, namespace `/livebetfeed`. (The `pvp.duel.com` arena
URL exists in the env but is dead config; the per-namespace resolver falls back
to `roulette.<host>`, live-verified 2026-09-17.) Auth is two-stage and
bundle-verified: `uid` + `token` (the short-lived `socket_token`) ride the
handshake query string, then the client emits `identify` with
`{uid, authorizationToken: socket_token, signature: socket_signature, uuid}`
(`socket_signature` is issued to signed-in sessions only; guests omit it).
`server_draining` / `force_reconnect` from the server mean "fetch fresh
credentials and reconnect". `realtime/betfeed.py` implements a read-only
listener for this; every detail is recorded in `site_spec.json →
metadata.realtime`.

**The handshake is bot-gated.** From a non-browser client, the site's
engine.io middleware answers every connect with
`{"code":3,"message":"Bad request"}` — correct credentials, cookies, Origin,
Sec-Fetch and device headers make no difference — and websocket upgrades are
refused even earlier. That is the same class of trust boundary as the
Turnstile-gated login, and the project treats it the same way: surfaced
honestly, not bypassed. `betfeed` will report the refusal cleanly; a live
listener needs browser-grade trust (e.g. a future CDP bridge).

## Failure handling

The spec's `auth_failure_signals` and `recovery_ladder` are implemented as typed
exceptions rather than silent retries:

| Signal | Exception | Response |
|---|---|---|
| 401 / 419 | `AuthRequired` | restore a captured session; re-auth needs a browser |
| 403 + `cf-mitigated` | `CloudflareChallenge` | `__cf_bm` went stale → auto-refresh once, then re-capture |
| 429 | `RateLimited` | bounded retries honouring numeric `Retry-After` (backoff capped at 30 s, `max_429_retries` default 2), then raises with the delay attached |
| missing captcha token | `CaptchaRequired` | obtain a token from a real browser |

Ladder steps that *are* automated: `reload_cache` (re-run metadata — now also the
automatic response to a Cloudflare challenge) and `refresh_tokens`
(`metadata/socket-token`). `headless_reauth` is disabled, because
`captcha_on_login` makes unattended re-auth impossible.

The ladder's last step is `fail`: surface `AuthRequired` / `CloudflareChallenge`
to the caller rather than retrying into an anti-bot flag. Auto-refresh honours
that — it *is* `reload_cache`, executed once, and the fallthrough is still
`fail`. A persistent challenge costs exactly two requests (the original plus one
refresh) and then raises; `test_auto_refresh_retries_exactly_once_and_never_loops`
pins the bound.

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
---

## Strategy backtesting (read-only)

`backtest/` evaluates staking strategies against **round histories**. It holds no
HTTP client and has no write path — rounds come from a JSONL capture or a
generator — so it cannot place a bet even by accident. That is structural, not
convention: `tests/test_backtest.py` walks the package AST and fails if anything
in it imports `httpx`, `requests`, `urllib`, `socket` or `automation_client`.

```bash
python -m backtest run --schedule martingale --base 0.50 --max-rungs 3 \
    --threshold 2.0 --payout 2.0 --profit-target 5.00 --stop-loss 4.00 \
    --edge 0.0 --sessions 50000
```

| Command | Purpose |
|---|---|
| `run` | aggregate statistics over many sessions |
| `sessions` | traced bankroll path for a handful of sessions |
| `rounds` | summarise a JSONL capture |

`--edges-from edges.json --game crash` replaces the guessed `--edge` with the
site's published house edge (from the rakeback document or the games
catalogue). A game that publishes no edge is excluded from the table rather
than silently read as 0, and `--edges-from` may not be combined with `--edge`.

Each run reports EV per session with a standard error, P(profit target),
P(stop loss), mean wagered, `implied_edge`, mean max drawdown, net percentiles,
and mean time-to-ruin.

### Round capture format

One round per line, so a capture can be appended to while it grows:

```json
{"round_id": "b7f1", "timestamp": 1758000000.0, "outcomes": {"crash": 1.94}}
```

`read_rounds` / `write_rounds` handle it, and `run --capture FILE` replays one.
**Live BetFeed capture is implemented** (`automation_cli.py betfeed`, module
`realtime/`) and records raw namespace events as JSONL, append-as-it-grows —
but note the handshake is bot-gated against non-browser clients (see
*Realtime* above), so today it records nothing without browser-grade trust.
What is *not* implemented yet is the normalization from BetFeed events into
`rounds` rows — BetFeed carries settled bets (bet id, game, wager, multiplier,
payout), while the backtest wants one row per game round. The loader is here
so a converted capture plugs in when that mapping is written. Without a
capture, `--edge` generates rounds from `P(X >= m) = (1 - edge) / m`, which is
exact by construction.

Two traps the CLI will call out:

- `RecordedSource(cycle=True)` wraps a short capture, so every session replays
  the same outcomes and they are *not* independent. `run` warns when the capture
  is shorter than the session count — a 5-round capture can otherwise report a
  100% win rate.
- `--edge 0.0` is the default and assumes a perfectly fair game. It reports EV
  0.0000, which no staking strategy can beat.

### What the numbers say

At zero edge the 3-rung ladder above returns **EV 0.0000**, exactly. Ruin is
**0.5314** — not the ~0.49 that a cycle-atomic model gives (barriers checked once
per completed ladder). The engine settles stop-loss after *every bet*, which is
what a live loop does; `tests/test_backtest.py` pins both figures, the second
with an independent linear solve of the absorbing chain.

`stop_loss=4.00` means "quit once net <= -4.00", evaluated after a bet settles.
Because rungs are lumpy, a 2.00 rung lost while sitting at -3.50 lands at
**-5.50**: the realized loss can exceed the stated cap.

More generally, bet sizing cannot move the sign of the expectation.
`E[net] = -edge * E[wagered]` holds for every schedule ever devised, and the
engine recovers it as `implied_edge`. A martingale raises `E[wagered]`, which is
why it loses *faster* than flat betting at the same base stake.

---

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