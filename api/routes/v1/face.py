from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.orm import Session

from api.core.config import get_settings
from api.deps import CurrentUser, Permission, require
from api.services import face as svc
from database.models import User
from database.session import get_db

router = APIRouter(prefix="/face", tags=["face"])

DB = Annotated[Session, Depends(get_db)]
# ~2 MB of base64 per photo is plenty for a 640x480 JPEG.
Photo = Annotated[str, StringConstraints(min_length=100, max_length=2_800_000)]


class EnrollRequest(BaseModel):
    images: list[Photo] = Field(min_length=1, max_length=20, description="Base64 JPEG/PNG (data URLs accepted)")


class EnrollResponse(BaseModel):
    enrolled: bool
    embeddings_stored: int


class EnrollmentInfo(BaseModel):
    enrolled: bool
    min_images: int
    max_images: int


@router.get("/enrollment", response_model=EnrollmentInfo)
def enrollment(user: CurrentUser) -> EnrollmentInfo:
    s = get_settings()
    return EnrollmentInfo(enrolled=user.face_enrolled, min_images=s.face_enroll_min_images, max_images=s.face_enroll_max_images)


@router.post("/enroll", response_model=EnrollResponse, status_code=status.HTTP_201_CREATED)
async def enroll(
    body: EnrollRequest,
    user: Annotated[User, Depends(require(Permission.attendance_mark_self))],
    db: DB,
) -> EnrollResponse:
    """Validates the photos, checks MySQL + the vector DB for an existing enrollment/face, then ingests."""
    stored = await svc.enroll(db, user, body.images)
    return EnrollResponse(enrolled=True, embeddings_stored=stored)
