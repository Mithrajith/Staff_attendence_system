from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from api.core.config import get_settings

_templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates" / "web")

router = APIRouter(include_in_schema=False)

_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(self)",
    "Cache-Control": "no-store",
}


@router.get("/")
@router.get("/reset-password")  # target of the emailed link: /reset-password?token=...
def auth_page(request: Request):
    return _templates.TemplateResponse(
        request, "login.html", {"api": get_settings().api_prefix}, headers=_HEADERS
    )
