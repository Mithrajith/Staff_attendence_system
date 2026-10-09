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
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(self)",
    "Cache-Control": "no-store",
}

_ABOUT_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com; "
        "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com data:; "
        "img-src 'self' data: blob: https:; "
        "frame-ancestors 'none'"
    ),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cache-Control": "no-store",
}


@router.get("/about")
def about_page(request: Request):
    return _templates.TemplateResponse(
        request, "about.html", {"api": get_settings().api_prefix}, headers=_ABOUT_HEADERS
    )


@router.get("/")
@router.get("/reset-password")  # target of the emailed link: /reset-password?token=...
def auth_page(request: Request):
    return _templates.TemplateResponse(
        request, "login.html", {"api": get_settings().api_prefix}, headers=_HEADERS
    )


@router.get("/admin")  # the page is only a shell; every data call is authorized by the API
def admin_page(request: Request):
    return _templates.TemplateResponse(
        request, "admin.html", {"api": get_settings().api_prefix}, headers=_HEADERS
    )


@router.get("/portal")  # shell for staff: own status and attendance report; every call is authorized by the API
def portal_page(request: Request):
    settings = get_settings()
    return _templates.TemplateResponse(
        request, "portal.html", {"api": settings.api_prefix, "tz": settings.app_timezone}, headers=_HEADERS
    )


@router.get("/kiosk")  # same: a shell for the system user's check-in/out screen
def kiosk_page(request: Request):
    return _templates.TemplateResponse(
        request, "kiosk.html", {"api": get_settings().api_prefix}, headers=_HEADERS
    )

