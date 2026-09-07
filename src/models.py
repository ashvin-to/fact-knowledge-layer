"""
Pydantic models for the fact-extraction and fact-comparison service.

Covers:
  - FactExtraction          — shape of one fact as returned by the LLM
  - Document                — API response shape for a document row
  - Fact                    — API response shape for a fact row
  - DocumentUploadResponse  — response body for POST /documents
  - FactsResponse           — response body for GET /documents/{id}/facts
  - FactRelationshipVerdict — LLM output shape for pairwise fact comparison
  - InlinedFactDetail       — full fact and document detail inlined in relationships
  - FactRelationshipItem    — relationship row with inlined fact details
  - CompareRequest          — request body for POST /compare
  - CompareResponse         — response body for POST /compare
  - RelationshipsResponse   — response body for GET /relationships
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, field_validator

RelationshipType = Literal["corroborate", "contradict", "context_reconciled", "unrelated"]
ReconciliationFactor = Literal["time", "scope", "unit", "other"]


# ---------------------------------------------------------------------------
# LLM output schemas
# ---------------------------------------------------------------------------

class FactExtraction(BaseModel):
    """Validated shape of a single fact object returned by the LLM."""

    subject: str
    predicate: str
    value: str
    value_type: Literal["numeric", "categorical", "boolean", "text"]
    unit: Optional[str] = None
    time_scope: Optional[str] = None
    qualifier: Optional[str] = None
    confidence: float
    evidence_text: str


class FactRelationshipVerdict(BaseModel):
    """Validated shape of pairwise fact comparison verdict returned by reasoner LLM."""

    relationship_type: RelationshipType
    explanation: str
    confidence: float
    reconciliation_factor: Optional[ReconciliationFactor] = None

    @field_validator("reconciliation_factor")
    @classmethod
    def validate_factor(cls, v: Optional[str], info) -> Optional[str]:
        rel = info.data.get("relationship_type")
        if rel == "context_reconciled" and not v:
            raise ValueError("reconciliation_factor is required when relationship_type is 'context_reconciled'")
        return v


# ---------------------------------------------------------------------------
# DB / API response shapes
# ---------------------------------------------------------------------------

class Document(BaseModel):
    """Full document row as returned by GET /documents/{id}."""

    id: str
    filename: str
    storage_path: str
    upload_time: str
    page_count: Optional[int] = None
    pdf_type: Optional[str] = None
    classification_confidence: Optional[float] = None
    status: str
    skipped_pages: list[int] = []
    failed_pages: list[int] = []
    error_message: Optional[str] = None

    @field_validator("skipped_pages", "failed_pages", mode="before")
    @classmethod
    def _parse_json_list(cls, v: object) -> list[int]:
        if v is None:
            return []
        if isinstance(v, str):
            return json.loads(v)
        return v  # type: ignore[return-value]


class Fact(BaseModel):
    """Full fact row as returned by GET /documents/{id}/facts."""

    id: str
    document_id: str
    subject: str
    predicate: str
    value: str
    value_type: str
    unit: Optional[str] = None
    time_scope: Optional[str] = None
    qualifier: Optional[str] = None
    confidence: float
    pdf_page_index: int
    evidence_text: str
    extraction_method: str
    created_at: str
    embedding_model: Optional[str] = None


class DocumentUploadResponse(BaseModel):
    """Response body for POST /documents."""

    document_id: str
    filename: str
    page_count: Optional[int]
    pdf_type: Optional[str]
    status: str
    fact_count: int
    skipped_pages: list[int]
    failed_pages: list[int]
    error_message: Optional[str] = None


class FactsResponse(BaseModel):
    """Response body for GET /documents/{id}/facts."""

    document_id: str
    facts: list[Fact]


# ---------------------------------------------------------------------------
# Relationship models
# ---------------------------------------------------------------------------

class InlinedFactDetail(BaseModel):
    """Inlined fact detail with source document info for relationship inspection."""

    id: str
    document_id: str
    document_filename: str
    subject: str
    predicate: str
    value: str
    value_type: str
    unit: Optional[str] = None
    time_scope: Optional[str] = None
    qualifier: Optional[str] = None
    confidence: float
    pdf_page_index: int
    evidence_text: str


class FactRelationshipItem(BaseModel):
    """Full relationship representation with both facts inlined."""

    id: str
    relationship_type: RelationshipType
    explanation: str
    reconciliation_factor: Optional[str] = None
    confidence: float
    similarity_score: float
    reasoner_model: str
    needs_review: int
    second_opinion_verdict: Optional[str] = None
    second_opinion_model: Optional[str] = None
    created_at: str
    fact_a: InlinedFactDetail
    fact_b: InlinedFactDetail


class CompareRequest(BaseModel):
    """Optional request body for POST /compare."""

    document_ids: Optional[list[str]] = None


class CompareResponse(BaseModel):
    """Response body for POST /compare."""

    candidates_evaluated: int
    corroborate: int
    contradict: int
    context_reconciled: int
    unrelated: int
    needs_review: int


class RelationshipsResponse(BaseModel):
    """Response body for GET /relationships."""

    relationships: list[FactRelationshipItem]
