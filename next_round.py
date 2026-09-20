"""next_round.py - loss switch-up protocol decision script.

Reads session_ledger.json + live artifacts, fetches the BTC balance, applies
the verdict table, and prints the exact next-round command for manual approval.

Protocol:
  - After any losing round the next round must SWITCH strategy family
    (LADDER rotation, never re-run the same family) and run a capped
    recovery spec sized to the loss.
- Hard stops: balance floor 0.30 uBTC (reset 2026-09-19 micro bankroll after withdrawal, was 3.00), 3-loss streak, 10 uBTC give-back.

This script never executes a round. It writes recovery config files on a
RECOVERY verdict (unless --dry-run) and prints the command to run.
"""

import argparse
import json
import sys
from decimal import Decimal as D, ROUND_DOWN
from pathlib import Path

LADDER = ["plan1", "glacier", "paroli"]

PLAN1_CONFIGS = [
    "s01_flat.json", "s02_sprint.json", "s03_heavy2.json", "s04_spike.json",
    "s05_decay.json", "s06_ramp.json", "s07_antiramp.json", "s08_burst.json",
    "s09_original.json", "s10_slowspike.json", "s11_bookend.json",
]

FAMILY_CONFIGS = {"glacier": "s12_glacier.json", "paroli": "paroli.json"}

# Live-artifact naming: plan1 owns s01-s11, glacier owns s12, paroli its own
# file. live_s12_glacier.jsonl must never be read as a plan1 artifact.
GLACIER_LIVE = "live_s12_glacier.jsonl"
PAROLI_LIVE = "live_paroli.jsonl"

UBTC = D("1000000")
FLOOR_UBTC = D("0.30")
DRAWDOWN_LIMIT_BTC = D("0.00003000")      # 30.0 uBTC session give-back
RECOVERY_PROFIT_CAP = D("0.00000200")     # 2.0 uBTC single-config recovery cap
MIN_STAKE_BTC = D("0.00000005")           # 0.05 uBTC: smallest stake in live use (s13 micro)


def parse_args():
    p = argparse.ArgumentParser(
        description="Loss switch-up protocol: decide and print the next round command.")
    p.add_argument("--dry-run", action="store_true",
                   help="compute and print everything, write no config files")
    p.add_argument("--ignore-floor", action="store_true",
                   help="bypass the balance floor check temporarily")
    p.add_argument("--ledger", default="session_ledger.json",
                   help="ledger path (default: session_ledger.json)")
    p.add_argument("--balance", default=None, metavar="UBTC",
                   help="balance in uBTC; skips the live fetch")
    return p.parse_args()


def load_rows(path):
    """Return ledger rounds list; [] on missing/unreadable/empty ledger."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    rows = data.get("rounds")
    return rows if isinstance(rows, list) else []


def row_net(row):
    """Parse a row's net as Decimal; None when absent/unparseable."""
    try:
        return D(str(row.get("net")))
    except Exception:
        return None


def is_settled(row):
    """True when a ledger row records a round that actually placed bets.

    A 0-round 0-net row is a non-round artifact: an operator RESET marker, or a
    runner attempt that aborted before any wager. Such rows must not reset the
    loss streak or advance the round counter. A 0-round row with a non-zero net
    (e.g. a partial killed mid-round) still counts - the balance moved.
    """
    if not isinstance(row, dict):
        return True
    try:
        rounds = int(row.get("rounds", 1) or 0)
    except (TypeError, ValueError):
        return True
    if rounds != 0:
        return True
    net = row_net(row)
    return net is None or net != 0


def last_round(rows):
    """Last settled row with a numeric n -> (n, net). (None, None) when none."""
    for row in reversed(rows):
        if not is_settled(row):
            continue
        n = row.get("n") if isinstance(row, dict) else None
        if isinstance(n, (int, float)) and not isinstance(n, bool):
            return int(n), row_net(row)
    return None, None


def loss_streak(rows):
    """Count of trailing settled rows with net < 0.

    Unsettled artifacts (0 rounds AND 0 net) are skipped: an unfunded attempt
    must not reset the streak it never played.
    """
    k = 0
    for row in reversed(rows):
        if not is_settled(row):
            continue
        net = row_net(row)
        if net is None or net >= 0:
            break
        k += 1
    return k


