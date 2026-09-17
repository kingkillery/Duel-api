"""Session-lifecycle tests: capture stamping and the CLI status surface.

Neither touches the network nor a browser. ``build_session`` is the part of
capture that matters for replay, extracted so it can be tested without
Playwright; the CLI is exercised through ``main(argv)`` against session files
written into ``tmp_path``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import automation_cli
from automation_client import (
    CF_BM_TTL_SECONDS,
    STALE_WARNING_SECONDS,
    DuelClient,
    Session,
)
from capture_session import build_session, identity_from_storage


def write_profile(path: Path, session: Session) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(session.to_json(), encoding="utf-8")
    return path


# ---------------------------------------------------------------- capture stamp


def test_build_session_stamps_both_timestamps() -> None:
    """Without these, session age is unknowable and a 403 is undiagnosable."""
    observed = 1_700_000_000.0
    session = build_session(
        {"duel": "s", "__cf_bm": "bm"},
        {"security:uuid": "11111111-2222-4333-8444-555555555555"},
        observed_at=observed,
    )
    assert session.captured_at == observed
    assert session.cf_bm_at == observed
    assert session.device_uuid == "11111111-2222-4333-8444-555555555555"
    assert session.is_stale(now=observed + 60) is False
    assert session.is_stale(now=observed + CF_BM_TTL_SECONDS + 1) is True


def test_identity_from_storage_reads_nested_and_top_level_blobs() -> None:
    nested = identity_from_storage({"auth": '{"user":{"username":"prest","user_id":42}}'})
    top = identity_from_storage({"auth": '{"username":"prest","user_id":42}'})
    assert nested == ("prest", 42)
    assert top == ("prest", 42)


@pytest.mark.parametrize(
    "storage",
    [
        {},  # no auth blob at all
        {"auth": "not json {"},  # unparsable
        {"auth": '"just a string"'},  # wrong shape
        {"auth": "{}"},  # no keys
        {"auth": '{"user":"not-a-dict"}'},  # user not a dict, no top-level keys
        {"auth": '{"username":"prest"}'},  # user_id missing
        {"auth": '{"username":"prest","user_id":"42"}'},  # user_id not an int
    ],
)
def test_identity_from_storage_never_raises_and_defaults_to_unknown(storage: dict) -> None:
    assert identity_from_storage(storage) == (None, None)


def test_build_session_carries_identity_into_the_session() -> None:
    session = build_session({}, {"security:uuid": "u1", "auth": '{"username":"a","user_id":1}'})
    assert (session.username, session.user_id) == ("a", 1)
    unknown = build_session({}, {})
    assert (unknown.username, unknown.user_id) == (None, None)


def test_build_session_stamps_the_present_when_unspecified() -> None:
    before = time.time()
    session = build_session({"duel": "s"}, {})
    assert session.captured_at is not None
    assert before <= session.captured_at <= time.time()
    assert session.cf_bm_at == session.captured_at


def test_build_session_keeps_the_device_uuid_and_mints_one_if_absent() -> None:
    """security:uuid is a required header; a capture without it must still work."""
    with_uuid = build_session({}, {"security:uuid": "fixed-uuid"})
    assert with_uuid.device_uuid == "fixed-uuid"

    without = build_session({}, {"auth": "{}"})
    assert len(without.device_uuid) == 36, "must fall back to a fresh uuid"
    assert without.local_storage == {"auth": "{}"}


def test_build_session_drops_cookies_outside_the_known_set() -> None:
    """Persisting unknown cookies would bloat the profile with tracking junk."""
    session = build_session(
        {"duel": "s", "__cf_bm": "bm", "third_party_tracker": "junk"}, {}
    )
    assert session.cookies == {"duel": "s", "__cf_bm": "bm"}


def test_build_session_survives_a_save_and_reload(tmp_path: Path) -> None:
    path = write_profile(tmp_path / "session.json", build_session({"duel": "s"}, {}))
    restored = Session.load(path)
    assert restored.captured_at is not None
    assert restored.cf_bm_at is not None
    assert restored.is_stale() is False


# ------------------------------------------------------------------ CLI status


def test_session_status_reports_a_missing_profile(tmp_path: Path, capsys) -> None:
    profile = tmp_path / "absent.json"
    assert automation_cli.main(["--profile", str(profile), "session-status"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["exists"] is False
    assert "capture_session.py" in payload["advice"]


def test_session_status_passes_a_fresh_session(tmp_path: Path, capsys) -> None:
    session = Session(
        cookies={"duel": "s", "__cf_bm": "bm"},
        username="alice",
        user_id=42,
        captured_at=time.time(),
        cf_bm_at=time.time(),
    )
    profile = write_profile(tmp_path / "session.json", session)
    assert automation_cli.main(["--profile", str(profile), "session-status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale"] is False
    assert payload["username"] == "alice"
    assert payload["user_id"] == 42
    assert payload["age_minutes"] < 1.0


def test_session_status_flags_an_expired_session(tmp_path: Path, capsys) -> None:
    old = time.time() - CF_BM_TTL_SECONDS - 120
    profile = write_profile(
        tmp_path / "session.json",
        Session(
            cookies={"duel": "s", "__cf_bm": "bm"},
            username="alice",
            user_id=42,
            captured_at=old,
            cf_bm_at=old,
        ),
    )
    assert automation_cli.main(["--profile", str(profile), "session-status"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale"] is True
    assert payload["age_minutes"] > payload["ttl_minutes"]
    assert "403" in payload["advice"]


def test_session_status_treats_an_untimestamped_profile_as_unknown(tmp_path: Path, capsys) -> None:
    """A legacy profile must not be reported as fresh just because it has cookies."""
    profile = write_profile(
        tmp_path / "session.json",
        Session(
            cookies={"duel": "s", "__cf_bm": "bm"}, username="alice", user_id=42
        ),
    )
    # Advisory, not fatal: stale is unknown rather than True, so exit 0.
    assert automation_cli.main(["--profile", str(profile), "session-status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["stale"] is None
    assert payload["age_minutes"] is None
    assert "unknown" in payload["advice"]


def test_session_status_explains_a_missing_duel_cookie(tmp_path: Path, capsys) -> None:
    """Cookies without `duel` means unauthenticated, and login() cannot fix it."""
    profile = write_profile(
        tmp_path / "session.json",
        Session(cookies={"__cf_bm": "bm"}, captured_at=time.time()),
    )
    assert automation_cli.main(["--profile", str(profile), "session-status"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["has_duel_cookie"] is False
    assert "Turnstile" in payload["advice"]


def test_session_status_command_is_registered() -> None:
    actions = {
        action.dest
        for action in automation_cli.build_parser()._subparsers._group_actions
    }
    assert "command" in actions


# ------------------------------------------------------------------- warnings


def _client_for(session: Session, tmp_path: Path) -> DuelClient:
    profile = write_profile(tmp_path / "session.json", session)
    return DuelClient.from_profile(profile)


def test_warn_if_stale_reports_an_expired_session(tmp_path: Path, capsys) -> None:
    old = time.time() - CF_BM_TTL_SECONDS - 60
    with _client_for(
        Session(cookies={"duel": "s", "__cf_bm": "bm"}, captured_at=old, cf_bm_at=old),
        tmp_path,
    ) as client:
        automation_cli._warn_if_stale(client)
    message = capsys.readouterr().err
    assert message.startswith("warning:")
    assert "past its" in message and "TTL" in message


def test_warn_if_stale_gives_advance_notice_near_the_ttl(tmp_path: Path, capsys) -> None:
    """Warn before expiry, not only after the 403 has already happened."""
    recent = time.time() - STALE_WARNING_SECONDS - 60
    with _client_for(
        Session(
            cookies={"duel": "s", "__cf_bm": "bm"},
            captured_at=recent,
            cf_bm_at=recent,
        ),
        tmp_path,
    ) as client:
        automation_cli._warn_if_stale(client)
    message = capsys.readouterr().err
    assert message.startswith("note:")
    assert "expires at" in message


def test_warn_if_stale_says_nothing_about_a_fresh_session(tmp_path: Path, capsys) -> None:
    now = time.time()
    with _client_for(
        Session(
            cookies={"duel": "s", "__cf_bm": "bm"}, captured_at=now, cf_bm_at=now
        ),
        tmp_path,
    ) as client:
        automation_cli._warn_if_stale(client)
    assert capsys.readouterr().err == ""


def test_warn_if_stale_flags_an_untimestamped_session(tmp_path: Path, capsys) -> None:
    with _client_for(
        Session(cookies={"duel": "s", "__cf_bm": "bm"}), tmp_path
    ) as client:
        automation_cli._warn_if_stale(client)
    assert "no timestamp" in capsys.readouterr().err


def test_warn_if_stale_stays_quiet_without_a_duel_cookie(tmp_path: Path, capsys) -> None:
    """An unauthenticated session's real problem is auth, not cookie age."""
    with _client_for(Session(cookies={"__cf_bm": "bm"}), tmp_path) as client:
        automation_cli._warn_if_stale(client)
    assert capsys.readouterr().err == ""


