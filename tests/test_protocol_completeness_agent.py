import pytest
from unittest.mock import AsyncMock, patch
from src.reporting.protocol_completeness import (
    STATUS_MISSING,
    STATUS_PRESENT,
    verify_missing_items_with_agent,
)


@pytest.mark.asyncio
async def test_verify_missing_items_agent_grounded_upgrade():
    """Verify that a missing item is upgraded to STATUS_PRESENT when LLM returns a grounded quote."""
    report = {
        "docId": "case_101",
        "summary": {"totalItems": 1, "applicableItems": 1, "present": 0, "missing": 1, "coveragePercent": 0.0},
        "items": [
            {
                "id": "rights_warning_form",
                "pillar": "interrogations",
                "label": "אזהרה וזכויות נחקר",
                "description": "תיעוד אזהרת חשוד וזכות היוועצות",
                "severity": "critical",
                "status": STATUS_MISSING,
                "reason": "לא אותר תיעוד של אזהרת החשוד",
                "evidence": [],
            }
        ],
    }

    raw_text = "הנחקר הוזהר בלשון זו: דע לך כי אתה נחקר באזהרה ויש לך זכות להיוועץ בעורך דין."

    mock_llm_json = """
    {
        "matches": [
            {
                "item_id": "rights_warning_form",
                "status": "found",
                "confidence": 0.95,
                "verbatim_quote": "דע לך כי אתה נחקר באזהרה ויש לך זכות להיוועץ בעורך דין",
                "source_location": "doc_101_page_1.pdf",
                "reason": "אותר תיעוד מפורש של אזהרת נחקר"
            }
        ]
    }
    """

    with patch("src.query_agent.nodes.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_llm_json

        updated = await verify_missing_items_with_agent(
            report=report,
            doc_id="case_101",
            raw_text=raw_text,
            critical_only=False,
        )

        item = updated["items"][0]
        assert item["status"] == STATUS_PRESENT
        assert item["evidenceStrength"] == "agent_verified"
        assert item["agentVerification"]["verifiedByAgent"] is True
        assert "דע לך כי אתה נחקר באזהרה" in item["agentVerification"]["verbatimQuote"]
        assert updated["summary"]["coveragePercent"] == 100.0


@pytest.mark.asyncio
async def test_verify_missing_items_agent_ungrounded_rejection():
    """Verify that if LLM returns a quote NOT in the text, the item is NOT upgraded (ironclad grounding rule)."""
    report = {
        "docId": "case_102",
        "summary": {"totalItems": 1, "applicableItems": 1, "present": 0, "missing": 1, "coveragePercent": 0.0},
        "items": [
            {
                "id": "rights_warning_form",
                "pillar": "interrogations",
                "label": "אזהרה וזכויות נחקר",
                "description": "תיעוד אזהרת חשוד וזכות היוועצות",
                "severity": "critical",
                "status": STATUS_MISSING,
                "reason": "לא אותר תיעוד של אזהרת החשוד",
                "evidence": [],
            }
        ],
    }

    raw_text = "דברי הסבר כלליים על האירוע."

    # LLM hallucinates a quote not in raw_text
    mock_llm_json = """
    {
        "matches": [
            {
                "item_id": "rights_warning_form",
                "status": "found",
                "confidence": 0.90,
                "verbatim_quote": "החשוד הוזהר כחוק בנוכחות עורכי דין",
                "source_location": "doc_102_page_1.pdf",
                "reason": "הנחה שהייתה אזהרה"
            }
        ]
    }
    """


    with patch("src.query_agent.nodes.call_llm", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = mock_llm_json

        updated = await verify_missing_items_with_agent(
            report=report,
            doc_id="case_102",
            raw_text=raw_text,
            critical_only=False,
        )

        item = updated["items"][0]
        # Should NOT upgrade because quote is ungrounded
        assert item["status"] == STATUS_MISSING
        assert item["agentVerification"]["verifiedByAgent"] is False
