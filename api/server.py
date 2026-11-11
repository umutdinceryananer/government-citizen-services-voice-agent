"""
Government Citizen Services API server.

Run:
    uvicorn api.server:app --reload --port 8001
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from api.auth import router as auth_router
from api.handoff import router as handoff_router, guest_router
from api.services import (
    router as services_router,
    public_router as services_public_router,
    admin_router as services_admin_router,
)
from api.models import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Government Citizen Services API",
    version="0.1.0",
    description="Mock government backend — authentication, application status, appointments.",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(handoff_router)
app.include_router(guest_router)
app.include_router(services_router)
app.include_router(services_public_router)
app.include_router(services_admin_router)


@app.get("/health")
def health():
    return {"status": "ok"}
