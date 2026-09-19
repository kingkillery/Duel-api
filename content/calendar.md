# Content calendar — duel-api release schedule

Positioning: the honest gambling tool. Every episode shows real math,
including HALTs and $0 stakes. Affiliate link carries UTM
(`?utm_source=<channel>&utm_medium=video&utm_campaign=<episode>`), and
every post carries the disclosure line at the bottom of this file.

Pipeline: scripts below are written in the 5-beat shape
(`hook / recon / gauntlet / verdict / cta`) that `tools/make_demo_video.py`
renders — horizontal, 9:16 vertical, voiced, and GIF. Beat timings in each
script are **estimates**; replace with real TTS timings before rendering
(see `tools/narration-timing.json` schema). EP02 already has a starter
timing JSON.

## Schedule

| Week | Episode | Title | Channel | Goal |
| ---- | ------- | ----- | ------- | ---- |
| 1 | EP01 (exists) | Hero demo (re-render) | YouTube + X | Re-render `docs/demo.mp4` — CTA still says "297 tests", now 331 |
| 2 | EP02 | The bot that refuses to bet | YouTube + X | Flagship: HALT honesty, protocol verdict gates |
| 3 | EP03 | Sizing changes variance, never the sign | YouTube + blog/SEO | Backtester fails closed; Kelly returns exactly zero |
| 4 | EP04 | Your first verdict in 5 minutes | YouTube + docs | Capture → dry-run → `next_round`; onboarding conversion |
| 5 | EP05 | 0.30 µBTC bankroll diaries | X thread + Shorts | Micro-bankroll saga (62W/2L, then the honest HALT) |
| 6 | — | Docs site launch (mkdocs) | SEO | Evergreen funnel for all episodes |

Cadence after week 6: one short (vertical cut of the best-performing
30 seconds) per week; one full episode per month driven by real ledger
events (only publish wins *and* losses — the honesty is the brand).

## Per-episode checklist

- [ ] Narration recorded; timing JSON measured (replace estimates)
- [ ] Rendered: horizontal + vertical + voiced + GIF (`tools/make_demo_video.py`)
- [ ] Watched end-to-end: numbers on screen match the script
- [ ] Affiliate link with episode UTM; disclosure line included
- [ ] Pinned comment: install command + ToS warning + responsible-gambling note

## Required disclosure (every post, verbatim or equivalent)

> I may earn a commission if you sign up through my link, at no cost to
> you. This tool automates play and may violate duel.com's Terms of
> Service — your account, your risk. Nothing here is financial advice.
> Never gamble money you can't afford to lose.
