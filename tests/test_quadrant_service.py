"""
Integration tests for quadrant_service.py

Requires a running Qdrant server.

Run:

    pytest -v tests/test_quadrant_service.py
"""

import uuid

import numpy as np
import pytest
from qdrant_client import models

from services.quadrant_service import (
    client,
    ingest_embedding,
    search_embedding,
    delete_embedding,
    delete_by_filter,
    count_embeddings,
    get_collection_info,
    health_check,
    EMBEDDING_DIM,
)
import time

# ============================================================================
# Test Collection
# ============================================================================

TEST_COLLECTION = (
    f"test_face_embeddings_{uuid.uuid4().hex[:8]}"
)


# ============================================================================
# Fixture
# ============================================================================

@pytest.fixture(
    scope="module",
    autouse=True,
)
def _test_collection_fixture():

    # Create temporary collection if it does not already exist.
    if not client.collection_exists(TEST_COLLECTION):

        client.create_collection(
            collection_name=TEST_COLLECTION,
            vectors_config=models.VectorParams(
                size=EMBEDDING_DIM,
                distance=models.Distance.COSINE,
            ),
        )

    yield

    # Cleanup.
    try:
        client.delete_collection(
            collection_name=TEST_COLLECTION
        )
    except Exception:
        pass


# ============================================================================
# Helpers
# ============================================================================

def random_embedding(seed: int) -> list[float]:
    """
    Generate deterministic normalized 512-D embedding.
    """

    rng = np.random.default_rng(seed)

    vector = rng.random(
        EMBEDDING_DIM,
        dtype=np.float32,
    )

    vector /= np.linalg.norm(vector)

    return vector.tolist()


def similar_embedding(
    base: list[float],
    seed: int,
    noise: float,
) -> list[float]:
    """
    Generate an embedding close to the base vector.
    """

    rng = np.random.default_rng(seed)

    vector = np.asarray(
        base,
        dtype=np.float32,
    )

    vector = (
        vector
        + rng.normal(
            0,
            noise,
            EMBEDDING_DIM,
        )
    )

    vector /= np.linalg.norm(vector)

    return vector.tolist()


# ============================================================================
# QDRANT HEALTH
# ============================================================================

def test_health_check():
    assert health_check() is True


# ============================================================================
# COLLECTION
# ============================================================================

def test_collection():

    info = get_collection_info(
        TEST_COLLECTION
    )

    assert info is not None

    assert (
        info.config.params.vectors.size
        == EMBEDDING_DIM
    )


# ============================================================================
# INGEST
# ============================================================================

def test_ingest():

    embedding = random_embedding(1)

    point_id = ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=1001,
        person_name="Mithun",
        department="CSE",
        camera_id="CAM01",
    )

    assert point_id is not None
    assert isinstance(point_id, str)


# ============================================================================
# SEARCH SAME EMBEDDING
# ============================================================================

def test_search_same_embedding():

    embedding = random_embedding(10)

    point_id = ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=2001,
        person_name="Sai",
    )

    results = search_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        top_k=5,
    )

    assert len(results) >= 1

    # Identical embedding must be first.
    assert results[0]["id"] == point_id

    # Cosine similarity should be approximately 1.
    assert results[0]["score"] > 0.99

    assert (
        results[0]["payload"]["person_id"]
        == 2001
    )


# ============================================================================
# TOP-K SEARCH
# ============================================================================

def test_top_k_search():

    query = random_embedding(20)

    embeddings = [
        (
            similar_embedding(
                query,
                seed=21,
                noise=0.001,
            ),
            "Closest",
        ),
        (
            similar_embedding(
                query,
                seed=22,
                noise=0.03,
            ),
            "Medium",
        ),
        (
            similar_embedding(
                query,
                seed=23,
                noise=0.10,
            ),
            "Far",
        ),
    ]

    for embedding, name in embeddings:

        ingest_embedding(
            embedding,
            collection_name=TEST_COLLECTION,
            wait=True,
            person_name=name,
        )

    results = search_embedding(
        query,
        collection_name=TEST_COLLECTION,
        top_k=3,
    )

    assert len(results) == 3

    scores = [
        result["score"]
        for result in results
    ]

    # Highest similarity first.
    assert scores == sorted(
        scores,
        reverse=True,
    )

    # Closest should have the highest score.
    assert results[0]["score"] > results[1]["score"]
    assert results[1]["score"] > results[2]["score"]


# ============================================================================
# TOP-K LIMIT
# ============================================================================

def test_top_k_limit():

    query = random_embedding(30)

    for i in range(10):

        embedding = similar_embedding(
            query,
            seed=100 + i,
            noise=0.02 + i * 0.005,
        )

        ingest_embedding(
            embedding,
            collection_name=TEST_COLLECTION,
            wait=True,
            person_id=4000 + i,
        )

    results = search_embedding(
        query,
        collection_name=TEST_COLLECTION,
        top_k=3,
    )

    assert len(results) <= 3


# ============================================================================
# SCORE THRESHOLD
# ============================================================================

