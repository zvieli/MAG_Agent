import json
from unittest.mock import AsyncMock, MagicMock, patch

import networkx as nx
import pytest

from src.models.base import SchemaConfigModel
from src.processing import orchestration_activities as activities
from src.reporting.report_generator import (
    analyze_graph_health,
    fallback_community_summaries,
    summarize_communities,
)


def test_schema_disables_llm_community_summaries_by_default():
    data = json.loads(open("schemas/ontology_schema.json", encoding="utf-8").read())
    schema = SchemaConfigModel.model_validate(data)

    assert schema.graph_analysis.enable_llm_community_summaries is False


@pytest.mark.asyncio
async def test_summarize_communities_uses_fallback_without_llm_call():
    graph = nx.Graph()
    graph.add_node("suspect", label="חשוד מרכזי")
    graph.add_node("event", label="אירוע מעצר")
    graph.add_edge("suspect", "event")
    communities = {"c_0": ["suspect", "event"]}
    labels = {}

    with patch("src.reporting.report_generator._build_azure_client") as build_client:
        summaries = await summarize_communities(
            graph,
            communities,
            labels,
            enable_llm=False,
        )

    build_client.assert_not_called()
    assert summaries == {"c_0": "קהילה מצומצמת המרוכזת סביב חשוד מרכזי."}
    assert labels == {"c_0": "חשוד מרכזי"}


def test_analyze_graph_health_does_not_penalize_missing_optional_llm_summaries():
    graph = nx.Graph()
    graph.graph["communities"] = {"c_0": ["a", "b", "c", "d"]}
    graph.graph["llm_community_summaries_enabled"] = False
    for node_id in ["a", "b", "c", "d"]:
        graph.add_node(node_id, label=node_id, description=f"description {node_id}")
    graph.add_edges_from([("a", "b"), ("b", "c"), ("c", "d")])

    mock_store = MagicMock()
    mock_store.exists.return_value = False
    with patch("src.reporting.report_generator.get_artifact_store", return_value=mock_store):
        report = analyze_graph_health(graph)

    assert report["community_summary_scoring_enabled"] is False
    assert report["communities"][0]["summary"] == "No community_summary available."
    assert report["final_quality_score"] > 1


def test_fallback_community_summaries_selects_highest_degree_label():
    graph = nx.Graph()
    graph.add_node("center", label="ציר מרכזי")
    graph.add_node("leaf_1", label="עלה 1")
    graph.add_node("leaf_2", label="עלה 2")
    graph.add_edges_from([("center", "leaf_1"), ("center", "leaf_2")])
    labels = {}

    summaries = fallback_community_summaries(
        graph,
        {"c_0": ["center", "leaf_1", "leaf_2"]},
        labels,
    )

    assert labels["c_0"] == "ציר מרכזי"
    assert summaries["c_0"] == "קהילה מצומצמת המרוכזת סביב ציר מרכזי."


@pytest.mark.asyncio
async def test_community_summary_payload_skips_llm_summarizer_when_disabled():
    graph = nx.Graph()
    graph.add_node("a", label="א")
    graph.add_node("b", label="ב")
    graph.add_edge("a", "b")

    with patch(
        "src.processing.orchestration_activities.summarize_communities",
        new_callable=AsyncMock,
    ) as summarize_mock:
        summaries, labels, payload, enabled = await activities._build_community_summary_payload(
            graph,
            {"c_0": ["a", "b"]},
            {},
        )

    summarize_mock.assert_not_called()
    assert enabled is False
    assert labels == {"c_0": "א"}
    assert summaries == {"c_0": "קהילה מצומצמת המרוכזת סביב א."}
    assert payload["summaryMode"] == "fallback"
    assert payload["llmEnabled"] is False
