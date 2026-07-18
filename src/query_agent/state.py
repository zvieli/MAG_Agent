from pydantic import BaseModel, Field, ConfigDict
from typing import List, Dict, Any, Optional, Literal, Union
from langgraph.managed import RemainingSteps

class ChatMessageBase(BaseModel):
    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

class UserMessage(ChatMessageBase):
    role: Literal["user"] = "user"
    content: str

class AssistantMessage(ChatMessageBase):
    role: Literal["assistant"] = "assistant"
    content: Optional[str] = None
    thought: Optional[str] = None

class SystemMessage(ChatMessageBase):
    role: Literal["system"] = "system"
    content: str

class ToolMessage(ChatMessageBase):
    role: Literal["tool"] = "tool"
    content: str
    name: str
    tool_call_id: str

ChatMessage = Union[UserMessage, AssistantMessage, SystemMessage, ToolMessage]

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

class InvestigationBaseState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    doc_id: str = Field(description="Active investigation document partition ID context")
    investigation_plan: List[str] = Field(default_factory=list, description="Pending investigative tasks")
    verified_facts: List[VerifiedFact] = Field(default_factory=list, description="Ledger of Admissible Facts")
    explored_entities: List[str] = Field(default_factory=list, description="Visited entities to prevent infinite loops")
    current_task: Optional[str] = None
    messages: List[ChatMessage] = Field(default_factory=list, description="Conversation messages and thoughts")
    errors: List[str] = Field(default_factory=list, description="Warning/error traces")
    retry_count: int = 0

class InvestigationInputState(InvestigationBaseState):
    pending_fact_approval: Optional[PendingFactApproval] = None
    user_feedback: Optional[str] = None

class InvestigationOutputState(InvestigationBaseState):
    final_answer: Optional[str] = None
    pending_fact_approval: Optional[PendingFactApproval] = None
    user_feedback: Optional[str] = None

class InvestigationState(InvestigationBaseState):
    final_answer: Optional[str] = None
    pending_fact_approval: Optional[PendingFactApproval] = None
    user_feedback: Optional[str] = None
    
    # Transient fields for parallel execution. Pruned automatically.
    transient_doc_results: Optional[str] = None
    transient_graph_results: Optional[str] = None
    transient_synthesis_results: Optional[str] = None
    
    # Managed Value for recursion tracking
    remaining_steps: Optional[RemainingSteps] = None
