"""Regression tests for the protocol's funding/floor gates and ledger hygiene.

Two verified defects (2026-09-19) motivated these tests:

1. ``next_round.py`` issued verdicts naming commands its own preflight then
   refused, and STANDARD options whose ``max_loss`` would carry the balance
   through ``FLOOR_UBTC``. plan1's exposure is the SUM over its 11 slots
   (``round_flow.py`` runs them sequentially, each with its own cap), not the
   single largest cap.
2. ``run_single_round.py`` recorded a 0-round 0-net attempt as a settled round,
   which reset the loss streak and flipped RECOVERY -> STANDARD.

The ledger shapes pinned below are copied from real rows of
``session_ledger.json``, including the ``n=13`` partial (0 rounds, non-zero net)
that must still count because the balance moved.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from decimal import Decimal as D

import pytest

import next_round as nr


@pytest.fixture(scope="module")
def rsr():
    """run_single_round.py parses argv at import; feed it a synthetic round."""
    saved = sys.argv
    sys.argv = ["run_single_round.py", "999", "s12_glacier.json"]
    try:
        spec = importlib.util.spec_from_file_location("rsr_under_test",
                                                      "run_single_round.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.argv = saved


def make_cfg(tmp_path, name, base, loss, buffer="0.00000005"):
    """Write a minimal strategy config and return its path."""
    path = tmp_path / name
    path.write_text(json.dumps({"base_stake": base, "max_loss": loss,
                                "buffer": buffer}))
    return str(path)


class TestLedgerSettlement:
    def test_zero_round_zero_net_row_is_not_a_round(self):
        assert nr.is_settled({"n": 73, "net": "0", "rounds": 0}) is False

    def test_operator_reset_marker_is_not_a_round(self):
        assert nr.is_settled({"n": "RESET", "net": "0", "rounds": 0}) is False

    def test_zero_round_row_with_a_net_still_counts(self):
        # real row n=13: "partial, killed by harness clamp mid-round"
        assert nr.is_settled({"n": 13, "net": "0.00000078", "rounds": 0}) is True

    def test_legacy_row_without_rounds_counts(self):
        assert nr.is_settled({"n": 9, "net": "0.0000001"}) is True

    def test_noop_row_does_not_reset_the_loss_streak(self):
        settled = [{"n": 71, "net": "-1E-7", "rounds": 1},
                   {"n": 72, "net": "-1E-7", "rounds": 1}]
        noop = {"n": 73, "net": "0", "rounds": 0}
        assert nr.loss_streak(settled) == 2
        assert nr.loss_streak(settled + [noop]) == 2

    def test_noop_row_does_not_become_last_round(self):
        rows = [{"n": 72, "net": "-1E-7", "rounds": 1},
                {"n": 73, "net": "0", "rounds": 0}]
        assert nr.last_round(rows)[0] == 72

    def test_noop_row_does_not_change_drawdown(self):
        settled = [{"n": 1, "net": "2E-7", "rounds": 1},
                   {"n": 2, "net": "-3E-7", "rounds": 1}]
        noop = {"n": 3, "net": "0", "rounds": 0}
        assert nr.session_drawdown(settled + [noop]) == nr.session_drawdown(settled)


class TestFundingGates:
    def test_plan1_exposure_is_the_sum_over_slots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nr, "PLAN1_CONFIGS",
                            [make_cfg(tmp_path, f"s{i}.json", "0.00000050",
                                      "0.00000150") for i in range(3)])
        ok, reason = nr.family_fitness("plan1", D("4.50"))
        # 3 slots x 1.50 = 4.50 exposure against 4.20 of headroom
        assert ok is False
        assert "4.50" in reason and "3 slot" in reason

    def test_plan1_viable_when_the_sum_fits(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nr, "PLAN1_CONFIGS",
                            [make_cfg(tmp_path, f"s{i}.json", "0.00000050",
                                      "0.00000150") for i in range(3)])
        ok, _ = nr.family_fitness("plan1", D("10.00"))
        assert ok is True

    def test_unfunded_family_is_rejected_by_entry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nr, "FAMILY_CONFIGS",
                            {"glacier": make_cfg(tmp_path, "g.json",
                                                 "0.00000050", "0.00000150")})
        ok, reason = nr.family_fitness("glacier", D("0.49"))
        assert ok is False and "entry" in reason

    def test_family_fitness_reports_a_missing_config(self, monkeypatch):
        monkeypatch.setattr(nr, "FAMILY_CONFIGS", {"glacier": "absent_xyz.json"})
        ok, reason = nr.family_fitness("glacier", D("5.00"))
        assert ok is False and "unreadable" in reason

    def test_clamp_preserves_ratio_and_fits_headroom(self):
        cfg = {"base_stake": "0.00000025", "max_loss": "0.00000150",
               "buffer": "0.00000005"}
        updates, reason = nr.clamp_stake("x.json", cfg, D("0.00000025"),
                                         D("0.00000150"), D("0.64"))
        assert reason is None
        base, loss = D(updates["base_stake"]), D(updates["max_loss"])
        assert loss / base == D("6")                          # shape preserved
        assert loss * nr.UBTC <= D("0.64") - nr.FLOOR_UBTC    # inside headroom

    def test_clamp_refuses_below_min_stake(self):
        cfg = {"base_stake": "0.00000025", "max_loss": "0.00000150",
               "buffer": "0.00000005"}
        updates, reason = nr.clamp_stake("x.json", cfg, D("0.00000025"),
                                         D("0.00000150"), D("0.42"))
        assert updates is None and "MIN_STAKE" in reason

    def test_clamp_refuses_a_breached_floor(self):
        cfg = {"base_stake": "0.00000025", "max_loss": "0.00000150",
               "buffer": "0.00000005"}
        updates, reason = nr.clamp_stake("x.json", cfg, D("0.00000025"),
                                         D("0.00000150"), D("0.29"))
        assert updates is None and "floor breached" in reason

    def test_plan1_clamp_divides_headroom_by_slots(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nr, "PLAN1_CONFIGS",
                            [make_cfg(tmp_path, f"s{i}.json", "0.00000050",
                                      "0.00000150") for i in range(11)])
        specs, reason = nr.recovery_updates("plan1", 1, D("0.00000050"), D("0.64"))
        # 0.34 of headroom over 11 slots cannot fund a slot above MIN_STAKE
        assert specs is None and "MIN_STAKE" in reason

    def test_recovery_is_fundable_and_floor_safe_when_it_can_be(self, tmp_path,
                                                               monkeypatch):
        monkeypatch.setattr(nr, "PLAN1_CONFIGS",
                            [make_cfg(tmp_path, f"s{i}.json", "0.00000050",
                                      "0.00000150") for i in range(2)])
        specs, reason = nr.recovery_updates("plan1", 1, D("0.00000050"), D("1.00"))
        assert reason is None
        total = sum(D(s["max_loss"]) for s in specs.values()) * nr.UBTC
        assert total <= D("1.00") - nr.FLOOR_UBTC
        for spec in specs.values():
            assert D(spec["base_stake"]) >= nr.MIN_STAKE_BTC
            assert "E" not in spec["base_stake"]      # plain decimal, not 1E-7
            assert "E" not in spec["max_loss"]

    def test_unknown_family_yields_no_spec(self):
        specs, reason = nr.recovery_updates("nope", 1, D("0.00000050"), D("5.00"))
        assert specs is None and "unknown family" in reason


class TestLedgerWriteGuard:
    def test_append_ledger_refuses_a_noop_round(self, rsr, tmp_path):
        led = tmp_path / "ledger.json"
        led.write_text(json.dumps({"rounds": []}))
        with pytest.raises(ValueError, match="0 rounds settled"):
            rsr.append_ledger(74, D("0"), 0, "unfunded attempt", path=led)

    def test_append_ledger_records_a_real_round(self, rsr, tmp_path):
        led = tmp_path / "ledger.json"
        led.write_text(json.dumps({"rounds": []}))
        total = rsr.append_ledger(74, D("-0.00000050"), 15, "real round",
                                  path=led)
        assert D(total) == D("-0.00000050")
        written = json.loads(led.read_text())["rounds"][0]
        assert written["rounds"] == 15 and written["n"] == 74

    def test_a_refused_noop_leaves_the_ledger_untouched(self, rsr, tmp_path):
        led = tmp_path / "ledger.json"
        original = json.dumps({"rounds": [{"n": 72, "net": "-5E-7",
                                           "rounds": 1}]})
        led.write_text(original)
        with pytest.raises(ValueError):
            rsr.append_ledger(73, D("0"), 0, "no-op", path=led)
        assert led.read_text() == original