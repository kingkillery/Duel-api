# Security Policy

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| 0.2.x   | :white_check_mark: |
| < 0.2   | :x:                |

## Reporting a vulnerability

**Do not open a public issue for security reports.** Email the maintainer
(address on the GitHub profile) with:

- what you found and where (file, command, output),
- steps to reproduce that stay read-only (no live bets, no чуж sessions),
- what you think the impact is.

You will get an acknowledgement within 72 hours and a fix-or-wontfix
decision within 14 days. Please give us a reasonable window before
disclosing publicly.

## Rules of engagement for researchers

- **Sessions are credentials.** `captures/`, `*-session*.json`, browser
  profiles, and `security_token` values authenticate as the account owner.
  Never paste them into issues, logs, or reproductions. Redact before sharing.
- **Read-only by default.** Reproduce against `demo`, `audit`, the
  backtester, and dry-run commands. Do not place live wagers to test a report.
- **No account or service harm.** No credential stuffing, no token harvesting
  from other users, no load testing duel.com.

## Scope notes

- `dice-bet` stays a dry run unless `--live` is given; money moves only
  through the four gated commands documented in `docs/betting-gates.md`.
- Automating play may violate duel.com's Terms of Service; that risk sits
  with the operator, not this policy. This policy covers defects in *this
  code*, not disputes with the platform.
- Nothing here is financial advice, and no report can establish live balance,
  payout, or settlement state — only the platform can.
