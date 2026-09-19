# Contributing

## Ground rules

1. **Read-only by default.** Everything new should work offline (`demo`,
   `audit`, backtester, dry runs) unless its whole purpose is a guarded live
   path. Tests must never touch the network — `tests/` is AST-guarded and CI
   enforces it.
2. **No live wagers in development or tests.** Not to verify, not as a joke.
   A local calculation is never proof of live balance, payout, or settlement.
3. **Sessions are credentials.** Never commit `captures/`, session JSON,
   tokens, browser profiles, or round logs with account identifiers. When in
   doubt, `git status` before `git add`, and keep new scratch outputs covered
   by `.gitignore`.
4. **Money gates stay closed.** Any change touching `dice-bet`, `autobet`,
   the gates in `docs/betting-gates.md`, or the verdict logic in
   `next_round.py` must update the gates doc *and* extend
   `tests/test_protocol_funding.py`. Green suite or it doesn't land.

## Workflow

- Python 3.10+; use `py -3.13` (Windows) or `python3` per the README.
- Install: `pip install -e ".[all,dev]"`.
- Run the suite: `pytest` (currently 331 tests, ~40 s). Targeted first,
  full suite before push.
- Style: keep the existing plain-stdlib shape; no new dependencies without
  discussion in an issue.
- Commit messages: short imperative subject; explain *why* in the body when
  the change is a judgment call (floors, gates, clamps).

## Issues and PRs

- Bugs: use the bug template — command, expected vs actual, redacted logs.
- Features: use the feature template — offline-first proposal, gate impact.
- PRs: fill the checklist. Small, focused PRs merge fastest. One concern per PR.

## Releases

Maintainers cut releases by tagging `vX.Y.Z` (see `CHANGELOG.md`); CI runs
the suite and the smoke job, and tag pushes publish to PyPI via Trusted
Publishing.
