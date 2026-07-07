from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class VerifiedFact(BaseModel):
    fact: str
    source_id: str
    doc_id: str
    confidence: float
    audit_trace: Dict[str, Any] = Field(
        default_factory=dict,
        description="Records tool name, arguments, and LLM reasoning trace leading to this fact"
    )

class PendingFactApproval(BaseModel):
    fact: str
    source_id: str
    doc_id: str
    audit_trace: Dict[str, Any]

class InvestigationState(BaseModel):
    doc_id: str = Field(description="Active investigation document partition ID context")
    investigation_plan: List[str] = Field(default_factory=list, description="Pending investigative tasks")
    verified_facts: List[VerifiedFact] = Field(default_factory=list, description="Ledger of Admissible Facts")
    explored_entities: List[str] = Field(default_factory=list, description="Visited entities to prevent infinite loops")
    current_task: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list, description="Conversation messages and thoughts")
    errors: List[str] = Field(default_factory=list, description="Warning/error traces")
    retry_count: int = 0
    final_answer: Optional[str] = None
    pending_fact_approval: Optional[PendingFactApproval] = None
    user_feedback: Optional[str] = None  # Approved fact content, or "rejected"
    
    # Transient fields for parallel execution. Pruned automatically to prevent state bloat.
    transient_doc_results: Optional[str] = None
    transient_graph_results: Optional[str] = None
    transient_synthesis_results: Optional[str] = None