def session_drawdown(rows):
    """Running max of cumulative net minus current cumulative net (BTC).

    Unsettled artifacts are skipped; a 0-round row with a recorded net still
    counts, because the balance moved.
    """
    cum = D(0)
    peak = D(0)
    for row in rows:
        if not is_settled(row):
            continue
        net = row_net(row)
        if net is None:
            continue
        cum += net
        if cum > peak:
            peak = cum
    return peak - cum


def family_of(path):
    """Map a live artifact filename to its family, or None if unrecognised.

    plan1 owns s01-s11 only: live_s12_glacier.jsonl also starts with "live_s1"
    and must never be read as a plan1 artifact.
    """
    name = path.name
    if name == GLACIER_LIVE:
        return "glacier"
    if name == PAROLI_LIVE:
        return "paroli"
    if name.startswith("live_s") and name.endswith(".jsonl"):
        index = name[len("live_s"):len("live_s") + 2]
        if index.isdigit() and 1 <= int(index) <= 11:
            return "plan1"
    return None


def newest_family(*dirs):
    """Family of the most recently written live artifact across `dirs`.

    An archive directory is not guaranteed to hold exactly one round's output
    (a runner may sweep several), so recency decides - not the first filename
    that happens to match a pattern.
    """
    best_mtime, best = None, None
    for d in dirs:
        directory = Path(d)
        if not directory.is_dir():
            continue
        for path in directory.glob("live_*.jsonl"):
            family = family_of(path)
            if family is None:
                continue
            mtime = path.stat().st_mtime
            if best_mtime is None or mtime > best_mtime:
                best_mtime, best = mtime, family
    return best


def infer_family(last_n):
    """Detect the family played in the last round from live artifacts."""
    dirs = [Path(".")]
    if last_n is not None:
        dirs.append(Path(f"round{last_n}_archive"))
    return newest_family(*dirs) or "plan1"


def fetch_balance_ubtc(balance_arg):
    """Return balance in uBTC (Decimal), or None when unavailable."""
    if balance_arg is not None:
        try:
            return D(str(balance_arg))
        except Exception:
            print(f"error: --balance value {balance_arg!r} is not numeric")
            sys.exit(1)
    try:
        from automation_client import DuelClient, DEFAULT_PROFILE
        client = DuelClient.from_profile(DEFAULT_PROFILE)
        try:
            btc = client.balance_for("BTC")
        finally:
            client.close()
        return D(str(btc)) * UBTC
    except Exception as e:
        print(f"warning: balance fetch failed ({e}); skipping floor check")
        return None


def btc_str(value):
    """Plain (never scientific) BTC decimal string."""
    return f"{value:f}"


def config_requirements(name, configs=None):
    """(entry_ubtc, max_loss_ubtc) a config needs, or None when unreadable.

    `configs` is an optional {filename: parsed dict} map; when given, no file
    is read and a missing/non-dict entry counts as unreadable.
    """
    if configs is not None:
        cfg = configs.get(name)
        if not isinstance(cfg, dict):
            return None
    else:
        try:
            cfg = json.loads(Path(name).read_text())
        except Exception:
            return None
    entry = (D(str(cfg.get("base_stake", "0")))
             + D(str(cfg.get("buffer", "0.00000100"))))
    return entry * UBTC, D(str(cfg.get("max_loss", "0"))) * UBTC


