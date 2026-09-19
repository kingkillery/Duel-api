# Duel-api agent guidance

## Read the Design-and-Building wiki first

Before investigating, modifying, or using this project's tooling, read its existing
Design-and-Building wiki. Do not rediscover the API or build a parallel client
without first consulting the documented architecture, decisions, and findings.

- Vault root: `C:/dev/Vaults/Design-and-Building`
- Project hub: `C:/dev/Vaults/Design-and-Building/Portfolio/Current Projects/duel-api.md`
- Project chapters: `C:/dev/Vaults/Design-and-Building/Portfolio/Current Projects/duel-api/`
- Open in Obsidian: [Duel-api wiki](obsidian://open?vault=Design-and-Building&file=Portfolio%2FCurrent%20Projects%2Fduel-api)
- With vault-aware tools: `vault://Design-and-Building/Portfolio/Current Projects/duel-api.md`

### Required reading order

Paths below are relative to the vault root:

1. `Portfolio/Current Projects/duel-api/02-context-pack.md` — repository map,
   existing tooling, environment, session lifecycle, and known gotchas.
2. `Portfolio/Current Projects/duel-api/03-decisions.md` — design rationale,
   security boundaries, verification requirements, and working agreements.
3. `Portfolio/Current Projects/duel-api/01-scope.md` — scope and interface contracts.
4. `Portfolio/Current Projects/duel-api.md` — project hub and linked work records.
5. `Portfolio/Current Projects/duel-api/04-agent-log.md` — latest dated changes,
   runner history, and operational findings.
6. `Daily Todos/Open/duel-api-fix-batch-and-open-findings.md` — open token-flow
   integration and verification work.

The hub, context pack, and scope include older snapshots; the agent log contains
newer changes. Compare dates and verify claims against current source and focused
tests. Historical run results, balances, test counts, and session state are not
current verification or authorization. Surface contradictions rather than silently
assuming they are resolved.

## Existing implementation

Consult the wiki before reading the relevant source:

- `automation_client.py` — `DuelClient`, session handling, and guarded transport.
- `automation_cli.py` — existing command-line entrypoints.
- `capture_session.py` — session capture tooling.
- `site_spec.json` — endpoint contracts and recorded bundle provenance.
- `round_flow.py` and `run_single_round.py` — runners referenced in the agent log;
  inspect source without executing them during discovery.
- `backtest/` and `tests/` — offline analysis and regression checks.
- `docs/betting-gates.md` — the money gates, token rules, and the protocol verdict
  gates (entry affordability, floor safety, exposure as the SUM over plan1's slots).
- `tests/test_protocol_funding.py` — regression tests pinning those verdict gates and
  the ledger rule that a 0-round 0-net row is not a settled round.

Use `py -3.13` for project Python commands. Keep credentials and session values out
of documentation and logs. Preserve read-only defaults, dry-run behavior, and
safety guards; do not use live wagers to test code or treat a local calculation as
proof of live balance, payout, or settlement state. Do not place live bets on the
user's behalf. Commit or push only when explicitly requested.
