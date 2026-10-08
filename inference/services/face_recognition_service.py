"""
Face recognition: InceptionResnetV1 (facenet-pytorch, VGGFace2 weights) for
512-D embeddings, matched against Qdrant for fast approximate nearest-neighbor
identity search.
"""

import logging
import threading
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np
import torch
from facenet_pytorch import InceptionResnetV1, fixed_image_standardization

from inference import config
from inference.services import quadrant_service

logger = logging.getLogger("inference.face_recognition")


@dataclass
class RecognitionResult:
    identity: str  # emp_id, or "Unknown"
    confidence: float  # cosine similarity score, 0..1
    payload: dict


class FaceRecognizer:
    """Thread-safe embedding extractor + Qdrant identity lookup."""

    def __init__(
        self,
        device=config.DEVICE,
        score_threshold: float = config.RECOGNITION_SCORE_THRESHOLD,
    ):
        self.device = device
        self.score_threshold = score_threshold
        self._lock = threading.Lock()

        self.resnet = InceptionResnetV1(pretrained="vggface2").eval().to(self.device)
        quadrant_service.ensure_collection()

        logger.info("Face recognizer ready (device=%s, threshold=%s)", self.device, self.score_threshold)

    @staticmethod
    def preprocess(face_crop_bgr: np.ndarray) -> torch.Tensor:
        """Resize a BGR face crop to 160x160 and normalize for InceptionResnetV1."""
        face_rgb = cv2.cvtColor(face_crop_bgr, cv2.COLOR_BGR2RGB)
        face_rgb = cv2.resize(
            face_rgb,
            (config.FACE_EMBEDDING_SIZE, config.FACE_EMBEDDING_SIZE),
            interpolation=cv2.INTER_LINEAR,
        )
        tensor = torch.from_numpy(face_rgb).permute(2, 0, 1).float()
        return fixed_image_standardization(tensor)

    def embed_batch(self, face_crops_bgr: List[np.ndarray]) -> np.ndarray:
        """Compute embeddings for a batch of face crops in a single forward pass."""
        if not face_crops_bgr:
            return np.empty((0, 512), dtype=np.float32)

        batch = torch.stack([self.preprocess(crop) for crop in face_crops_bgr]).to(self.device)
        with self._lock, torch.no_grad():
            embeddings = self.resnet(batch)
        return embeddings.cpu().numpy()

    def embed(self, face_crop_bgr: np.ndarray) -> np.ndarray:
        return self.embed_batch([face_crop_bgr])[0]

    def identify(self, embedding: np.ndarray, **metadata_filters) -> RecognitionResult:
        """Look up the closest known identity for a single embedding via Qdrant."""
        matches = quadrant_service.search_embedding(
            embedding,
            top_k=1,
            score_threshold=self.score_threshold,
            **metadata_filters,
        )
        if not matches:
            return RecognitionResult(identity="Unknown", confidence=0.0, payload={})

        best = matches[0]
        emp_id = best["payload"].get("emp_id", best["id"])
        return RecognitionResult(identity=str(emp_id), confidence=best["score"], payload=best["payload"])

    def identify_batch(self, embeddings: np.ndarray) -> List[RecognitionResult]:
        return [self.identify(embeddings[i]) for i in range(len(embeddings))]

    def register(self, face_crop_bgr: np.ndarray, emp_id: str, **metadata) -> str:
        """Compute an embedding for a face crop and store it in Qdrant under emp_id."""
        embedding = self.embed(face_crop_bgr)
        return quadrant_service.ingest_embedding(embedding, emp_id=emp_id, **metadata)


_recognizer_singleton: Optional[FaceRecognizer] = None
_singleton_lock = threading.Lock()


def get_recognizer() -> FaceRecognizer:
    """Process-wide singleton so the model is loaded exactly once."""
    global _recognizer_singleton
    if _recognizer_singleton is None:
        with _singleton_lock:
            if _recognizer_singleton is None:
                _recognizer_singleton = FaceRecognizer()
    return _recognizer_singleton