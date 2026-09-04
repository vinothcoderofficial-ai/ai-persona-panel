"""FastAPI application entrypoint.

Wires together db.py (SQLModel/SQLite storage + startup seeding) and the
planograms/variants/sessions/experiments/whatif routers. resolve() itself lives
only in api/app/resolve.py - there is no client-side resolver; the web app
always calls GET /variants/{id}/resolved.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.db import init_db, seed_all
from api.app.routers import experiments, planograms, sessions, variants, whatif, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_all()
    # POST /whatif has a p95 < 1,000 ms budget, so the fixed parts of it - the persona
    # policies and the unpatched baseline every lift is measured against - are computed
    # here rather than on the first request. Best effort: see whatif.warm_up().
    whatif.warm_up()
    yield


app = FastAPI(title="ShopperTwin API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(planograms.router)
app.include_router(variants.router)
app.include_router(sessions.router)
app.include_router(experiments.router)
app.include_router(whatif.router)
app.include_router(ws.router)
