"""jev_questions.py - every jev question and threshold in one reviewable place.

TypeSafe guidance: humans review the questions and threshold constants, so
they are defined here and nowhere else. Question ids are for our code only;
the model never sees them - the full question lives in `instructions`.

Thresholds:
  PICK_CONFIDENCE      min confidence for jev to choose a family/config set
  SIT_OUT_CONFIDENCE   higher bar before jev may tighten a verdict to HALT
  RUN_CONFIG_NOUL      min yes-probability for a config to stay in a batch
  RUN_BATCH_NOUL       min yes-probability to run the batch at all
  CONTINUE_NOUL        mid-batch check: stop when below this
"""

PICK_CONFIDENCE = 0.60
SIT_OUT_CONFIDENCE = 0.80
RUN_CONFIG_NOUL = 0.50
RUN_BATCH_NOUL = 0.50
CONTINUE_NOUL = 0.40

SIT_OUT = "sit_out"

# --- next_round.py: pick one strategy family for the next round ------------

FAMILY_INSTRUCTIONS = (
    "Pick the strategy family for the next betting round. "
    "`context` gives the deterministic protocol verdict, balance, loss "
    "streak, session drawdown, and per-family recent results. "
    "`families` lists the options admitted by the deterministic verdict; "
    "you may narrow these options, not authorize a wager. A missing balance "
    "means funding and floor safety are unverified, not that funds are available. "
    "Do not infer affordability or clearance from a family being listed. "
    "Prefer the family whose recent results and profile best fit the "
    "current bankroll headroom and streak. Choose `sit_out` only when the "
    "context argues no round should be played at all right now."
)


def family_choice_question(families):
    """Choice question over approved family names plus sit_out.

    `families` maps family name -> one-line description (from its config).
    """
    criteria = dict(families)
    criteria[SIT_OUT] = "Play no round now; conditions argue for pausing."
    return {"type": "choice", "instructions": FAMILY_INSTRUCTIONS,
            "criteria": criteria}


# --- run_11_strategies.py: select and order the plan1 config batch ---------

RUN_BATCH_INSTRUCTIONS = (
    "Should this batch of plan1 strategy configs run now? "
    "`context` gives balance, streak, drawdown, and recent per-config "
    "results. Answer yes unless the context argues the batch is a poor "
    "use of the session right now."
)

RUN_CONFIG_INSTRUCTIONS = (
    "Should this plan1 config run in the upcoming batch? "
    "`context` gives balance, streak, drawdown, and recent per-config "
    "results; `config` describes this slot's stake schedule and caps. "
    "Answer yes when the config's profile fits the current bankroll "
    "headroom and recent behavior; answer no when it is a poor fit "
    "(e.g. high variance into a thin bankroll, or a shape that has been "
    "losing recently)."
)

CONTINUE_INSTRUCTIONS = (
    "The batch is partway through and running net is negative. "
    "Should the remaining configs still run, or is the session "
    "deteriorating enough that stopping now is the better call? "
    "Answer yes to continue, no to stop the batch early."
)


def run_batch_question():
    return {"type": "noul", "instructions": RUN_BATCH_INSTRUCTIONS}


def run_config_question(name):
    return {"type": "noul",
            "instructions": f"{RUN_CONFIG_INSTRUCTIONS} Config: `{name}`."}


def continue_question():
    return {"type": "noul", "instructions": CONTINUE_INSTRUCTIONS}
