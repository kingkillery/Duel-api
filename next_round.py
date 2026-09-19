"""next_round.py - loss switch-up protocol decision script.

Reads session_ledger.json + live artifacts, fetches the BTC balance, applies
the verdict table, and prints the exact next-round command for manual approval.

Protocol:
  - After any losing round the next round must SWITCH strategy family
    (LADDER rotation, never re-run the same family) and run a capped
    recovery spec sized to the loss.
  - Hard stops: balance floor 75.00 uBTC, 3-loss streak, 10 uBTC give-back.

This script never executes a round. It writes recovery config files on a
RECOVERY verdict (unless --dry-run) and prints the command to run.
"""

import argparse
import json
import sys
from decimal import Decimal as D
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
FLOOR_UBTC = D("75.00")
DRAWDOWN_LIMIT_BTC = D("0.00003000")      # 30.0 uBTC session give-back
RECOVERY_PROFIT_CAP = D("0.00000200")     # 2.0 uBTC single-config recovery cap


def parse_args():
    p = argparse.ArgumentParser(
        description="Loss switch-up protocol: decide and print the next round command.")
    p.add_argument("--dry-run", action="store_true",
                   help="compute and print everything, write no config files")
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


def last_round(rows):
    """Last row with a numeric n -> (n, net). (None, None) when none."""
    for row in reversed(rows):
        n = row.get("n") if isinstance(row, dict) else None
        if isinstance(n, (int, float)) and not isinstance(n, bool):
            return int(n), row_net(row)
    return None, None


def loss_streak(rows):
    """Count of trailing rows (any n) with net < 0."""
    k = 0
    for row in reversed(rows):
        net = row_net(row)
        if net is None or net >= 0:
            break
        k += 1
    return k


def session_drawdown(rows):
    """Running max of cumulative net minus current cumulative net (BTC)."""
    cum = D(0)
    peak = D(0)
    for row in rows:
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


def recovery_updates(family, streak, abs_loss):
    """{config_filename: {key: value}} recovery spec for the family."""
    base = "0.00000025" if streak == 2 else "0.00000050"
    if family == "glacier":
        return {"s12_glacier.json": {
            "base_stake": base,
            "target": 9800,
            "side": "UNDER",
            "max_loss": "0.00000150",
            "max_profit": str(min(abs_loss, RECOVERY_PROFIT_CAP)),
            "max_rounds": 40,
        }}
    if family == "paroli":
        return {"paroli.json": {
            "base_stake": base,
            "factor": 2,
            "bank_after": 3,
            "max_stake": "0.00000200",
            "target": 5000,
            "side": "UNDER",
            "max_loss": "0.00000150",
            "max_profit": str(min(abs_loss, RECOVERY_PROFIT_CAP)),
            "max_rounds": 15,
        }}
    if family == "plan1":
        upd = {
            "base_stake": base,
            "max_loss": "0.00000150",
            "max_profit": "0.00000150",
            "max_rounds": 15,
        }
        return {name: dict(upd) for name in PLAN1_CONFIGS}
    return {}


def write_recovery_configs(family, streak, abs_loss, dry_run):
    """Write recovery specs (update listed keys only). Exit 1 if missing."""
    specs = recovery_updates(family, streak, abs_loss)
    missing = [name for name in specs if not Path(name).exists()]
    if missing:
        print(f"error: missing config file(s) for {family} recovery: "
              + ", ".join(missing))
        sys.exit(1)
    if dry_run:
        print("  (dry-run: no files written)")
        return
    for name, updates in specs.items():
        p = Path(name)
        cfg = json.loads(p.read_text())
        cfg.update(updates)
        p.write_text(json.dumps(cfg, indent=1) + "\n")
        print(f"  wrote {name} (recovery spec)")


def command_for(family, next_n, mode, streak):
    note = f'--note "R{next_n} {family} {mode} streak={streak}"'
    if family == "plan1":
        return f"py -3.13 round_flow.py {next_n} --max-rounds 15 {note}"
    return (f"py -3.13 run_single_round.py {next_n} "
            f"{FAMILY_CONFIGS[family]} {note}")


def print_report(last_n, last_net, family, streak, drawdown, balance_ubtc,
                 note=None):
    print("─── LOSS SWITCH-UP PROTOCOL ───")
    print(f"last round:     {last_n if last_n is not None else 'n/a'}")
    if last_net is None:
        print("last net:       n/a")
    else:
        print(f"last net:       {last_net * UBTC:+.2f} µBTC")
    print(f"last family:    {family}")
    print(f"streak:         {streak}")
    print(f"drawdown:       {drawdown * UBTC:.2f} µBTC")
    if balance_ubtc is None:
        print("balance:        unavailable")
        print("floor headroom: unknown")
    else:
        print(f"balance:        {balance_ubtc:.2f} µBTC")
        print(f"floor headroom: {balance_ubtc - FLOOR_UBTC:.2f} µBTC")
    if note:
        print(f"note:           {note}")
    print()


def main():
    args = parse_args()
    rows = load_rows(Path(args.ledger))
    ledger_note = None if rows else "no ledger data"

    last_n, last_net = last_round(rows)
    streak = loss_streak(rows)
    drawdown = session_drawdown(rows)
    family = infer_family(last_n)
    balance_ubtc = fetch_balance_ubtc(args.balance)
    next_n = (last_n or 0) + 1

    print_report(last_n, last_net, family, streak, drawdown, balance_ubtc,
                 note=ledger_note)

    # Verdict table - evaluated in order, first match wins.
    if balance_ubtc is not None and balance_ubtc < FLOOR_UBTC:
        print("VERDICT: HALT (floor breach)")
    elif streak >= 3:
        print("VERDICT: HALT (three consecutive losses)")
    elif drawdown >= DRAWDOWN_LIMIT_BTC:
        print("VERDICT: HALT (session give-back limit)")
    elif last_net is not None and last_net < 0:
        next_family = LADDER[(LADDER.index(family) + 1) % len(LADDER)]
        print(f"VERDICT: RECOVERY {next_family}")
        write_recovery_configs(next_family, streak, abs(last_net),
                               args.dry_run)
        print(f"  {command_for(next_family, next_n, 'recovery', streak)}")
    else:
        print("VERDICT: STANDARD")
        for i, fam in enumerate(LADDER, 1):
            print(f"  [{i}] {command_for(fam, next_n, 'standard', streak)}")


if __name__ == "__main__":
    main()
