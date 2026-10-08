"""
Qdrant service for face embedding storage and similarity search.

Face model:
    InceptionResnetV1(pretrained="vggface2")

Embedding dimension:
    512

Similarity:
    Cosine similarity
"""

import os
import uuid
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models


# ============================================================================
# Configuration
# ============================================================================

load_dotenv()

QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://localhost:6333",
)

QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")

QDRANT_COLLECTION = os.getenv(
    "QDRANT_COLLECTION",
    "face_embeddings",
)

# InceptionResnetV1(pretrained="vggface2") -> 512 dimensions
EMBEDDING_DIM = 512


# ============================================================================
# Qdrant Client
# ============================================================================

client = QdrantClient(
    url=QDRANT_URL,
    api_key=QDRANT_API_KEY,
    timeout=30,
)


# ============================================================================
# Internal Helpers
# ============================================================================

def _validate_embedding(embedding: Any) -> List[float]:
    """
    Validate and convert an embedding into a Python float list.

    Supports:
        - list
        - tuple
        - numpy array
        - torch tensor
        - shape [512]
        - shape [1, 512]
    """

    if embedding is None:
        raise ValueError("Embedding cannot be None.")

    # Torch tensor -> CPU
    if hasattr(embedding, "detach"):
        embedding = embedding.detach()

    if hasattr(embedding, "cpu"):
        embedding = embedding.cpu()

    # NumPy / tensor -> ndarray
    if hasattr(embedding, "numpy"):
        embedding = embedding.numpy()

    # Handle [1, 512]
    if hasattr(embedding, "ndim"):

        if embedding.ndim == 2:

            if embedding.shape[0] != 1:
                raise ValueError(
                    f"Expected a single embedding, "
                    f"but received shape {embedding.shape}."
                )

            embedding = embedding[0]

        elif embedding.ndim != 1:

            raise ValueError(
                f"Invalid embedding shape: {embedding.shape}."
            )

    # Convert to Python list
    if not isinstance(embedding, list):
        embedding = embedding.tolist()

    # Validate dimension
    if len(embedding) != EMBEDDING_DIM:
        raise ValueError(
            f"Invalid embedding dimension. "
            f"Expected {EMBEDDING_DIM}, got {len(embedding)}."
        )

    return [float(value) for value in embedding]


def _build_filter(
    metadata_filters: Dict[str, Any],
) -> Optional[models.Filter]:
    """
    Convert keyword metadata into a Qdrant filter.

    Example:

        department="CSE",
        camera_id="CAM01"

    becomes:

        department == "CSE"
        AND
        camera_id == "CAM01"

    Lists use MatchAny:

        department=["CSE", "ECE"]
    """

    if not metadata_filters:
        return None

    conditions = []

    for key, value in metadata_filters.items():

        if value is None:
            continue

        # Multiple possible values
        if isinstance(value, (list, tuple, set)):

            values = list(value)

            if not values:
                continue

            conditions.append(
                models.FieldCondition(
                    key=key,
                    match=models.MatchAny(
                        any=values
                    ),
                )
            )

        else:

            conditions.append(
                models.FieldCondition(
                    key=key,
                    match=models.MatchValue(
                        value=value
                    ),
                )
            )

    if not conditions:
        return None

    return models.Filter(
        must=conditions
    )


# ============================================================================
# Collection
# ============================================================================

def ensure_collection(
    collection_name: str = QDRANT_COLLECTION,
) -> None:
    """
    Create the face embedding collection if it does not exist.

    Safe to call during application startup.
    """

    if client.collection_exists(collection_name):
        return

    client.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=EMBEDDING_DIM,
            distance=models.Distance.COSINE,
        ),
        hnsw_config=models.HnswConfigDiff(
            m=16,
            ef_construct=100,
        ),
    )


# ============================================================================
# INGEST
# ============================================================================

def ingest_embedding(
    embedding: Any,
    *,
    embedding_id: Optional[Union[str, int]] = None,
    collection_name: str = QDRANT_COLLECTION,
    wait: bool = False,
    **metadata: Any,
) -> str:
    """
    Insert or update an embedding.

    Parameters
    ----------
    embedding:
        512-D face embedding.

    embedding_id:
        Optional unique Qdrant point ID.

        If not supplied, UUID is generated.

    wait:
        If True, wait until Qdrant confirms the operation.

        False is better for high-throughput ingestion.

    metadata:
        Arbitrary metadata stored in the Qdrant payload.

    Example
    -------

        point_id = ingest_embedding(
            embedding,
            person_id=123,
            person_name="Mithun",
            camera_id="CAM01",
        )
    """

    vector = _validate_embedding(embedding)

    point_id = str(
        embedding_id or uuid.uuid4()
    )

    client.upsert(
        collection_name=collection_name,
        points=[
            models.PointStruct(
                id=point_id,
                vector=vector,
                payload=metadata,
            )
        ],
        wait=wait,
    )

    return point_id


# ============================================================================
# SEARCH
# ============================================================================

