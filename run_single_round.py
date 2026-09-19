"""Single-config round flow (Plan 4+): archive prior paroli log, capture token,
run ONE strategy config live, archive output, append session ledger.

Usage:  py -3.13 run_single_round.py <round_number> <config.json> [--note "..."] [--token <browser-security-token>]
(CLI-minted tokens are rejected with 400 incorrect_2fa; paste one from DevTools Network POST dice/bet.)
"""
import asyncio, json, os, shutil, subprocess, sys, time
from decimal import Decimal as D
from pathlib import Path

ROUND = int(sys.argv[1])
CFG = sys.argv[2]
STEM = Path(CFG).stem
OUT = Path(f"live_{STEM}.jsonl")
NOTE = sys.argv[sys.argv.index("--note") + 1] if "--note" in sys.argv else None
TOK_OVERRIDE = sys.argv[sys.argv.index("--token") + 1] if "--token" in sys.argv else None


def archive_prev(n):
    """Archive this runner's own previous output.

    One config maps to one live file, so this only ever moves live_{STEM}.jsonl.
    Sweeping every live_*.jsonl here would merge several rounds' output into a
    single archive directory, which breaks family inference in next_round.py.
    """
    if not OUT.exists():
        return
    arch = Path(f"round{n}_archive")
    arch.mkdir(exist_ok=True)
    shutil.copy2(OUT, arch / OUT.name)
    OUT.unlink()


def mint_token_rest():
    """Mint a security token via the SPA's own REST endpoint (no browser).

    POST /api/v2/user/security/token with the no-2FA sentinel code "0000" -
    exactly what the frontend sends as its first attempt. Returns the token
    string, or None on any failure (caller falls back to CDP capture).
    """
    from automation_client import DuelClient, DEFAULT_PROFILE
    try:
        with DuelClient.from_profile(DEFAULT_PROFILE) as c:
            r = c.security_token(token_type="standard", code="0000", confirm=True)
        tok = str(r.get("token") or (r.get("data") or {}).get("token") or "")
        if len(tok) >= 20:
            return tok
        print("REST mint returned no usable token:", str(r)[:200])
    except Exception as e:
        print(f"REST token mint failed: {type(e).__name__}: {str(e)[:160]}")
    return None


async def capture_token():
    from playwright.async_api import async_playwright
    got = {"tok": None}

    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        pg = next((x for x in b.contexts[0].pages if "duel.com/dice" in x.url), None)
        if pg is None:
            print("dice page NOT found: pages =",
                  [x.url for x in b.contexts[0].pages])
            return None

        def on_req(req):
            if got["tok"] or req.method != "POST" or "/api/v2/dice/bet" not in req.url:
                return
            try:
                body = json.loads(req.post_data or "{}")
            except Exception:
                return
            tok = body.get("security_token", "")
            if len(tok) >= 20:
                got["tok"] = tok

        pg.on("request", on_req)
        el = await pg.query_selector("text=Roll Dice")
        if el is None:
            print("Roll Dice button missing")
            return None
        await el.click()
        print(f"[{time.strftime('%H:%M:%S')}] clicked Roll Dice, waiting for token...", flush=True)
        for _ in range(30):
            if got["tok"]:
                return got["tok"]
            await asyncio.sleep(2)
        return None


def preflight():
    from backtest.autobet_strategies import load_strategy
    spec = json.loads(Path(CFG).read_text())
    s = load_strategy(spec)
    first = s.next_stake(D(spec.get("base_stake", "0.00000050")), False)
    if first is None:
        sys.exit(f"DEAD SLOT: {CFG} produced no first stake")
    return spec


def run_live(spec, tok, max_rounds):
    # Currency follows the config: 101 -> BTC, 109 -> SOL (balance-type ids).
    code = {101: "BTC", 109: "SOL"}.get(int(spec.get("currency_id", 101)), "BTC")
    cmd = [
        "py", "-3.13", "automation_cli.py", "--yes", "autobet",
        "--config", CFG, "--currency", code,
        "--side", str(spec.get("side", "UNDER")),
        "--target", str(spec.get("target", 5000)),
        "--max-loss", str(spec.get("max_loss", "0.00000300")),
        "--max-profit", str(spec.get("max_profit", "0.00000350")),
        "--max-rounds", str(max_rounds), "--confirm",
        "--security-token", tok,
        "--out", str(OUT),
    ]
    rr = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return rr
