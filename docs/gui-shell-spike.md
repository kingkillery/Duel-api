# GUI shell spike — decision report (B0)

Date: 2026-09-20 · Host: Windows 11 26200, Python 3.13.14 · Scope: spike only — no
product source changed, no compute_verdict work, no commit/push, no network beyond
package downloads, no runners/live wagers.

Spike artifacts live outside the repo at `C:/dev/.spike-envs/gui-spike/` with the
isolated venv `C:/dev/.spike-envs/gui-spike-venv/` (PySide6 6.11.2, PyInstaller
6.22.3, fastapi 0.141.1, uvicorn 0.53.0, httpx 0.28.1, pywebview 6.2.1). Repo
`git status` was clean before the spike; after it, the only delta is this
untracked report file. `pyproject.toml` untouched.

## Criteria (from docs/gui-wireframes.md)

1. Tabs 1–2 work with no account, no network, no session file.
2. Tab 3 reproduces the CLI verdict for the same balance/floor inputs.
3. Tab 4 never renders a fetched token/cookie/balance.
4. Windows standalone packaging path proven (PyInstaller) before Tauri talk.
5. pytest covers verdict parity + redaction.
Sequence note: "PySide6 MVP (Python-native, calls existing modules directly) …
No Streamlit/Gradio default (localhost server surface)."

## CodeGraph impact analysis (index: 68 files, 1541 nodes, 3731 edges, up to date)

Commands run from repo root (`codegraph` CLI):

- `codegraph status` → index up to date.
- `codegraph impact "run" -d 2` → `demo.run` affects `sandbox/app.py:simulate`,
  `POST /api/simulate` route, `tests/test_demo.py` (3 tests), `automation_cli.py:
  cmd_demo` + `build_parser`. A GUI calling `demo.run` in-process adds a caller,
  not a change to existing callers — though a call graph does not capture shared
  state, so this is not a zero-blast-radius guarantee.
- `codegraph impact "collect" -d 2` → `doctor.collect` affects
  `tests/test_doctor.py` (2 tests) + `automation_cli.py:cmd_doctor`.
- `codegraph impact "family_fitness" -d 2` → `next_round.py:main` +
  `tests/test_protocol_funding.py` (4 tests). Tab 3 must call these functions
  directly, not re-implement them.
- `codegraph impact "simulate" -d 2` → leaf: only its own route. Extending the
  sandbox touches nothing else.
- `codegraph affected demo.py doctor.py next_round.py sandbox/app.py
  automation_cli.py -f "tests/*.py"` → 7 test files: test_backtest, test_cli,
  test_demo, test_doctor, test_protocol_funding, test_sandbox,
  test_session_lifecycle.
