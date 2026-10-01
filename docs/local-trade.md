# Local trade desk

`sandbox/trade.py` is a localhost-only manual wager page. It is not the hosted
sandbox. `sandbox/app.py` serves only the offline backtester. It has no bet form
and no local roll.

Install the optional web dependencies if needed: `py -3.13 -m pip install -e ".[sandbox]"`.

The desk starts disabled. This command shows credential presence and does not
send a wager:

```console
py -3.13 -m sandbox.trade
```

Open `http://127.0.0.1:8765/`. Live mode is a separate server-process opt-in.
It also requires an explicit stake ceiling. There is no default ceiling.
The ceiling is in the selected coin's units, not fiat. Choose it deliberately
before switching currencies; the desk does not convert this limit between coins.

```console
$env:DUEL_TRADE_LIVE = "1"
$env:DUEL_TRADE_MAX_STAKE = "0.00000050"
$env:DUEL_TRADE_SECURITY_TOKEN = "<browser-minted token>"
py -3.13 -m sandbox.trade
```

`DUEL_TRADE_SECURITY_TOKEN` is only required for a Duel wager. Put a token the
browser already minted into the server environment. The page has no token field,
does not accept one in the request, and the server never returns the value.
DuckDice uses the existing server-side key resolution and does not use that token.

Duel's captured session is loaded from `DUEL_TRADE_PROFILE`, or
`.private-api-automation/profiles/default/session.json` by default. Supply tokens
through your existing secret workflow rather than saving real values in shell
history or committed scripts. An expired token/session is an error, not permission
to retry a wager. This desk does not capture credentials or bypass bot gates.

## What the page can do

- Choose `duel` or `duckdice`, then currency, stake, target, and side.
- Send one confirmed wager through the existing `place_dice_bet()` method.
- Show the provider result the server returned. The page does not recompute
  profit or balance.

## What the page cannot do

- Turn live mode on, set the stake ceiling, choose a profile, or supply a key.
- Retry. Duel clients used here set `max_429_retries=0`, `auto_refresh=False`,
  and `follow_redirects=False`.
- Continue after an ambiguous send. Reconcile the wager in the provider's own
  account history **before** restarting the blocked process. Restarting only clears
  the local lock; it does not establish whether a wager settled.

The Bet control requires the on-page confirmation checkbox, and the server
rejects a missing or false `confirm` even if the checkbox is bypassed. A second
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
credentials, stake ceilings, request mapping, displayed results, duplicate and
concurrent submissions, error secrecy, and the ambiguous-outcome lock after reload.

This establishes local UI-to-adapter behavior, **not** current live session/token
validity, provider acceptance, or a settled real wager. No live wager is used as a test.

Automated play may violate the operator's terms. If gambling stops being fun:
1-800-GAMBLER (US), or your national helpline.
