"""Filesystem locations that differ between a source checkout and an install.

``site_spec.json`` lives at the repository root in a checkout, but an installed
package has no such directory. Every entry point therefore resolves the spec
through :func:`default_spec_path`, which prefers a local file (so a developer can
edit the spec without reinstalling) and otherwise falls back to the copy shipped
with the distribution.
"""

from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path

SPEC_FILENAME = "site_spec.json"
DEFAULT_PROFILE = Path(".private-api-automation/profiles/default/session.json")
_SHARE_SUBDIR = ("share", "duel-api")


def package_dir() -> Path:
    """Directory holding the installed modules (or the source checkout)."""
    return Path(__file__).resolve().parent


def _data_dir_candidates() -> list[Path]:
    """Where a wheel's ``data-files`` land once installed."""
    out: list[Path] = []
    for scheme in ("data", "purelib", "platlib"):
        try:
            root = sysconfig.get_path(scheme)
        except KeyError:
            continue
        if root:
            out.append(Path(root).joinpath(*_SHARE_SUBDIR))
    prefix = getattr(sys, "prefix", None)
    if prefix:
        out.append(Path(prefix).joinpath(*_SHARE_SUBDIR))
    return out


def default_spec_path() -> Path:
    """Resolve the spec: local override, then beside the modules, then install data.

    The working-directory copy wins so a developer can point the tool at an
    edited spec without reinstalling; the packaged copies are what let an
    installed ``duel-api`` work from any directory.
    """
    local = Path(SPEC_FILENAME)
    if local.is_file():
        return local
    beside = package_dir() / SPEC_FILENAME
    if beside.is_file():
        return beside
    for candidate in _data_dir_candidates():
        spec_file = candidate / SPEC_FILENAME
        if spec_file.is_file():
            return spec_file
    return local


def default_profile_path() -> Path:
    """Session profile path, honouring ``DUEL_PROFILE`` when it is set."""
    override = os.environ.get("DUEL_PROFILE")
    return Path(override) if override else DEFAULT_PROFILE