def family_fitness(family, balance_ubtc, configs=None):
    """(ok, reason): is this family funded AND floor-safe at this balance?

    A verdict must never name a command the runner's own preflight will refuse,
    nor one whose loss cap would carry the balance through FLOOR_UBTC.

    Exposure is not the max: plan1 runs its 11 configs sequentially, each with
    its own cap (round_flow.py:184-185), so its worst case is the SUM of the
    per-config max_loss values. Entry is the largest single stake, because the
    slots run one at a time.
    """
    if balance_ubtc is None:
        return True, "balance unavailable - funding not verified"
    names = PLAN1_CONFIGS if family == "plan1" else [FAMILY_CONFIGS[family]]
    entry = D(0)
    exposure = D(0)
    for name in names:
        req = config_requirements(name, configs)
        if req is None:
            return False, f"missing/unreadable config {name}"
        entry = max(entry, req[0])
        exposure += req[1]
    if balance_ubtc < entry:
        return False, f"entry {entry:.2f} uBTC > balance {balance_ubtc:.2f} uBTC"
    if balance_ubtc - exposure < FLOOR_UBTC:
        return False, (f"worst case {exposure:.2f} uBTC over {len(names)} slot(s) "
                       f"would breach floor {FLOOR_UBTC:.2f} "
                       f"(lands at {balance_ubtc - exposure:.2f})")
    return True, (f"entry {entry:.2f} uBTC, worst case "
                  f"{balance_ubtc - exposure:.2f} uBTC")


def clamp_stake(name, cfg, base_btc, loss_btc, balance_ubtc, count=1):
    """Scale a stake/loss pair down to what the bankroll can actually carry.

    Preserves the config's own loss:stake ratio (its strategy shape) and never
    drops the stake below MIN_STAKE_BTC. `count` is how many configs share the
    balance concurrently in the same family batch (11 for plan1, 1 otherwise),
    so the family's aggregate exposure - count x max_loss - fits the headroom.
    Returns ({updates}, None), or (None, reason) when the family cannot be run
    fundably and floor-safely.
    """
    if balance_ubtc is None:
        return None, f"{name}: balance unavailable - funding not verified"
    cur_base = D(str(cfg.get("base_stake", "0")))
    cur_loss = D(str(cfg.get("max_loss", "0")))
    if cur_base <= 0 or cur_loss <= 0:
        return None, f"{name}: base_stake/max_loss missing"
    ratio = cur_loss / cur_base                     # the ladder's shape
    headroom = balance_ubtc - FLOOR_UBTC
    if headroom <= 0:
        return None, f"{name}: floor breached (headroom {headroom:.2f} uBTC)"
    budget = headroom / count                       # this config's share
    loss_ubtc = min(max(loss_btc, base_btc) * UBTC, budget)
    base = (loss_ubtc / ratio / UBTC).quantize(D("0.00000001"),
                                               rounding=ROUND_DOWN)
    if base < MIN_STAKE_BTC:
        return None, (f"{name}: stake would fall to {base * UBTC:.3f} uBTC, below "
                      f"MIN_STAKE {MIN_STAKE_BTC * UBTC:.2f} uBTC "
                      f"(headroom {headroom:.2f} uBTC over {count} slot(s))")
    buffer = D(str(cfg.get("buffer", "0.00000100")))
    if balance_ubtc < (base + buffer) * UBTC:
        return None, (f"{name}: entry {(base + buffer) * UBTC:.2f} uBTC > "
                      f"balance {balance_ubtc:.2f} uBTC")
    loss_out = (base * ratio).quantize(D("0.00000001"), rounding=ROUND_DOWN)
    return {"base_stake": btc_str(base), "max_loss": btc_str(loss_out)}, None


def recovery_updates(family, streak, abs_loss, balance_ubtc=None,
                     configs=None):
    """({config_filename: {key: value}}, None) recovery spec, or (None, reason).

    Every stake/loss pair is clamped to the bankroll: the entry must be
    affordable and max_loss must fit inside the floor headroom. A family whose
    stake would have to fall below MIN_STAKE_BTC is not viable and must not be
    recommended. `configs` is an optional {filename: parsed dict} map; when
    given, no files are read.
    """
    base = D("0.00000025") if streak == 2 else D("0.00000050")
    loss = D("0.00000150")
    profit = str(min(abs_loss, RECOVERY_PROFIT_CAP))
    if family == "glacier":
        raw = {"s12_glacier.json": {
            "base_stake": base, "target": 9800, "side": "UNDER",
            "max_loss": loss, "max_profit": profit, "max_rounds": 40,
        }}
    elif family == "paroli":
        raw = {"paroli.json": {
            "base_stake": base, "factor": 2, "bank_after": 3,
            "max_stake": "0.00000200", "target": 5000, "side": "UNDER",
            "max_loss": loss, "max_profit": profit, "max_rounds": 15,
        }}
    elif family == "plan1":
        upd = {"base_stake": base, "max_loss": loss,
               "max_profit": loss, "max_rounds": 15}
        raw = {name: dict(upd) for name in PLAN1_CONFIGS}
    else:
        return None, f"unknown family {family!r}"

    specs = {}
    slots = len(raw)
    for name, updates in raw.items():
        if configs is not None:
            cfg = configs.get(name)
            if not isinstance(cfg, dict):
                return None, f"missing config {name}"
        else:
            path = Path(name)
            if not path.exists():
                return None, f"missing config {name}"
            cfg = json.loads(path.read_text())
        clamped, why = clamp_stake(name, cfg, D(str(updates["base_stake"])),
                                   D(str(updates["max_loss"])), balance_ubtc,
                                   count=slots)
        if clamped is None:
            # Fail closed: clamping only some slots would leave the rest at the
            # old cap, so the batch's aggregate exposure would still breach.
            return None, f"{why} [family needs all {slots} slot(s) clamped]"
        merged = dict(updates)
        merged.update(clamped)
        specs[name] = merged
    return specs, None


