#!/usr/bin/env python3
"""Command-line front end for the Duel.com private-API client.

Examples
--------
    python automation_cli.py spec
    python automation_cli.py metadata
    python automation_cli.py whoami
    python automation_cli.py session-status
    python automation_cli.py import-session captures/session.json
    python automation_cli.py games --limit 5
    python automation_cli.py call GET /api/v2/user/settings

Betting is gated: only `dice-bet` can move money (dry-run by default; a live
bet needs --yes, --enable-betting, --live and --confirm-bet together). See README.md.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from automation_client import (
    DEFAULT_PROFILE,
    STALE_WARNING_SECONDS,
    AuthRequired,
    CaptchaRequired,
    CloudflareChallenge,
    DuelClient,
    DuelError,
    Session,
    UnsupportedAction,
    WriteNotAllowed,
)

from decimal import Decimal

from bankroll import BankrollPolicy, EdgeRefused, PlayPolicy


def _emit(value: object) -> None:
    print(json.dumps(value, indent=1, sort_keys=True, default=str))


def _warn_if_stale(client: DuelClient, *, quiet: bool = False) -> None:
    """Warn when the bot cookie is expired or close to it.

    Fires only when the session carries a timestamp, or is provably expired. An
    untimestamped profile is reported as age-unknown rather than as fresh, since
    guessing would hide exactly the failure this exists to explain.
    """
    if quiet:
        return
    status = client.session_status()
    stale = status["stale"]
    age = status["age_minutes"]
    if stale:
        print(
            f"warning: session bot cookie is {age} min old, past its "
            f"~{status['ttl_minutes']} min TTL, so a 403 is likely. Re-capture "
            "with capture_session.py.",
            file=sys.stderr,
        )
    elif age is not None and age >= STALE_WARNING_SECONDS / 60.0:
        print(
            f"note: session bot cookie is {age} min old and expires at "
            f"~{status['ttl_minutes']} min.",
            file=sys.stderr,
        )
    elif stale is None and status["has_duel_cookie"]:
        print(
            "warning: session carries no timestamp, so its age is unknown and a "
            "403 would not be diagnosable. Re-capture to stamp one.",
            file=sys.stderr,
        )


def _client(args: argparse.Namespace) -> DuelClient:
    """Build a client, honouring the global ``--yes`` write opt-in."""
    profile = Path(args.profile)
    writes = bool(getattr(args, "yes", False))
    if profile.exists():
        client = DuelClient.from_profile(profile, allow_writes=writes)
        _warn_if_stale(client, quiet=bool(getattr(args, "quiet", False)))
        return client
    client = DuelClient(profile=profile, allow_writes=writes)
    # Seed the device uuid / cookie jar without authenticating.
    try:
        client.metadata()
        client.save()
    except DuelError as exc:
        print(f"warning: bootstrap failed: {exc}", file=sys.stderr)
    return client


def cmd_spec(args: argparse.Namespace) -> int:
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    _emit(
        {
            "site_name": spec["site_name"],
            "base_url": spec["base_url"],
            "spec_version": spec.get("spec_version"),
            "endpoints": [
                {
                    "id": ep["id"],
                    "label": ep["label"],
                    "method": ep["request"]["method"],
                    "url": ep["request"]["url"],
                    "likely_action": ep.get("likely_action", False),
                }
                for ep in spec.get("endpoints", [])
            ],
            "token_sources": [ts["name"] for ts in spec.get("token_sources", [])],
            "out_of_scope": spec.get("metadata", {}).get("out_of_scope", []),
        }
    )
    return 0


def cmd_spec_check(args: argparse.Namespace) -> int:
    """Compare the live SPA bundle hash to the spec's provenance record.

    Exit 0 means the bundle the spec was recovered from is still what the site
    serves. Exit 1 means it drifted (every captured endpoint is suspect) or the
    spec records no hash to compare against.
    """
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    with _client(args) as client:
        result = client.check_spec_drift(spec)
    _emit(result)
    if result["drifted"] is None:
        print(f"spec-check: {result.get('reason', 'unknown')}", file=sys.stderr)
        return 1
    return 0 if result["drifted"] is False else 1


def cmd_betfeed(args: argparse.Namespace) -> int:
    """Listen to the read-only /livebetfeed namespace and record raw events.

    Connects the way the SPA does (uid/token handshake query params, identify
    on connect), forwards every server event to the optional JSONL sink, and
    prints a per-event tally. --duration or Ctrl-C ends the listen cleanly.
    The listener never emits anything except the protocol-mandated identify.
    """
    try:
        import socketio
    except ImportError:
        print(
            'betfeed needs python-socketio: pip install "python-socketio[client]"',
            file=sys.stderr,
        )
        return 1

    from collections import Counter

    from realtime.betfeed import BetFeedClient, JsonlEventSink, auth_from_client

    counts: Counter = Counter()
    sink = JsonlEventSink(args.out) if args.out else None

    def on_event(event: str, data: list, observed_at: float) -> None:
        counts[event] += 1
        if sink is not None:
            sink(event, data, observed_at)

    def on_state(state: str) -> None:
        if not args.quiet:
            print(f"[betfeed] {state}", file=sys.stderr)

    with _client(args) as client:
        feed = BetFeedClient(
            socketio.Client(reconnection=True, reconnection_delay=2.0),
            lambda: auth_from_client(client),
            on_event=on_event,
            on_state=on_state,
        )
        # Browser-parity handshake headers: the endpoint is gated by an
        # engine.io middleware that denies non-browser requests, so pass
        # everything a real handshake would carry and surface the refusal
        # honestly if it still comes.
        headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in client.session.cookies.items()),
            "x-duel-device-identifier": client.session.device_uuid,
            "x-env-class": "main",
        }
        try:
            feed.connect(headers=headers)
            feed.wait(args.duration)
        except Exception as exc:
            print(
                f"[betfeed] connect failed: {exc}\n"
                "the BetFeed handshake is gated by the site's anti-bot middleware; "
                "this listener does not bypass it (see realtime/betfeed.py)",
                file=sys.stderr,
            )
            return 1
        finally:
            feed.disconnect()
    _emit(
        {
            "events": sum(counts.values()),
            "by_event": dict(counts.most_common()),
            "recorded_to": str(args.out) if args.out else None,
        }
    )
    return 0 if counts else 1


def cmd_dice_bet(args: argparse.Namespace) -> int:
    """Place one dice bet - or dry-run it (the default).

    REAL MONEY. Automated wagering almost certainly violates the operator's
    terms, risks account closure and forfeiture, and can lose funds fast -
    check the terms first, never stake more than you can afford to lose, and
    prefer dry runs while developing. If gambling stops being fun, stop.

    A dry run validates the parameters and prints the would-be payload
    without sending anything; it never needs --security-token. A live bet
    needs all five gates: --yes (write opt-in) + --enable-betting +
    --confirm-bet + --security-token + --live, and the stake must fit
    --max-stake.
    """
    token = args.security_token or None
    if args.live and token is None:
        raise argparse.ArgumentError(
            None,
            "Live dice bet requires --security-token (obtain from the page's network request).",
        )
    try:
        if not args.live:
            with _client(args) as client:
                result = client.place_dice_bet(
                    args.amount,
                    side=args.side,
                    currency=args.currency,
                    target=args.target,
                    security_token=token,
                )
            _emit(result)
            return 0
        missing = [
            flag
            for flag, present in (
                ("--yes", getattr(args, "yes", False)),
                ("--enable-betting", args.enable_betting),
                ("--confirm-bet", args.confirm_bet),
            )
            if not present
        ]
        if missing:
            print(
                f"dice-bet: a live bet needs {' '.join(missing)} "
                "(dry run is the default; run without --live to validate only)",
                file=sys.stderr,
            )
            return 1
        print(
            "REAL-MONEY dice bet: automated wagering almost certainly violates the "
            "operator's terms and can lose funds fast. Proceeding only because "
            "--yes, --enable-betting, --live, --confirm-bet and --security-token "
            "were all supplied.",
            file=sys.stderr,
        )
        with _client(args) as client:
            client.betting_enabled = bool(args.enable_betting)
            client.max_stake = args.max_stake
            policy = edge = None
            if args.edge_guard and args.entertainment:
                raise ValueError(
                    "--edge-guard and --entertainment are mutually exclusive: the first "
                    "refuses negative-edge play, the second labels and caps it. Pick one."
                )
            if args.edge_guard:
                cap = args.session_loss_cap
                policy = BankrollPolicy(
                    session_loss_cap=Decimal(str(cap)) if cap is not None else None
                )
                edge = client.dice_edge(target=args.target, side=args.side)
                client.bankroll = client.balance_for(args.currency)
            elif args.entertainment:
                if args.session_loss_cap is None:
                    raise ValueError(
                        "--entertainment requires --session-loss-cap: play mode is defined "
                        "by its budget, not by its stake."
                    )
                policy = PlayPolicy(
                    stake=Decimal(str(args.amount)),
                    session_loss_cap=Decimal(str(args.session_loss_cap)),
                )
                print(
                    "entertainment mode: this game has no edge to capture (EV < 0). "
                    f"Fixed stake {args.amount} every round, hard stop at "
                    f"{args.session_loss_cap} realised loss. The expected value is "
                    "negative by design - you are buying rounds, not returns.",
                    file=sys.stderr,
                )
            result = client.place_dice_bet(
                args.amount,
                side=args.side,
                currency=args.currency,
                target=args.target,
                security_token=token,
                confirm=args.confirm_bet,
                dry_run=False,
                policy=policy,
                edge=edge,
            )
            client.save()
        _emit(result if isinstance(result, dict) else {"result": result})
        return 0
    except EdgeRefused as exc:
        print(f"dice-bet: refused by the bankroll policy: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"dice-bet: invalid parameters: {exc}", file=sys.stderr)
        return 1



def cmd_metadata(args: argparse.Namespace) -> int:
    with _client(args) as client:
        doc = client.metadata(refresh_session=args.new_uuid)
        client.save()
        user = doc.get("user")
        _emit(
            {
                "device_uuid": client.session.device_uuid,
                "session_id": doc.get("session_id"),
                "authenticated": bool(user),
                "user": {"id": user.get("id"), "username": user.get("username")} if user else None,
                "captcha_on_login": (doc.get("features") or {}).get("captcha_on_login"),
                "turnstile_sitekey": doc.get("turnstile_sitekey"),
                "feature_count": len(doc.get("features") or {}),
                "cookies": sorted(k for k, v in client.session.cookies.items() if v),
            }
        )
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    if not Path(args.profile).exists():
        _emit({"authenticated": False, "reason": "no captured session", "profile": args.profile})
        return 1
    with DuelClient.from_profile(args.profile) as client:
        try:
            user = (client.metadata() or {}).get("user")
        except DuelError as exc:
            _emit({"authenticated": False, "reason": str(exc)})
            return 1
        _emit({"authenticated": bool(user), "user": user})
        return 0 if user else 1


def cmd_session_status(args: argparse.Namespace) -> int:
    """Offline session health report. Deliberately makes no request.

    Exit status: 0 when the session is usable as-is (has a ``duel`` cookie and
    is not provably expired), 1 otherwise. A 1 is advisory - ``auto_refresh``
    may still rescue an expired bot cookie on the first real call.
    """
    profile = Path(args.profile)
    if not profile.exists():
        _emit(
            {
                "profile": str(profile),
                "exists": False,
                "advice": "no captured session; run capture_session.py",
            }
        )
        return 1
    with DuelClient.from_profile(profile) as client:
        status = client.session_status()
    status["profile"] = str(profile)
    status["exists"] = True
    _emit(status)
    return 0 if status["has_duel_cookie"] and status["stale"] is not True else 1


def cmd_login(args: argparse.Namespace) -> int:
    token = args.captcha_token or os.environ.get("DUEL_CAPTCHA_TOKEN", "")
    password = args.password or os.environ.get("DUEL_PASSWORD", "")
    if not password:
        print("error: pass --password or set DUEL_PASSWORD", file=sys.stderr)
        return 2
    with DuelClient(profile=args.profile) as client:
        result = client.login(args.identifier, password, captcha_token=token)
        _emit({"ok": True, "user": (result or {}).get("user")})
    return 0


def cmd_import_session(args: argparse.Namespace) -> int:
    """Import a session captured from a real browser (see capture_session.py)."""
    session = Session.load(args.file)
    client = DuelClient(session, profile=args.profile)
    try:
        user = (client.metadata() or {}).get("user")
    except DuelError as exc:
        print(f"error: imported session not usable: {exc}", file=sys.stderr)
        return 1
    if user:
        session.username = user.get("username")
        session.user_id = user.get("id")
    session.save(args.profile)
    _emit({"ok": True, "authenticated": bool(user), "user": user, "saved_to": args.profile})
    return 0 if user else 1


def _non_negative(value: str) -> int:
    """argparse type for a count or offset that cannot be negative.

    ``--limit -5`` used to slice ``items[:-5]``, which silently returned a
    *smaller, arbitrary* subset rather than rejecting the argument - and
    ``--start`` was passed straight through to the API.
    """
    try:
        parsed = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if parsed < 0:
        raise argparse.ArgumentTypeError(f"must be zero or greater, got {parsed}")
    return parsed


def cmd_games(args: argparse.Namespace) -> int:
    """Game catalogue listing.

    Live shape is ``{success, data: {category, items, total}}`` - ``items`` holds
    the games.  Emit a compact digest rather than the raw multi-megabyte blob.
    """
    with _client(args) as client:
        payload = client.games(start=args.start, game_filter=args.filter, provider=args.provider)

    body = payload.get("data", payload) if isinstance(payload, dict) else {}
    items = body.get("items") or []
    category = body.get("category") or {}

    digest = [
        {
            "id": g.get("id"),
            "name": g.get("name"),
            "code": g.get("code"),
            "provider": (g.get("provider") or {}).get("name"),
            "type": g.get("type"),
            "rtp": g.get("overridden_rtp") or g.get("rtp"),
        }
        for g in items[: args.limit]
    ]
    _emit(
        {
            "category": category.get("name") or args.filter,
            "total": body.get("total"),
            "returned": len(digest),
            "games": digest,
        }
    )
    return 0


def cmd_rates(args: argparse.Namespace) -> int:
    with _client(args) as client:
        _emit(client.exchange_rates())
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    """Escape hatch: call any documented path directly."""
    body = json.loads(args.body) if args.body else None
    with _client(args) as client:
        _emit(client.request(args.method, args.path, json_body=body))
    return 0


def cmd_settings_update(args: argparse.Namespace) -> int:
    """PATCH /api/v2/user/settings - account settings (not wagering)."""
    settings = json.loads(args.settings)
    with _client(args) as client:
        _emit(client.update_settings(settings))
    return 0


def cmd_seed_set(args: argparse.Namespace) -> int:
    """POST /api/v2/client-seed - set the provably-fair client seed."""
    with _client(args) as client:
        _emit(client.set_client_seed(args.seed))
    return 0


def cmd_seed_rotate(args: argparse.Namespace) -> int:
    """POST /api/v2/client-seed/rotate - rotate the provably-fair client seed."""
    with _client(args) as client:
        _emit(client.rotate_client_seed(args.seed))
    return 0


def cmd_2fa_setup(args: argparse.Namespace) -> int:
    """POST /api/v2/user/security/two-factor-setup."""
    with _client(args) as client:
        _emit(client.two_factor_setup())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="automation_cli.py",
        description=(
            "Duel.com private-API client. Reads are open; state-changing commands "
            "require --yes. No wagering: that surface is deliberately absent."
        ),
    )
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE), help="session profile path")
    parser.add_argument("--spec", default="site_spec.json", help="path to site_spec.json")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="opt in to state-changing calls (required by the action commands)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress session-age warnings on stderr",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("spec", help="summarise site_spec.json").set_defaults(func=cmd_spec)
    p = sub.add_parser(
        "spec-check",
        help="compare the live SPA bundle hash to the spec's provenance record",
    )
    p.add_argument("--json", action="store_true", help="accepted for consistency; output is always JSON")
    p.set_defaults(func=cmd_spec_check)
    p = sub.add_parser(
        "betfeed",
        help="listen to the read-only live-bet feed namespace and record events",
    )
    p.add_argument("--duration", type=float, default=60.0, help="seconds to listen before disconnecting")
    p.add_argument("--out", default=None, help="append raw events to this JSONL file")
    p.set_defaults(func=cmd_betfeed)
    p = sub.add_parser(
        "dice-bet",
        help=(
            "place one dice bet (REAL MONEY; dry-run by default; --live needs "
            "--confirm-bet and --security-token)"
        ),
    )
    p.add_argument("--amount", required=True, help="stake as a decimal string, e.g. 0.5")
    p.add_argument("--side", required=True, choices=["OVER", "UNDER"], help="roll over or under the target")
    p.add_argument("--currency", required=True, help="currency code, e.g. USDT")
    p.add_argument("--target", required=True, help="roll target x100 as an integer string, e.g. 5005 for 50.05")
    p.add_argument(
        "--security-token",
        type=str,
        help="Security token required for live bets (generated by the Duel web page)",
    )
    p.add_argument("--max-stake", type=float, default=1.0, help="client-side per-bet cap in the bet currency")
    p.add_argument("--enable-betting", action="store_true", help="opt this client into real-money betting")
    p.add_argument("--live", action="store_true", help="actually send the bet (default is a dry run)")
    p.add_argument("--confirm-bet", action="store_true", help="per-call confirmation for the live bet")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the would-be payload without sending (the default; no token needed)",
    )
    p.add_argument(
        "--edge-guard",
        action="store_true",
        help=(
            "apply the bankroll stake-sizing rules before sending: derive the edge from "
            "the live config, size against the balance in the bet currency, and refuse "
            "the bet when the measured edge is below the EV floor (see bankroll.py)"
        ),
    )
    p.add_argument(
        "--session-loss-cap",
        type=float,
        default=None,
        help="with --edge-guard, stop once realised session loss reaches this amount",
    )
    p.add_argument(
        "--entertainment",
        action="store_true",
        help=(
            "explicitly play a negative-EV game for entertainment: fixed stake every "
            "round (never raised after a loss) with a mandatory --session-loss-cap. "
            "Labelled, not sold as a strategy - see PlayPolicy in bankroll.py"
        ),
    )
    p.set_defaults(func=cmd_dice_bet)
    sub.add_parser("whoami", help="report authentication status").set_defaults(func=cmd_whoami)
    sub.add_parser(
        "session-status",
        help="report session age and staleness offline (makes no request)",
    ).set_defaults(func=cmd_session_status)

    p = sub.add_parser("metadata", help="bootstrap/session document (public)")
    p.add_argument("--new-uuid", action="store_true", help="mint a fresh device uuid first")
    p.set_defaults(func=cmd_metadata)

    p = sub.add_parser("login", help="log in (needs an externally obtained captcha token)")
    p.add_argument("identifier", help="username or email")
    p.add_argument("--password", default=None, help="or set DUEL_PASSWORD")
    p.add_argument("--captcha-token", default=None, help="Turnstile token; or set DUEL_CAPTCHA_TOKEN")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("import-session", help="import a browser-captured session file")
    p.add_argument("file", help="path to captured session JSON")
    p.set_defaults(func=cmd_import_session)

    p = sub.add_parser("games", help="game catalogue listing (public)")
    p.add_argument("--start", type=_non_negative, default=0)
    p.add_argument("--filter", default="popular")
    p.add_argument("--provider", default="")
    p.add_argument("--limit", type=_non_negative, default=10)
    p.set_defaults(func=cmd_games)

    sub.add_parser("rates", help="exchange rates (public)").set_defaults(func=cmd_rates)

    p = sub.add_parser("call", help="call any documented path directly")
    p.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
    p.add_argument("path", help="e.g. /api/v2/user/settings")
    p.add_argument("--body", default=None, help="JSON request body")
    p.set_defaults(func=cmd_call)

    p = sub.add_parser("settings-update", help="PATCH /api/v2/user/settings (needs --yes)")
    p.add_argument("settings", help="JSON object of settings to merge")
    p.set_defaults(func=cmd_settings_update)

    p = sub.add_parser("seed-set", help="POST /api/v2/client-seed (needs --yes)")
    p.add_argument("seed", help="new provably-fair client seed")
    p.set_defaults(func=cmd_seed_set)

    p = sub.add_parser("seed-rotate", help="POST /api/v2/client-seed/rotate (needs --yes)")
    p.add_argument("seed", help="new provably-fair client seed")
    p.set_defaults(func=cmd_seed_rotate)

    sub.add_parser("2fa-setup", help="POST /api/v2/user/security/two-factor-setup (needs --yes)").set_defaults(
        func=cmd_2fa_setup
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except argparse.ArgumentError as exc:
        # Subcommand-level argument validation (e.g. a live dice-bet without
        # --security-token) reports through the same channel argparse uses.
        print(f"argument error: {exc}", file=sys.stderr)
        return 2
    except CaptchaRequired as exc:
        print(f"captcha required: {exc}", file=sys.stderr)
        return 3
    except CloudflareChallenge as exc:
        print(f"cloudflare challenge: {exc}", file=sys.stderr)
        return 4
    except AuthRequired as exc:
        print(f"auth required: {exc}", file=sys.stderr)
        return 5
    except WriteNotAllowed as exc:
        print(f"write not allowed: {exc}\n(hint: pass --yes to opt in)", file=sys.stderr)
        return 6
    except UnsupportedAction as exc:
        print(f"unsupported action: {exc}", file=sys.stderr)
        return 7
    except DuelError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())