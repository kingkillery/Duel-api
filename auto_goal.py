"""auto_goal.py - Autonomous protocol-driven session driver toward +0.0001 BTC goal.

Runs the Loss Switch-Up Protocol in a loop:
  - Checks live BTC balance & circuit breakers before each round
  - Resolves next family (Glacier -> Paroli -> Plan 1) based on outcome
  - Runs single-round or batch using REST-minted security tokens
  - Appends to session_ledger.json
  - Stops when:
      1. cumulative session profit >= +0.0001 BTC (+100.0 uBTC)
      2. balance < safety floor (0.25 uBTC)
      3. 3-loss streak reached
      4. user abort / kill
"""
import json
import subprocess
import sys
import time
from decimal import Decimal as D
from pathlib import Path

GOAL_TARGET_BTC = D("0.00010000")  # +100.0 uBTC
MIN_BALANCE_BTC = D("0.00000025")  # 0.25 uBTC absolute bottom floor


def fetch_balance():
    cmd = ["py", "-3.13", "-c", """
from automation_client import DuelClient, DEFAULT_PROFILE
with DuelClient.from_profile(DEFAULT_PROFILE) as c:
    print(c.balance_for('BTC'))
"""]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return D(r.stdout.strip())
    except Exception:
        return None


def get_verdict():
    cmd = ["py", "-3.13", "next_round.py", "--ignore-floor", "--dry-run"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    out = r.stdout
    lines = out.splitlines()
    verdict_line = next((l for l in lines if l.startswith("VERDICT:")), "")
    command_line = ""
    for l in lines:
        if l.strip().startswith("py -3.13 run_single_round.py") or l.strip().startswith("py -3.13 round_flow.py"):
            command_line = l.strip()
            break
    return verdict_line, command_line, out


def get_session_total():
    try:
        d = json.loads(Path("session_ledger.json").read_text(encoding="utf-8"))
        return D(str(d.get("session_total", "0")))
    except Exception:
        return D("0")


def get_last_round_n():
    try:
        d = json.loads(Path("session_ledger.json").read_text(encoding="utf-8"))
        for r in reversed(d.get("rounds", [])):
            if "n" in r and isinstance(r["n"], int):
                return r["n"]
    except Exception:
        pass
    return 73


def main():
    print(f"[{time.strftime('%H:%M:%S')}] Starting autonomous goal driver toward {GOAL_TARGET_BTC} BTC (+100 uBTC)...")
    
    round_count = 0
    max_auto_rounds = 50

    while round_count < max_auto_rounds:
        bal = fetch_balance()
        sess_total = get_session_total()
        last_n = get_last_round_n()
        next_n = last_n + 1

        print(f"\n=======================================================")
        print(f"[{time.strftime('%H:%M:%S')}] Round {next_n} Pre-Flight")
        print(f"  BTC Balance:    {bal} ({float(bal)*1e6:.2f} uBTC)" if bal else "  BTC Balance:    Unknown")
        print(f"  Session Total:  {sess_total} ({float(sess_total)*1e6:.2f} uBTC)")
        print(f"  Target Goal:    {GOAL_TARGET_BTC} (+100.00 uBTC)")
        print(f"=======================================================")

        if sess_total >= GOAL_TARGET_BTC:
            print(f"\n🎉 GOAL REACHED! Session total {sess_total} >= {GOAL_TARGET_BTC}")
            break

        if bal is not None and bal < MIN_BALANCE_BTC:
            print(f"\n⛔ HALT: Balance {bal} below minimum operational floor {MIN_BALANCE_BTC}")
            break

        verdict, cmd, full_out = get_verdict()
        print(f"  {verdict}")

        if "HALT" in verdict:
            print(f"\n⛔ Protocol HALT condition triggered:")
            print(full_out)
            break

        # Check which command to run
        if not cmd:
            # If standard verdict, default to s12_glacier for safest steady accumulation on micro balance
            if bal and bal < D("0.00000060"):
                cfg = "s12_glacier.json"
                family = "glacier"
            else:
                cfg = "paroli.json"
                family = "paroli"
            cmd = f"py -3.13 run_single_round.py {next_n} {cfg} --note \"R{next_n} {family} auto-mode\""

        print(f"  Executing: {cmd}")
        parts = cmd.split()
        r = subprocess.run(parts, capture_output=True, text=True, timeout=180)
        print(r.stdout.strip())
        if r.stderr:
            print("STDERR:", r.stderr.strip()[:300])

        round_count += 1
        time.sleep(1)


if __name__ == "__main__":
    main()
