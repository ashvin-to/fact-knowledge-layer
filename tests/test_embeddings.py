"""Tests for embedding text formatting and candidate generation."""

from __future__ import annotations

import numpy as np
import pytest

from src.embeddings import generate_candidate_pairs, get_embedding_text


class TestGetEmbeddingText:
    def test_subject_predicate_only(self) -> None:
        assert get_embedding_text("GDP growth", "rate", None) == "GDP growth — rate"

    def test_with_unit(self) -> None:
        assert get_embedding_text("Revenue", "amount", "USD") == "Revenue — amount (USD)"

    def test_unit_none_string_not_leaked(self) -> None:
        assert get_embedding_text("Inflation", "annual", "None") == "Inflation — annual"
        assert get_embedding_text("Inflation", "annual", "none") == "Inflation — annual"
        assert get_embedding_text("Inflation", "annual", "") == "Inflation — annual"
        assert get_embedding_text("Inflation", "annual", "   ") == "Inflation — annual"

    def test_strips_whitespace(self) -> None:
        assert get_embedding_text("  Real GDP  ", "  growth rate  ", " % ") == "Real GDP — growth rate (%)"


class TestGenerateCandidatePairs:
    def _make_fact(self, fact_id: str, doc_id: str, vec: np.ndarray) -> dict:
        return {
            "id": fact_id,
            "document_id": doc_id,
            "subject": f"Subj {fact_id}",
            "predicate": "pred",
            "value": "100",
            "unit": "%",
            "vector": vec,
        }

    def test_never_compares_same_document(self) -> None:
        v = np.array([1.0, 0.0], dtype=np.float32)
        f1 = self._make_fact("f1", "doc-1", v)
        f2 = self._make_fact("f2", "doc-1", v)  # Same document!

        candidates = generate_candidate_pairs([f1, f2], threshold=0.5, existing_pairs=set())
        assert candidates == []

    def test_matches_above_threshold_across_documents(self) -> None:
        v1 = np.array([1.0, 0.0], dtype=np.float32)
        v2 = np.array([1.0, 0.0], dtype=np.float32)  # Identical
        f1 = self._make_fact("f1", "doc-1", v1)
        f2 = self._make_fact("f2", "doc-2", v2)

        candidates = generate_candidate_pairs([f1, f2], threshold=0.6, existing_pairs=set())
        assert len(candidates) == 1
        assert candidates[0][0]["id"] == "f1"
        assert candidates[0][1]["id"] == "f2"
        assert pytest.approx(candidates[0][2], 0.01) == 1.0

    def test_discards_below_threshold(self) -> None:
        v1 = np.array([1.0, 0.0], dtype=np.float32)
        v2 = np.array([0.0, 1.0], dtype=np.float32)  # Orthogonal (similarity = 0.0)
        f1 = self._make_fact("f1", "doc-1", v1)
        f2 = self._make_fact("f2", "doc-2", v2)

        candidates = generate_candidate_pairs([f1, f2], threshold=0.6, existing_pairs=set())
        assert candidates == []

    def test_skips_existing_pairs_both_directions(self) -> None:
        v = np.array([1.0, 0.0], dtype=np.float32)
        f1 = self._make_fact("f1", "doc-1", v)
        f2 = self._make_fact("f2", "doc-2", v)

        # Existing pair stored as canonical (f1, f2)
        existing = {("f1", "f2")}
        candidates = generate_candidate_pairs([f1, f2], threshold=0.5, existing_pairs=existing)
        assert candidates == []
