"""Pydantic tool contracts for MAG_Agent tools and data providers."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ToolExecutionError(BaseModel):
    """Structured machine-readable error response returned when a tool call fails."""

    error_code: str = Field(description="Error code identifier, e.g. INVALID_PARTITION_KEY, TIMEOUT")
    error_message: str = Field(description="Human readable diagnostic error message")
    tool_name: str = Field(description="Name of the tool that encountered the error")
    doc_id: Optional[str] = Field(default=None, description="Partition key doc_id context")
    retryable: bool = Field(default=False, description="Indicates whether retrying the call is safe")
    details: Dict[str, Any] = Field(default_factory=dict, description="Additional context or diagnostics")


class SearchQueryInput(BaseModel):
    """Input payload contract for Azure AI Search document chunk retrieval."""

    query: str = Field(description="Text search query")
    doc_id: str = Field(description="Target investigation document partition key")
    top_k: int = Field(default=5, ge=1, le=50, description="Number of document chunks to retrieve")
    min_score: float = Field(default=0.0, ge=0.0, description="Minimum search relevance threshold")


class SearchChunkItem(BaseModel):
    """Retrieved document chunk payload."""

    chunk_id: str
    doc_id: str
    text: str
    score: float
    source_location: Optional[str] = None


class SearchQueryResult(BaseModel):
    """Structured success response for Azure AI Search."""

    status: Literal["success"] = "success"
    doc_id: str
    total_found: int
    chunks: List[SearchChunkItem]
    duration_ms: float


class GraphQueryInput(BaseModel):
    """Input payload contract for Gremlin Cosmos DB graph traversal."""

    doc_id: str = Field(description="Target investigation document partition key doc_id")
    gremlin_query: str = Field(description="Gremlin query string (e.g. g.V().has('doc_id', doc_id)...)")
    max_nodes: int = Field(default=20, ge=1, le=100, description="Maximum graph nodes to return")


class GraphNodeItem(BaseModel):
    """Structured graph node."""

    id: str
    name: str
    label: str
    doc_id: str
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphQueryResult(BaseModel):
    """Structured success response for Cosmos DB Gremlin queries."""

    status: Literal["success"] = "success"
    doc_id: str
    nodes_count: int
    nodes: List[GraphNodeItem]
    raw_preview: Optional[str] = None
    duration_ms: float


class EvidenceFetchInput(BaseModel):
    """Input payload contract for Blob Storage evidence extraction."""

    source_location: str = Field(description="Source location identifier in Blob Storage")
    doc_id: str = Field(description="Active document partition key")


class EvidenceFetchResult(BaseModel):
    """Structured evidence verbatim quote response."""

    status: Literal["success"] = "success"
    source_location: str
    doc_id: str
    verbatim_text: str
    retrieved: bool
