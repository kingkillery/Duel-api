# Dry-run results for 11 autobet strategy configs

Working directory: `C:/dev/Desktop-Projects/Duel-api`  
Interpreter: `py -3.13`  
Live bets: none (`--dry-run` only)

## Results

| config | mode | rounds simulated | exit code | any errors |
|---|---|---:|---:|---|
| s01_flat.json | flat | 10 | 0 | |
| s02_martingale.json | martingale | 6 | 0 | |
| s03_fibonacci.json | fibonacci | 8 | 0 | |
| s04_dalembert.json | dalembert | 10 | 0 | |
| s05_custom_a.json | custom_steps | 12 | 0 | |
| s06_custom_b.json | custom_steps | 10 | 0 | |
| s07_custom_c.json | custom_steps | 12 | 0 | |
| s08_under_alt.json | custom_steps | 12 | 0 | |
| s09_over_alt.json | custom_steps | 10 | 0 | |
| s10_small_steps.json | custom_steps | 10 | 0 | |
| s11_wide_steps.json | custom_steps | 6 | 0 | |

Rounds stopped before `--max-rounds 20` because the alternating win/loss simulator hit `--max-loss 0.00000500`.

## CLI flags each config needs beyond `--config` / `--dry-run`

Argparse requires `--currency`, `--side`, and `--target` on every run. These dry-runs also passed stop limits and `--out`.

Shared: `--currency BTC --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20`

| config | extra flags |
|---|---|
| s01_flat.json | `--currency BTC --side UNDER --target 5000 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s01_flat.jsonl` |
| s02_martingale.json | `--currency BTC --side UNDER --target 5000 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s02_martingale.jsonl` |
| s03_fibonacci.json | `--currency BTC --side UNDER --target 5000 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s03_fibonacci.jsonl` |
| s04_dalembert.json | `--currency BTC --side UNDER --target 5000 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s04_dalembert.jsonl` |
| s05_custom_a.json | `--currency BTC --side UNDER --target 5000 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s05_custom_a.jsonl` |
| s06_custom_b.json | `--currency BTC --side UNDER --target 5100 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s06_custom_b.jsonl` |
| s07_custom_c.json | `--currency BTC --side UNDER --target 4900 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s07_custom_c.jsonl` |
| s08_under_alt.json | `--currency BTC --side UNDER --target 4700 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s08_under_alt.jsonl` |
| s09_over_alt.json | `--currency BTC --side OVER --target 5300 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s09_over_alt.jsonl` |
| s10_small_steps.json | `--currency BTC --side UNDER --target 5000 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s10_small_steps.jsonl` |
| s11_wide_steps.json | `--currency BTC --side UNDER --target 5000 --max-loss 0.00000500 --max-profit 0.00001000 --max-rounds 20 --out dryrun_s11_wide_steps.jsonl` |

## Fixes made (paths)

- `backtest/autobet_strategies.py`: `load_strategy` accepts `mode` as well as `type`; `dAlembert` matches via case-fold; default dAlembert `unit` is `base_stake` instead of `0.01`.
- `automation_cli.py`: `--dry-run` no longer fetches a live session/balance; writes per-round JSONL to `--out`.
- `s01_flat.json`: rewritten to real `flat` mode.
- Created: `s02_martingale.json`, `s03_fibonacci.json`, `s04_dalembert.json`, `s05_custom_a.json`, `s06_custom_b.json`, `s07_custom_c.json`, `s08_under_alt.json`, `s09_over_alt.json`, `s10_small_steps.json`, `s11_wide_steps.json`.
- `tests/test_autobet.py`: `mode` key + dAlembert default unit.

Nothing was committed. No live bets were placed.
