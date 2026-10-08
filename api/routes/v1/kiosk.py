import logging
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.core.config import get_settings
from api.core.security import create_match_token, decode_match_token
from api.deps import CurrentUser, Permission, has_permission, require
from api.routes.models.kiosk import DetectOut, FrameIn, IdentifyOut, RecordIn, RecordOut
from api.services import attendance as attendance_svc
from api.services import face as face_svc
from database.models import AttendanceType, Role, User
from database.session import get_db

logger = logging.getLogger("api.kiosk")

router = APIRouter(prefix="/kiosk", tags=["kiosk"], dependencies=[Depends(require(Permission.kiosk_scan))])

DB = Annotated[Session, Depends(get_db)]


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Face recognition unavailable")


@router.post("/detect", response_model=DetectOut)
def detect(body: FrameIn) -> DetectOut:
    """Polled by the kiosk page (small frames) to know when someone is standing in front of the camera."""
    try:
        return DetectOut(faces=face_svc.count_faces(body.image, get_settings().kiosk_min_face_ratio))
    except Exception:
        logger.exception("Face detection failed")
        raise _unavailable()


def _person(user: User) -> dict:
    return dict(name=user.full_name, employee_id=user.employee_id, department=user.department.name if user.department else None)


def _eligible(user: User | None) -> bool:
    return user is not None and user.is_active and has_permission(user, Permission.attendance_mark_self)


@router.post("/identify", response_model=IdentifyOut)
def identify(body: FrameIn, db: DB, kiosk: CurrentUser) -> IdentifyOut:
    """Recognizes the one face in the frame. Records nothing: the person confirms with /record."""
    try:
        crop = face_svc.extract_single_face(body.image)
        if crop is None:
            return IdentifyOut(status="no_face")
        identity, confidence = face_svc.identify_scored([crop])[0]
    except Exception:
        logger.exception("Face recognition failed")
        raise _unavailable()

    # The vector-DB identity is the user id (see api/services/face.py).
    user = db.get(User, int(identity)) if identity.isdigit() else None
    if not _eligible(user):
        admin = db.scalar(select(User.full_name).where(User.role == Role.admin, User.is_active).order_by(User.id).limit(1))
        return IdentifyOut(status="unknown", contact=admin)
    return IdentifyOut(
        status="matched",
        match_token=create_match_token(user.id, kiosk.id, confidence),
        checked_in=attendance_svc.is_checked_in(db, user),
        **_person(user),
    )


@router.post("/record", response_model=RecordOut)
def record(body: RecordIn, db: DB, kiosk: CurrentUser) -> RecordOut:
    """Saves the action the person chose for the face that /identify just matched."""
    match = decode_match_token(body.match_token, kiosk.id)
    user = db.get(User, match["pid"]) if match else None
    if not _eligible(user):
        return RecordOut(status="expired")

    event = attendance_svc.record_scan(db, user, body.action, match.get("conf", 0.0))
    if event is None:
        rejected = "already_checked_in" if body.action == AttendanceType.check_in else "not_checked_in"
        return RecordOut(status=rejected, action=body.action, **_person(user))
    local = event.occurred_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(get_settings().app_timezone))
    return RecordOut(status="recorded", action=body.action, occurred_at=event.occurred_at, hour=local.hour, **_person(user))
