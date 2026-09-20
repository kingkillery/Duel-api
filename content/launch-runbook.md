# Launch runbook — EP02 posting sequence

Date: 2026-09-20 · Repo `8a63c41` (HEAD; working tree carries the uncommitted
B1 `next_round.py` extraction + this runbook — verify `git status` before
posting) · Package `duel-api==0.2.2`.
This runbook sequences the remaining human launch steps. It posts nothing
itself; every step below is a human action on @pkslots / YouTube / GitHub.

## 0. Verification status — recorded vs fresh

**Recorded (from prior sessions, not re-verified by posting again):**

- EP02 Post 1 went live from @pkslots on 2026-09-19 (`Your post was sent.`,
  profile count 2 → 3). X did not hydrate status URLs that session, so replies
  2–7 were **never attached** — that is the gap this runbook closes.
- `content/x-thread-ep02-pkslots.md` holds the approved reply copy; the same
  text is mirrored in `06-marketing-sprint.md` §3 with attachment assignments.
- Issue #1 and Discussion #3 were reworded live on 2026-09-20 (ToS + RG kept).

**Freshly verified 2026-09-20 (commands actually run this session):**

| Check | Command | Result |
|---|---|---|
| Repo public | `curl -L https://github.com/kingkillery/Duel-api` | HTTP 200 |
| Issue #1 | `curl -L https://github.com/kingkillery/Duel-api/issues/1` | HTTP 200 |
| Discussion #3 | `curl -L https://github.com/kingkillery/Duel-api/discussions/3` | HTTP 200 |
| Pages index | `curl https://kingkillery.github.io/Duel-api/simple/` | HTTP 200, `text/html` |
| Index contents | `curl https://kingkillery.github.io/Duel-api/simple/duel-api/` | serves `duel_api-0.2.2` only |
| Affiliate link | `curl -L https://duel.com/r/jumpyhitman` | HTTP 200 → `duel.com/?utm_…=jumpyhitman` |
| Fresh install | isolated venv smoke — exact commands in §0a | `duel-api 0.2.2` installed; help/doctor/demo exit 0 |

### 0a. Reproducible isolated smoke (exact commands, run 2026-09-20)

Run from an empty dir outside the repo with an empty HOME/USERPROFILE so
`doctor` cannot see a real profile. Forward-slash paths work on Windows.

```bash
py -3.13 -m venv C:/dev/.smoke-duel-api/venv
mkdir -p C:/dev/.smoke-duel-api/home C:/dev/.smoke-duel-api/work
cd C:/dev/.smoke-duel-api/work
C:/dev/.smoke-duel-api/venv/Scripts/python.exe -m pip install \
  --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api==0.2.2

export HOME='C:/dev/.smoke-duel-api/home'
export USERPROFILE='C:/dev/.smoke-duel-api/home'
export DUEL_PROFILE='C:/dev/.smoke-duel-api/home/session.json'

C:/dev/.smoke-duel-api/venv/Scripts/python.exe -m pip show duel-api   # Version: 0.2.2
C:/dev/.smoke-duel-api/venv/Scripts/duel-api.exe --help               # exit 0
C:/dev/.smoke-duel-api/venv/Scripts/duel-api.exe doctor               # exit 0
C:/dev/.smoke-duel-api/venv/Scripts/duel-api.exe demo --sessions 200  # exit 0
```

Observed: `pip show` → `Version: 0.2.2`; `--help` lists 21 subcommands;
`doctor` reports the packaged spec and **"no session profile"** — expected
offline, that is *not* an all-green credential proof; `demo` prints the Kelly
summary (`f*=-0.001000`, `stake=0` verdict). Note: `duel-api --version` does
not exist — bare invocation exits 2 (argparse requires a subcommand); use
`pip show duel-api` for the version.

## 1. Post EP02 replies 2–7 under Post 1 (@pkslots, in order)

Copy verbatim from `content/x-thread-ep02-pkslots.md` §2–§7. Attachments live
in `content/media/`. Fill the URL + time columns as each reply lands.

