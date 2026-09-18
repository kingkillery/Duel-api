"""Environment and capability report behind ``duel-api doctor``.

Offline by design. ``doctor`` has to be safe to run before a session exists, with
no network, and with optional extras missing. It answers one question -- *what can
I run right now, and if not, exactly what is missing* -- so an absent optional
dependency never looks like a broken tool.

Nothing here imports ``automation_client`` at module scope: that module requires
``httpx``, and ``doctor`` must still be able to explain that httpx is absent.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

import paths
import theme

__all__ = ["CAPABILITIES", "TIERS", "Capability", "Tier", "collect", "render"]


@dataclass(frozen=True)
class Capability:
    label: str
    module: str
    extra: str | None
    unlocks: str


@dataclass(frozen=True)
class Tier:
    name: str
    commands: tuple[str, ...]
    needs: tuple[str, ...]
    note: str


CAPABILITIES: tuple[Capability, ...] = (
    Capability("httpx", "httpx", None, "every network command (core dependency)"),
    Capability("python-socketio", "socketio", "realtime", "betfeed read-only live-bet stream"),
    Capability("playwright", "playwright", "browser", "capture_session / cdp_token_capture browser capture"),
    Capability("rich", "rich", "pretty", "optional richer rendering"),
)

TIERS: tuple[Tier, ...] = (
    Tier(
        "offline",
        ("doctor", "spec", "session-status", "demo", "audit"),
        (),
        "no network, no session, no extras",
    ),
    Tier(
        "read-only live",
        ("whoami", "metadata", "games", "rates", "spec-check", "call GET"),
        ("httpx",),
        "needs a captured session",
    ),
    Tier(
        "realtime feed",
        ("betfeed",),
        ("httpx", "socketio"),
        "read-only; handshake is bot-gated",
    ),
    Tier(
        "browser capture",
        ("capture_session.py", "cdp_token_capture.py"),
        ("httpx", "playwright"),
        "scripts; needs Chrome on CDP",
    ),
    Tier(
        "state-changing",
        ("settings-update", "seed-set", "seed-rotate", "2fa-setup"),
        ("httpx",),
        "requires --yes",
    ),
    Tier(
        "live money",
        ("dice-bet", "autobet"),
        ("httpx",),
        "gated four deep - see betting-gates",
    ),
)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def install_mode() -> str:
    """Distinguish a checkout from an installed distribution."""
    here = paths.package_dir()
    for candidate in (here, here.parent):
        if (candidate / ".git").exists():
            return "source checkout"
    text = str(here).lower()
    if "site-packages" in text or "dist-packages" in text:
        return "installed"
    return "editable or unknown"


def _environment() -> dict:
    return {
        "python": platform.python_version(),
        "executable": sys.executable,
        "platform": platform.platform(),
        "install_mode": install_mode(),
        "package_dir": str(paths.package_dir()),
    }


def _spec() -> dict:
    path = paths.default_spec_path()
    info: dict = {"path": str(path), "found": path.is_file()}
    if not info["found"]:
        return info
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info
    provenance = (spec.get("metadata") or {}).get("provenance") or {}
    info.update(
        {
            "site_name": spec.get("site_name"),
            "base_url": spec.get("base_url"),
            "spec_version": spec.get("spec_version"),
            "bundle": provenance.get("bundle"),
            "bundle_sha256_prefix": provenance.get("bundle_sha256_prefix"),
            "endpoints": len(spec.get("endpoints") or []),
        }
    )
    return info


def _session(profile: Path) -> dict:
    info: dict = {"path": str(profile), "found": profile.is_file()}
    if not info["found"]:
        return info
    if not _has("httpx"):
        info["note"] = "cannot inspect: httpx is not installed"
        return info
    try:
        from automation_client import DuelClient

        status = DuelClient.from_profile(profile).session_status()
    except Exception as exc:
        info["note"] = f"{type(exc).__name__}: {exc}"
        return info
    info.update(
        {
            "has_duel_cookie": status.get("has_duel_cookie"),
            "age_minutes": status.get("age_minutes"),
            "ttl_minutes": status.get("ttl_minutes"),
            "stale": status.get("stale"),
        }
    )
    return info


def _next_step(report: dict) -> str:
    if not report["spec"]["found"]:
        return "site_spec.json not found - reinstall, or pass --spec PATH"
    if not any(c["installed"] for c in report["capabilities"] if c["module"] == "httpx"):
        return "install the core dependency: pip install duel-api   (or: pip install httpx)"
    session = report["session"]
    if not session["found"]:
        return "no session yet - capture one: python capture_session.py --cdp-url http://127.0.0.1:9223"
    if session.get("stale") is True:
        return "session bot cookie is past its TTL - re-capture: python capture_session.py --cdp-url http://127.0.0.1:9223"
    if session.get("stale") is None and session.get("has_duel_cookie"):
        return "session carries no timestamp, so its age is unknown - re-capture to stamp one"
    return "ready - try: duel-api whoami"


def collect(*, profile: Path | None = None) -> dict:
    """Gather the report. Reads the filesystem only; makes no network request."""
    profile = Path(profile) if profile is not None else paths.default_profile_path()
    capabilities = [
        {
            "label": c.label,
            "module": c.module,
            "extra": c.extra,
            "unlocks": c.unlocks,
            "installed": _has(c.module),
        }
        for c in CAPABILITIES
    ]
    available = {c["module"] for c in capabilities if c["installed"]}
    tiers = [
        {
            "name": t.name,
            "commands": list(t.commands),
            "needs": list(t.needs),
            "note": t.note,
            "runnable": all(need in available for need in t.needs),
        }
        for t in TIERS
    ]
    report = {
        "environment": _environment(),
        "spec": _spec(),
        "capabilities": capabilities,
        "session": _session(profile),
        "tiers": tiers,
    }
    report["next_step"] = _next_step(report)
    return report


def _session_line(session: dict) -> str:
    if not session["found"]:
        return theme.warn("none - no session captured yet")
    if "note" in session:
        return theme.warn(f"present, but unreadable ({session['note']})")
    age = session.get("age_minutes")
    ttl = session.get("ttl_minutes")
    stale = session.get("stale")
    if stale is True:
        return theme.red(f"present, {age} min old - past its ~{ttl} min TTL, re-capture")
    if stale is False:
        return theme.green(f"present, {age} min old - inside the ~{ttl} min TTL")
    return theme.warn("present, age unknown (no timestamp to compare)")


def _compact_commands(cmds: list[str], keep: int = 2) -> str:
    """First few commands plus a count, so summary rows stay under ~100 chars."""
    if len(cmds) <= keep:
        return ", ".join(cmds)
    return ", ".join(cmds[:keep]) + f" +{len(cmds) - keep}"


def render(report: dict) -> str:
    """Human-readable report. Colour and glyphs come from :mod:`theme`."""
    env = report["environment"]
    spec = report["spec"]
    session = report["session"]

    out: list[str] = [theme.bold("duel-api doctor"), theme.dim(theme.rule(72)), ""]

    out.append(theme.bold("environment"))
    out.append(theme.kv("python", f"{env['python']}  {theme.dim(env['executable'])}"))
    out.append(theme.kv("platform", env["platform"]))
    out.append(theme.kv("install", env["install_mode"]))
    out.append(theme.kv("package dir", theme.dim(env["package_dir"])))
    out.append("")

    out.append(theme.bold("spec"))
    if spec["found"]:
        out.append(theme.kv("path", theme.dim(spec["path"])))
        out.append(theme.kv("site", f"{spec.get('site_name')}  {theme.dim(spec.get('base_url') or '')}"))
        out.append(theme.kv("spec version", spec.get("spec_version")))
        out.append(
            theme.kv(
                "bundle",
                f"{spec.get('bundle')}  {theme.dim('sha256 prefix ' + str(spec.get('bundle_sha256_prefix')))}",
            )
        )
        out.append(theme.kv("endpoints", spec.get("endpoints")))
    else:
        out.append(theme.kv("path", theme.dim(spec["path"])))
        out.append(theme.kv("state", theme.fail("not found")))
    out.append("")

    out.append(theme.bold("capabilities"))
    rows = []
    for cap in report["capabilities"]:
        state = theme.green(theme.sym("ok")) if cap["installed"] else theme.yellow("missing")
        rows.append([cap["label"], state, cap["extra"] or theme.dim("core"), cap["unlocks"]])
    out.append(theme.table(["component", "state", "extra", "unlocks"], rows, aligns=("left", "center", "left", "left")))
    out.append("")

    out.append(theme.bold("session"))
    out.append(theme.kv("profile", theme.dim(session["path"])))
    out.append(theme.kv("state", _session_line(session)))
    if session["found"] and "has_duel_cookie" in session:
        cookie = session.get("has_duel_cookie")
        out.append(theme.kv("duel cookie", theme.green("present") if cookie else theme.red("absent")))
    out.append("")

    out.append(theme.bold("what you can run"))
    rows = []
    for tier in report["tiers"]:
        state = theme.green("yes") if tier["runnable"] else theme.dim("no")
        cap_by_mod = {c["module"]: c for c in report["capabilities"]}
        missing = [cap_by_mod.get(n, {}).get("label", n) for n in tier["needs"] if not cap_by_mod.get(n, {}).get("installed", False)]
        note = tier["note"]
        if missing:
            note = f"{theme.dim('needs ' + ', '.join(missing))} - {note}"
        rows.append([tier["name"], _compact_commands(tier["commands"]), state, note])
    out.append(theme.table(["tier", "commands", "ready", "notes"], rows, aligns=("left", "left", "center", "left")))
    out.append("")

    out.append(theme.bold("next step"))
    out.append(f"  {theme.cyan(report['next_step'])}")
    return "\n".join(out)