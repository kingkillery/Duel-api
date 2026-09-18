"""Sandbox scaffold tests (skipped unless the [sandbox] extra is installed)."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from sandbox.app import app

client = TestClient(app)


def test_index_serves_page() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "duel-api sandbox" in response.text


def test_simulate_defaults() -> None:
    response = client.post("/api/simulate", json={})
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 3
    assert data["kelly_fraction"] == pytest.approx(-0.001)
    assert "zero" in data["verdict"]


def test_simulate_is_deterministic() -> None:
    body = {"sessions": 300, "seed": 5}
    a = client.post("/api/simulate", json=body).json()
    b = client.post("/api/simulate", json=body).json()
    assert a["rows"] == b["rows"]


def test_simulate_rejects_absurd_sessions() -> None:
    assert client.post("/api/simulate", json={"sessions": 10**9}).status_code == 422
    assert client.post("/api/simulate", json={"edge": 0.9}).status_code == 422