| # | Reply | Attach | Posted URL | Posted at (UTC) | Done |
|---|---|---|---|---|---|
| 2 | protocol gates | `gates-card.png` | | | [ ] |
| 3 | gauntlet | `halt-card.png` | | | [ ] |
| 4 | verdict | `trust-line.png` | | | [ ] |
| 5 | install — **PIN THIS** (§2) | `install-card.png` | | | [ ] |
| 6 | start here (links Issue #1 + Discussion #3) | — | | | [ ] |
| 7 | disclosure + affiliate — always last | — | | | [ ] |

Fallback: if a card fails to attach, post the reply without it rather than
reordering — order matters more than media.

## 2. Pin the install reply (reply 5)

Pin reply 5 immediately after posting. The pinned command is the **pinned
version**, not floating latest:

```text
pip install --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api==0.2.2
```

(The thread copy in `x-thread-ep02-pkslots.md` §5 shows the unpinned one-liner;
either is correct — the index currently serves only 0.2.2 — but the pinned form
is what this runbook verified end-to-end today.)

Then: `duel-api doctor && duel-api demo` — both offline-safe, no account needed.

## 3. Short-form video post

- [ ] Upload `content/media/ep02-short-vertical.mp4` (19.44 s, 1080×1920,
  voiced) as an X video post — URL: ______________  time (UTC): ______________
- [ ] Same file as a YouTube Short — URL: ______________  time (UTC): ______________
- Caption: the 30 s spoken hook from `content/ep02-spoken-hook-30s.md`, with the
  §7 disclosure appended.
- YouTube pinned comment (verbatim from `06-marketing-sprint.md` §4):

```text
Install: pip install --extra-index-url https://kingkillery.github.io/Duel-api/simple/ duel-api
Then: duel-api doctor && duel-api demo
Repo: https://github.com/kingkillery/Duel-api
Referral (supports the channel): https://duel.com/r/jumpyhitman
Automated play may violate duel.com ToS — your account, your risk. 1-800-GAMBLER.
```

## 4. Link targets (all verified 200 today)

- Repo: `https://github.com/kingkillery/Duel-api`
- Onboarding issue: `https://github.com/kingkillery/Duel-api/issues/1`
- Start-here discussion: `https://github.com/kingkillery/Duel-api/discussions/3`
- Install index: `https://kingkillery.github.io/Duel-api/simple/`
- Affiliate (verbatim, footnote-only, never in code/CLI):
  `https://duel.com/r/jumpyhitman`

## 5. Metrics — fill the baseline on launch day

Template: `content/metrics-dashboard.md`. Fill the **Week 1** row the day the
replies land; the pre-launch snapshot (stars 1, Issue #1 comments 0, Discussion
#3 replies 0) is already recorded there.

| Metric | Source |
|---|---|
| Wheel downloads | HF dataset `pkkidking/duel-api-dl` counters — **coverage unverified**: whether Pages `pip install` hits are captured by these counters is not confirmed; treat download numbers as a floor, not a total |
| Repo stars | GitHub repo |
| Issue #1 reactions/comments | `issues/1` |
| Discussion #3 replies | `discussions/3` |
| Per-episode repo-link clicks | UTM `utm_campaign=ep02` |
| Referral clicks/signups | duel.com affiliate panel |
| X impressions / Short views | @pkslots + YouTube analytics |

Red flag to watch: referral clicks without repo-link clicks = pitch-first
drift; rewrite that episode's CTA footnote-first.

## 6. Response kit

Copy-paste replies live in `content/community-response-templates.md`:

- "Why didn't it bet?" → HALT explainer (link `docs/betting-gates.md` §5)
- "What's the win rate?" → EV invariant, no win-rate claims (link
  `docs/backtest-math.md`)
- "Is this allowed on duel.com?" → ToS verbatim + Issue #1
- "Where's your referral?" → footnote link + disclosure

Moderation stance: publish wins and losses; correct fake win screenshots, don't
amplify; "the bot refused" reports are first-class feedback — ask for the HALT
reason + config, file as a protocol-clarity issue.

## 7. Claim hygiene (standing, every post)

- Stay in the offline/demo/protocol lane — never imply live end-to-end betting
  works (token-flow items are still open in the work record).
- No profit / prediction / passive-income claims; EV invariant holds.
- Disclosure + ToS warning on every post; `1-800-GAMBLER` on long-form.
- Affiliate link verbatim, footnote-only, never link-first. CTA order:
  math → HALT → install → repo → disclosure → referral footnote.

## 8. Abort conditions

- Any link in §4 stops resolving → fix before posting reply 5/6.
- Pages index serves anything other than `duel_api-0.2.2` → re-verify the
  pinned command before pinning the reply.
- A HALT-reason question you can't answer from `docs/betting-gates.md` → file
  it; don't improvise protocol claims in replies.
