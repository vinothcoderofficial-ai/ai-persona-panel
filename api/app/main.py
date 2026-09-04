"""FastAPI application entrypoint.

Wires together db.py (SQLModel/SQLite storage + startup seeding) and the
planograms/variants/sessions/experiments routers. resolve() itself lives
only in api/app/resolve.py - there is no client-side resolver; the web app
always calls GET /variants/{id}/resolved.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.app.db import init_db, seed_all
from api.app.routers import experiments, planograms, sessions, variants


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    seed_all()
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
