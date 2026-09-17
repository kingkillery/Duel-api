"""Validate the captured site_spec.json against the methodology contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SPEC_PATH = Path(__file__).resolve().parents[1] / "site_spec.json"


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def test_spec_file_exists() -> None:
    assert SPEC_PATH.is_file(), "site_spec.json must sit next to the client"


def test_required_top_level_fields(spec: dict) -> None:
    assert spec["site_name"] == "Duel.com"
    assert spec["base_url"] == "https://duel.com"
    assert isinstance(spec["endpoints"], list) and spec["endpoints"]


def test_every_endpoint_has_id_label_and_request(spec: dict) -> None:
    seen = set()
    for ep in spec["endpoints"]:
        assert ep["id"], "endpoint id is required"
        assert ep["id"] not in seen, f"duplicate endpoint id: {ep['id']}"
        seen.add(ep["id"])
        assert ep["label"]
        req = ep["request"]
        assert req["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE"}
        assert req["url"].startswith("https://duel.com/")


def test_every_endpoint_targets_api_v2(spec: dict) -> None:
    for ep in spec["endpoints"]:
        assert "/api/v2/" in ep["request"]["url"], ep["id"]


def test_token_sources_cover_session_and_captcha(spec: dict) -> None:
    names = {ts["name"] for ts in spec["token_sources"]}
    assert "duel" in names, "the primary session cookie must be declared"
    assert "x-duel-device-identifier" in names
    assert "captcha_token" in names, "login is captcha-gated; declare the token source"


def test_captcha_is_declared_enabled(spec: dict) -> None:
    captcha = spec["metadata"]["captcha"]
    assert captcha["enabled"] is True
    assert captcha["provider"] == "Cloudflare Turnstile"


def test_auth_failure_signals_include_401_and_403(spec: dict) -> None:
    codes = {s.get("status_code") for s in spec["auth_failure_signals"]}
    assert {401, 403} <= codes
    assert any(s.get("value") == "/?logout" for s in spec["auth_failure_signals"])


def test_headless_reauth_is_disabled(spec: dict) -> None:
    """Unattended re-auth is impossible while captcha_on_login is on."""
    ladder = {step["kind"]: step for step in spec["recovery_ladder"]}
    assert ladder["headless_reauth"]["enabled"] is False
    assert ladder["fail"]["enabled"] is True


def test_wagering_is_explicitly_gated(spec: dict) -> None:
    """Wagering exists only through reviewed, gated paths - never by accident.

    Dice betting is supported via the dedicated place_dice_bet() path
    (validated parameters, dry-run default, betting_enabled + confirm +
    max_stake gates). Everything else money-moving stays banned: no endpoint
    id/URL may structurally touch deposit/withdrawal, and the generic
    request()/call path still refuses all money verbs unconditionally (see
    test_money_moving_paths_are_refused_even_with_writes_enabled and
    test_generic_request_still_refuses_the_dice_bet_path).
    """
    meta = spec["metadata"]
    assert meta["out_of_scope"], "money-moving endpoints must be explicitly excluded"
    assert meta["out_of_scope_reason"]
    assert meta.get("wagering"), "the gated wagering path must be documented"
    assert meta["wagering"]["gates"], "the gates must be enumerated"

    by_id = {ep["id"]: ep for ep in spec["endpoints"]}
    assert by_id["dice.bet"]["request"]["method"] == "POST"
    assert by_id["dice.bet"]["likely_action"] is True
    assert by_id["dice.config"]["request"]["method"] == "GET"

    banned = ("deposit", "withdraw")
    for ep in spec["endpoints"]:
        structural = f"{ep['id']} {ep['request']['url'].split('?')[0]}".lower()
        # A read-only listing of available methods is fine; submission is not.
        if structural.rstrip("/").endswith("methods"):
            continue
        # The reviewed dice path is allowlisted; everything else is checked.
        if ep["id"] in ("dice.bet", "dice.config"):
            continue
        for verb in banned:
            assert verb not in structural, f"{ep['id']} touches '{verb}'"
        # Every non-GET endpoint must be explicitly reviewed and listed here.
        # Session lifecycle plus account management plus the gated dice bet -
        # nothing else moves state.
        if ep["request"]["method"] != "GET":
            reviewed_writes = {
                "metadata.socket-token",  # token fetch, not a mutation
                "auth.login",
                "auth.logout",
                "user.settings.update",
                "client-seed.set",
                "client-seed.rotate",
                "user.security.token",
                "user.security.two-factor-setup",
                "dice.bet",  # gated: place_dice_bet() only, dry-run default
            }
            assert ep["id"] in reviewed_writes, (
                f"unreviewed non-GET endpoint: {ep['id']}"
            )

def test_storage_layout_matches_project(spec: dict) -> None:
    layout = spec["storage"]
    assert layout["root_dir_name"] == ".private-api-automation"
    assert layout["spec_file"] == "site_spec.json"
    assert (SPEC_PATH.parent / layout["spec_file"]).is_file()


def test_validates_against_methodology_schema(spec: dict) -> None:
    """Validate against the source schema when it is available locally."""
    schema_path = (
        Path.home()
        / "AppData/Local/Temp/private-api-automation/src/private_api_automation/contracts/site_spec.schema.json"
    )
    if not schema_path.is_file():
        pytest.skip("methodology schema not present locally")

    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.validate(spec, schema)