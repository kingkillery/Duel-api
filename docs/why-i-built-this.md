# Why I Built This

I did not set out to build a betting bot. I set out to answer a question: *what does it actually take to automate against a modern SPA that does not want to be automated?*

duel.com was the perfect subject. Cloudflare-gated login. A Vue bundle with no documented API. A realtime feed that refuses non-browser handshakes. Short-lived tokens. Every wall a scraper hits, in one place.

---

## What I found

**The automation was the easy part, eventually.** Capture a real browser session over CDP, fingerprint it faithfully, replay it politely, and the API talks to you. The hard part was everything around it:

- Sessions are **perishable** — the Cloudflare `__cf_bm` cookie lives ~30 minutes, and the honest answer to "is my session stale?" is sometimes *"I don't know"* (which is why staleness here is a tri-state, not a boolean).
- Sites **drift**. A frontend deploy changes the bundle hash and your captured assumptions with it. So there is a drift tripwire (`spec-check`) instead of a pile of mysteriously broken tests.
- Bot gates are **not puzzles to solve**. When the live feed answers a non-browser handshake with `code 3`, this tool reports it and stays read-only. Every spoofing trick would work exactly until the account that paid for it gets banned.

## What the math found

Then I built the thing most people would want from this repo — a strategy tester — and it betrayed the premise:

```
E[net] = −edge × E[total wagered]
```

Every stake schedule is a way of distributing an inevitable cost. Kelly sizing across every observed dice tier returns a **negative** fraction. The growth-optimal stake is zero. Martingale ladders overflow before they recover. Paroli banks more often *only* under a take-profit cap while paying ~2× the edge cost.

The most useful thing this repository produces is a tool that tells you not to use it. I kept it that way.

## The honest ask

This project is free, MIT-licensed, and the analysis above is the point. If you play on duel.com anyway:

- **play with entertainment money** — the budget you'd spend on a night out, not money you need
- **set the budget before you start** (`PlayPolicy` exists for exactly this)

and if you're signing up regardless, using the author's referral link costs you nothing and funds more work like this:

> **Referral:** `https://duel.com/r/YOUR_CODE` *(placeholder — set before publishing)*

It is a footnote on purpose. If the referral ever becomes the pitch, the tool has failed at its one job.

## If gambling stops being fun

1-800-GAMBLER (US), or search your national helpline. No automation, edge table, or stake schedule changes the sign of expectation — that is the one finding in this repo I am certain of.
