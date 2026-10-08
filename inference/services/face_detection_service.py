"""
Ultra-fast real-time face detection using a YOLOv8 face-tuned model.

Design goals:
    * Low per-frame latency (single forward pass, no NMS surprises, half
      precision on GPU when available).
    * GPU is optional: everything works on CPU, just slower.
    * Auto-downloads the pretrained face weights on first run so the service
      is usable out of the box.
"""

import logging
import os
import threading
import urllib.request
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
from ultralytics import YOLO

from inference import config

logger = logging.getLogger("inference.face_detection")


@dataclass
class Detection:
    box: tuple  # (x1, y1, x2, y2) in pixel coordinates
    confidence: float


def _download_weights(url: str, destination: str) -> None:
    logger.info("Downloading YOLO face model from %s", url)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    tmp_path = destination + ".part"
    try:
        urllib.request.urlretrieve(url, tmp_path)
        os.replace(tmp_path, destination)
        logger.info("Saved YOLO face model to %s", destination)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


class YOLOFaceDetector:
    """Thin, thread-safe wrapper around an Ultralytics YOLO face model."""

    def __init__(
        self,
        model_path: str = config.YOLO_FACE_MODEL_PATH,
        model_url: str = config.YOLO_FACE_MODEL_URL,
        device=config.DEVICE,
        conf_threshold: float = config.DETECTION_CONF_THRESHOLD,
        img_size: int = config.DETECTION_IMG_SIZE,
        half: bool = config.USE_HALF_PRECISION,
    ):
        if not os.path.exists(model_path):
            _download_weights(model_url, model_path)

        self.device = device
        self.conf_threshold = conf_threshold
        self.img_size = img_size
        self.half = half
        self._lock = threading.Lock()

        self.model = YOLO(model_path)
        try:
            self.model.to(self.device)
        except Exception as exc:  # pragma: no cover - depends on local hardware
            logger.warning("Could not move YOLO model to %s (%s); staying on CPU.", self.device, exc)

        logger.info(
            "YOLO face detector ready (device=%s, half=%s, img_size=%s)",
            self.device,
            self.half,
            self.img_size,
        )

    def warmup(self) -> None:
        """Run one dummy inference so the first real frame isn't slow."""
        dummy = np.zeros((self.img_size, self.img_size, 3), dtype=np.uint8)
        try:
            self.detect(dummy)
        except Exception as exc:  # pragma: no cover
            logger.warning("YOLO warmup failed (non-fatal): %s", exc)

    def detect(self, frame_bgr: np.ndarray) -> List[Detection]:
        """Run detection on a single BGR frame (as read by OpenCV).

        Returns bounding boxes sorted by area, largest first, capped at
        `config.MAX_FACES_PER_FRAME` to bound downstream recognition cost.
        """
        if frame_bgr is None or frame_bgr.size == 0:
            return []

        with self._lock:
            results = self.model.predict(
                frame_bgr,
                imgsz=self.img_size,
                conf=self.conf_threshold,
                device=self.device,
                half=self.half,
                verbose=False,
            )[0]

        boxes = results.boxes
        if boxes is None or len(boxes) == 0:
            return []

        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()

        detections = [
            Detection(box=tuple(map(float, xyxy[i])), confidence=float(confs[i]))
            for i in range(len(xyxy))
        ]
        detections.sort(
            key=lambda d: (d.box[2] - d.box[0]) * (d.box[3] - d.box[1]),
            reverse=True,
        )
        return detections[: config.MAX_FACES_PER_FRAME]


_detector_singleton: Optional[YOLOFaceDetector] = None
_singleton_lock = threading.Lock()


def get_detector() -> YOLOFaceDetector:
    """Process-wide singleton so the model is loaded exactly once."""
    global _detector_singleton
    if _detector_singleton is None:
        with _singleton_lock:
            if _detector_singleton is None:
                _detector_singleton = YOLOFaceDetector()
                _detector_singleton.warmup()
    return _detector_singleton