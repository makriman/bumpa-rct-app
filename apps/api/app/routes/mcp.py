from fastapi import APIRouter, Depends

from app.core.dependencies import Principal, require_tenant

router = APIRouter(prefix="/mcp", tags=["mcp"])

REGISTRY = [
    {"provider": "google_drive", "name": "Google Drive", "enabled": False},
    {"provider": "google_sheets", "name": "Google Sheets", "enabled": False},
    {"provider": "gmail", "name": "Gmail", "enabled": False},
    {"provider": "calendar", "name": "Google Calendar", "enabled": False},
    {"provider": "meta_ads", "name": "Meta Ads", "enabled": False},
]


@router.get("/registry")
def registry(_principal: Principal = Depends(require_tenant)) -> list[dict]:
    return REGISTRY
