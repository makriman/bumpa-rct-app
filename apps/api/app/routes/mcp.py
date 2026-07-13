from fastapi import APIRouter, Depends

from app.core.config import Settings, get_settings
from app.core.dependencies import Principal, require_tenant
from app.services.mcp_oauth import registry as provider_registry

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.get("/registry")
def registry(
    _principal: Principal = Depends(require_tenant),
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    return provider_registry(settings)
