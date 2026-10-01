"""jev.py - thin client for TypeSafe's jev System One model via Vercel AI Gateway.

One call carries one state plus many questions evaluated in parallel; this
module owns transport, auth, and fail-closed error handling. All question
text and thresholds live in jev_questions.py so they stay reviewable.

Safety contract: jev output is advisory selection input only. It can pick
among already-approved options or recommend sitting out; it can never widen
the option set, change stakes, or override a deterministic HALT.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from pathlib import Path

GATEWAY_URL = "https://ai-gateway.vercel.sh/typesafe/v1/systemone"
MODEL = "typesafe-ai/jev"
ENV_KEY = "AI_GATEWAY_API_KEY"
TIMEOUT_S = 20


class JevError(Exception):
    """Gateway call failed or returned a malformed answer."""


def load_api_key(env_path=None):
    """AI_GATEWAY_API_KEY from the environment, else the repo .env."""
    val = os.environ.get(ENV_KEY)
    if val:
        return val.strip()
    path = Path(env_path) if env_path else Path(__file__).resolve().parent / ".env"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    pattern = re.compile(
        rf'^\s*(?:export\s+)?{ENV_KEY}\s*=\s*["\']?(.*?)["\']?\s*$')
    for line in lines:
        m = pattern.match(line)
        if m:
            return m.group(1).strip() or None
    return None


def system_one(state, questions, *, timeout=TIMEOUT_S):
    """POST one System One request; return the `answers` dict.

    Raises JevError on missing key, transport failure, HTTP error, or a
    response without an answers object.
    """
    key = load_api_key()
    if not key:
        raise JevError(f"{ENV_KEY} not set (environment or .env)")
    body = json.dumps(
        {"model": MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(GATEWAY_URL, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read())
    except Exception as e:
        raise JevError(f"gateway request failed: {e}") from e
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise JevError(f"malformed response: {str(payload)[:200]}")
    return answers


def choice_of(answers, qid):
    """(choice, confidence, probabilities) for a choice answer; JevError if absent."""
    a = answers.get(qid)
    if not isinstance(a, dict) or a.get("choice") is None:
        raise JevError(f"missing choice answer for {qid!r}")
    try:
        conf = float(a.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    probs = a.get("probabilities")
    return a["choice"], conf, probs if isinstance(probs, dict) else {}


def noul_of(answers, qid):
    """Float noul for an answer; JevError if absent or unparseable."""
    a = answers.get(qid)
    if not isinstance(a, dict) or a.get("noul") is None:
        raise JevError(f"missing noul answer for {qid!r}")
    try:
        return float(a["noul"])
    except (TypeError, ValueError) as e:
        raise JevError(f"unparseable noul for {qid!r}") from e


def pick_family(answers, qid, candidates, sit_out, min_conf, sit_out_conf):
    """Resolve a choice answer to (family, confidence, probabilities).

    `candidates` is the approved option list; `sit_out` is the reserved
    sit-out option id. Returns:
      (family, conf, probs)  - confident pick inside candidates
      (sit_out, conf, probs) - confident sit-out (caller decides what that means)
      (None, conf, probs)    - low confidence or off-list pick: caller falls back
    """
    choice, conf, probs = choice_of(answers, qid)
    if choice == sit_out:
        if conf >= sit_out_conf:
            return sit_out, conf, probs
        return None, conf, probs
    if choice in candidates and conf >= min_conf:
        return choice, conf, probs
    return None, conf, probs


def rank_configs(answers, names, qid_prefix, min_noul, top=None):
    """Order config names by their noul answers, dropping any below min_noul.

    Returns (ordered_names, dropped) where dropped is [(name, noul)].
    Names with missing/unparseable answers are dropped with noul None.
    """
    scored, dropped = [], []
    for name in names:
        try:
            p = noul_of(answers, f"{qid_prefix}{Path(name).stem}")
        except JevError:
            p = None
        if p is None or p < min_noul:
            dropped.append((name, p))
        else:
            scored.append((p, name))
    scored.sort(key=lambda t: t[0], reverse=True)
    ordered = [name for _p, name in scored]
    if top is not None:
        dropped += [(n, None) for n in ordered[top:]]
        ordered = ordered[:top]
    return ordered, dropped