def parse_jsonl():
    """Schema: {"round","stake","won","net","cumulative_loss","cumulative_profit","timestamp"}."""
    nets, wins, losses = [], 0, 0
    if not OUT.exists():
        return D(0), 0, wins, losses
    for line in OUT.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if "net" not in r:
            continue
        net = D(str(r["net"]))
        nets.append(net)
        # The log's `won` field is unreliable (known bug: it reads false on
        # settled wins). A settled win always nets positive, a loss always
        # nets exactly minus the stake, so derive the outcome from the net.
        if net > 0:
            wins += 1
        else:
            losses += 1
    return (sum(nets, D(0)) if nets else D(0)), len(nets), wins, losses


def append_ledger(n, net, rounds, note, path=Path("session_ledger.json")):
    if int(rounds or 0) <= 0:
        raise ValueError(
            f"refusing to record round {n}: 0 rounds settled. An unfunded or "
            "no-op attempt is not a round (next_round.py is_settled ignores it).")
    d = json.loads(path.read_text())
    row = {"n": n, "net": str(net), "rounds": rounds, "note": note}
    existing = next((i for i, r in enumerate(d["rounds"]) if r.get("n") == n), None)
    if existing is not None:
        d["rounds"][existing] = row
    else:
        d["rounds"].append(row)
    d["session_total"] = str(sum(D(str(r["net"])) for r in d["rounds"]))
    d["session_total_source"] = "sum_of_round_nets"
    path.write_text(json.dumps(d, indent=1))
    return d["session_total"]


def main():
    spec = preflight()
    max_rounds = int(spec.get("max_rounds", 15))
    print(f"[{time.strftime('%H:%M:%S')}] archiving round {ROUND-1} {STEM}...", flush=True)
    archive_prev(ROUND - 1)
    # automation_cli.py opens the autobet --out path in APPEND mode, so the
    # previous run's rows would be re-counted into this round's net. Truncate
    # after archiving (round_flow.py does the same as its "clear" step).
    if OUT.exists():
        OUT.unlink()
    tok = TOK_OVERRIDE
    if tok:
        print(f"[{time.strftime('%H:%M:%S')}] using pasted browser token (len {len(tok)})", flush=True)
        Path("fresh_token.txt").write_text(tok)
    else:
        tok = mint_token_rest()
        if tok:
            print(f"[{time.strftime('%H:%M:%S')}] REST-minted token (len {len(tok)})", flush=True)
            Path("fresh_token.txt").write_text(tok)
        else:
            print(f"[{time.strftime('%H:%M:%S')}] falling back to CDP token capture...", flush=True)
            tok = asyncio.run(capture_token())
            if not tok:
                sys.exit("TOKEN CAPTURE FAILED (REST + CDP)")
            print(f"[{time.strftime('%H:%M:%S')}] CDP token captured (len {len(tok)})", flush=True)
            Path("fresh_token.txt").write_text(tok)

    rr = run_live(spec, tok, max_rounds)
    net, rounds, wins, losses = parse_jsonl()
    print(f"\n{STEM}: exit={rr.returncode} rounds={rounds} W/L={wins}/{losses} net={net}")
    if rounds == 0:
        print("STDERR:", (rr.stderr or "")[-800:])
        print(f"NO ROUND SETTLED (exit={rr.returncode}, rounds=0): the runner "
              "refused or aborted before any wager. Ledger left unchanged.")
        sys.exit(rr.returncode or 1)
    if rr.returncode != 0:
        print("STDERR:", (rr.stderr or "")[-800:])

    arch = Path(f"round{ROUND}_archive")
    arch.mkdir(exist_ok=True)
    if OUT.exists():
        shutil.copy2(OUT, arch / OUT.name)
    total = append_ledger(ROUND, net, rounds, NOTE or f"{STEM} single")
    print(f"\nROUND {ROUND}: {rounds} rounds | net {net} | session total {total}")


if __name__ == "__main__":
    main()