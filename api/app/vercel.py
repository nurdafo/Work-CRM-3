"""Serve the CRM frontend and API together from one Vercel Python function."""

from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .main import app as crm_api


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must be at least 32 characters")
    if os.getenv("VERCEL") and settings.database_url.startswith("sqlite"):
        raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database for Vercel")
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/api", crm_api)
app.mount("/", StaticFiles(directory=Path(__file__).resolve().parents[2] / "web" / "dist", html=True), name="web")