def write_specs(specs, dry_run):
    """Write a computed recovery spec map; returns the config names written."""
    if dry_run:
        print("  (dry-run: no files written)")
        return list(specs)
    for name, updates in specs.items():
        p = Path(name)
        cfg = json.loads(p.read_text())
        cfg.update(updates)
        p.write_text(json.dumps(cfg, indent=1) + "\n")
        print(f"  wrote {name} (recovery spec)")
    return list(specs)


def write_recovery_configs(family, streak, abs_loss, dry_run, balance_ubtc=None):
    """Write clamped recovery specs. Returns (names, None) or ([], reason)."""
    specs, reason = recovery_updates(family, streak, abs_loss, balance_ubtc)
    if specs is None:
        return [], reason
    return write_specs(specs, dry_run), None


def command_for(family, next_n, mode, streak):
    note = f'--note "R{next_n} {family} {mode} streak={streak}"'
    if family == "plan1":
        return f"py -3.13 round_flow.py {next_n} --max-rounds 15 {note}"
    return (f"py -3.13 run_single_round.py {next_n} "
            f"{FAMILY_CONFIGS[family]} {note}")


def print_report(last_n, last_net, family, streak, drawdown, balance_ubtc,
                 note=None):
    print("--- LOSS SWITCH-UP PROTOCOL ---")
    print(f"last round:     {last_n if last_n is not None else 'n/a'}")
    if last_net is None:
        print("last net:       n/a")
    else:
        print(f"last net:       {last_net * UBTC:+.2f} uBTC")
    print(f"last family:    {family}")
    print(f"streak:         {streak}")
    print(f"drawdown:       {drawdown * UBTC:.2f} uBTC")
    if balance_ubtc is None:
        print("balance:        unavailable")
        print("floor headroom: unknown")
    else:
        print(f"balance:        {balance_ubtc:.2f} uBTC")
        print(f"floor headroom: {balance_ubtc - FLOOR_UBTC:.2f} uBTC")
    if note:
        print(f"note:           {note}")
    print()


def load_configs():
    """Parse every family config once for compute_verdict().

    Unreadable or non-object files are omitted so the verdict reports them as
    missing (fail closed) instead of crashing mid-decision.
    """
    configs = {}
    for name in list(PLAN1_CONFIGS) + list(FAMILY_CONFIGS.values()):
        try:
            cfg = json.loads(Path(name).read_text())
        except Exception:
            continue
        if isinstance(cfg, dict):
            configs[name] = cfg
    return configs


