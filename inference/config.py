"""
Central configuration for the real-time face detection / recognition service.

All settings are environment-driven (see `.env.example`) so the same code can
run on a GPU box or a plain CPU laptop without edits. GPU is strictly optional:
if CUDA is not available (or not working), everything falls back to CPU.
"""

import os
import logging

import torch
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("inference.config")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))
MODELS_DIR = os.path.join(BASE_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)


def _get_safe_device() -> torch.device:
    """Use CUDA only if it is actually usable; otherwise fall back to CPU.

    GPU is an optional accelerator for this service, never a hard requirement.
    """
    force_cpu = os.getenv("FORCE_CPU", "false").lower() == "true"
    if force_cpu:
        return torch.device("cpu")

    if torch.cuda.is_available():
        try:
            probe = torch.zeros(1, device="cuda")
            _ = probe + 1
            return torch.device("cuda:0")
        except Exception as exc:  # pragma: no cover - depends on local hardware
            logger.warning("CUDA reported available but unusable (%s); using CPU.", exc)
    return torch.device("cpu")


DEVICE = _get_safe_device()
USE_HALF_PRECISION = DEVICE.type == "cuda" and os.getenv("USE_FP16", "true").lower() == "true"

# ---------------------------------------------------------------------------
# YOLO face detector
# ---------------------------------------------------------------------------
YOLO_FACE_MODEL_NAME = os.getenv("YOLO_FACE_MODEL", "yolov8n-face.pt")
YOLO_FACE_MODEL_PATH = os.path.join(MODELS_DIR, YOLO_FACE_MODEL_NAME)
YOLO_FACE_MODEL_URL = os.getenv(
    "YOLO_FACE_MODEL_URL",
    f"https://github.com/akanametov/yolo-face/releases/download/1.0.0/{YOLO_FACE_MODEL_NAME}",
)
DETECTION_CONF_THRESHOLD = float(os.getenv("DETECTION_CONF_THRESHOLD", "0.5"))
DETECTION_IMG_SIZE = int(os.getenv("DETECTION_IMG_SIZE", "640"))
MAX_FACES_PER_FRAME = int(os.getenv("MAX_FACES_PER_FRAME", "5"))

# ---------------------------------------------------------------------------
# ResNet (facenet-pytorch InceptionResnetV1) recognizer
# ---------------------------------------------------------------------------
RECOGNITION_SCORE_THRESHOLD = float(os.getenv("RECOGNITION_SCORE_THRESHOLD", "0.55"))
FACE_EMBEDDING_SIZE = 160

# ---------------------------------------------------------------------------
# Qdrant
# ---------------------------------------------------------------------------
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "face_embeddings")

# ---------------------------------------------------------------------------
# Redis (pub/sub broadcast + recent-recognition cache + attendance debounce)
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_EVENTS_CHANNEL = os.getenv("REDIS_EVENTS_CHANNEL", "face_recognition_events")
RECOGNITION_CACHE_TTL = int(os.getenv("RECOGNITION_CACHE_TTL", "5"))  # seconds, de-dupe identical hits
ATTENDANCE_DEBOUNCE_TTL = int(os.getenv("ATTENDANCE_DEBOUNCE_TTL", "60"))  # seconds between repeat check-in/out

# ---------------------------------------------------------------------------
# Misc / storage paths
# ---------------------------------------------------------------------------
DJANGO_DB_PATH = os.path.join(REPO_ROOT, "db.sqlite3")
MEDIA_DIR = os.path.join(REPO_ROOT, "media")
JSON_EMBEDDINGS_EXPORT = os.path.join(BASE_DIR, "face_embeddings.json")

# WebSocket streaming tunables
WS_FRAME_QUEUE_SIZE = 1  # always process only the latest frame -> true real-time, no backlog
WS_RESULT_HEARTBEAT_INTERVAL = float(os.getenv("WS_HEARTBEAT_INTERVAL", "15"))

# ---------------------------------------------------------------------------
# Testing sandbox (separate from the real Django media/Qdrant records)
# ---------------------------------------------------------------------------
TESTING_DIR = os.path.join(REPO_ROOT, "testing")
TESTING_UPLOADS_DIR = os.path.join(TESTING_DIR, "uploads")
TESTING_METADATA_FLAG = "test_sandbox"  # Qdrant payload key marking sandbox-only embeddings

