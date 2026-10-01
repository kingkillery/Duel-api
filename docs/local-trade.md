# Local trade desk

`sandbox/trade.py` is a localhost-only manual wager page. It is not the hosted
sandbox. `sandbox/app.py` serves only the offline backtester. It has no bet form
and no local roll.

Install the optional web dependencies if needed: `py -3.13 -m pip install -e ".[sandbox]"`.

The desk starts disabled. This command shows credential presence and does not
send a wager:

```console
$env:DUEL_TRADE_CDP_URL = "http://127.0.0.1:51537"
py -3.13 -m sandbox.trade
```

Open `http://127.0.0.1:8765/`. Live mode is a separate server-process opt-in.
It also requires an explicit stake ceiling. There is no default ceiling.
Use `DUEL_TRADE_MAX_USD` for a USD-denominated ceiling, recalculated from the
fresh reference quote for every wager. `DUEL_TRADE_MAX_STAKE` instead sets a
native-coin ceiling. If both are set, the tighter limit applies. Neither has a default.

The desk also prices a required minimum of 1 US cent in the selected coin.
That floor comes from Duel's public `GET /api/v2/metadata/exchange-rates`,
rounded up to 8 decimal places. DuckDice has no verified rate route, so its
floor uses the same Duel USD reference; it is not a minimum DuckDice advertises.
If the rate cannot be read or parsed, the wager is refused. It is not skipped.
The minimum is displayed even with betting disabled. The page accepts **USD**
stakes, initially $0.01, while SOL remains the underlying selected currency.
**Use minimum** fills the USD minimum without sending a wager. The exact native
coin minimum is shown alongside it. An operator floor is rounded up to a whole
cent for this input; the native floor and coin ceiling are never clamped.
Rates are fetched once again at dispatch; unavailable or unreliable quotes close
the bet gate. A positive finite USD stake below $0.01 is always refused, even if
coin rounding would otherwise bring it above one cent. Conversion normally rounds
upward to the quote's coin quantum. An input exactly equal to the USD ceiling rounds
down instead, so it cannot exceed that ceiling. Both native floor and ceiling are
then enforced. Native-coin API inputs must satisfy the USD ceiling too.
If the direct public GET is challenged, the server can read the same fixed GET
through the already-open Duel tab at `DUEL_TRADE_CDP_URL`. It does not navigate,
mint a token, or send a wager. Keep that browser open. Mocked tests cannot use
this fallback to contact a real browser.

`DUEL_TRADE_MIN_STAKE` is optional and in the selected coin's units. It can
only raise that 1-cent floor. It cannot lower it. If the resulting floor is
above the effective ceiling, the desk refuses the configuration rather than
clamping the floor down. A floor equal to the ceiling allows exactly that stake.

The API remains compatible: `amount` is a decimal string and `amount_unit`
defaults to `coin`. The page explicitly sends `amount_unit: "usd"`. Only the
normalized coin amount reaches either provider adapter; the original request
is not changed. The USD ceiling uses the same reference quote as conversion and
is rounded down to native precision before being passed to the provider adapter.
It is a per-wager reference-value limit, not a cumulative spending limit or a
guarantee of market value after dispatch. The display shows both limits.

```console
$env:DUEL_TRADE_LIVE = "1"
$env:DUEL_TRADE_MAX_USD = "1.00" # explicit $1 reference-value maximum per wager
py -3.13 -m sandbox.trade
```

A Duel wager needs a bet token held in server memory. The page has no token
field, does not accept one in the request, and the server never returns the
value. `DUEL_TRADE_SECURITY_TOKEN` remains an explicit server-side override for
a token the browser already minted. Prefer Capture on the page: it attaches to
the operator's own Chrome at `DUEL_TRADE_CDP_URL` (loopback only, default
`http://127.0.0.1:9222`) and listens. It does not mint. Login alone does not
produce a bet token, and this desk does not call the token endpoint or reuse a
hardcoded code. Capture must be listening **while** the page obtains a token;
it cannot recover a response emitted before listening began. If no usable
response arrives within five minutes, capture fails closed. Only reusable
standard/silent tokens with finite expiry and the same session fingerprint are
held in memory. Do not place a wager just to test capture. Live token acquisition
has not been established; the Capture button is not an automatic mint action.

DuckDice uses the existing server-side key resolution and does not use that token.

Duel's captured session is loaded from `DUEL_TRADE_PROFILE`, or
`.private-api-automation/profiles/default/session.json` by default. Supply tokens
through your existing secret workflow rather than saving real values in shell
history or committed scripts. An expired token/session is an error, not permission
to retry a wager. This desk does not capture credentials or bypass bot gates.

## What the page can do

- Choose `duel` or `duckdice`, then currency, stake, target, and side.
- Send one confirmed wager through the existing `place_dice_bet()` method.
- Show USD-equivalent stake, payout, profit and balance where available, with
  native coin detail. USD equivalents use the dispatch quote and actual returned
  settlement amounts, not the input or a predicted payout. Authoritative native
  round fields remain in the response. Profit is shown only when returned by the
  provider. Status balance uses its current reference quote and is approximate.

## What the page cannot do

- Turn live mode on, set the stake ceiling, choose a profile, or supply a key.
- Retry. Duel clients used here set `max_429_retries=0`, `auto_refresh=False`,
  and `follow_redirects=False`.
- Continue after an ambiguous send. Reconcile the wager in the provider's own
  account history **before** restarting the blocked process. Restarting only clears
  the local lock; it does not establish whether a wager settled.

Capture listens for up to five minutes after attaching to Chrome. The red
Connecting indicator turns green only after the response listener is installed,
then shows the remaining seconds. Progress reads server memory only; it does not
poll the provider. Success or timeout stops listening. It cannot recover cached
tokens, does not mint a token, and never clicks or submits a wager.

Clicking Bet confirms and submits one real wager; there is no extra checkbox.
The UI sends `confirm: true` only on submission. The server still rejects a
missing or false `confirm` from API callers. A second
request while a wager is in flight is refused rather than sent. Provider errors
are returned as a code and exception type, not the provider's message.

Run one process/worker on loopback only. Do not deploy behind a proxy or expose
the app on the LAN: its in-flight and consumed-nonce guards are process-local.
Every POST requires JSON and an exact same-origin localhost Origin. Provider
results expose only selected round fields, never arbitrary account metadata.

## End-to-end verification

Run `py -3.13 tests/e2e_trade_desk.py` with Playwright and Chromium installed.
The check opens the actual page in Chromium, clicks Bet for both providers,
and routes the real client adapters through mocked HTTP transports. The fixture
uses temporary synthetic credentials and denies external DNS/socket connections,
including balance/status reads. It checks disabled mode, confirmation, missing
credentials, stake ceilings, USD-to-SOL request mapping for both providers,
USD result display, below-cent and missing-rate refusal, duplicate and concurrent
submissions, error secrecy, and the ambiguous-outcome lock after reload. Direct
API checks retain default coin units to exercise backwards compatibility.

This establishes local UI-to-adapter behavior, **not** current live session/token
validity, provider acceptance, or a settled real wager. No live wager is used as a test.
`py -3.13 tests/e2e_token_capture.py` exercises the real passive CDP listener
against a separate synthetic browser, then proves the captured token reaches
the mocked adapter without appearing in the desk's HTTP, DOM, console, or logs.
It never attaches to the operator's real browser or mints a real token.

Automated play may violate the operator's terms. If gambling stops being fun:
1-800-GAMBLER (US), or your national helpline.
