"""
Health-check endpoint.

GET /health  — lightweight liveness probe (no DB)
GET /health/ready — readiness probe (checks DB connection)
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.config import get_settings
from app.database import check_db_connection

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: str


@router.get(
    "",
    response_model=HealthResponse,
    summary="Liveness probe",
    description="Returns 200 when the server process is running.",
)
async def health_live() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    description="Returns 200 only when the server AND the database are reachable.",
)
async def health_ready() -> ReadinessResponse:
    settings = get_settings()
    db_ok = await check_db_connection()
    return ReadinessResponse(
        status="ok" if db_ok else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        database="ok" if db_ok else "unavailable",
    )
