"""FastAPI wrapper around the offline demo engine.

One endpoint, one page. ``sessions`` is capped so a hosted instance cannot be
used as a CPU firehose, and every input is clamped rather than trusted.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import demo

app = FastAPI(
    title="duel-api sandbox",
    description="A backtester whose headline output is *don't bet*. "
    "The engine is network-free by AST guard; this service adds no network use either.",
    version="0.1.0",
)

MAX_SESSIONS = 50_000
MAX_BASE = 10_000.0
MIN_EDGE, MAX_EDGE = 0.0, 0.05

STATIC = Path(__file__).resolve().parent / "static"


class SimulateRequest(BaseModel):
    edge: float = Field(default=demo.BEST_OBSERVED_EDGE, ge=MIN_EDGE, le=MAX_EDGE)
    sessions: int = Field(default=10_000, ge=100, le=MAX_SESSIONS)
    base: float = Field(default=0.50, gt=0.0, le=MAX_BASE)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.post("/api/simulate")
def simulate(req: SimulateRequest) -> JSONResponse:
    try:
        result = demo.run(
            edge=req.edge, sessions=req.sessions, base=req.base, seed=req.seed
        )
    except Exception as exc:  # engine raises typed config errors; surface them
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=400)
    return JSONResponse(
        {
            "edge": result.edge,
            "sessions": result.sessions,
            "base": result.base,
            "rows": result.rows,
            "kelly_fraction": result.kelly_fraction,
            "verdict": result.verdict,
            "pooled_implied_edge": result.pooled_implied_edge,
        }
    )
