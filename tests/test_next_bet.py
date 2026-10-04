"""Regression tests for next_bet.py, the pre-bet checker.

next_bet.py is the gate that runs BEFORE every bet, so what it can and cannot
prove matters. The wiki work record (2026-09-22) warned that its local labels
had been read as live clearance; these tests pin exactly which signals it
actually establishes and which it merely restates.

The four things worth pinning:

1. Check 5 ("no unsettled bet") tests the CALLER's `--pending` integer. It
   never queries the site. Omitted, `--pending` defaults to 0, so check 5
   passes without any evidence that settlement happened.
2. `ADVANCE` is written to target_hit_log.jsonl BEFORE the exit code is
   decided, so a run that FAILED still records `ADVANCE`.
3. The exit code - not the log label - is the only real pass/fail signal.
4. `--balance` omitted is a hard error, not a pass.

`main()` calls sys.exit, so these run it as a subprocess in a tmp cwd and
read the ledger it writes.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
NEXT_BET = REPO / "next_bet.py"
LEDGER_NAME = "target_hit_log.jsonl"


def policy(**overrides) -> dict:
    """A policy whose single row passes checks 0a/0b/1/2/3/4a on its own.

    B0 = 1.00 token -> T = 1.80 (180c, ceil of 1.80x), F = 0.40 (40c, ceil of
    0.40x). The one row sits exactly on the starting node and, on a loss,
    lands exactly on the floor. At 42.00% the floored payout is 142c, so a
    win reaches 1.82 and clears the 1.80 target - every gate passes on its
    own, leaving check 5 as the only one that can independently fail.
    """
    doc = {
        "baseline_tokens": "1.00",
        "starting_tokens": "1.00",
        "win_balance_tokens": "1.80",
        "protected_balance_tokens": "0.40",
        "steps": [
            {
                "step": 1,
                "expected_balance": "1.00",
                "bet_tokens": "0.60",
                "win_chance_pct": "42.00",
                "roll_over_label": "42.00",
                "multiplier_display_5dp": "2.38000",
                "balance_on_loss": "0.40",
            }
        ],
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def run(tmp_path):
    """Run next_bet.py in an isolated cwd; return (exit_code, stdout, ledger)."""

    def _run(doc: dict, *args: str):
        policy_file = tmp_path / "policy.json"
        policy_file.write_text(json.dumps(doc), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(NEXT_BET), str(policy_file), *args],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        ledger_path = tmp_path / LEDGER_NAME
        ledger = []
        if ledger_path.exists():
            ledger = [
                json.loads(line)
                for line in ledger_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        return proc.returncode, proc.stdout + proc.stderr, ledger

    return _run


def failed(stdout: str) -> str:
    """The [FAIL] lines, as one blob, for substring matching.

    Check names carry parenthetical suffixes and an `= ...` clause, so exact
    equality is brittle; callers assert a distinctive substring instead.
    """
    return "\n".join(
        line for line in stdout.splitlines() if line.startswith("[FAIL]")
    )


def passing(stdout: str) -> str:
    """The [PASS] lines, as one blob, for substring matching."""
    return "\n".join(
        line for line in stdout.splitlines() if line.startswith("[PASS]")
    )


# --------------------------------------------------------------------------
# 1. Check 5 asserts the caller's number; it does not query settlement.
# --------------------------------------------------------------------------

def test_omitted_pending_defaults_to_zero_and_passes_check_5(run):
    """With no --pending, check 5 PASSES on an assumed 0 - no site query.

    This is the shape the work record flagged: the pre-bet gate reports
    "no unsettled bet" purely because the caller did not say otherwise.
    """
    code, out, _ = run(policy(), "--balance", "1.00")

    assert "5 no unsettled bet: pending=0" in passing(out)
    assert "5 no unsettled bet" not in failed(out)
    assert code == 0


def test_a_supplied_pending_fails_check_5_without_any_site_contact(run):
    """--pending is honored verbatim: it is an input, never a measurement."""
    code, out, _ = run(policy(), "--balance", "1.00", "--pending", "1")

    assert "5 no unsettled bet: pending=1" in failed(out)
    assert code == 1


def test_check_5_passes_without_proving_settlement_anywhere(run):
    """Every check can PASS while the run has proven nothing about settlement.

    The gate is arithmetic over the policy plus the caller's two numbers; the
    only place a live quote enters is the printed [LIVE] 4b line, which is
    advisory text, not a check.
    """
    code, out, ledger = run(policy(), "--balance", "1.00", "--report")

    assert code == 0
    assert failed(out) == ""
    # Nothing in the output is a record of having read the site.
    assert "[LIVE] 4b site quote must show" in out
    assert ledger, "the passing run should still write a ledger row"


# --------------------------------------------------------------------------
# 2. ADVANCE is written before the exit decision.
# --------------------------------------------------------------------------

def test_advance_is_logged_even_when_the_run_failed(run):
    """A FAILED run still records outcome=ADVANCE in the ledger.

    The log call sits above the sys.exit, so the label cannot be read as
    clearance - only the exit code distinguishes pass from fail.
    """
    code, out, ledger = run(policy(), "--balance", "1.00", "--pending", "1", "--report")

    assert code == 1, "the run must fail on check 5"
    assert "5 no unsettled bet" in failed(out)
    assert len(ledger) == 1
    assert ledger[0]["outcome"] == "ADVANCE"


def test_the_advance_label_is_not_by_itself_evidence_of_a_clean_run(run):
    """Pin the exact trap: ADVANCE in the ledger AND a non-zero exit.

    Anyone auditing the ledger alone sees ADVANCE; only the paired exit code
    reveals the run failed.
    """
    code, out, ledger = run(policy(), "--balance", "1.00", "--pending", "2", "--report")

    assert ledger[0]["outcome"] == "ADVANCE"
    assert code != 0
    assert "5 no unsettled bet" in failed(out)


# --------------------------------------------------------------------------
# 3. The exit code is the signal.
# --------------------------------------------------------------------------

def test_exit_zero_only_when_every_check_passes(run):
    code, out, _ = run(policy(), "--balance", "1.00")
    assert code == 0
    assert failed(out) == ""


def test_exit_nonzero_when_a_check_fails(run):
    code, out, _ = run(policy(), "--balance", "1.00", "--pending", "1")
    assert code == 1
    assert "5 no unsettled bet" in failed(out)


def test_an_off_node_balance_pauses_instead_of_improvising(run):
    """A balance that matches no scheduled row must stop, not continue."""
    code, out, ledger = run(policy(), "--balance", "1.23", "--report")

    assert code == 1
    assert "PAUSE: balance is not a scheduled node" in out
    assert ledger[0]["outcome"] == "OFF-NODE"


def test_a_win_at_the_target_stops_the_attempt(run):
    code, out, ledger = run(policy(), "--balance", "1.80", "--report")

    assert code == 0
    assert "ATTEMPT WON" in out
    assert ledger[0]["outcome"] == "WON"


def test_a_loss_at_the_floor_stops_the_attempt(run):
    code, out, ledger = run(policy(), "--balance", "0.40", "--report")

    assert code == 0
    assert "ATTEMPT LOST" in out
    assert ledger[0]["outcome"] == "LOST"


# --------------------------------------------------------------------------
# 4. --balance is required; --audit changes nothing about the ledger.
# --------------------------------------------------------------------------

def test_omitting_balance_is_a_hard_error(run):
    code, out, ledger = run(policy())

    assert code != 0
    assert "need --balance" in out
    assert ledger == []


def test_audit_mode_runs_the_schedule_checks_and_writes_no_ledger(run):
    code, out, ledger = run(policy(), "--audit")

    assert "0a boundaries" in out
    assert "0b schedule chain" in out
    assert ledger == []


def test_audit_ignores_a_stray_balance_it_is_given(run):
    """--audit exits after the schedule checks, before --balance is read."""
    code, out, ledger = run(policy(), "--audit", "--balance", "1.00")

    assert "ACTIVE ROW" not in out
    assert ledger == []


# --------------------------------------------------------------------------
# The policy itself has to hold up; a doctored policy fails closed.
# --------------------------------------------------------------------------

def test_boundaries_must_be_the_declared_ratios(run):
    """0a fails when win/protected do not sit at 1.80x/0.40x of baseline."""
    code, out, _ = run(policy(win_balance_tokens="9.99"), "--balance", "1.00")

    assert "0a boundaries" in failed(out)
    assert code == 1


def test_a_broken_schedule_chain_fails_check_0b(run):
    """A row whose loss does not land on the floor breaks the chain."""
    doc = policy()
    doc["steps"][0]["bet_tokens"] = "0.10"  # loss lands at 0.90, not the floor
    code, out, _ = run(doc, "--balance", "1.00")

    assert "0b schedule chain" in failed(out)
    assert code == 1
