#!/usr/bin/env python3
"""Pre-bet checker for TARGET-HIT 80/60 policies.

Runs the five BEFORE-EVERY-BET checks from the policy contract against a
policy JSON and the currently reported balance. Independent of the
generator: it re-derives everything from the JSON's published fields.

Usage:
    python next_bet.py <policy.json> --balance 5.38 [--pending 0]
    python next_bet.py <policy.json> --audit        # full schedule audit
    python next_bet.py <policy.json> --balance 7.73 --report   # + ledger entry

Exit code 0 only if every applicable check passes.
"""
import sys
import json
from decimal import Decimal, ROUND_CEILING

M_NUM = 999000000  # 99.9 * 100 * 100000


def c(x):
    return int((Decimal(x) * 100).quantize(Decimal("1"), rounding=ROUND_CEILING))


def money(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def row_math(step):
    """Recompute (balance, wager, cp, payout_cents) from a JSON step row."""
    b = c(step["expected_balance"])
    s = c(step["bet_tokens"])
    cp = int(Decimal(step["win_chance_pct"]) * 100)
    payout = (s * (M_NUM // cp)) // 100000          # floor to centoken
    return b, s, cp, payout


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    doc = json.load(open(argv[1]))
    T = c(doc["win_balance_tokens"])
    F = c(doc["protected_balance_tokens"])
    B0 = c(doc["baseline_tokens"])
    steps = doc["steps"]

    args = argv[2:]
    audit = "--audit" in args
    bal_arg, pending = None, 0
    for i, a in enumerate(args):
        if a == "--balance" and i + 1 < len(args):
            bal_arg = args[i + 1]
        if a == "--pending" and i + 1 < len(args):
            pending = int(args[i + 1])
    report = "--report" in args

    def log(balance_c, outcome, step_no):
        """Append a settlement entry to target_hit_log.jsonl (for the record)."""
        if not report:
            return
        import datetime
        with open("target_hit_log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.datetime.now().isoformat(timespec="seconds"),
                "policy_file": argv[1],
                "balance": money(balance_c),
                "outcome": outcome,
                "step": step_no,
            }) + "\n")

    results = []  # (name, status, detail)

    def check(name, ok, detail):
        results.append((name, "PASS" if ok else "FAIL", detail))

    # --- schedule integrity (audit mode; also guards pre-bet mode) ---
    t_ok = T == c(Decimal(money(B0)) * Decimal("1.8"))
    f_ok = F == c(Decimal(money(B0)) * Decimal("0.4"))
    check("0a boundaries = 1.80x/0.40x baseline (ceil)",
          t_ok and f_ok, f"T={money(T)} F={money(F)} from B0={money(B0)}")
    head = c(doc.get("starting_tokens") or doc["baseline_tokens"])
    chain_ok = c(steps[0]["expected_balance"]) == head
    for k in range(len(steps)):
        b, s, cp, payout = row_math(steps[k])
        chain_ok &= (b - s >= F and s >= 1
                     and b - s + payout >= T
                     and (k == len(steps) - 1
                          and b - s == F
                          or k < len(steps) - 1
                          and b - s == c(steps[k + 1]["expected_balance"])))
    check("0b schedule chain (loss path, floors, targets, cents)",
          chain_ok,
          f"{len(steps)} rows, all-loss -> {money(F)}, all wins -> >= {money(T)}")

    if audit:
        for name, st, d in results:
            print(f"[{st}] {name}: {d}")
        sys.exit(0 if all(st == "PASS" for _, st, _ in results) else 1)

    if bal_arg is None:
        sys.exit("need --balance <actual>")
    bal = c(bal_arg)
    if bal == T:
        print(f"ATTEMPT WON: balance {money(bal)} == target {money(T)}. Stop.")
        log(bal, "WON", None)
        sys.exit(0)
    if bal == F:
        print(f"ATTEMPT LOST: balance {money(bal)} == floor {money(F)}. Stop.")
        log(bal, "LOST", None)
        sys.exit(0)
    active = next((st for st in steps if c(st["expected_balance"]) == bal), None)

    # 1. balance matches expected balance for this step
    check("1 balance matches expected", active is not None,
          f"reported {money(bal)}; expected nodes: "
          + ", ".join(st["expected_balance"] for st in steps))

    if active is None:
        for name, st, d in results:
            print(f"[{st}] {name}: {d}")
        print("PAUSE: balance is not a scheduled node. Do not improvise.")
        log(bal, "OFF-NODE", None)
        sys.exit(1)

    b, s, cp, payout = row_math(active)

    # 2. wager at least 0.01 token
    check("2 wager >= 0.01", s >= 1, f"wager {money(s)} (cent granularity)")

    # 3. a loss leaves at least LOSS_FLOOR
    check("3 loss keeps floor", b - s >= F,
          f"{money(b)} - {money(s)} = {money(b - s)} >= floor {money(F)}")

    # 4. quoted payout reaches WIN_TARGET on a win
    needed = T - (b - s)                     # cents of payout required
    min_M = Decimal(needed) / s              # cents/cents -> unitless multiplier
    check("4a payout math (trunc 5dp, floor cent)", b - s + payout >= T,
          f"win balance {money(b - s + payout)} >= {money(T)}")
    print(f"[LIVE] 4b site quote must show >= {min_M.quantize(Decimal('0.00001'))}x "
          f"for roll-over {active['roll_over_label']} "
          f"(policy canonical {active['multiplier_display_5dp']}x)")

    # 5. no previous bet unsettled
    check("5 no unsettled bet", pending == 0,
          f"pending={pending}; settlement consistent "
          f"(reported balance == scheduled node {money(bal)})")

    print(f"\nACTIVE ROW -> step {active['step']}: wager {active['bet_tokens']} "
          f"ROLL OVER {active['roll_over_label']} "
          f"({active['win_chance_pct']}%); "
          f"win->{money(T)} loss->{active['balance_on_loss']}")
    for name, st, d in results:
        print(f"[{st}] {name}: {d}")
    log(bal, "ADVANCE", active["step"])
    sys.exit(0 if all(st == "PASS" for _, st, _ in results) and pending == 0 else 1)


if __name__ == "__main__":
    main(sys.argv)
