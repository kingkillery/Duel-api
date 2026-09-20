# EP03 — "Sizing changes variance, never the sign"

Runtime target: ~60 s. Same 5-beat pipeline shape. Durations estimated.

## hook — 10 s

> Flat stake, Martingale, Fibonacci, whatever your favorite trader swears
> by. I simulated ten thousand sessions of each. The answer rhymes every
> single time.

Visuals: system names slamming in, equity curves fanning out.

## recon — 14 s

> The backtester in duel-api is network-free and seeded — same seed, same
> result, anyone can reproduce it. It runs every staking schedule at the
> most favorable edge tier the exchange has ever shown: one tenth of one
> percent house edge.

Visuals: `demo --sessions 10000 --seed 7` terminal, determinism note.

## gauntlet — 12 s

> Martingale spikes harder. Fibonacci grinds longer. Flat just bleeds
> slower. Different shapes, same destination — because the math only has
> one term that matters: negative edge times total wagered.

Visuals: overlaid equity fans per system, EV equation card.

## verdict — 17 s

> And fractional Kelly — the "optimal" sizing — returns exactly zero stake
> at every observed tier. Not small. Zero. The tool's most useful feature
> is that it fails closed: when the edge is negative, the correct bet size
> is nothing, and it says so.

Visuals: Kelly row of zeros, `stake = 0` card, "fails closed" stamp.

## cta — 7 s

> Run it yourself — one command, no account needed. Repo link below (UTM
> `?utm_source=youtube&utm_medium=video&utm_campaign=ep03`), with the
> usual disclosure: sign up through https://duel.com/r/jumpyhitman and you
> support the channel free.

UTM: `?utm_source=youtube&utm_medium=video&utm_campaign=ep03`.
Install on screen: `uvx … duel-api demo --sessions 10000`.
