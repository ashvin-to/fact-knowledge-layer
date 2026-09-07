"""Tests for fact extraction logic (src/extraction.py)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.extraction import FactExtractionPageError, _parse_response, extract_facts_from_page, extract_facts_from_page_image


class TestParseResponse:
    def test_valid_array(self) -> None:
        payload = json.dumps([
            {
                "subject": "Revenue",
                "predicate": "amount",
                "value": "500M",
                "value_type": "numeric",
                "unit": "USD",
                "time_scope": "FY2025",
                "qualifier": None,
                "confidence": 0.9,
                "evidence_text": "Revenue: 500M USD",
            }
        ])
        facts, err = _parse_response(payload)
        assert err is None
        assert len(facts) == 1
        assert facts[0].subject == "Revenue"

    def test_empty_array(self) -> None:
        facts, err = _parse_response("[]")
        assert err is None
        assert facts == []

    def test_invalid_json(self) -> None:
        _, err = _parse_response("not json at all")
        assert err is not None
        assert "JSON parse error" in err

    def test_not_a_list(self) -> None:
        _, err = _parse_response('{"key": "value"}')
        assert err is not None
        assert "JSON array" in err

    def test_missing_required_field(self) -> None:
        payload = json.dumps([{"subject": "X"}])  # missing many required fields
        _, err = _parse_response(payload)
        assert err is not None
        assert "Validation error" in err

    def test_strips_markdown_fences(self) -> None:
        payload = "```json\n[]\n```"
        facts, err = _parse_response(payload)
        assert err is None
        assert facts == []

    def test_invalid_value_type(self) -> None:
        payload = json.dumps([
            {
                "subject": "X",
                "predicate": "y",
                "value": "z",
                "value_type": "nonsense",  # not in Literal
                "confidence": 0.8,
                "evidence_text": "z",
            }
        ])
        _, err = _parse_response(payload)
        assert err is not None


class TestExtractFactsFromPage:
    def _make_client(self, response: str) -> MagicMock:
        client = MagicMock()
        client.model = "qwen2.5:7b-instruct"
        client.chat.return_value = response
        return client

    def _valid_payload(self) -> str:
        return json.dumps([
            {
                "subject": "Company",
                "predicate": "revenue",
                "value": "100",
                "value_type": "numeric",
                "unit": "USD",
                "time_scope": "Q1",
                "qualifier": None,
                "confidence": 0.95,
                "evidence_text": "Company revenue: 100 USD",
            }
        ])

    def test_success_returns_db_ready_dicts(self) -> None:
        client = self._make_client(self._valid_payload())
        facts = extract_facts_from_page("Company revenue: 100 USD", 0, "doc-1", client)
        assert len(facts) == 1
        f = facts[0]
        assert f["document_id"] == "doc-1"
        assert f["pdf_page_index"] == 0
        assert f["extraction_method"] == "local:qwen2.5:7b-instruct"
        assert "id" in f
        assert "created_at" in f

    def test_empty_page_returns_empty_list(self) -> None:
        client = self._make_client("[]")
        facts = extract_facts_from_page("Cover page", 0, "doc-1", client)
        assert facts == []

    def test_retry_on_bad_first_response_then_success(self) -> None:
        client = MagicMock()
        client.model = "qwen2.5:7b-instruct"
        # First call: bad JSON; second call: valid
        client.chat.side_effect = ["not valid json", self._valid_payload()]
        facts = extract_facts_from_page("Some page", 0, "doc-1", client)
        assert len(facts) == 1
        assert client.chat.call_count == 2

    def test_retry_fails_raises_page_error(self) -> None:
        client = MagicMock()
        client.model = "qwen2.5:7b-instruct"
        client.chat.side_effect = ["bad json 1", "bad json 2"]
        with pytest.raises(FactExtractionPageError):
            extract_facts_from_page("Some page", 0, "doc-1", client)
        assert client.chat.call_count == 2

    def test_extraction_method_uses_model_name(self) -> None:
        client = MagicMock()
        client.model = "llama3.1:8b"
        client.chat.return_value = self._valid_payload()
        facts = extract_facts_from_page("text", 0, "doc-1", client)
        assert facts[0]["extraction_method"] == "local:llama3.1:8b"


class TestExtractFactsFromPageImage:
    def test_vision_extraction_success(self) -> None:
        payload = json.dumps([
            {
                "subject": "Chart Revenue",
                "predicate": "q1_value",
                "value": "450",
                "value_type": "numeric",
                "unit": "USD M",
                "time_scope": "Q1 2024",
                "qualifier": "chart bar",
                "confidence": 0.95,
                "evidence_text": "Q1 2024 bar: 450 USD M",
            }
        ])
        client = MagicMock()
        client.model = "qwen2.5-vl:7b"
        client.chat.return_value = payload

        dummy_img = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        facts = extract_facts_from_page_image(dummy_img, 2, "doc-img-1", client)
        assert len(facts) == 1
        assert facts[0]["subject"] == "Chart Revenue"
        assert facts[0]["pdf_page_index"] == 2
        assert facts[0]["extraction_method"] == "vision:qwen2.5-vl:7b"

