from fastapi import APIRouter

from app.core.settings import load_settings
from app.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = load_settings()
    return HealthResponse(
        ok=True,
        service=settings.service_name,
        sessions_persistence=settings.sessions_persistence,
    )
