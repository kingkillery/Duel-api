"""Run 11 similar custom_steps strategies sequentially with one browser token."""
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

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=None)
    ap.add_argument("--token-file", default="fresh_token.txt")
    ap.add_argument("--max-rounds", type=int, default=25)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    token = None if args.dry_run else load_token(args)
    summary = []
    for name in CONFIGS:
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
        if proc.returncode != 0 and not args.dry_run:
            err = (proc.stderr or "") + (proc.stdout or "")
            if "security" in err.lower() or "401" in err or "token" in err.lower():
                print("stopping: token/auth failure", flush=True); break
    out_summary = ROOT / "run_11_summary.json"
    out_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\nSUMMARY written to", out_summary)
    for row in summary:
        print(f"{row['config']}: exit={row['exit']} rounds={row.get('rounds')} W/L={row.get('wins')}/{row.get('losses')} net={row.get('sum_net')}")
    return 0 if all(r["exit"] == 0 for r in summary) else 1
if __name__ == "__main__":
    raise SystemExit(main())
