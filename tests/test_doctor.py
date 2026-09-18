"""Tests for the doctor report, theme, and install path resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import automation_cli
import doctor
import paths
import theme


def test_paths_default_spec_path_finds_local_spec() -> None:
    path = paths.default_spec_path()
    assert path.is_file()
    assert path.name == "site_spec.json"


def test_paths_fallback_to_data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_share = tmp_path / "share" / "duel-api"
    fake_share.mkdir(parents=True)
    fake_spec = fake_share / "site_spec.json"
    fake_spec.write_text('{"site_name": "FallbackDuel"}', encoding="utf-8")

    # Force local and beside checks to fail so it falls back to data dir
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "package_dir", lambda: tmp_path / "fake_pkg")
    monkeypatch.setattr(paths, "_data_dir_candidates", lambda: [fake_share])

    resolved = paths.default_spec_path()
    assert resolved == fake_spec
    assert "FallbackDuel" in resolved.read_text(encoding="utf-8")

def test_paths_package_dir_is_directory() -> None:
    pkg = paths.package_dir()
    assert pkg.is_dir()
    assert (pkg / "automation_cli.py").is_file()


def test_theme_visible_len_strips_ansi() -> None:
    raw = "hello"
    styled = theme.style(raw, "bold", "red")
    assert theme.visible_len(styled) == len(raw)


def test_theme_table_formatting() -> None:
    headers = ["Name", "Status"]
    rows = [["item1", "ok"], ["item2", "pending"]]
    output = theme.table(headers, rows)
    assert "Name" in output
    assert "Status" in output
    assert "item1" in output
    assert "item2" in output


def test_doctor_collect_produces_valid_structure() -> None:
    report = doctor.collect()
    assert "environment" in report
    assert "spec" in report
    assert "capabilities" in report
    assert "session" in report
    assert "tiers" in report
    assert "next_step" in report

    # Must be strictly JSON-serializable (no unhandled Paths or custom objects)
    serialized = json.dumps(report)
    deserialized = json.loads(serialized)
    assert deserialized["environment"]["platform"]
    assert deserialized["spec"]["found"] is True
    assert len(deserialized["tiers"]) == 6


def test_doctor_render_produces_text() -> None:
    report = doctor.collect()
    rendered = doctor.render(report)
    assert "duel-api doctor" in rendered
    assert "environment" in rendered
    assert "capabilities" in rendered
    assert "what you can run" in rendered
    assert "next step" in rendered


def test_cli_doctor_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    code = automation_cli.main(["doctor"])
    assert code == 0
    captured = capsys.readouterr()
    assert "duel-api doctor" in captured.out
    assert "capabilities" in captured.out


def test_cli_doctor_json_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    code = automation_cli.main(["doctor", "--json"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "capabilities" in data
    assert "tiers" in data
    assert "environment" in data


def test_cli_spec_packaged_default(capsys: pytest.CaptureFixture[str]) -> None:
    # Running 'spec' without explicit '--spec' should resolve the default spec
    code = automation_cli.main(["spec"])
    assert code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data.get("site_name") == "Duel.com"
