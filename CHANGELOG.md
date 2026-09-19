# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/); versioning follows
[SemVer](https://semver.org/).

## [0.2.1] — 2026-09-19

Week-1 production batch (no runtime behavior changes).

### Added

- `SECURITY.md`, `CONTRIBUTING.md`, issue/PR templates.
- `publish.yml`: PyPI Trusted Publishing on `v*` tags, TestPyPI on demand.
- CI `smoke` job (installed entry points + offline engine) and `gitleaks`
  secret scan.
- `content/`: 6-week release calendar, EP02–EP05 scripts, EP02 timing starter.

## [0.2.0] — 2026-09-19

Public release.

### Added

- Loss switch-up protocol (`next_round.py`): per-family entry affordability,
  floor safety against a 0.30 µBTC micro floor, plan1 exposure as the SUM
  over its 11 slots, bankroll-clamped recovery stakes, `VERDICT: HALT` with
  per-family reasons, `--ignore-floor` opt-out.
- Runner hardening (`run_single_round.py`): refuses 0-round ledger rows,
  `--token` browser-token paste with REST/CDP fallback, SOL currency support.
- Offline policy tools: `target_hit_policy.py`, `next_bet.py`, `auto_goal.py`;
  micro configs `s13`/`s14`.
- `docs/betting-gates.md` §5 pins the verdict gates; README states the
  protocol and offline-tool limits.
- `tests/test_protocol_funding.py`: 20 cases pinning the verdict gates and
  the rule that a 0-round 0-net row is not a settled round (331 tests total).

### Changed

- Micro-bankroll reset: floor 75.00 → 0.30 µBTC (`goal_state.json`).
- `automation_cli.py`: per-config `buffer` override.
- Session ledger rounds 70–73 (R73 glacier 22W/0L, +0.107 µBTC).

## [0.1.0] — 2026-09-16

Initial public snapshot: reversal-engineered duel.com dice client,
capture → spec → replay tooling, honest backtester, demo video suite,
297-test suite, MIT license.
