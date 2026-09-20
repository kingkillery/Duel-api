# Community response templates

Copy-paste replies for the predictable launch questions. Rules: never soften a guardrail for marketing, always link the doc instead of re-arguing, disclosure stays on any post that carries the referral.

## "Why didn't it bet?" → HALT explainer

```text
That's the feature, not a bug. The protocol HALTs when no family passes all three gates: entry affordability, worst-case floor safety, and fundable total exposure (the SUM over Plan 1's eleven slots, not the average).

An unrunnable bet named anyway would be a lie. Details: docs/betting-gates.md §5. Try `duel-api demo` and watch it refuse on camera.
```

## "What's the win rate?" → EV invariant, no win-rate claims

```text
We don't publish win rates — they'd imply the edge is positive, and the backtester says it isn't. Sizing changes variance, never the sign of expected value; Kelly returns zero at every observed edge tier.

Inspect it yourself: docs/backtest-math.md, then `duel-api demo --sessions 10000 --seed 7`.
```

## "Is this allowed on duel.com?" → ToS verbatim

```text
Automated play may violate duel.com's Terms of Service — your account, your risk. The tool defaults to dry-run / read-only, session values are credentials (never share them), and nothing here is financial advice. Start: https://github.com/kingkillery/Duel-api/issues/1
```

## "Where's your referral?" → footnote link + disclosure

```text
Referral (supports the channel): https://duel.com/r/jumpyhitman

I may earn a commission at no extra cost to you. Same ToS warning as above applies — your account, your risk. Never gamble money you can't afford to lose. Need help? 1-800-GAMBLER.
```

## Moderation stance

- Publish wins and losses; correct fake win screenshots, don't amplify them.
- "The bot refused" reports are first-class feedback — ask for the HALT reason + config, file as a protocol-clarity issue.
- Never argue edge in replies; link the math.
