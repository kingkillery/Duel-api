# EP02 — "The bot that refuses to bet" (flagship)

Runtime target: ~60 s. Beats match `tools/make_demo_video.py` scenes.
Durations are TTS estimates — re-measure before rendering.
Companion timing starter: `ep02-timing.json` (same schema as
`tools/narration-timing.json`).

## hook — 11 s

> Every betting bot promises to print money. Mine just told me… to do
> nothing. And that refusal is the most valuable output it has ever produced.

Visuals: kinetic slam, `VERDICT: HALT` card. Caption the word NOTHING.

## recon — 15 s

> This is duel-api's loss switch-up protocol. Before it names any bet, it
> checks three things: can this family afford the entry, does the worst case
> stay above the floor, and is the total exposure fundable — the SUM over all
> eleven slots, not the average, not the best case.

Visuals: architecture flow — policy → per-family math → verdict. On-screen:
`balance − exposure ≥ floor`.

## gauntlet — 11 s

> Watch it work. Balance: zero point six four micro-BTC. Floor: zero point
> three. Plan one exposure sums to over one micro — more than the account
> holds. Every family fails the same check, one by one, with its reason
> printed next to it.

Visuals: terminal stream of per-family `excluded …` reasons scrolling,
ending on the HALT card.

## verdict — 17 s

> Here is the discipline most tools will never show you: naming an
> unrunnable command would be a lie, so the protocol halts instead. No
> blended exposure, no silent clamp, no bet sized on hope. A tool that says
> "don't" is a tool you can trust when it says "do".

Visuals: card states — HALT reasons → the three gate rules → trust line.

## cta — 7 s

> Three hundred thirty-one tests pin these gates. Inspect the math yourself
> — link below. And if you sign up there, it supports the channel at no cost
> to you.

Visuals: repo + install command (`uvx … duel-api doctor`), UTM:
`?utm_source=youtube&utm_medium=video&utm_campaign=ep02`.