- **Tool caveat:** `codegraph affected` with the *default* glob reported "No test
  files affected" — a false negative; `-f "tests/*.py"` is required for this
  repo's layout. Reported upstream.

## Experiments (all commands executed; outcomes measured)

### E1 — PySide6 native, offscreen window + worker

- Wheel check: `pip index versions PySide6` → 6.11.2; install pulled
  `pyside6-6.11.2-cp310-abi3-win_amd64.whl` (+Essentials 76.9 MB, Addons 168.2 MB,
  shiboken6). **cp310-abi3 wheel — compatible with Python 3.13, not a cp313 tag.**
- `qt_spike.py` (offscreen `QApplication`, `QWidget`+labels+button,
  `QThreadPool`+`QRunnable` worker calling `demo.run(sessions=10_000, seed=7)`,
  50 ms `QTimer` responsiveness probe, second worker with `base=-1.0` for the
  error path, `doctor.collect()` in-process):
  - v1 with a manual `processEvents()` pump starved the probe (1 tick) —
    measurement artifact, fixed by using `app.exec()`.
  - v2 result: `pyside_import_s=0.209`, `app_window_s=0.055`,
    `exec_wall_s=4.383`, **66 timer ticks, mean 72.8 ms, max 491 ms** during the
    ~3.4 s CPU-bound worker → GUI thread stays responsive (worst stall 0.5 s).
    Verdict delivered via signal; `ScheduleError: base_stake must be positive,
    got -1.0` delivered via `failed` signal; `doctor.collect()` → spec found.
- Baseline cost: `demo.run` = 3.42 s @10k sessions, 20.85 s @50k → a worker
  thread is mandatory, not optional. `QThreadPool` proved viable at the tested
  load (491 ms worst stall); heavier loads (50k sessions ≈ 21 s) may still
  warrant a subprocess — viability is provisional, not proven for all inputs.

### E2 — Extend the existing FastAPI sandbox

- `sandbox_spike.py` (uvicorn `Server` on an ephemeral 127.0.0.1 port, httpx
  client): `startup_s=0.042`, `POST /api/simulate` 500 sessions → 200 in
  `0.386 s` with the correct verdict; `edge=9.9` → **422** with a clean pydantic
  `detail` body; `should_exit` → clean shutdown.
- Works, but it *is* the localhost-server surface the wireframes explicitly
  reject as the default. Also adds a second lifecycle (server thread + browser)
  and a port to manage/secure. Viable fallback, not the default.

### E3 — Thin browser wrapper (pywebview / WebView2)

- `webview_spike.py`: WebView2 runtime present (`pv` 153.0.4234.48 via registry).
  v1 destroyed the window 1.5 s in and raced async init → `E_ABORT` (spike
  artifact). v2 waits on `events.loaded`: **loaded in 1.112 s, clean destroy at
  1.889 s**, no server involved (`html=` inline). A `js_api` bridge calling
  `demo.run` directly (no localhost at all) was proposed but **not exercised**.
- Feasible on this host, but it reintroduces a WebView2 runtime dependency and
  an HTML/JS UI layer for a 4-tab local console — heavier than Qt widgets for
  this shape, and packaging a WebView2 app still needs PyInstaller anyway.

### E4 — PyInstaller, console payload (onefile)

- Build: `C:/dev/.spike-envs/gui-spike-venv/Scripts/python.exe -m PyInstaller
  --noconfirm --clean --onefile --console --name frozen_spike
  --paths C:/dev/Desktop-Projects/Duel-api
  --add-data "C:/dev/Desktop-Projects/Duel-api/site_spec.json;."
  --distpath C:/dev/.spike-envs/gui-spike/dist
  --workpath C:/dev/.spike-envs/gui-spike/build
  --specpath C:/dev/.spike-envs/gui-spike
  C:/dev/.spike-envs/gui-spike/frozen_spike.py` → built in ~16 s, **11 MB exe**.
- Run from a clean CWD (`docs/`): `C:/dev/.spike-envs/gui-spike/dist/frozen_spike.exe`
  → `frozen=true`, `_MEIPASS` set,
  `spec_path=<meipass>\site_spec.json`, `spec_found=true` — the bundled data file
  resolves through `paths.default_spec_path()`'s `package_dir()` branch with no
  code change. `demo.run(200)` → verdict in 0.07 s; `doctor.collect()` →
  `next_step: "no session yet"` (correct for a clean dir; no profile shipped).

### E5 — PyInstaller, Qt window (onedir)

- Build: same flags as E4 but `--onedir --name qt_frozen` on
  `C:/dev/.spike-envs/gui-spike/qt_frozen.py` → built in ~72 s, **111 MB dir**.
- Run from clean CWD: `C:/dev/.spike-envs/gui-spike/dist/qt_frozen/qt_frozen.exe`
  → `frozen=true`, `_internal\site_spec.json` found,
  offscreen `QApplication` + `QThreadPool` worker → demo verdict returned and
  app quit cleanly (~1.7 s wall). PySide6 hooks (contrib 2026.7) handled
  QtCore/QtGui/QtWidgets/shiboken6 automatically.

## Comparison vs wireframe criteria

| Criterion | PySide6 native | FastAPI sandbox | pywebview shell |
|---|---|---|---|
| 1. Offline tabs 1–2 | engine+doctor calls proven in-process; full tabs not built | engine call proven via HTTP; needs server+browser | window+load proven; js_api bridge proposed, not exercised |
| 2. Tab 3 verdict parity | **untested** — `next_round` fns impact-mapped only | **untested** | **untested** |
| 3. No fetched secrets | held in spike (no fetch path); not proven for a full app | held in spike; adds an attack surface | held in spike |
| 4. PyInstaller standalone | **proven twice** (E4 11 MB onefile, E5 111 MB onedir) | unproven (uvicorn freeze untested) | unproven |
| 5. pytest parity/redaction | testable in-process | testable via TestClient | harder (JS bridge) |
| Wireframe sequence fit | **matches "PySide6 MVP"** | rejected as default by wireframes | closer to Tauri-shaped; premature |

## Recommendation

**Proceed with PySide6 native** — the wireframes' prescribed path is supported by
measurement, not just preference: a cp310-abi3 wheel serves Python 3.13,
in-process calls to `demo`/`doctor`/`next_round` work unchanged (CodeGraph shows
a GUI adds callers, not changes), the GUI thread stayed responsive with the
engine on `QThreadPool` at the tested load (provisional — see E1), typed engine
errors arrive as signals, and the PyInstaller standalone path is proven
end-to-end including the `site_spec.json` data-file resolution. Keep the
FastAPI sandbox as the hosted/demo surface it already is; do not make it the
desktop default. pywebview is a viable plan-B if Qt styling becomes the blocker.

## Blocked / unproven items

- **Not tested:** a *visible* (non-offscreen) Qt window — all Qt runs used
  `QT_QPA_PLATFORM=offscreen`; real-window rendering on this display is unproven.
- **Not tested:** frozen uvicorn/FastAPI app; frozen pywebview app; `--windowed`
  (no-console) Qt build; onefile Qt build (slower startup, untested).
- **Not tested:** Tab 3 verdict parity itself — `next_round.py` functions were
  impact-mapped but not exercised in the spike (compute_verdict extraction is a
  separate assigned task).
- pywebview `hidden=True` still ran the full WebView2 init; a truly headless
  smoke isn't possible — the window existed briefly.
- `codegraph affected` default glob false-negative (see caveat above); graph
  absence was never used as sole evidence — all claims rest on executed commands.

## Changed files & environments

- Repo: **only this file** (`docs/gui-shell-spike.md`, untracked). No source,
  dependency, or config changes; no commits.
- Outside repo: `C:/dev/.spike-envs/gui-spike/` (4 spike scripts, 2 .spec files,
  `build/`, `dist/` with `frozen_spike.exe` + `qt_frozen/`) and venv
  `C:/dev/.spike-envs/gui-spike-venv/` (~708 MB). Delete both to clean up.
