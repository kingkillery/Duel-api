"""Tests for next_round.compute_verdict - the pure structured verdict API.

compute_verdict() is the verdict table main() prints, returned as data so a
GUI (docs/gui-shell-spike.md tab 3) or other caller can consume it without
re-implementing the gates. It must do no network, no file reads, and no
writes: configs arrive pre-parsed via the `configs` argument.

Parity tests run main() in-process (argv monkeypatched, --dry-run so nothing
is written) and assert the printed verdict block matches the structured
result for the same inputs.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

import next_round as nr


def write_cfg(tmp_path, name, base="0.00000050", loss="0.00000150",
              buffer="0.00000005"):
    """Write a strategy config into tmp_path and return its filename."""
    (tmp_path / name).write_text(json.dumps(
        {"base_stake": base, "max_loss": loss, "buffer": buffer}))
    return name


def write_ledger(tmp_path, rows):
    path = tmp_path / "ledger.json"
    path.write_text(json.dumps({"rounds": rows}))
    return str(path)


def configs_from(tmp_path, names):
    """Parse configs the way load_configs() would, from tmp_path."""
    return {n: json.loads((tmp_path / n).read_text()) for n in names}


def run_main(capsys, monkeypatch, argv, cwd):
    """Run next_round.main() with argv from `cwd`; return captured stdout."""
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(sys, "argv", ["next_round.py"] + argv)
    nr.main()
    return capsys.readouterr().out


def verdict_block(stdout):
    """The VERDICT line plus every indented detail line after it."""
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.startswith("VERDICT:"))
    return [line for line in lines[start:] if line.strip()]


@pytest.fixture
def funded_configs(tmp_path, monkeypatch):
    """All 13 family configs, each fundable at a few uBTC of headroom."""
    names = [write_cfg(tmp_path, f"s{i:02d}_x.json") for i in range(1, 12)]
    names.append(write_cfg(tmp_path, "s12_glacier.json"))
    names.append(write_cfg(tmp_path, "paroli.json"))
    monkeypatch.setattr(nr, "PLAN1_CONFIGS", names[:11])
    monkeypatch.setattr(nr, "FAMILY_CONFIGS",
                        {"glacier": names[11], "paroli": names[12]})
    return names


class TestVerdictTable:
    """Each gate, exercised directly on the structured result."""

    def test_floor_breach_halts_first(self):
        r = nr.compute_verdict(5, D("-0.000001"), "plan1", 4, D("0.0001"),
                               D("0.10"), configs={})
        assert r["verdict"] == "HALT" and r["reason"] == "floor breach"

    def test_ignore_floor_bypasses_only_the_floor_gate(self):
        # streak 4 would halt anyway; ignore_floor must not mask it
        r = nr.compute_verdict(5, D("-0.000001"), "plan1", 4, D("0"),
                               D("0.10"), ignore_floor=True, configs={})
        assert r["reason"] == "three consecutive losses"

    def test_streak_three_halts(self):
        r = nr.compute_verdict(5, D("-0.000001"), "plan1", 3, D("0"),
                               D("50"), configs={})
        assert r["verdict"] == "HALT"
        assert r["reason"] == "three consecutive losses"

    def test_drawdown_limit_halts(self):
        r = nr.compute_verdict(5, D("0.000001"), "plan1", 0,
                               nr.DRAWDOWN_LIMIT_BTC, D("50"), configs={})
        assert r["verdict"] == "HALT"
        assert r["reason"] == "session give-back limit"

    def test_recovery_rotates_off_last_family(self, funded_configs, tmp_path):
        # last family glacier -> rotation order is paroli, plan1
        r = nr.compute_verdict(5, D("-0.000001"), "glacier", 1, D("0"),
                               D("5.00"),
                               configs=configs_from(tmp_path, funded_configs))
        assert r["verdict"] == "RECOVERY" and r["family"] == "paroli"
        assert "run_single_round.py 6 paroli.json" in r["command"]
        assert set(r["specs"]) == {"paroli.json"}

    def test_recovery_skips_unviable_families(self, funded_configs, tmp_path):
        # 0.42 uBTC: 0.12 headroom clamps every stake below MIN_STAKE -> HALT
        r = nr.compute_verdict(5, D("-0.000001"), "glacier", 1, D("0"),
                               D("0.42"),
                               configs=configs_from(tmp_path, funded_configs))
        assert r["verdict"] == "HALT"
        assert r["reason"] == "no fundable floor-safe family"
        assert len(r["skipped"]) == 3            # paroli, plan1, glacier tried
        assert all(":" in line for line in r["skipped"])

    def test_recovery_specs_are_returned_not_written(self, funded_configs,
                                                     tmp_path):
        before = {n: (tmp_path / n).read_text() for n in funded_configs}
        r = nr.compute_verdict(5, D("-0.000001"), "glacier", 1, D("0"),
                               D("5.00"),
                               configs=configs_from(tmp_path, funded_configs))
        assert r["verdict"] == "RECOVERY"
        assert {n: (tmp_path / n).read_text() for n in funded_configs} == before

    def test_standard_lists_every_family_with_reasons(self, funded_configs,
                                                      tmp_path):
        r = nr.compute_verdict(5, D("0.000001"), "glacier", 0, D("0"),
                               D("5.00"),
                               configs=configs_from(tmp_path, funded_configs))
        assert r["verdict"] == "STANDARD"
        assert [o["family"] for o in r["options"]] == nr.LADDER
        # 5.00 uBTC: glacier/paroli fit, plan1's 16.50 exposure does not
        by_family = {o["family"]: o for o in r["options"]}
        assert by_family["plan1"]["ok"] is False
        assert by_family["plan1"]["command"] is None
        assert "worst case" in by_family["plan1"]["reason"]
        assert by_family["glacier"]["ok"] and by_family["paroli"]["ok"]
        assert "round_flow.py 6" not in (by_family["glacier"]["command"] or "")

    def test_standard_halts_when_nothing_fits(self, funded_configs, tmp_path):
        r = nr.compute_verdict(5, D("0.000001"), "glacier", 0, D("0"),
                               D("0.49"),
                               configs=configs_from(tmp_path, funded_configs))
        assert r["verdict"] == "HALT"
        assert r["reason"] == "no fundable floor-safe family"
        assert len(r["skipped"]) == 3

    def test_missing_config_fails_closed(self, funded_configs, tmp_path):
        configs = configs_from(tmp_path, funded_configs)
        del configs["paroli.json"]             # unreadable == missing
        r = nr.compute_verdict(5, D("-0.000001"), "glacier", 1, D("0"),
                               D("5.00"), configs=configs)
        # paroli skipped for the missing config; plan1 still viable
        assert r["verdict"] == "RECOVERY" and r["family"] == "plan1"
        assert any("paroli: missing config" in s for s in r["skipped"])

    def test_unknown_last_family_raises(self):
        # LADDER.index(family) - same crash surface as the original main()
        with pytest.raises(ValueError):
            nr.compute_verdict(5, D("-0.000001"), "nope", 1, D("0"),
                               D("5.00"), configs={})

    def test_balance_none_marks_funding_unverified(self, funded_configs,
                                                   tmp_path):
        r = nr.compute_verdict(5, D("0.000001"), "glacier", 0, D("0"), None,
                               configs=configs_from(tmp_path, funded_configs))
        assert r["verdict"] == "STANDARD"
        assert all(o["ok"] for o in r["options"])
        assert all("not verified" in o["reason"] for o in r["options"])


class TestPurity:
    """compute_verdict must not touch the filesystem or the network."""

    @staticmethod
    def _ban_path(monkeypatch):
        """Replace next_round's Path with a trap: any use proves file I/O."""
        def trap(*a, **k):
            raise AssertionError("compute_verdict performed file I/O")
        monkeypatch.setattr(nr, "Path", trap)

    def test_no_io_with_injected_configs(self, funded_configs, tmp_path,
                                         monkeypatch):
        self._ban_path(monkeypatch)
        r = nr.compute_verdict(5, D("-0.000001"), "glacier", 1, D("0"),
                               D("5.00"),
                               configs=configs_from(tmp_path, funded_configs))
        assert r["verdict"] == "RECOVERY"

    def test_no_io_on_standard_path(self, funded_configs, tmp_path,
                                    monkeypatch):
        self._ban_path(monkeypatch)
        r = nr.compute_verdict(5, D("0.000001"), "glacier", 0, D("0"),
                               D("5.00"),
                               configs=configs_from(tmp_path, funded_configs))
        assert r["verdict"] == "STANDARD"