def compute_verdict(last_n, last_net, family, streak, drawdown, balance_ubtc,
                    ignore_floor=False, configs=None):
    """Pure verdict computation: the same table main() prints, as data.

    No network, no file reads, no writes: `configs` supplies the parsed
    strategy configs ({filename: dict}); a missing entry counts as an
    unreadable config, so the verdict fails closed. Returns a dict:

      verdict   "HALT" | "RECOVERY" | "STANDARD"
      reason    HALT cause, else None
      next_n    round number the command/specs target
      streak    loss streak the verdict was computed with
      family    chosen family (RECOVERY only)
      command   exact runner command (RECOVERY, or per-option for STANDARD)
      specs     computed recovery config updates, NOT written (RECOVERY only)
      skipped   "family: reason" lines for a no-fundable HALT
      options   per-family {family, ok, reason, command} for STANDARD
    """
    configs = {} if configs is None else configs
    next_n = (last_n or 0) + 1
    result = {"verdict": None, "reason": None, "next_n": next_n,
              "streak": streak, "family": None, "command": None,
              "specs": None, "skipped": [], "options": []}

    # Verdict table - evaluated in order, first match wins. A verdict must
    # never name a command the runner will refuse, nor one whose loss cap
    # would carry the balance through FLOOR_UBTC.
    if balance_ubtc is not None and balance_ubtc < FLOOR_UBTC and not ignore_floor:
        result.update(verdict="HALT", reason="floor breach")
    elif streak >= 3:
        result.update(verdict="HALT", reason="three consecutive losses")
    elif drawdown >= DRAWDOWN_LIMIT_BTC:
        result.update(verdict="HALT", reason="session give-back limit")
    elif last_net is not None and last_net < 0:
        start = LADDER.index(family) + 1
        order = LADDER[start:] + LADDER[:start]
        skipped, chosen, chosen_specs = [], None, None
        for fam in order:
            specs, reason = recovery_updates(fam, streak, abs(last_net),
                                             balance_ubtc, configs)
            if specs is not None:
                chosen, chosen_specs = fam, specs
                break
            skipped.append(f"{fam}: {reason}")
        if chosen is None:
            result.update(verdict="HALT",
                          reason="no fundable floor-safe family",
                          skipped=skipped)
        else:
            result.update(verdict="RECOVERY", family=chosen,
                          specs=chosen_specs, skipped=skipped,
                          command=command_for(chosen, next_n, "recovery",
                                              streak))
    else:
        options = []
        for fam in LADDER:
            ok, reason = family_fitness(fam, balance_ubtc, configs)
            options.append({"family": fam, "ok": ok, "reason": reason,
                            "command": (command_for(fam, next_n, "standard",
                                                    streak) if ok else None)})
        if any(o["ok"] for o in options):
            result.update(verdict="STANDARD", options=options)
        else:
            result.update(verdict="HALT",
                          reason="no fundable floor-safe family",
                          skipped=[f"{o['family']}: {o['reason']}"
                                   for o in options])
    return result


def main():
    args = parse_args()
    rows = load_rows(Path(args.ledger))
    unsettled = [r.get("n") for r in rows if not is_settled(r)]
    ledger_note = None if rows else "no ledger data"
    if unsettled:
        ledger_note = (f"{len(unsettled)} unsettled row(s) ignored: "
                       + ", ".join(str(n) for n in unsettled))

    last_n, last_net = last_round(rows)
    streak = loss_streak(rows)
    drawdown = session_drawdown(rows)
    family = infer_family(last_n)
    balance_ubtc = fetch_balance_ubtc(args.balance)
    next_n = (last_n or 0) + 1

    print_report(last_n, last_net, family, streak, drawdown, balance_ubtc,
                 note=ledger_note)

    # The verdict table lives in compute_verdict(); main only renders it and
    # performs the RECOVERY config write the computation produced.
    result = compute_verdict(last_n, last_net, family, streak, drawdown,
                             balance_ubtc, ignore_floor=args.ignore_floor,
                             configs=load_configs())
    if result["verdict"] == "HALT":
        print(f"VERDICT: HALT ({result['reason']})")
        for line in result["skipped"]:
            print(f"  skip {line}")
    elif result["verdict"] == "RECOVERY":
        print(f"VERDICT: RECOVERY {result['family']}")
        write_specs(result["specs"], args.dry_run)
        print(f"  {result['command']}")
    else:
        print("VERDICT: STANDARD")
        for i, opt in enumerate(result["options"], 1):
            if opt["ok"]:
                print(f"  [{i}] {opt['command']}")
            else:
                print(f"  [{i}] skip {opt['family']}: {opt['reason']}")


if __name__ == "__main__":
    main()
