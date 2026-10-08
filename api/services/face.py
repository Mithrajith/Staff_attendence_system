"""Face enrollment. Bridges the user API to the in-process inference service.

Inference imports (torch, ultralytics, ...) are lazy so the API starts without loading ML models
until a face feature is actually used. The face identity stored in the vector DB is `str(user.id)`.
"""

import logging

import numpy as np
from fastapi import HTTPException, status
from fastapi.concurrency import run_in_threadpool
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.core.config import get_settings
from database.models import User, utcnow

logger = logging.getLogger("api.face")

MIN_FACE_PX = 80
_VECTOR_ERRORS = (ResponseHandlingException, UnexpectedResponse, ConnectionError)


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Face database unavailable")


# --- seams over the inference service (patched in tests) -------------------------------------------------------


def extract_single_face(image_b64: str) -> np.ndarray | None:
    """Face crop if the image contains exactly one sufficiently large face, else None."""
    from inference.api import decode_base64_image
    from inference.pipeline import get_pipeline

    frame = decode_base64_image(image_b64)
    if frame is None:
        return None
    detections = get_pipeline().detector.detect(frame)
    if len(detections) != 1:
        return None
    x1, y1, x2, y2 = map(int, detections[0].box)
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if min(x2 - x1, y2 - y1) < MIN_FACE_PX:
        return None
    return frame[y1:y2, x1:x2]


def identify_scored(crops: list[np.ndarray]) -> list[tuple[str, float]]:
    """(identity, similarity) for each crop; identity is "Unknown" when nothing in the vector DB is close enough."""
    from inference.services.face_recognition_service import get_recognizer

    recognizer = get_recognizer()
    return [(r.identity, r.confidence) for r in recognizer.identify_batch(recognizer.embed_batch(crops))]


def identify_faces(crops: list[np.ndarray]) -> list[str]:
    """Identity (vector-DB emp_id) for each crop, or "Unknown"."""
    return [identity for identity, _ in identify_scored(crops)]


def count_faces(image_b64: str, min_ratio: float) -> int:
    """Faces at least `min_ratio` of the frame width wide (cheap detection only, no recognition)."""
    from inference.api import decode_base64_image
    from inference.pipeline import get_pipeline

    frame = decode_base64_image(image_b64)
    if frame is None:
        return 0
    min_px = min_ratio * frame.shape[1]
    return sum(1 for d in get_pipeline().detector.detect(frame) if d.box[2] - d.box[0] >= min_px)


def vector_count(user_id: int) -> int:
    from inference.services import quadrant_service

    return quadrant_service.count_embeddings(emp_id=str(user_id))


def delete_vectors(user_id: int) -> None:
    from inference.services import quadrant_service

    quadrant_service.delete_by_filter(emp_id=str(user_id), wait=True)


async def register(user_id: int, images: list[str]) -> int:
    """Calls the inference service's register-staff-faces endpoint handler. Returns embeddings stored."""
    from inference.api import RegisterStaffFacesRequest, register_staff_faces

    result = await register_staff_faces(RegisterStaffFacesRequest(emp_id=str(user_id), images=images))
    return int(result["embeddings_stored"])


# --- orchestration ---------------------------------------------------------------------------------------------


def _validate(user: User, images: list[str]) -> list[str]:
    """Pre-ingest checks: face quality + vector-DB duplicates. Returns the usable images."""
    s = get_settings()
    if len(images) > s.face_enroll_max_images:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"At most {s.face_enroll_max_images} images allowed")

    try:
        if vector_count(user.id) > 0:
            raise HTTPException(status.HTTP_409_CONFLICT, "Face already enrolled for this account")

        usable, crops = [], []
        for img in images:
            crop = extract_single_face(img)
            if crop is not None:
                usable.append(img)
                crops.append(crop)
        if len(usable) < s.face_enroll_min_images:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"Need at least {s.face_enroll_min_images} clear photos with exactly one face "
                f"({len(usable)} usable). Retake in good lighting.",
            )
        if any(identity not in ("Unknown", str(user.id)) for identity in identify_faces(crops)):
            raise HTTPException(status.HTTP_409_CONFLICT, "This face is already registered to another account")
        return usable
    except _VECTOR_ERRORS:
        logger.exception("Vector DB error during enrollment validation")
        raise _unavailable()


async def enroll(db: Session, user: User, images: list[str]) -> int:
    # MySQL check first: cheapest, and the authoritative "already enrolled" flag.
    if user.face_enrolled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Face already enrolled for this account")

    usable = await run_in_threadpool(_validate, user, images)

    # Row lock so two concurrent enrollments for the same user can't both ingest.
    locked = db.scalar(
        select(User).where(User.id == user.id).with_for_update().execution_options(populate_existing=True)
    )
    if locked.face_enrolled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Face already enrolled for this account")
    try:
        stored = await register(user.id, usable)
        if stored < get_settings().face_enroll_min_images:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Could not process enough photos. Please retry")
    except Exception as exc:
        # No partial identities left behind in the vector DB.
        try:
            await run_in_threadpool(delete_vectors, user.id)
        except Exception:
            logger.exception("Failed to clean up partial embeddings for user %s", user.id)
        db.rollback()
        if isinstance(exc, _VECTOR_ERRORS):
            logger.exception("Vector DB error during enrollment")
            raise _unavailable()
        raise
    locked.face_enrolled_at = utcnow()
    db.commit()
    return stored
