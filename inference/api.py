"""
Real-time face detection & recognition service.

Core pipeline: YOLOv8 face detector -> InceptionResnetV1 (VGGFace2) embeddings
-> Qdrant nearest-neighbor identity search, with Redis providing pub/sub
broadcast of recognition/attendance events plus de-dupe and attendance
debounce. GPU is optional; everything also runs on CPU.

Primary interface: WebSocket streaming (continuous, low-latency). A small set
of REST endpoints are kept for backward compatibility with the existing
Django front-end (staff registration, single-shot check-in/out).
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import uuid
from contextlib import asynccontextmanager
from typing import List, Optional

import cv2
import numpy as np
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from inference import config
from inference.pipeline import get_pipeline
from inference.services import quadrant_service
from inference.services.face_recognition_service import get_recognizer
from inference.services.redis_service import get_redis
from inference.ws_manager import StreamSession, dashboard_broadcaster

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("inference.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load models / connect to external services once, at startup, so the
    # first WebSocket frame and the first REST call are both already warm.
    logger.info("Starting up: loading detection + recognition models ...")
    get_pipeline()
    logger.info("Models loaded. Verifying Qdrant and Redis connectivity ...")
    if not quadrant_service.health_check():
        logger.warning("Qdrant is not reachable at %s - recognition lookups will fail until it is.", config.QDRANT_URL)
    if not get_redis().health_check():
        logger.warning("Redis is not reachable at %s - events/caching/debounce will be degraded.", config.REDIS_URL)
    yield
    logger.info("Shutting down inference service.")


app = FastAPI(title="Realtime Face Attendance Service", lifespan=lifespan)

# When mounted inside the main API (INFERENCE_EMBEDDED=true) the main app owns CORS and authentication.
if os.getenv("INFERENCE_EMBEDDED", "false").lower() != "true":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # In production, replace with specific origins
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

os.makedirs(config.TESTING_UPLOADS_DIR, exist_ok=True)
app.mount("/testing-ui", StaticFiles(directory=config.TESTING_DIR, html=True), name="testing-ui")


def decode_base64_image(img_b64: str) -> Optional[np.ndarray]:
    try:
        if "," in img_b64:
            img_b64 = img_b64.split(",", 1)[1]
        img_bytes = base64.b64decode(img_b64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    except Exception as exc:
        logger.warning("Error decoding base64 image: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "device": str(config.DEVICE),
        "qdrant": quadrant_service.health_check(),
        "redis": get_redis().health_check(),
    }


# ---------------------------------------------------------------------------
# WebSocket: continuous real-time streaming from the client's camera
# ---------------------------------------------------------------------------
@app.websocket("/ws/stream/{connection_id}")
async def ws_stream(websocket: WebSocket, connection_id: str):
    """
    Protocol
    --------
    Client -> Server messages (JSON text frames):
        {"type": "init", "mode": "recognize_only" | "attendance", "action": "check_in" | "check_out"}
        {"type": "frame", "image": "<base64 jpeg/png>"}
    Client -> Server messages (binary frames):
        raw JPEG bytes, treated the same as a "frame" message.

    Server -> Client messages (JSON):
        {"type": "result", "faces": [...], "processing_ms": ...}
        {"type": "error", "message": "..."}
    """
    await websocket.accept()
    loop = asyncio.get_event_loop()
    session = StreamSession(connection_id=connection_id, loop=loop)
    session.start()

    async def sender():
        while True:
            payload = await session.result_queue.get()
            await websocket.send_json(payload)

    sender_task = asyncio.create_task(sender())
    try:
        while True:
            message = await websocket.receive()

            if message["type"] == "websocket.disconnect":
                break

            if "bytes" in message and message["bytes"] is not None:
                nparr = np.frombuffer(message["bytes"], np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is not None:
                    session.submit_frame(frame)
                continue

            if "text" in message and message["text"] is not None:
                try:
                    data = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type")
                if msg_type == "init":
                    session.mode = data.get("mode", "recognize_only")
                    session.action = data.get("action", "check_in")
                elif msg_type == "frame":
                    frame = decode_base64_image(data.get("image", ""))
                    if frame is not None:
                        session.submit_frame(frame)
    except WebSocketDisconnect:
        logger.info("WebSocket stream %s disconnected", connection_id)
    finally:
        sender_task.cancel()
        session.stop()


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    """Dashboard/monitoring feed: broadcasts every recognition/attendance event
    published to Redis, regardless of which /ws/stream connection produced it."""
    await websocket.accept()
    await dashboard_broadcaster.register(websocket)
    try:
        while True:
            # We don't expect inbound messages, but keep the socket alive and
            # detect client-initiated disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await dashboard_broadcaster.unregister(websocket)


# ---------------------------------------------------------------------------
# REST compatibility layer (used by the Django front-end)
# ---------------------------------------------------------------------------
class RecognizeFrameRequest(BaseModel):
    image: str
    action: Optional[str] = "check_in"


def _recognize_and_mark(frame: np.ndarray, action: str) -> dict:
    if frame is None:
        raise HTTPException(status_code=400, detail="Invalid or empty image frame provided.")

    pipeline = get_pipeline()
    result = pipeline.process_frame(frame, connection_id="rest", mark_attendance_action=action)

    if not result.faces:
        raise HTTPException(status_code=400, detail="No face detected. Please face the camera directly with good lighting.")

    best = max(result.faces, key=lambda f: f.recognition_confidence)
    if best.identity == "Unknown":
        raise HTTPException(status_code=400, detail="Face not recognized. Please ensure your face is registered in the system.")

    return {
        "status": "success",
        "message": f"{'Checked in' if action == 'check_in' else 'Checked out'} successfully",
        "action_type": action,
        "employee": {
            "name": best.emp_name,
            "id": best.identity,
            "department": best.department,
            "confidence": f"{best.recognition_confidence:.2%}",
        },
    }


@app.post("/recognize-frame")
async def recognize_frame(req: RecognizeFrameRequest):
    frame = decode_base64_image(req.image)
    return _recognize_and_mark(frame, req.action or "check_in")


class ImageActionRequest(BaseModel):
    image: str


@app.post("/check-in")
async def check_in(req: ImageActionRequest):
    frame = decode_base64_image(req.image)
    return _recognize_and_mark(frame, "check_in")


@app.post("/check-out")
async def check_out(req: ImageActionRequest):
    frame = decode_base64_image(req.image)
    return _recognize_and_mark(frame, "check_out")


class RegisterStaffFacesRequest(BaseModel):
    emp_id: str
    images: List[str]


@app.post("/register-staff-faces/")
async def register_staff_faces(request: RegisterStaffFacesRequest):
    """Detect+crop the face in each captured image, save it to media/, and
    ingest its embedding into Qdrant so the employee is immediately
    recognizable in real time."""
    emp_id = request.emp_id.strip()
    if not emp_id or not request.images:
        raise HTTPException(status_code=400, detail="Staff ID and images are required")

    media_base = os.path.abspath(os.path.join(config.REPO_ROOT, "..", "media"))
    if not os.path.exists(media_base):
        media_base = config.MEDIA_DIR

    staff_images_dir = os.path.join(media_base, emp_id, "images")
    profile_pics_dir = os.path.join(media_base, "profile_pics")
    os.makedirs(staff_images_dir, exist_ok=True)
    os.makedirs(profile_pics_dir, exist_ok=True)

    pipeline = get_pipeline()
    recognizer = get_recognizer()

    saved_count = 0
    embeddings_stored = 0
    profile_saved = False

    for idx, img_b64 in enumerate(request.images):
        frame = decode_base64_image(img_b64)
        if frame is None:
            continue

        detections = pipeline.detector.detect(frame)
        if detections:
            x1, y1, x2, y2 = map(int, detections[0].box)
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
            face_crop = frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else frame
        else:
            face_crop = frame

        img_path = os.path.join(staff_images_dir, f"image_{idx + 1}.jpg")
        cv2.imwrite(img_path, face_crop)
        cv2.imwrite(os.path.join(media_base, emp_id, f"img_{idx + 1}.jpg"), face_crop)
        saved_count += 1

        if not profile_saved:
            cv2.imwrite(os.path.join(profile_pics_dir, f"{emp_id}.jpg"), face_crop)
            profile_saved = True

        if face_crop is not None and face_crop.size > 0:
            try:
                recognizer.register(face_crop, emp_id=emp_id)
                embeddings_stored += 1
            except Exception as exc:
                logger.warning("Failed to store embedding for %s image %s: %s", emp_id, idx, exc)

    if saved_count == 0:
        raise HTTPException(status_code=400, detail="No valid face images could be processed. Please try capturing again.")

    logger.info("Stored %s face photos and %s embeddings for staff %s", saved_count, embeddings_stored, emp_id)
    return {
        "status": "success",
        "message": f"Successfully captured and stored {saved_count} face photos for staff {emp_id}",
        "images_saved": saved_count,
        "embeddings_stored": embeddings_stored,
        "images_directory": staff_images_dir,
    }


class BulkEmbeddingRequest(BaseModel):
    db_path: str


@app.post("/store_embeddings/")
def store_embeddings(request: BulkEmbeddingRequest):
    """Bulk-ingest a dataset directory (one sub-folder per emp_id, containing
    face images) into Qdrant."""
    if not os.path.exists(request.db_path):
        raise HTTPException(status_code=400, detail="Dataset path does not exist")

    recognizer = get_recognizer()
    pipeline = get_pipeline()
    identities_count = 0
    embeddings_count = 0

    for emp_id in os.listdir(request.db_path):
        if emp_id in ("profile_pics", "__pycache__") or emp_id.startswith("."):
            continue
        identity_path = os.path.join(request.db_path, emp_id)
        if not os.path.isdir(identity_path):
            continue

        image_files = []
        for root, _dirs, files in os.walk(identity_path):
            for f in sorted(files):
                if f.lower().endswith((".jpg", ".jpeg", ".png")):
                    image_files.append(os.path.join(root, f))

        stored_for_identity = 0
        for image_path in image_files[:10]:
            frame = cv2.imread(image_path)
            if frame is None:
                continue
            detections = pipeline.detector.detect(frame)
            if detections:
                x1, y1, x2, y2 = map(int, detections[0].box)
                h, w = frame.shape[:2]
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
                face_crop = frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else frame
            else:
                face_crop = frame

            try:
                recognizer.register(face_crop, emp_id=emp_id)
                stored_for_identity += 1
                embeddings_count += 1
            except Exception as exc:
                logger.warning("Error ingesting %s: %s", image_path, exc)

        if stored_for_identity:
            identities_count += 1

    return {
        "message": "Embeddings stored successfully in Qdrant",
        "identities_count": identities_count,
        "embeddings_count": embeddings_count,
    }


@app.get("/load_embeddings/")
def load_embeddings():
    """Return Qdrant collection stats (replaces the old flat-file dump)."""
    try:
        info = quadrant_service.get_collection_info()
        return {
            "collection": config.QDRANT_COLLECTION,
            "points_count": info.points_count,
            "status": str(info.status),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant unavailable: {exc}")


@app.post("/reload-embeddings")
async def reload_embeddings():
    """Kept for front-end compatibility. Identity data lives in Qdrant and is
    always queried live, so there is nothing to reload - this simply reports
    connectivity."""
    if not quadrant_service.health_check():
        raise HTTPException(status_code=503, detail="Qdrant is unreachable")
    return {"status": "success", "message": "Qdrant is live; embeddings are always up to date."}


# ---------------------------------------------------------------------------
# Testing sandbox: upload-to-train + real-time detect, fully isolated from
# the real Django media/Qdrant records so it can be wiped at any time.
# Backs the simple HTML page served at /testing-ui/.
# ---------------------------------------------------------------------------
@app.post("/testing/register")
async def testing_register(
    emp_id: str = Form(...),
    name: str = Form(""),
    files: List[UploadFile] = File(...),
):
    """Detect the face in each uploaded image and ingest its embedding into
    Qdrant tagged as sandbox data, so it is instantly recognizable in the
    real-time stream and can be cleanly removed later via /testing/clear."""
    emp_id = emp_id.strip()
    if not emp_id or not files:
        raise HTTPException(status_code=400, detail="emp_id and at least one file are required")

    pipeline = get_pipeline()
    recognizer = get_recognizer()

    person_dir = os.path.join(config.TESTING_UPLOADS_DIR, emp_id)
    os.makedirs(person_dir, exist_ok=True)

    saved = 0
    embedded = 0
    for upload in files:
        raw = await upload.read()
        nparr = np.frombuffer(raw, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            continue

        detections = pipeline.detector.detect(frame)
        if detections:
            x1, y1, x2, y2 = map(int, detections[0].box)
            h, w = frame.shape[:2]
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
            face_crop = frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else frame
        else:
            face_crop = None

        if face_crop is None or face_crop.size == 0:
            continue

        filename = f"{uuid.uuid4().hex}.jpg"
        cv2.imwrite(os.path.join(person_dir, filename), face_crop)
        saved += 1

        try:
            recognizer.register(
                face_crop,
                emp_id=emp_id,
                emp_name=name or emp_id,
                **{config.TESTING_METADATA_FLAG: True},
            )
            embedded += 1
        except Exception as exc:
            logger.warning("Testing sandbox: failed to embed upload for %s: %s", emp_id, exc)

    if embedded == 0:
        raise HTTPException(status_code=400, detail="No face could be detected in the uploaded image(s).")

    return {
        "status": "success",
        "emp_id": emp_id,
        "images_saved": saved,
        "embeddings_trained": embedded,
    }


@app.get("/testing/list")
def testing_list():
    """List identities currently trained in the sandbox, with embedding counts."""
    try:
        points = quadrant_service.list_embeddings(**{config.TESTING_METADATA_FLAG: True})
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Qdrant unavailable: {exc}")

    counts: dict = {}
    for point in points:
        payload = point["payload"]
        emp_id = payload.get("emp_id", point["id"])
        name = payload.get("emp_name", emp_id)
        entry = counts.setdefault(emp_id, {"emp_id": emp_id, "emp_name": name, "embeddings": 0})
        entry["embeddings"] += 1

    return {"identities": list(counts.values()), "total_embeddings": sum(v["embeddings"] for v in counts.values())}


@app.post("/testing/clear")
def testing_clear():
    """Remove all sandbox embeddings from Qdrant and delete uploaded test images."""
    try:
        quadrant_service.delete_by_filter(**{config.TESTING_METADATA_FLAG: True})
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Failed to clear Qdrant sandbox data: {exc}")

    if os.path.exists(config.TESTING_UPLOADS_DIR):
        shutil.rmtree(config.TESTING_UPLOADS_DIR)
    os.makedirs(config.TESTING_UPLOADS_DIR, exist_ok=True)

    return {"status": "success", "message": "Sandbox embeddings and uploaded images cleared."}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5600)
