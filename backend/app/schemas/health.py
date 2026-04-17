from pydantic import BaseModel


class HealthResponse(BaseModel):
    ok: bool
    service: str
    sessions_persistence: bool = False
