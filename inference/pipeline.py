"""
RealtimeFacePipeline: orchestrates YOLO detection -> batched ResNet
recognition -> Qdrant identity lookup -> Redis de-dupe/attendance for a
single video frame.

This is the "hot path" shared by the WebSocket stream and the single-shot
REST endpoints, so detector/recognizer models are only ever loaded once.
"""

import logging
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np

from inference import config
from inference.services.face_detection_service import get_detector
from inference.services.face_recognition_service import get_recognizer
from inference.services.redis_service import get_redis

logger = logging.getLogger("inference.pipeline")


@dataclass
class FaceResult:
    box: tuple
    detection_confidence: float
    identity: str
    recognition_confidence: float
    emp_name: Optional[str] = None
    department: Optional[str] = None


@dataclass
class FrameResult:
    faces: List[FaceResult] = field(default_factory=list)
    processed_at: float = field(default_factory=time.time)


def _lookup_employee(emp_id: str) -> Optional[dict]:
    """Best-effort lookup against the Django sqlite DB shared by this project."""
    try:
        conn = sqlite3.connect(config.DJANGO_DB_PATH)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT emp_name, department FROM Home_employee WHERE emp_id = ?", (emp_id,))
            row = cursor.fetchone()
            if row:
                return {"emp_name": row[0], "department": row[1]}
            return None
        finally:
            conn.close()
    except sqlite3.Error as exc:
        logger.warning("Employee lookup failed for %s: %s", emp_id, exc)
        return None


def _save_attendance(emp_id: str, detection_time: str, check_type: str) -> None:
    conn = sqlite3.connect(config.DJANGO_DB_PATH)
    try:
        cursor = conn.cursor()
        current_date = datetime.now().strftime("%Y-%m-%d")
        cursor.execute(
            "SELECT id, time_in_list, time_out_list FROM Home_attendance WHERE emp_id = ? AND date = ?",
            (emp_id, current_date),
        )
        record = cursor.fetchone()

        if record:
            attendance_id, time_in, time_out = record
            if check_type == "check_in":
                updated = f"{time_in},{detection_time}" if time_in else detection_time
                cursor.execute("UPDATE Home_attendance SET time_in_list = ? WHERE id = ?", (updated, attendance_id))
            else:
                updated = f"{time_out},{detection_time}" if time_out else detection_time
                cursor.execute("UPDATE Home_attendance SET time_out_list = ? WHERE id = ?", (updated, attendance_id))
        else:
            if check_type == "check_in":
                cursor.execute(
                    "INSERT INTO Home_attendance (date, emp_id, time_in_list, time_out_list) VALUES (?, ?, ?, ?)",
                    (current_date, emp_id, detection_time, ""),
                )
            else:
                cursor.execute(
                    "INSERT INTO Home_attendance (date, emp_id, time_in_list, time_out_list) VALUES (?, ?, ?, ?)",
                    (current_date, emp_id, "", detection_time),
                )
        conn.commit()
    finally:
        conn.close()


class RealtimeFacePipeline:
    def __init__(self):
        self.detector = get_detector()
        self.recognizer = get_recognizer()
        self.redis = get_redis()

    def process_frame(
        self,
        frame_bgr: np.ndarray,
        connection_id: str = "global",
        mark_attendance_action: Optional[str] = None,
    ) -> FrameResult:
        """Run the full detect -> recognize -> (optional) attendance pipeline.

        `mark_attendance_action`, when set to "check_in" or "check_out",
        auto-marks attendance for recognized identities (subject to a Redis
        debounce window so repeated frames don't spam the attendance table).
        """
        detections = self.detector.detect(frame_bgr)
        if not detections:
            return FrameResult(faces=[])

        crops = []
        valid_detections = []
        h, w = frame_bgr.shape[:2]
        for det in detections:
            x1, y1, x2, y2 = map(int, det.box)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            crops.append(frame_bgr[y1:y2, x1:x2])
            valid_detections.append(det)

        if not crops:
            return FrameResult(faces=[])

        embeddings = self.recognizer.embed_batch(crops)
        results = self.recognizer.identify_batch(embeddings)

        faces: List[FaceResult] = []
        for det, result in zip(valid_detections, results):
            face = FaceResult(
                box=det.box,
                detection_confidence=det.confidence,
                identity=result.identity,
                recognition_confidence=result.confidence,
            )

            if result.identity != "Unknown":
                employee = _lookup_employee(result.identity)
                if employee:
                    face.emp_name = employee["emp_name"]
                    face.department = employee["department"]

                # De-duped broadcast: only publish once per cache TTL per connection+identity.
                if not self.redis.was_recently_seen(connection_id, result.identity):
                    self.redis.mark_recently_seen(connection_id, result.identity)
                    self.redis.publish_event(
                        "face_recognized",
                        {
                            "emp_id": result.identity,
                            "emp_name": face.emp_name,
                            "department": face.department,
                            "confidence": result.confidence,
                            "connection_id": connection_id,
                        },
                    )

                if mark_attendance_action in ("check_in", "check_out"):
                    self._maybe_mark_attendance(result.identity, mark_attendance_action, face)

            faces.append(face)

        return FrameResult(faces=faces)

    def _maybe_mark_attendance(self, emp_id: str, action: str, face: FaceResult) -> None:
        if not self.redis.can_mark_attendance(emp_id, action):
            return

        self.redis.debounce_attendance(emp_id, action)
        now_time = datetime.now().strftime("%H:%M:%S")
        try:
            _save_attendance(emp_id, now_time, action)
        except Exception as exc:
            logger.error("Failed to save attendance for %s: %s", emp_id, exc)
            return

        self.redis.publish_event(
            "attendance_marked",
            {
                "emp_id": emp_id,
                "emp_name": face.emp_name,
                "department": face.department,
                "action": action,
                "time": now_time,
            },
        )


_pipeline_singleton: Optional[RealtimeFacePipeline] = None


def get_pipeline() -> RealtimeFacePipeline:
    global _pipeline_singleton
    if _pipeline_singleton is None:
        _pipeline_singleton = RealtimeFacePipeline()
    return _pipeline_singleton
