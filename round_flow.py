"""One-call round flow: archive -> clear -> tap+roll -> batch -> ledger.

Usage:  py -3.13 round_flow.py <round_number> [--max-rounds 25]

Collapses the previous 3-call sequence into one process: attaches CDP once,
starts the passive bet-body listener, clicks Roll Dice itself, waits for the
browser-minted token, runs the 11-strategy batch, archives, and appends the
result to session_ledger.json.
"""
import asyncio, json, os, shutil, subprocess, sys, time
from decimal import Decimal as D
from pathlib import Path

ROUND = int(sys.argv[1]) if len(sys.argv) > 1 else 0
MAXR = sys.argv[sys.argv.index("--max-rounds") + 1] if "--max-rounds" in sys.argv else "25"
NOTE = sys.argv[sys.argv.index("--note") + 1] if "--note" in sys.argv else None


def archive_prev(n):
    lives = list(Path(".").glob("live_s*.jsonl")) + list(Path(".").glob("live_s*[!.jsonl]"))
    # Keep the glob tight: jsonl logs plus any extensionless retry leftovers.
    lives = list(Path(".").glob("live_s*.jsonl"))
    lives += [p for p in Path(".").glob("live_s*") if p.is_file() and p.suffix == ""]
    if not lives:
        return 0
    arch = Path(f"round{n}_archive")
    arch.mkdir(exist_ok=True)
    k = 0
    for p in lives:
        shutil.copy2(p, arch / (p.name if p.suffix else p.name + ".jsonl")); k += 1
    for name in ("run_11_summary.json", f"run_11_round{n}_summary.json"):
        p = Path(name)
        if p.exists():
            shutil.copy2(p, arch / name); k += 1
    return k


def mint_token_rest():
    """Mint security token via the SPA's REST endpoint without browser dependency."""
    from automation_client import DuelClient, DEFAULT_PROFILE
    try:
        with DuelClient.from_profile(DEFAULT_PROFILE) as c:
            r = c.security_token(token_type="standard", code="0000", confirm=True)
        tok = str(r.get("token") or (r.get("data") or {}).get("token") or "")
        if len(tok) >= 20:
            Path("fresh_token.txt").write_text(tok)
            return tok
    except Exception as e:
        pass
    return None


def get_token():
    """Try REST mint first; fall back to CDP browser capture if needed."""
    tok = mint_token_rest()
    if tok:
        return tok
    return asyncio.run(capture_token())


async def capture_token():
    """Attach CDP, listen for the bet body, click Roll Dice, return token."""
    from playwright.async_api import async_playwright
    got = {"tok": None}

    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        pg = next((x for x in b.contexts[0].pages if "duel.com/dice" in x.url), None)
        if pg is None:
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
                Path("fresh_token.txt").write_text(tok)
                Path("captured_bet.json").write_text(json.dumps(
                    {"source": "browser_dice_bet", "payload": body}))

        pg.on("request", on_req)
        el = await pg.query_selector("text=Roll Dice")
        if el is None:
            return None
        await el.click()
        for _ in range(30):  # 60s for the bet to fire + settle
            if got["tok"]:
                return got["tok"]
            await asyncio.sleep(2)
        return None


def preflight():
    """Every config must produce a first stake; a dead slot wastes the batch."""
    bad = []
    for cfg in sorted(Path(".").glob("s*.json")):
        if not cfg.stem.split("_")[0].startswith("s"):
            continue
        try:
            import json as _j
            from backtest.autobet_strategies import load_strategy
            from decimal import Decimal as _D
            s = load_strategy(_j.loads(cfg.read_text()))
            first = s.next_stake(_D("0.00000100"), False)
            if first is None:
                bad.append(cfg.name)
        except Exception as e:
            bad.append(f"{cfg.name} ({e})")
    if bad:
        sys.exit(f"DEAD SLOT(S): {bad}")


def append_ledger(n, net, rounds, note=None):
    d = json.loads(Path("session_ledger.json").read_text())
    row = {"n": n, "net": str(net), "rounds": rounds}
    if note:
        row["note"] = note
    existing = next((i for i, r in enumerate(d["rounds"]) if r.get("n") == n), None)
    if existing is not None:
        d["rounds"][existing] = row
    else:
        d["rounds"].append(row)
    # Always recompute from rows. The running-increment field was minted
    # 0.00001831 high at R15 init and would hide that phantom forever.
    d["session_total"] = str(sum(D(str(r["net"])) for r in d["rounds"]))
    d["session_total_source"] = "sum_of_round_nets"
    Path("session_ledger.json").write_text(json.dumps(d, indent=1))
    return d["session_total"]


