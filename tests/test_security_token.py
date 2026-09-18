import pytest
from httpx import Response

# Import helpers from existing tests
from tests.test_client import _betting_client, _recording_client, make_client

# -------------------------------------------------
# 1. test_security_token_included_in_dice_bet_payload

def test_security_token_included_in_dice_bet_payload() -> None:
    """When a security_token is supplied the POST payload includes it."""
    seen: list[tuple[str, str, dict | None]] = []
    with _betting_client(seen) as client:
        # Perform a live dice bet (dry_run=False, confirm=True)
        client.place_dice_bet(
            "0.5",
            bet_type="OVER",
            currency=109,
            target="5005",
            security_token="tok",
            confirm=True,
            dry_run=False,
        )
    # After the bet there should be exactly one outgoing request
    assert len(seen) == 1
    method, path, body = seen[0]
    assert method == "POST"
    assert path == "/api/v2/dice/bet"
    assert body is not None
    assert body["security_token"] == "tok"

# -------------------------------------------------
# 2. test_security_token_omitted_when_not_provided

def test_security_token_omitted_when_not_provided() -> None:
    """Without a provided token the key is omitted from the payload."""
    seen: list[tuple[str, str, dict | None]] = []
    with _betting_client(seen) as client:
        # Live bet with no explicit security_token
        client.place_dice_bet(
            "0.5",
            bet_type="OVER",
            currency=109,
            target="5005",
            confirm=True,
            dry_run=False,
        )
    assert len(seen) == 1
    _, _, body = seen[0]
    assert body is not None
    assert "security_token" not in body

# -------------------------------------------------
# 3. test_security_token_method_posts_to_correct_endpoint

def test_security_token_method_posts_to_correct_endpoint() -> None:
    """The security_token() method posts to the correct API endpoint.
    It must include the device_uuid and the requested token type.
    """
    seen: list[tuple[str, str, dict | None]] = []
    with _recording_client(seen, allow_writes=True) as client:
        client.session.device_uuid = "11111111-2222-4333-8444-555555555555"
        # token_type is the keyword used in the client implementation.
        client.security_token(token_type="standard")
    assert len(seen) == 1
    method, path, body = seen[0]
    assert method == "POST"
    assert path == "/api/v2/user/security/token"
    assert body == {
        "uuid": "11111111-2222-4333-8444-555555555555",
        "code": "0000",
        "type": "standard",
    }

# -------------------------------------------------
# 4. test_non_finite_stake_still_rejected

def test_non_finite_stake_still_rejected() -> None:
    """A non‑finite stake always raises ValueError even with a token supplied."""
    with make_client(lambda request: Response(200, json={})) as client:
        with pytest.raises(ValueError, match="finite decimal stake"):
            client.place_dice_bet(
                "NaN",
                bet_type="UNDER",
                currency="SOL",
                target="5005",
                security_token="tok",
            )

"""Additional imports that appear in the original test file are hidden.
"""
