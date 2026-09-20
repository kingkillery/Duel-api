# Metrics dashboard sketch

Record week-1 values the day EP02 replies go live — that row is the baseline everything else is measured against. No tracking pixels, no fingerprinting: counts from public surfaces + download counters only.

## Baseline row (fill on launch day)

| Metric | Source | Week 1 | Week 2 | Week 3 | Week 4 | Week 5 | Week 6 |
|---|---|---|---|---|---|---|---|
| Wheel downloads | HF dataset `pkkidking/duel-api-dl` counters | | | | | | |
| Repo stars | GitHub repo | | | | | | |
| Issue #1 reactions/comments | `issues/1` | | | | | | |
| Discussion #3 replies | `discussions/3` | | | | | | |
| Per-episode repo-link clicks | UTM `utm_campaign=ep0X` (wherever links are shortened/counted) | | | | | | |
| Referral clicks/signups | duel.com affiliate panel | | | | | | |
| X impressions / Short views | @pkslots + YouTube analytics | | | | | | |

## Pre-launch snapshot (auto-pulled 2026-09-20, pre-posting)

| Metric | Value |
|---|---|
| Repo stars | 1 |
| Open issues | 1 |
| Issue #1 comments | 0 |
| Discussion #3 replies | 0 |

Week-1 baseline still gets filled on launch day (above); this snapshot proves the floor it starts from.

## UTM convention (already frozen in calendar.md)

- Repo/demo links: `?utm_source=<channel>&utm_medium=video&utm_campaign=<episode>` — e.g. `?utm_source=youtube&utm_medium=video&utm_campaign=ep02`.
- Referral `https://duel.com/r/jumpyhitman` used verbatim — never append UTM to it.
- Per-episode checklist item: confirm the episode's UTM appears on its repo links before posting.

## What "working" looks like

- Leading: install attempts (`doctor`/`demo` runs can't be counted — offline by design; use download counts as proxy), Discussion questions about HALT reasons (trust signal, not support burden).
- Lagging: referral conversions concentrated in EP04/EP05 weeks (tutorial + diaries convert; EP02 builds trust).
- Red flag: referral clicks without repo-link clicks on the same episode = pitch-first drift; rewrite that episode's CTA footnote-first.