def _row_from_jsonl(config, path, stdout="", stderr=""):
    rounds = []
    if path.exists() and path.stat().st_size:
        rounds = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    nets = [float(r.get("net", 0)) for r in rounds]
    return {
        "config": config,
        "exit": 0,
        "stdout": stdout or json.dumps({
            "mode": "live",
            "net": str(sum(nets)),
            "output": str(path),
            "rounds_played": len(rounds),
        }),
        "stderr": stderr,
        "rounds": len(rounds),
        "wins": sum(1 for n in nets if n > 0),
        "losses": sum(1 for n in nets if n < 0),
        "sum_net": sum(nets),
    }


def _sum_rows(rows):
    net, total_rounds = D(0), 0
    for row in rows:
        total_rounds += int(row.get("rounds") or 0)
        if "sum_net" in row and row["sum_net"] is not None:
            net += D(str(row["sum_net"]))
            continue
        try:
            o = json.loads(row.get("stdout", "{}"))
            net += D(str(o.get("net", 0)))
            total_rounds = total_rounds  # rounds already counted above if present
            if not row.get("rounds"):
                total_rounds += int(o.get("rounds_played", 0) or 0)
        except Exception:
            pass
    return net, total_rounds


def _retry_one(cfg, tok):
    spec = json.loads(Path(cfg).read_text(encoding="utf-8"))
    out = Path(f"live_{Path(cfg).stem}.jsonl")
    cmd = [
        "py", "-3.13", "automation_cli.py", "--yes", "autobet",
        "--config", cfg, "--currency", "BTC",
        "--side", str(spec.get("side", "UNDER")),
        "--target", str(spec.get("target", 5000)),
        "--max-loss", str(spec.get("max_loss", "0.00000500")),
        "--max-profit", str(spec.get("max_profit", "0.00001000")),
        "--max-rounds", MAXR, "--confirm",
        "--security-token", tok,
        "--out", str(out),
    ]
    rr = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    row = _row_from_jsonl(cfg, out, stdout=(rr.stdout or "").strip()[-500:],
                          stderr=(rr.stderr or "").strip()[-500:])
    row["exit"] = rr.returncode
    return row, (rr.stderr or "") + (rr.stdout or "")


def main():
    if ROUND < 1:
        sys.exit("usage: round_flow.py <round_number>")
    preflight()
    print(f"[{time.strftime('%H:%M:%S')}] archiving round {ROUND-1}...", flush=True)
    archive_prev(ROUND - 1)
    Path("fresh_token.txt").write_text("")
    if Path("captured_bet.json").exists():
        os.remove("captured_bet.json")

    tok = get_token()
    if not tok:
        sys.exit("ERROR: no token captured (REST mint and CDP fallback failed)")
    print(f"[{time.strftime('%H:%M:%S')}] token acquired (len {len(tok)})", flush=True)

    r = subprocess.run(
        ["py", "-3.13", "run_11_strategies.py", "--token-file", "fresh_token.txt",
         "--max-rounds", MAXR], capture_output=True, text=True, timeout=600)
    print("\n".join(r.stdout.strip().splitlines()[-13:]))
    if r.returncode != 0:
        print("RUNNER STDERR:", r.stderr[-500:])

    # Token-consumption retry: any config that died on a retired token gets
    # ONE fresh capture + re-run. Write .jsonl (not a suffix-less leftover)
    # and merge the retry row back into the summary so the ledger is complete.
    rows = json.loads(Path("run_11_summary.json").read_text())
    failed = [row["config"] for row in rows
              if "Security token required" in (row.get("stderr") or "")]
    note = None
    if failed:
        print(f"TOKEN RETIRED mid-batch; retrying {len(failed)} config(s) with fresh token")
        tok2 = get_token()
        if not tok2:
            print("  recapture failed - round recorded as PARTIAL")
            note = "PARTIAL: token recapture failed for " + ",".join(failed)
        else:
            by_cfg = {row["config"]: row for row in rows}
            for cfg in failed:
                row, err = _retry_one(cfg, tok2)
                if "Security token required" in err or (row.get("rounds") or 0) == 0:
                    print(f"  retry {cfg} got a dead token; recapturing once more")
                    tok2 = get_token() or tok2
                    row, err = _retry_one(cfg, tok2)
                by_cfg[cfg] = row
                print(f"  retry {cfg}: {row.get('rounds', 0)} rounds net {row.get('sum_net')}")
            rows = [by_cfg[row["config"]] for row in rows]
            Path("run_11_summary.json").write_text(json.dumps(rows, indent=2))
            note = "token retired mid-batch; retried " + ",".join(failed)

    if NOTE:
        note = (note + " | " if note else "") + NOTE
    net, total_rounds = _sum_rows(rows)
    shutil.copy2("run_11_summary.json", f"run_11_round{ROUND}_summary.json")
    total = append_ledger(ROUND, net, total_rounds, note=note)
    print(f"\nROUND {ROUND}: {total_rounds} rounds | net {net} | session total {total}")


if __name__ == "__main__":
    main()
