from fastapi import APIRouter

from api.routes.v1 import attendance, auth, departments, face, users

router = APIRouter()
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(departments.router)
router.include_router(attendance.router)
router.include_router(face.router)
