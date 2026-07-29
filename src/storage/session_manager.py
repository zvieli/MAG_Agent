"""Multi-turn Session Memory Manager for MAG_Agent state persistence across turns."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from src.query_agent.state import InvestigationState, from_external_query_state, to_external_query_state

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages loading, updating, and saving multi-turn investigation sessions."""

    def __init__(self):
        self._in_memory_sessions: Dict[str, Dict[str, Any]] = {}

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve serialized state payload for a given session_id."""
        return self._in_memory_sessions.get(session_id)

    def save_session(self, session_id: str, state_payload: Dict[str, Any] | InvestigationState) -> None:
        """Persist or update state for session_id."""
        if isinstance(state_payload, InvestigationState):
            payload = to_external_query_state(state_payload)
        else:
            payload = state_payload
        self._in_memory_sessions[session_id] = payload
        logger.info(f"[SessionManager] Saved session state for session_id='{session_id}' (turn={payload.get('turn_index', 0)})")

    def rehydrate_or_create(
        self,
        session_id: str,
        doc_id: str,
        initial_question: str,
    ) -> InvestigationState:
        """Rehydrates an existing multi-turn session or initializes a new investigation state."""
        existing = self.get_session(session_id)
        if existing:
            state = from_external_query_state(existing)
            state.turn_index += 1
            from src.query_agent.state import UserMessage
            state.messages.append(UserMessage(content=initial_question))
            logger.info(f"[SessionManager] Rehydrated session='{session_id}' -> advancing to turn {state.turn_index}")
            return state

        from src.query_agent.state import UserMessage
        state = InvestigationState(
            doc_id=doc_id,
            session_id=session_id,
            turn_index=1,
        )
        state.messages.append(UserMessage(content=initial_question))
        logger.info(f"[SessionManager] Created new investigation session='{session_id}'")
        return state
