"""Tests for MAG_Agent System Architecture Upgrade (Tool Contracts, Policy Validation, Session Memory, Observability)."""

import pytest
from src.tools.contracts import (
    SearchQueryInput,
    SearchQueryResult,
    GraphQueryInput,
    GraphQueryResult,
    ToolExecutionError,
)
from src.storage.session_manager import SessionManager
from src.utils.observability import calculate_estimated_cost, log_trajectory_step


def test_search_query_input_contract():
    payload = SearchQueryInput(query="Bezeq deal", doc_id="files_01.01.2021", top_k=10)
    assert payload.doc_id == "files_01.01.2021"
    assert payload.top_k == 10


def test_graph_query_input_contract():
    payload = GraphQueryInput(doc_id="files_01.01.2021", gremlin_query="g.V().limit(5)")
    assert payload.doc_id == "files_01.01.2021"
    assert payload.gremlin_query == "g.V().limit(5)"


def test_session_manager_lifecycle():
    sm = SessionManager()
    session_id = "test-session-123"
    doc_id = "files_01.01.2021"

    # 1. New session
    state = sm.rehydrate_or_create(session_id, doc_id, "What happened with the Bezeq deal?")
    assert state.session_id == session_id
    assert state.turn_index == 1
    assert len(state.messages) == 1

    # Save session
    sm.save_session(session_id, state)

    # 2. Rehydrate session for Turn 2
    state_turn2 = sm.rehydrate_or_create(session_id, doc_id, "Who was involved in this deal?")
    assert state_turn2.session_id == session_id
    assert state_turn2.turn_index == 2
    assert len(state_turn2.messages) == 2


def test_cost_calculation():
    cost = calculate_estimated_cost(prompt_tokens=1000, completion_tokens=500, model_name="gpt-4o")
    # 1000 * 2.5e-6 + 500 * 1.0e-5 = 0.0025 + 0.005 = 0.0075
    assert cost == 0.0075