def test_quiet_suppresses_the_warning(tmp_path: Path, capsys) -> None:
    old = time.time() - CF_BM_TTL_SECONDS - 60
    with _client_for(
        Session(cookies={"duel": "s", "__cf_bm": "bm"}, captured_at=old, cf_bm_at=old),
        tmp_path,
    ) as client:
        automation_cli._warn_if_stale(client, quiet=True)
    assert capsys.readouterr().err == ""


def test_quiet_flag_is_accepted_by_the_parser() -> None:
    args = automation_cli.build_parser().parse_args(
        ["--quiet", "--profile", "x.json", "session-status"]
    )
    assert args.quiet is True
    assert args.profile == "x.json"


def test_session_status_does_not_read_a_cookie_as_authentication(tmp_path: Path, capsys) -> None:
    """A live probe showed ``duel`` present with ``user: null``.

    Cookie presence is not evidence of identity. capture does not record
    identity at all, so this branch fires for every captured profile and must
    neither claim the session is authenticated nor claim it is anonymous - only
    that whoami is authoritative.
    """
    now = time.time()
    profile = write_profile(
        tmp_path / "session.json",
        Session(cookies={"duel": "s", "__cf_bm": "bm"}, captured_at=now, cf_bm_at=now),
    )
    assert automation_cli.main(["--profile", str(profile), "session-status"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["has_duel_cookie"] is True
    assert payload["has_identity"] is False
    advice = payload["advice"]
    assert "whoami" in advice, "must point at the authoritative check"
    assert "cannot be confirmed offline" in advice
    assert "is not authenticated" not in advice, "would overstate what is known"
    assert "anonymous" not in advice, "would also overstate what is known"


def test_session_status_reports_identity_from_either_field(tmp_path: Path, capsys) -> None:
    """username alone, or user_id alone, is enough to count as an identity."""
    for session in (
        Session(cookies={"duel": "s"}, username="alice", captured_at=time.time()),
        Session(cookies={"duel": "s"}, user_id=42, captured_at=time.time()),
    ):
        profile = write_profile(tmp_path / "s.json", session)
        automation_cli.main(["--profile", str(profile), "session-status"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["has_identity"] is True