class TestCliParity:
    """main() must print exactly what compute_verdict() decided."""

    def _assert_parity(self, capsys, monkeypatch, tmp_path, rows, balance,
                       family_files, extra_argv=()):
        ledger = write_ledger(tmp_path, rows)
        argv = ["--dry-run", "--ledger", ledger, "--balance", str(balance),
                *extra_argv]
        out = run_main(capsys, monkeypatch, argv, tmp_path)
        # Recompute the inputs main() derived, then the verdict itself.
        loaded = nr.load_rows(Path(ledger))
        last_n, last_net = nr.last_round(loaded)
        streak = nr.loss_streak(loaded)
        drawdown = nr.session_drawdown(loaded)
        family = nr.infer_family(last_n)
        result = nr.compute_verdict(
            last_n, last_net, family, streak, drawdown, D(str(balance)),
            ignore_floor="--ignore-floor" in extra_argv,
            configs=nr.load_configs())
        printed = verdict_block(out)
        if result["verdict"] == "HALT":
            assert printed[0] == f"VERDICT: HALT ({result['reason']})"
            assert printed[1:] == [f"  skip {s}" for s in result["skipped"]]
        elif result["verdict"] == "RECOVERY":
            assert printed[0] == f"VERDICT: RECOVERY {result['family']}"
            assert printed[-1] == f"  {result['command']}"
            assert "  (dry-run: no files written)" in printed
        else:
            assert printed[0] == "VERDICT: STANDARD"
            expected = []
            for i, o in enumerate(result["options"], 1):
                expected.append(f"  [{i}] {o['command']}" if o["ok"]
                                else f"  [{i}] skip {o['family']}: {o['reason']}")
            assert printed[1:] == expected
        return result

    def test_parity_halt_floor(self, capsys, monkeypatch, tmp_path,
                               funded_configs):
        rows = [{"n": 73, "net": "0.00000011", "rounds": 22}]
        r = self._assert_parity(capsys, monkeypatch, tmp_path, rows, "0.10",
                                funded_configs)
        assert r["reason"] == "floor breach"

    def test_parity_standard(self, capsys, monkeypatch, tmp_path,
                             funded_configs):
        rows = [{"n": 73, "net": "0.00000011", "rounds": 22}]
        r = self._assert_parity(capsys, monkeypatch, tmp_path, rows, "5.00",
                                funded_configs)
        assert r["verdict"] == "STANDARD"

    def test_parity_recovery(self, capsys, monkeypatch, tmp_path,
                             funded_configs):
        rows = [{"n": 73, "net": "-0.00000100", "rounds": 3}]
        r = self._assert_parity(capsys, monkeypatch, tmp_path, rows, "5.00",
                                funded_configs)
        assert r["verdict"] == "RECOVERY"

    def test_parity_halt_no_fundable_family(self, capsys, monkeypatch,
                                            tmp_path, funded_configs):
        rows = [{"n": 73, "net": "-0.00000100", "rounds": 3}]
        r = self._assert_parity(capsys, monkeypatch, tmp_path, rows, "0.42",
                                funded_configs)
        assert r["verdict"] == "HALT"
        assert r["reason"] == "no fundable floor-safe family"

    def test_parity_unsettled_rows_still_ignored(self, capsys, monkeypatch,
                                                 tmp_path, funded_configs):
        # a 0-round 0-net row must not reset the streak through the new path
        rows = [{"n": 71, "net": "-1E-7", "rounds": 1},
                {"n": 72, "net": "-1E-7", "rounds": 1},
                {"n": 73, "net": "0", "rounds": 0}]
        r = self._assert_parity(capsys, monkeypatch, tmp_path, rows, "5.00",
                                funded_configs)
        assert r["streak"] == 2 and r["verdict"] == "RECOVERY"