def test_score_threshold():

    embedding = random_embedding(40)

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=5001,
    )

    results = search_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        top_k=10,
        score_threshold=0.99,
    )

    assert len(results) >= 1

    assert all(
        result["score"] >= 0.99
        for result in results
    )


# ============================================================================
# METADATA FILTER
# ============================================================================

def test_metadata_filter():

    embedding = random_embedding(50)

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=6001,
        department="CSE",
    )

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=6002,
        department="ECE",
    )

    results = search_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        top_k=10,
        department="CSE",
    )

    assert len(results) >= 1

    for result in results:

        assert (
            result["payload"]["department"]
            == "CSE"
        )


# ============================================================================
# MULTIPLE METADATA FILTERS
# ============================================================================

def test_multiple_metadata_filters():

    embedding = random_embedding(60)

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=7001,
        department="CSE",
        camera_id="CAM01",
    )

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=7002,
        department="CSE",
        camera_id="CAM02",
    )

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=7003,
        department="ECE",
        camera_id="CAM01",
    )

    results = search_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        top_k=10,
        department="CSE",
        camera_id="CAM01",
    )

    assert len(results) >= 1

    for result in results:

        payload = result["payload"]

        assert payload["department"] == "CSE"
        assert payload["camera_id"] == "CAM01"


# ============================================================================
# MULTIPLE VALUES
# ============================================================================

def test_multiple_metadata_values():

    embedding = random_embedding(70)

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=8001,
        department="CSE",
    )

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=8002,
        department="ECE",
    )

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=8003,
        department="MECH",
    )

    results = search_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        top_k=10,
        department=["CSE", "ECE"],
    )

    departments = {
        result["payload"]["department"]
        for result in results
    }

    assert "CSE" in departments
    assert "ECE" in departments
    assert "MECH" not in departments


# ============================================================================
# COUNT
# ============================================================================

def test_count():

    embedding = random_embedding(80)

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=9001,
        test_group="COUNT_TEST",
    )

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=9002,
        test_group="COUNT_TEST",
    )

    count = count_embeddings(
        collection_name=TEST_COLLECTION,
        test_group="COUNT_TEST",
    )

    assert count >= 2


# ============================================================================
# DELETE BY ID
# ============================================================================

def test_delete_by_id():

    embedding = random_embedding(90)

    point_id = ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=10001,
        test_group="DELETE_ID_TEST",
    )

    delete_embedding(
        point_id,
        collection_name=TEST_COLLECTION,
        wait=True,
    )

    count = count_embeddings(
        collection_name=TEST_COLLECTION,
        person_id=10001,
    )

    assert count == 0


# ============================================================================
# DELETE BY METADATA
# ============================================================================

def test_delete_by_metadata():

    embedding = random_embedding(100)

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=11001,
        test_group="DELETE_METADATA_TEST",
    )

    delete_embedding(
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=11001,
        test_group="DELETE_METADATA_TEST",
    )

    count = count_embeddings(
        collection_name=TEST_COLLECTION,
        person_id=11001,
    )

    assert count == 0


# ============================================================================
# DELETE BY FILTER
# ============================================================================

def test_delete_by_filter():

    embedding = random_embedding(110)

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=12001,
        test_group="DELETE_FILTER_TEST",
    )

    ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=12002,
        test_group="DELETE_FILTER_TEST",
    )

    delete_by_filter(
        collection_name=TEST_COLLECTION,
        wait=True,
        test_group="DELETE_FILTER_TEST",
    )

    count = count_embeddings(
        collection_name=TEST_COLLECTION,
        test_group="DELETE_FILTER_TEST",
    )

    assert count == 0


# ============================================================================
# INVALID EMBEDDING
# ============================================================================

def test_invalid_embedding_dimension():

    invalid_embedding = [0.1] * 128

    with pytest.raises(ValueError):

        ingest_embedding(
            invalid_embedding,
            collection_name=TEST_COLLECTION,
        )


# ============================================================================
# INVALID TOP-K
# ============================================================================

def test_invalid_top_k():

    embedding = random_embedding(120)

    with pytest.raises(ValueError):

        search_embedding(
            embedding,
            collection_name=TEST_COLLECTION,
            top_k=0,
        )


# ============================================================================
# NUMPY EMBEDDING
# ============================================================================

def test_numpy_embedding():

    embedding = np.asarray(
        random_embedding(130),
        dtype=np.float32,
    )

    point_id = ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=13001,
    )

    results = search_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        top_k=1,
    )

    assert len(results) == 1
    assert results[0]["id"] == point_id


# ============================================================================
# BATCH EMBEDDING [1, 512]
# ============================================================================

def test_batch_embedding():

    embedding = np.asarray(
        random_embedding(140),
        dtype=np.float32,
    )

    # Same shape produced by the model.
    embedding = embedding.reshape(1, 512)

    point_id = ingest_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        wait=True,
        person_id=14001,
    )

    results = search_embedding(
        embedding,
        collection_name=TEST_COLLECTION,
        top_k=1,
    )

    assert len(results) == 1
    assert results[0]["id"] == point_id
