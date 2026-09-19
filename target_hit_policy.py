#!/usr/bin/env python3
"""TARGET-HIT 80/60 policy generator (fixed boundaries, conservative payout checks).

Boundaries for a baseline B0:
    WIN_TARGET = 1.80 x B0   (rounded UP to a whole centoken)
    LOSS_FLOOR = 0.40 x B0   (rounded UP to a whole centoken)
Both stay fixed for the whole attempt; they never trail and never reset.

Every wager must satisfy BOTH:
    1. a loss cannot take the balance below LOSS_FLOOR;
    2. a win still reaches WIN_TARGET, checked conservatively with the
       multiplier truncated at 5 decimals and the gross payout floored to
       the centoken.

Recurrence (r = centokens of loss allowance remaining, V(0) = 0):
    V(r) = max over legal wagers s of [ p(s) + (1 - p(s)) * V(r - s) ]
where p(s) is the highest permitted win chance (0.02%-97.99%, 0.01pp steps)
whose conservatively floored payout still reaches the target.

Payout model (dice): M = 99.9 / win_chance_percent, stake returned.
Win chance granularity: 0.01 pp. Wager granularity: 0.01 token.

Usage:
    python target_hit_policy.py 10.00                    # fresh policy (JSON)
    python target_hit_policy.py 10.00 --table            # fresh policy (table)
    python target_hit_policy.py 10.00 --at 5.38 --table  # continuation inside
                                                        # the SAME attempt
Standard library only. Places no bets, connects to nothing.
"""
import sys
import json
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

SC = 100000            # multiplier scale for 5-decimal truncation
M_NUM = 999000000      # 99.9 * 100 * 100000  -> M_scaled = M_NUM // cp
CP_MIN, CP_MAX = 2, 9799   # 0.02% .. 97.99% in hundredths of a percent


def cents_up(tok):
    """Tokens (string/Decimal) -> integer centokens, rounded UP."""
    return int((Decimal(tok) * 100).quantize(Decimal("1"), rounding=ROUND_CEILING))


def money(c):
    return f"{c // 100}.{c % 100:02d}"


def pct(cp):
    return f"{cp // 100}.{cp % 100:02d}"


def max_cp(b_c, s_c, T_c):
    """Highest win chance (hundredths of a percent) whose conservatively
    floored payout still reaches T_c from balance b_c wagering s_c.
    Returns None when even 0.02% cannot reach (impossible here)."""
    d = T_c - b_c + s_c            # centokens of payout needed on a win
    if d <= 0:
        return CP_MAX
    cp = (9990 * s_c) // d         # continuous upper bound on p
    if cp > CP_MAX:
        cp = CP_MAX
    while cp >= CP_MIN:
        payout_c = (s_c * (M_NUM // cp)) // SC      # floor to centoken
        if b_c - s_c + payout_c >= T_c:
            return cp
        cp -= 1
    return None


def solve(T_c, F_c, start_c):
    r_max = start_c - F_c
    V = [0.0] * (r_max + 1)
    choice = [None] * (r_max + 1)
    for r in range(1, r_max + 1):
        b_c = F_c + r
        best, arg = 0.0, None
        for s_c in range(1, r + 1):
            cp = max_cp(b_c, s_c, T_c)
            if cp is None:
                continue
            p = cp / 10000.0
            cand = p + (1.0 - p) * V[r - s_c]
            if cand > best:
                best, arg = cand, (s_c, cp)
        V[r] = best
        choice[r] = arg
    return V, choice


def walk(T_c, F_c, start_c, choice):
    steps, r = [], start_c - F_c
    while r > 0 and choice[r] is not None:
        s_c, cp = choice[r]
        b_c = F_c + r
        m_scaled = M_NUM // cp
        payout_c = (s_c * m_scaled) // SC
        steps.append({
            "step": len(steps) + 1,
            "expected_balance": money(b_c),
            "bet_tokens": money(s_c),
            "direction": "over",
            "win_chance_pct": pct(cp),
            "roll_over_label": pct(10000 - cp),
            "multiplier_display_5dp": str(
                (Decimal(m_scaled) / SC).quantize(Decimal("0.00001"),
                                                  rounding=ROUND_HALF_UP)),
            "minimum_balance_on_win": money(b_c - s_c + payout_c),
            "balance_on_loss": money(b_c - s_c),
            "_p": cp / 10000.0,
        })
        r -= s_c
    return steps


def stats(steps):
    survive, expected_rolls = 1.0, 0.0
    for st in steps:
        expected_rolls += survive
        survive *= (1.0 - st["_p"])
    return expected_rolls, 1.0 - survive


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    b0 = Decimal(argv[1])
    T_c = cents_up(b0 * Decimal("1.8"))
    F_c = cents_up(b0 * Decimal("0.4"))
    at = Decimal("5.38") if False else None
    args = argv[2:]
    table = "--table" in args
    start_tok = b0
    for i, a in enumerate(args):
        if a == "--at" and i + 1 < len(args):
            start_tok = Decimal(args[i + 1])
    start_c = cents_up(start_tok)

    V, choice = solve(T_c, F_c, start_c)
    steps = walk(T_c, F_c, start_c, choice)
    expected_rolls, _ = stats(steps)
    doc = {
        "policy": "target_hit",
        "mode": "fresh" if start_c == cents_up(b0) else "continuation",
        "baseline_tokens": str(b0),
        "starting_tokens": str(start_tok),
        "win_balance_tokens": money(T_c),
        "protected_balance_tokens": money(F_c),
        "success_probability_from_here": V[start_c - F_c],
        "maximum_rolls": len(steps),
        "expected_rolls": round(expected_rolls, 2),
        "on_win": "Stop if credited balance reaches target; otherwise pause for payout mismatch.",
        "on_loss": "Advance one step; stop at protected balance.",
        "on_unexpected_balance_or_quote": "Pause. Do not improvise or change the starting baseline.",
        "rounding": "Target and protected reserve rounded UP to a whole centoken.",
        "steps": [{k: v for k, v in s.items() if not k.startswith("_")} for s in steps],
    }
    if table:
        w = (4, 14, 7, 12, 11, 12, 18)
        hdr = ("Step", "Balance before", "Wager", "Win chance", "Roll over",
               "Multiplier", "Balance if lost")
        print("START: {}  TARGET: {}  FLOOR: {}  P(success from here): {:.6%}"
              .format(money(start_c), money(T_c), money(F_c), V[start_c - F_c]))
        print(("{:<4} {:>14} {:>7} {:>12} {:>11} {:>12} {:>18}").format(*hdr))
        for s in doc["steps"]:
            print(("{:<4} {:>14} {:>7} {:>12} {:>11} {:>12} {:>18}").format(
                s["step"], s["expected_balance"], s["bet_tokens"],
                s["win_chance_pct"] + "%", s["roll_over_label"],
                s["multiplier_display_5dp"], s["balance_on_loss"]))
        print("Max rolls: {}   Expected rolls: {}".format(
            doc["maximum_rolls"], doc["expected_rolls"]))
    else:
        print(json.dumps(doc, indent=2))


if __name__ == "__main__":
    main(sys.argv)
