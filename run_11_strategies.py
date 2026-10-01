"""Run 11 similar custom_steps strategies sequentially with one browser token.

With --jev, one jev System One call (speculative fan-out) scores every config
plus a batch-go question before the first bet: configs below RUN_CONFIG_NOUL
are dropped, the rest run in descending-score order, and --jev-top caps the
batch. When the running net goes negative mid-batch, a second jev call asks
whether to continue. jev only ever narrows the batch - it cannot add configs,
change stakes, or bypass autobet's own caps. Without --jev the behavior is
unchanged: all 11 configs in file order.
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
CONFIGS = ["s01_flat.json","s02_sprint.json","s03_heavy2.json","s04_spike.json","s05_decay.json","s06_ramp.json","s07_antiramp.json","s08_burst.json","s09_original.json","s10_slowspike.json","s11_bookend.json"]

def load_token(args):
    if args.token: return args.token.strip()
    path = Path(args.token_file)
    if not path.exists(): raise SystemExit(f"token file missing: {path}")
    tok = path.read_text(encoding="utf-8").strip()
    if not tok: raise SystemExit(f"token file empty: {path}")
    return tok

def batch_context():
    """Session context for jev batch questions; every field degrades to None."""
    import next_round as nr
    rows = nr.load_rows(ROOT / "session_ledger.json")
    last_n, last_net = nr.last_round(rows)
    recent = {}
    try:
        summary = json.loads((ROOT / "run_11_summary.json").read_text(encoding="utf-8"))
        for row in summary:
            if isinstance(row, dict) and row.get("sum_net") is not None:
                recent[row["config"]] = row["sum_net"]
    except Exception:
        pass
    return {
        "context": {
            "last_round": last_n,
            "last_net_ubtc": (None if last_net is None
                              else float(last_net * nr.UBTC)),
            "loss_streak": nr.loss_streak(rows),
            "drawdown_ubtc": float(nr.session_drawdown(rows) * nr.UBTC),
            "balance_ubtc": None,
            "floor_ubtc": float(nr.FLOOR_UBTC),
        },
        "recent_config_net_btc": recent,
    }

def jev_select(names, args):
    """One fan-out call: batch-go + per-config run questions.

    Returns (ordered_names, dropped, batch_noul). Raises jev.JevError only
    on transport/parse failure - a low batch_noul is a decline, not an
    error, and the caller must treat it as "do not run".
    """
    import jev
    import jev_questions as jq
    state = batch_context()
    state["configs"] = {}
    for name in names:
        try:
            cfg = json.loads((ROOT / name).read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        state["configs"][name] = {
            "description": cfg.get("description", name),
            "base_stake": cfg.get("base_stake"),
            "max_loss": cfg.get("max_loss"),
            "max_profit": cfg.get("max_profit"),
            "target": cfg.get("target"),
        }
    questions = {"run_batch": jq.run_batch_question()}
    for name in names:
        questions[f"run_{Path(name).stem}"] = jq.run_config_question(name)
    answers = jev.system_one(state, questions)
    batch_p = jev.noul_of(answers, "run_batch")
    ordered, dropped = jev.rank_configs(answers, names, "run_",
                                        jq.RUN_CONFIG_NOUL, top=args.jev_top)
    return ordered, dropped, batch_p

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None)
    ap.add_argument("--token-file", default="fresh_token.txt")
    ap.add_argument("--max-rounds", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--jev", action="store_true",
                    help="let jev select and order configs before the batch")
    ap.add_argument("--jev-top", type=int, default=None, metavar="N",
                    help="cap the jev-selected batch at N configs")
    ap.add_argument("--jev-strict", action="store_true",
                    help="abort when jev is unavailable instead of running all")
    args = ap.parse_args()
    token = None if args.dry_run else load_token(args)

    names = list(CONFIGS)
    jev = jq = None
    if args.jev:
        try:
            import jev as _jev
            import jev_questions as _jq
        except ImportError as e:
            raise SystemExit(f"--jev requested but jev modules missing: {e}")
        jev, jq = _jev, _jq
        try:
            names, dropped, batch_p = jev_select(names, args)
        except jev.JevError as e:
            if args.jev_strict:
                raise SystemExit(f"aborted: {e}")
            print(f"jev unavailable ({e}); running all configs", flush=True)
        else:
            if batch_p < jq.RUN_BATCH_NOUL:
                print(f"jev declined the batch (run_batch={batch_p:.2f}); "
                      "nothing to run")
                return 0
            for name, p in dropped:
                print(f"jev dropped {name}"
                      + ("" if p is None else f" (noul={p:.2f})"), flush=True)
            if not names:
                print("jev selected no configs; nothing to run")
                return 0
            print("jev order: " + ", ".join(names), flush=True)

    summary = []
    running_net = 0.0
    for name in names:
        cfg_path = ROOT / name
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        out = ROOT / f"live_{Path(name).stem}.jsonl"
        if out.exists(): out.unlink()
        cmd = ["py","-3.13",str(ROOT/"automation_cli.py"),"--yes","autobet","--config",str(cfg_path),"--currency","BTC","--side",str(cfg.get("side","UNDER")),"--target",str(cfg.get("target",5000)),"--max-loss",str(cfg.get("max_loss","0.00000500")),"--max-profit",str(cfg.get("max_profit","0.00001000")),"--max-rounds",str(args.max_rounds),"--out",str(out)]
        if args.dry_run: cmd.append("--dry-run")
        else: cmd.extend(["--confirm","--security-token",token])
        print(f"\n=== {name} ===", flush=True)
        proc = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True)
        print(proc.stdout)
        if proc.stderr: print(proc.stderr, file=sys.stderr)
        row = {"config": name, "exit": proc.returncode, "stdout": (proc.stdout or "").strip()[-500:], "stderr": (proc.stderr or "").strip()[-500:]}
        if out.exists() and out.stat().st_size:
            rounds = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
            nets = [float(r.get("net", 0)) for r in rounds]
            row.update({"rounds": len(rounds), "wins": sum(1 for r in rounds if float(r.get("net", 0)) > 0), "losses": sum(1 for r in rounds if float(r.get("net", 0)) < 0), "sum_net": sum(nets)})
        summary.append(row)
        running_net += float(row.get("sum_net") or 0.0)
        if proc.returncode != 0 and not args.dry_run:
            err = (proc.stderr or "") + (proc.stdout or "")
            if "security" in err.lower() or "401" in err or "token" in err.lower():
                print("stopping: token/auth failure", flush=True); break
        # Mid-batch jev check: only when the batch is losing and slots remain.
        if (jev is not None and running_net < 0
                and len(summary) < len(names)):
            try:
                state = batch_context()
                state["running_net_ubtc"] = running_net * 1e6
                state["remaining"] = names[len(summary):]
                p = jev.noul_of(jev.system_one(state,
                                             {"continue": jq.continue_question()}),
                                "continue")
                if p < jq.CONTINUE_NOUL:
                    print(f"jev stopped the batch early (continue={p:.2f})",
                          flush=True)
                    break
            except jev.JevError as e:
                print(f"jev continue-check failed ({e}); continuing", flush=True)
    out_summary = ROOT / "run_11_summary.json"
    out_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nSUMMARY written to", out_summary)
    for row in summary:
        print(f"{row['config']}: exit={row['exit']} rounds={row.get('rounds')} W/L={row.get('wins')}/{row.get('losses')} net={row.get('sum_net')}")
    return 0 if all(r["exit"] == 0 for r in summary) else 1
if __name__ == "__main__":
    raise SystemExit(main())
