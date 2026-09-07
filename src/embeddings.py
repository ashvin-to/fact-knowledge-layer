"""
Embedding computation and candidate pair generation.

Embeddings are local via sentence-transformers (model: all-MiniLM-L6-v2).
Cosine similarity is computed in-memory via numpy dot products.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
DEFAULT_SIMILARITY_THRESHOLD = 0.6


def get_embedding_text(subject: str, predicate: str, unit: str | None) -> str:
    """
    Format fact into text for embedding.

    Key design choice: deliberately excludes value, time_scope, and qualifier
    so we match facts describing the same underlying metric/claim regardless of
    when or under what scope it is stated.

    Avoids leaking the string 'None' if unit is missing or None.
    """
    s = str(subject).strip()
    p = str(predicate).strip()
    text = f"{s} — {p}"
    if unit is not None and str(unit).strip().lower() != "none" and str(unit).strip():
        text += f" ({str(unit).strip()})"
    return text


class EmbeddingService:
    """Local embedding service wrapping sentence-transformers."""

    def __init__(self, model_name: str = EMBEDDING_MODEL_NAME) -> None:
        self.model_name = model_name
        self._model = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            log.info("Loading embedding model %s...", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def encode_texts(self, texts: list[str]) -> np.ndarray:
        """Encode a batch of texts into normalized float32 numpy vectors."""
        if not texts:
            return np.empty((0, 384), dtype=np.float32)
        model = self._get_model()
        vectors = model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def ensure_embeddings(
        self, conn: sqlite3.Connection, facts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Ensure all facts have embeddings computed and persisted in SQLite.
        Attaches 'vector' (normalized np.ndarray) to each fact dict.
        """
        missing_indices: list[int] = []
        missing_texts: list[str] = []

        for idx, fact in enumerate(facts):
            raw_blob = fact.get("embedding")
            if raw_blob is not None:
                vec = np.frombuffer(raw_blob, dtype=np.float32)
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
                fact["vector"] = vec
            else:
                missing_indices.append(idx)
                text = get_embedding_text(
                    subject=fact["subject"],
                    predicate=fact["predicate"],
                    unit=fact.get("unit"),
                )
                missing_texts.append(text)

        if missing_texts:
            log.info("Computing embeddings for %d facts...", len(missing_texts))
            new_vectors = self.encode_texts(missing_texts)
            for i, vec in zip(missing_indices, new_vectors):
                fact = facts[i]
                fact["vector"] = vec
                blob = vec.tobytes()
                conn.execute(
                    "UPDATE facts SET embedding = ?, embedding_model = ? WHERE id = ?",
                    (blob, self.model_name, fact["id"]),
                )
            conn.commit()

        return facts


def generate_candidate_pairs(
    facts: list[dict[str, Any]],
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    existing_pairs: set[tuple[str, str]] | None = None,
    threshold: float | None = None,
) -> list[tuple[dict[str, Any], dict[str, Any], float]]:
    """
    Find candidate pairs of facts across different documents with cosine similarity >= threshold.
    Uses USearch SIMD index for accelerated search with automatic fallback.

    Guards:
      1. a.document_id != b.document_id (never compare facts from the same document)
      2. (min(a.id, b.id), max(a.id, b.id)) not in existing_pairs (skip previously evaluated pairs)
    """
    if threshold is not None:
        similarity_threshold = threshold
    if existing_pairs is None:
        existing_pairs = set()

    n = len(facts)
    if n < 2:
        return []

    # Try fast USearch SIMD index when available
    valid_indices = [i for i, f in enumerate(facts) if f.get("vector") is not None]
    if len(valid_indices) >= 2:
        try:
            from usearch.index import Index

            vectors = np.array([facts[i]["vector"] for i in valid_indices], dtype=np.float32)
            ndim = vectors.shape[1]
            index = Index(ndim=ndim, metric="cos", dtype="f32")
            index.add(np.arange(len(valid_indices)), vectors)

            # Query top neighbors per vector
            k = min(len(valid_indices), 50)
            matches = index.search(vectors, k)

            candidates: list[tuple[dict[str, Any], dict[str, Any], float]] = []
            seen_pairs: set[tuple[str, str]] = set()

            for i_sub, i_orig in enumerate(valid_indices):
                fact_a = facts[i_orig]
                doc_a = fact_a["document_id"]
                keys = matches.keys[i_sub]
                distances = matches.distances[i_sub]

                for j_sub, dist in zip(keys, distances):
                    if j_sub < 0 or j_sub >= len(valid_indices):
                        continue
                    j_orig = valid_indices[j_sub]
                    if j_orig <= i_orig:
                        continue

                    fact_b = facts[j_orig]
                    if fact_b["document_id"] == doc_a:
                        continue

                    pair_key = (min(fact_a["id"], fact_b["id"]), max(fact_a["id"], fact_b["id"]))
                    if pair_key in existing_pairs or pair_key in seen_pairs:
                        continue

                    sim = float(1.0 - dist)
                    if sim >= similarity_threshold:
                        seen_pairs.add(pair_key)
                        candidates.append((fact_a, fact_b, sim))

            return candidates
        except Exception as exc:
            log.debug("USearch SIMD candidate generation fallback to numpy: %s", exc)

    # Standard NumPy vectorized search fallback
    candidates = []
    for i in range(n):
        fact_a = facts[i]
        vec_a = fact_a.get("vector")
        if vec_a is None:
            continue

        for j in range(i + 1, n):
            fact_b = facts[j]
            if fact_a["document_id"] == fact_b["document_id"]:
                continue

            pair_key = (min(fact_a["id"], fact_b["id"]), max(fact_a["id"], fact_b["id"]))
            if pair_key in existing_pairs:
                continue

            vec_b = fact_b.get("vector")
            if vec_b is None:
                continue

            sim = float(np.dot(vec_a, vec_b))
            if sim >= similarity_threshold:
                candidates.append((fact_a, fact_b, sim))

    return candidates
