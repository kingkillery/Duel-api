# Open Questions (temp)

Decisions needed before the public push. Answers in chat; this file is scratch.

## Packaging / launch

1. **Repo & brand name** — keep `duel-api` (names the target), or rename to a
   target-agnostic brand with duel.com as "first supported target"? Affects
   `pyproject.toml` name, the `duel-api` entry point, and the README. A rename
   later is a one-line change in packaging but a breaking change for users who
   already installed.
2. **License** — MIT was added as a sensible default (copyright "kingkillery").
   Confirm MIT, or switch (Apache-2.0 adds an explicit patent grant; anything
   else changes the README classifier too)?
3. **Referral link** — `docs/why-i-built-this.md` currently carries a
   placeholder (`https://duel.com/r/YOUR_CODE`). Paste the real link before any
   public push; it is deliberately a footnote, not a CTA.
4. **PyPI** — publish `duel-api` to PyPI so the README's first line can be
   `uvx duel-api doctor` instead of the `git+https` form? Requires checking the
   name is free + setting up a trusted publisher.
5. **CI** — add `.github/workflows/ci.yml` (pytest on 3.10–3.13 + the network
   AST guard) for the green badge? The 297 tests already exist; this is ~20
   lines.
6. **Experiment configs in the public repo** — `s01_flat.json` … `s11_bookend.json`
   ship in the wheel as data. Keep them at root as "the experiment grid," or
   move under `examples/` (cleaner for newcomers, touches the wheel spec)?

## Session / ops (carry-over)

7. **Round loop** — resume the +0.0001 goal loop (last honest position
   +25.55µ / 25.5%) after the launch work, or park it?
8. **`round_flow.py`** — currently untracked local ops runner. Ship it as a
   documented batch tool, or keep it private?

## Verification

9. **Practice mode on duel.com** — `doctor`'s funnel plan assumes verifying
   whether duel.com exposes practice/demo play (check the 169 metadata feature
   flags). Worth one `duel-api metadata` pass before building the sandbox?
