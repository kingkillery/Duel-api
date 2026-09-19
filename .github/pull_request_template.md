## What and why

## Verification

- [ ] `pytest` green locally (331 tests)
- [ ] New behavior covered by tests (no network in tests, ever)
- [ ] Gate/verdict changes update `docs/betting-gates.md` + `tests/test_protocol_funding.py`
- [ ] No sessions, tokens, profiles, or account identifiers in the diff (`git status` checked)
- [ ] README/CHANGELOG updated if user-facing

## Live-path checklist (only if touching live commands)

- [ ] Dry-run default preserved; no live wagers placed during development
- [ ] Failure modes fail closed with a reason, never a silent bet
