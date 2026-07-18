# Design Specification: LangGraph Recursion Counter & Schema Separation

This design specification details the integration of:
1. **Proactive Recursion Handling** using LangGraph's managed value `RemainingSteps`.
2. **Explicit Input and Output Schemas** to separate public interfaces from internal transient state fields.

---

## 1. Proactive Recursion Handling (`RemainingSteps`)
To avoid crash conditions where the state machine runs out of execution steps and raises a hard `GraphRecursionError`, we will proactively monitor the remaining steps within the graph execution run.

### State & Node Updates:
1. Add `remaining_steps: Optional[RemainingSteps] = None` to the state schema.
2. In `coordinator_node` and `planner_node`, we will check if the remaining steps are running low (e.g. `remaining_steps <= 2`).
3. If the limit is approaching, the nodes will output a warning log and dynamically route to the `finalizer_node` immediately, bypassing the remaining plan tasks. This guarantees that the graph returns the best-effort report compiled from the currently verified facts instead of crashing.

---

## 2. Explicit Input and Output Schemas
To encapsulate internal graph state (transient execution details and counter metrics), we will separate the schemas.

### Schema Breakdown:

```python
class InvestigationBaseState(BaseModel):
    doc_id: str = Field(description="Active investigation document partition ID context")
    investigation_plan: List[str] = Field(default_factory=list, description="Pending investigative tasks")
    verified_facts: List[VerifiedFact] = Field(default_factory=list, description="Ledger of Admissible Facts")
    explored_entities: List[str] = Field(default_factory=list, description="Visited entities to prevent infinite loops")
    current_task: Optional[str] = None
    messages: List[Dict[str, Any]] = Field(default_factory=list, description="Conversation messages and thoughts")
    errors: List[str] = Field(default_factory=list, description="Warning/error traces")
    retry_count: int = 0
```

1. **`InvestigationInputState(InvestigationBaseState)`**:
   - Includes standard inputs plus optional interactive feedback fields:
     - `pending_fact_approval: Optional[PendingFactApproval] = None`
     - `user_feedback: Optional[str] = None`

2. **`InvestigationOutputState(InvestigationBaseState)`**:
   - Includes standard output fields plus final analysis and review payloads:
     - `final_answer: Optional[str] = None`
     - `pending_fact_approval: Optional[PendingFactApproval] = None`
     - `user_feedback: Optional[str] = None`

3. **`InvestigationState(InvestigationBaseState)`**:
   - The overall internal state of the graph. Inherits `InvestigationBaseState` and adds transient and runtime fields:
     - `final_answer: Optional[str] = None`
     - `pending_fact_approval: Optional[PendingFactApproval] = None`
     - `user_feedback: Optional[str] = None`
     - `transient_doc_results: Optional[str] = None`
     - `transient_graph_results: Optional[str] = None`
     - `transient_synthesis_results: Optional[str] = None`
     - `remaining_steps: Optional[RemainingSteps] = None`

---

## 3. Graph Compilation Changes
We compile the `StateGraph` in `src/query_agent/nodes.py` by supplying the overall state, plus input and output schemas:
```python
builder = StateGraph(
    InvestigationState,
    input_schema=InvestigationInputState,
    output_schema=InvestigationOutputState
)
```

---

## 4. Verification Plan

### Automated Tests
1. Add new pytest unit tests in `tests/test_langgraph_agent.py` to:
   - Verify `RemainingSteps` integration and test early fallback behavior (e.g. setting `recursion_limit` low and verifying it completes at `finalizer` with a partial report instead of throwing `GraphRecursionError`).
   - Verify input/output schema constraints at compilation time.
2. Execute the entire test suite `pytest` to verify all integration runs pass successfully.