def search_embedding(
    embedding: Any,
    *,
    top_k: int = 5,
    score_threshold: Optional[float] = None,
    collection_name: str = QDRANT_COLLECTION,
    query_filter: Optional[models.Filter] = None,
    with_payload: bool = True,
    with_vectors: bool = False,
    **metadata_filters: Any,
) -> List[Dict[str, Any]]:
    """
    Find the closest embeddings to an incoming embedding.

    Uses cosine similarity.

    Results are automatically sorted by similarity,
    highest score first.

    Parameters
    ----------
    embedding:
        Incoming 512-D face embedding.

    top_k:
        Number of nearest neighbors to return.

    score_threshold:
        Optional minimum similarity.

        Example:

            score_threshold=0.60

        Only results >= 0.60 are returned.

    metadata_filters:
        Optional payload filters.

        Example:

            department="CSE"

            camera_id="CAM01"

    Returns
    -------
    List[Dict]

        [
            {
                "id": "...",
                "score": 0.91,
                "payload": {...}
            }
        ]
    """

    if top_k <= 0:
        raise ValueError(
            "top_k must be greater than 0."
        )

    vector = _validate_embedding(embedding)

    if query_filter is None:
        query_filter = _build_filter(
            metadata_filters
        )

    results = client.query_points(
        collection_name=collection_name,
        query=vector,
        query_filter=query_filter,
        limit=top_k,
        score_threshold=score_threshold,
        with_payload=with_payload,
        with_vectors=with_vectors,
    ).points

    return [
        {
            "id": str(result.id),
            "score": float(result.score),
            "payload": result.payload or {},
        }
        for result in results
    ]


# ============================================================================
# DELETE BY ID
# ============================================================================

def delete_embedding(
    embedding_id: Optional[Union[str, int]] = None,
    *,
    collection_name: str = QDRANT_COLLECTION,
    query_filter: Optional[models.Filter] = None,
    wait: bool = False,
    **metadata_filters: Any,
) -> None:
    """
    Delete embeddings.

    Delete by point ID:

        delete_embedding(
            "abc123"
        )

    Delete by metadata:

        delete_embedding(
            person_id=123
        )

    Multiple metadata filters:

        delete_embedding(
            person_id=123,
            camera_id="CAM01"
        )
    """

    # ------------------------------------------------------------------------
    # Delete by point ID
    # ------------------------------------------------------------------------

    if embedding_id is not None:

        client.delete(
            collection_name=collection_name,
            points_selector=models.PointIdsList(
                points=[str(embedding_id)]
            ),
            wait=wait,
        )

        return

    # ------------------------------------------------------------------------
    # Delete by filter
    # ------------------------------------------------------------------------

    if query_filter is None:
        query_filter = _build_filter(
            metadata_filters
        )

    if query_filter is None:
        raise ValueError(
            "Provide either embedding_id or metadata filters."
        )

    client.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(
            filter=query_filter
        ),
        wait=wait,
    )


# ============================================================================
# DELETE BY FILTER
# ============================================================================

def delete_by_filter(
    *,
    collection_name: str = QDRANT_COLLECTION,
    query_filter: Optional[models.Filter] = None,
    wait: bool = False,
    **metadata_filters: Any,
) -> None:
    """
    Delete all embeddings matching the supplied metadata.

    Example:

        delete_by_filter(
            person_id=123
        )

    Or:

        delete_by_filter(
            department="CSE",
            camera_id="CAM01"
        )
    """

    if query_filter is None:
        query_filter = _build_filter(
            metadata_filters
        )

    if query_filter is None:
        raise ValueError(
            "At least one filter is required."
        )

    client.delete(
        collection_name=collection_name,
        points_selector=models.FilterSelector(
            filter=query_filter
        ),
        wait=wait,
    )


# ============================================================================
# COUNT
# ============================================================================

def count_embeddings(
    *,
    collection_name: str = QDRANT_COLLECTION,
    query_filter: Optional[models.Filter] = None,
    exact: bool = True,
    **metadata_filters: Any,
) -> int:
    """
    Count embeddings.

    Example:

        count_embeddings(
            person_id=123
        )
    """

    if query_filter is None:
        query_filter = _build_filter(
            metadata_filters
        )

    result = client.count(
        collection_name=collection_name,
        count_filter=query_filter,
        exact=exact,
    )

    return result.count


# ============================================================================
# SCROLL / LIST BY FILTER
# ============================================================================

def list_embeddings(
    *,
    collection_name: str = QDRANT_COLLECTION,
    query_filter: Optional[models.Filter] = None,
    with_payload: bool = True,
    with_vectors: bool = False,
    page_size: int = 100,
    **metadata_filters: Any,
) -> List[Dict[str, Any]]:
    """
    Retrieve every point matching the given metadata filters.

    Unlike `search_embedding`, this does not require a query vector - it is
    a plain listing, useful for admin/management views.

    Example:

        list_embeddings(test_sandbox=True)
    """

    if query_filter is None:
        query_filter = _build_filter(
            metadata_filters
        )

    results: List[Dict[str, Any]] = []
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=query_filter,
            limit=page_size,
            offset=offset,
            with_payload=with_payload,
            with_vectors=with_vectors,
        )

        for point in points:
            results.append(
                {
                    "id": str(point.id),
                    "payload": point.payload or {},
                }
            )

        if offset is None:
            break

    return results


# ============================================================================
# COLLECTION INFORMATION
# ============================================================================

def get_collection_info(
    collection_name: str = QDRANT_COLLECTION,
):
    """
    Return Qdrant collection information.
    """

    return client.get_collection(
        collection_name=collection_name
    )


# ============================================================================
# HEALTH CHECK
# ============================================================================

def health_check() -> bool:
    """
    Check whether Qdrant is reachable.
    """

    try:

        client.get_collections()

        return True

    except Exception:

        return False


# ============================================================================
# DELETE COLLECTION
# ============================================================================

def delete_collection(
    collection_name: str = QDRANT_COLLECTION,
) -> None:
    """
    Permanently delete an entire collection.

    Use carefully.
    """

    client.delete_collection(
        collection_name=collection_name
    )
