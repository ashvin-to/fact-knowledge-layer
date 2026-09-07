"""Tests for pairwise fact comparison and reasoning logic."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.comparison import _parse_verdict, evaluate_pair
from src.models import FactRelationshipVerdict


class TestParseVerdict:
    def test_valid_corroborate(self) -> None:
        payload = json.dumps({
            "relationship_type": "corroborate",
            "explanation": "Both documents state the same growth rate of 7.2%",
            "confidence": 0.95,
            "reconciliation_factor": None,
        })
        verdict, err = _parse_verdict(payload)
        assert err is None
        assert verdict is not None
        assert verdict.relationship_type == "corroborate"
        assert verdict.confidence == 0.95

    def test_valid_context_reconciled(self) -> None:
        payload = json.dumps({
            "relationship_type": "context_reconciled",
            "explanation": "Differs due to fiscal year vs calendar year scope",
            "confidence": 0.9,
            "reconciliation_factor": "time",
        })
        verdict, err = _parse_verdict(payload)
        assert err is None
        assert verdict is not None
        assert verdict.relationship_type == "context_reconciled"
        assert verdict.reconciliation_factor == "time"

    def test_context_reconciled_missing_factor_fails(self) -> None:
        payload = json.dumps({
            "relationship_type": "context_reconciled",
            "explanation": "Differences explained by context",
            "confidence": 0.9,
            "reconciliation_factor": None,  # Must not be None!
        })
        verdict, err = _parse_verdict(payload)
        assert err is not None
        assert "Validation error" in err

    def test_invalid_relationship_type(self) -> None:
        payload = json.dumps({
            "relationship_type": "agree",  # Not in Literal enum
            "explanation": "They agree",
            "confidence": 0.8,
        })
        verdict, err = _parse_verdict(payload)
        assert err is not None


class TestEvaluatePair:
    def _fact(self, fact_id: str, doc_id: str, subject="GDP") -> dict:
        return {
            "id": fact_id,
            "document_id": doc_id,
            "document_filename": f"{doc_id}.pdf",
            "subject": subject,
            "predicate": "growth rate",
            "value": "7.0",
            "unit": "%",
            "time_scope": "FY24",
            "qualifier": None,
            "evidence_text": "GDP grew by 7.0% in FY24",
        }

    def test_corroborate_high_confidence_no_second_opinion(self) -> None:
        reasoner = MagicMock()
        reasoner.model = "reasoner-model"
        reasoner.chat.return_value = json.dumps({
            "relationship_type": "corroborate",
            "explanation": "Exact match",
            "confidence": 0.95,
            "reconciliation_factor": None,
        })
        verifier = MagicMock()

        f1 = self._fact("f1", "doc-1")
        f2 = self._fact("f2", "doc-2")
        result = evaluate_pair(f1, f2, reasoner, verifier)

        assert result["relationship_type"] == "corroborate"
        assert result["needs_review"] == 0
        assert verifier.chat.call_count == 0  # Second opinion NOT triggered

    def test_contradict_triggers_second_opinion_with_disagreement(self) -> None:
        reasoner = MagicMock()
        reasoner.model = "reasoner-model"
        reasoner.chat.return_value = json.dumps({
            "relationship_type": "contradict",
            "explanation": "Values conflict",
            "confidence": 0.9,
            "reconciliation_factor": None,
        })

        verifier = MagicMock()
        verifier.model = "verifier-model"
        # Verifier says context_reconciled instead of contradict
        verifier.chat.return_value = json.dumps({
            "relationship_type": "context_reconciled",
            "explanation": "Differs by measurement base",
            "confidence": 0.85,
            "reconciliation_factor": "scope",
        })

        f1 = self._fact("f1", "doc-1")
        f2 = self._fact("f2", "doc-2")
        result = evaluate_pair(f1, f2, reasoner, verifier)

        assert result["relationship_type"] == "contradict"
        assert result["needs_review"] == 1  # Disagreement flagged!
        assert result["second_opinion_verdict"] == "context_reconciled"
        assert result["second_opinion_model"] == "verifier-model"
        assert verifier.chat.call_count == 1

    def test_low_confidence_triggers_second_opinion(self) -> None:
        reasoner = MagicMock()
        reasoner.model = "reasoner-model"
        reasoner.chat.return_value = json.dumps({
            "relationship_type": "corroborate",
            "explanation": "Probable match",
            "confidence": 0.55,  # < 0.7 triggers second opinion
            "reconciliation_factor": None,
        })

        verifier = MagicMock()
        verifier.model = "verifier-model"
        verifier.chat.return_value = json.dumps({
            "relationship_type": "corroborate",
            "explanation": "Agreed",
            "confidence": 0.8,
            "reconciliation_factor": None,
        })

        f1 = self._fact("f1", "doc-1")
        f2 = self._fact("f2", "doc-2")
        result = evaluate_pair(f1, f2, reasoner, verifier)

        assert result["relationship_type"] == "corroborate"
        assert result["needs_review"] == 0  # Both agreed
        assert verifier.chat.call_count == 1
