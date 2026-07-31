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
    quote: Optional[str] = None
    audit_trace: Dict[str, Any] = Field(
        default_factory=dict,
        description="Records tool name, arguments, and LLM reasoning trace leading to this fact"
    )

class PendingFactApproval(BaseModel):
    fact: str
    source_id: str
    doc_id: str
    grounding_score: float = 1.0
    source_type: Literal["doc_search", "graph_query", "synthesis"] = "doc_search"
    conflict_detected: bool = False
    quote: Optional[str] = None
    audit_trace: Dict[str, Any]

class InvestigationBaseState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    doc_id: str = Field(description="Active investigation document partition ID context")
    session_id: Optional[str] = Field(default=None, description="Persistent multi-turn investigation session ID")
    turn_index: int = Field(default=0, description="Sequential turn counter for current multi-turn session")
    investigation_plan: List[str] = Field(default_factory=list, description="Pending investigative tasks")
    verified_facts: List[VerifiedFact] = Field(default_factory=list, description="Ledger of Admissible Facts")
    explored_entities: List[str] = Field(default_factory=list, description="Visited entities to prevent infinite loops")
    trajectory_trace: List[Dict[str, Any]] = Field(default_factory=list, description="Audit ledger of trajectory step spans")
    current_task: Optional[str] = None
    messages: List[ChatMessage] = Field(default_factory=list, description="Conversation messages and thoughts")
    errors: List[str] = Field(default_factory=list, description="Warning/error traces")
    retry_count: int = 0
    autonomous_mode: bool = Field(default=False, description="When True, runs without HITL halts")
    pending_facts_queue: List[PendingFactApproval] = Field(default_factory=list, description="Queue of pending facts awaiting HITL review")



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
    transient_grounding_score: float = 1.0
    last_tool_path: List[str] = Field(default_factory=list)
    
    # Managed Value for recursion tracking
    remaining_steps: Optional[RemainingSteps] = None


ExternalQueryStateInput = Union[
    Dict[str, Any],
    InvestigationState,
    InvestigationInputState,
    InvestigationOutputState,
]


def to_external_query_state(state: ExternalQueryStateInput) -> Dict[str, Any]:
    """Return the JSON-safe query state that may be sent through Durable/client storage."""
    if isinstance(state, BaseModel):
        state_data = state.model_dump()
    else:
        state_data = state

    output_state = InvestigationOutputState.model_validate(state_data)
    return output_state.model_dump(mode="json", exclude_none=False)


def from_external_query_state(state: Dict[str, Any]) -> InvestigationState:
    """Rehydrate client/Durable JSON state into the internal Pydantic runtime state."""
    return InvestigationState.model_validate(state)
