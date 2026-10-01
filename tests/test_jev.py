"""Tests for the jev wiring: selection helpers and next_round integration.

All gateway calls are monkeypatched - no network. The invariants pinned here:

- jev only ever narrows the deterministic verdict: it picks among already
  fundable families or tightens to HALT via sit_out; it can never widen the
  option set or override a deterministic HALT.
- Low confidence, off-list picks, and transport failures all fall back to
  the deterministic result unchanged.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

import jev
import jev_questions as jq
import next_round as nr


def _cfg(base="0.00000050", max_loss="0.00000150", desc="test config"):
    return {"base_stake": base, "buffer": "0.00000005",
            "max_loss": max_loss, "max_profit": "0.00000150",
            "target": 5000, "side": "UNDER", "description": desc}


def _configs():
    cfgs = {name: _cfg(desc=f"{name} slot") for name in nr.PLAN1_CONFIGS}
    cfgs["s12_glacier.json"] = _cfg(base="0.00000025", desc="glacier")
    cfgs["paroli.json"] = _cfg(base="0.00000025", desc="paroli")
    return cfgs


def _standard_result(configs):
    return nr.compute_verdict(last_n=10, last_net=D("0.000001"), family="paroli",
                              streak=0, drawdown=D("0"), balance_ubtc=D("50"),
                              configs=configs)


def _recovery_result(configs):
    return nr.compute_verdict(last_n=10, last_net=D("-0.000001"), family="plan1",
                              streak=1, drawdown=D("0"), balance_ubtc=D("50"),
                              configs=configs)


def _answers(choice, conf):
    return {"pick": {"choice": choice, "confidence": conf,
                     "probabilities": {choice: conf}}}


class TestPickFamily:
    def test_confident_pick(self):
        fam, conf, _ = jev.pick_family(
            _answers("glacier", 0.9), "pick",
            ["plan1", "glacier"], jq.SIT_OUT, 0.6, 0.8)
        assert fam == "glacier" and conf == 0.9

    def test_low_confidence_falls_back(self):
        fam, conf, _ = jev.pick_family(
            _answers("glacier", 0.4), "pick",
            ["plan1", "glacier"], jq.SIT_OUT, 0.6, 0.8)
        assert fam is None and conf == 0.4

    def test_off_list_pick_falls_back(self):
        fam, _, _ = jev.pick_family(
            _answers("martingale", 0.99), "pick",
            ["plan1", "glacier"], jq.SIT_OUT, 0.6, 0.8)
        assert fam is None

    def test_sit_out_needs_higher_bar(self):
        fam, _, _ = jev.pick_family(
            _answers(jq.SIT_OUT, 0.7), "pick",
            ["plan1"], jq.SIT_OUT, 0.6, 0.8)
        assert fam is None  # 0.7 < SIT_OUT_CONFIDENCE
        fam, _, _ = jev.pick_family(
            _answers(jq.SIT_OUT, 0.95), "pick",
            ["plan1"], jq.SIT_OUT, 0.6, 0.8)
        assert fam == jq.SIT_OUT

    def test_missing_answer_raises(self):
        with pytest.raises(jev.JevError):
            jev.pick_family({}, "pick", ["plan1"], jq.SIT_OUT, 0.6, 0.8)


class TestRankConfigs:
    def test_orders_and_drops(self):
        answers = {"run_s01_flat": {"noul": 0.9},
                   "run_s02_sprint": {"noul": 0.3},
                   "run_s03_heavy2": {"noul": 0.7}}
        ordered, dropped = jev.rank_configs(
            answers, ["s01_flat.json", "s02_sprint.json", "s03_heavy2.json"],
            "run_", 0.5)
        assert ordered == ["s01_flat.json", "s03_heavy2.json"]
        assert dropped == [("s02_sprint.json", 0.3)]

    def test_top_cap(self):
        answers = {f"run_{n}": {"noul": 0.9}
                   for n in ["s01_flat", "s02_sprint", "s03_heavy2"]}
        ordered, dropped = jev.rank_configs(
            answers, ["s01_flat.json", "s02_sprint.json", "s03_heavy2.json"],
            "run_", 0.5, top=2)
        assert len(ordered) == 2 and len(dropped) == 1

    def test_missing_answer_dropped(self):
        ordered, dropped = jev.rank_configs(
            {}, ["s01_flat.json"], "run_", 0.5)
        assert ordered == [] and dropped == [("s01_flat.json", None)]


class TestApplyJev:
    def test_halt_untouched(self, monkeypatch):
        result = {"verdict": "HALT", "reason": "floor breach",
                  "next_n": 11, "options": [], "skipped": []}
        called = []
        monkeypatch.setattr(jev, "system_one",
                            lambda *a, **k: called.append(1) or {})
        out = nr.apply_jev(result, [], 10, D("0"), "plan1", 0, D("0"),
                           D("50"), _configs())
        assert out["verdict"] == "HALT" and not called

    def test_standard_pick_reorders_options(self, monkeypatch):
        configs = _configs()
        result = _standard_result(configs)
        monkeypatch.setattr(jev, "system_one",
                            lambda s, q: _answers("paroli", 0.9))
        out = nr.apply_jev(result, [], 10, D("0.000001"), "paroli", 0, D("0"),
                           D("50"), configs)
        assert out["verdict"] == "STANDARD"
        assert out["jev"]["pick"] == "paroli"
        # Picked option moved first so auto_goal's first-command parser sees it.
        assert out["options"][0]["family"] == "paroli"
        assert out["options"][0]["jev_pick"] is True

    def test_standard_sit_out_halts(self, monkeypatch):
        configs = _configs()
        result = _standard_result(configs)
        monkeypatch.setattr(jev, "system_one",
                            lambda s, q: _answers(jq.SIT_OUT, 0.95))
        out = nr.apply_jev(result, [], 10, D("0.000001"), "paroli", 0, D("0"),
                           D("50"), configs)
        assert out["verdict"] == "HALT"
        assert "sit-out" in out["reason"]
        assert out["command"] is None

    def test_low_confidence_keeps_deterministic(self, monkeypatch):
        configs = _configs()
        result = _standard_result(configs)
        monkeypatch.setattr(jev, "system_one",
                            lambda s, q: _answers("glacier", 0.3))
        out = nr.apply_jev(result, [], 10, D("0.000001"), "paroli", 0, D("0"),
                           D("50"), configs)
        assert out["verdict"] == "STANDARD"
        assert "pick" not in out["jev"]
        # Option order unchanged from the deterministic result.
        assert [o["family"] for o in out["options"]] == nr.LADDER

    def test_gateway_failure_keeps_deterministic(self, monkeypatch):
        configs = _configs()
        result = _standard_result(configs)
        def boom(*a, **k):
            raise jev.JevError("timeout")
        monkeypatch.setattr(jev, "system_one", boom)
        out = nr.apply_jev(result, [], 10, D("0.000001"), "paroli", 0, D("0"),
                           D("50"), configs)
        assert out["verdict"] == "STANDARD"
        assert "unavailable" in out["jev"]["status"]

    def test_recovery_pick_changes_family_and_specs(self, monkeypatch):
        configs = _configs()
        result = _recovery_result(configs)
        assert result["verdict"] == "RECOVERY"
        assert result["family"] == "glacier"  # deterministic first fundable
        monkeypatch.setattr(jev, "system_one",
                            lambda s, q: _answers("paroli", 0.9))
        out = nr.apply_jev(result, [], 10, D("-0.000001"), "plan1", 1, D("0"),
                           D("50"), configs)
        assert out["verdict"] == "RECOVERY"
        assert out["family"] == "paroli"
        assert list(out["specs"]) == ["paroli.json"]
        assert "paroli.json" in out["command"]

    def test_recovery_sit_out_halts(self, monkeypatch):
        configs = _configs()
        result = _recovery_result(configs)
        monkeypatch.setattr(jev, "system_one",
                            lambda s, q: _answers(jq.SIT_OUT, 0.9))
        out = nr.apply_jev(result, [], 10, D("-0.000001"), "plan1", 1, D("0"),
                           D("50"), configs)
        assert out["verdict"] == "HALT" and out["specs"] is None


class TestLoadApiKey:
    def test_env_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv(jev.ENV_KEY, "from-env")
        assert jev.load_api_key(tmp_path / ".env") == "from-env"

    def test_dotenv_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv(jev.ENV_KEY, raising=False)
        p = tmp_path / ".env"
        p.write_text('OTHER="x"\nAI_GATEWAY_API_KEY="from-file"\n')
        assert jev.load_api_key(p) == "from-file"

    def test_missing_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.delenv(jev.ENV_KEY, raising=False)
        assert jev.load_api_key(tmp_path / ".env") is None
