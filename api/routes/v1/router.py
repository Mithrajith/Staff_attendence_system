from fastapi import APIRouter

from api.routes.v1 import admin, attendance, auth, departments, face, users

router = APIRouter()
router.include_router(auth.router)
router.include_router(users.router)
router.include_router(departments.router)
router.include_router(attendance.router)
router.include_router(face.router)
router.include_router(admin.router)
