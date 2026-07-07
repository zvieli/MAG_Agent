import json
from pathlib import Path

import pytest

from src.models.base import SchemaConfigModel
from src.processing.strategies import DefaultEntityResolver


def _schema() -> SchemaConfigModel:
    data = json.loads(Path("schemas/ontology_schema.json").read_text(encoding="utf-8"))
    return SchemaConfigModel.model_validate(data)


async def _resolve(nodes, edges=None):
    resolver = DefaultEntityResolver(_schema())
    resolved_nodes, resolved_edges, report = await resolver.resolve(nodes, edges or [])
    return {node["id"]: node for node in resolved_nodes}, resolved_edges, report


@pytest.mark.asyncio
async def test_does_not_merge_different_elovitch_people():
    nodes = [
        {"id": "shaul_cohen", "label": "שאול כהן", "type": "OTHER"},
        {"id": "oron_cohen", "label": "אורן כהן", "type": "OTHER"},
        {"id": "moshe_levi", "label": "משה לוי", "type": "OTHER"},
    ]
    edges = [
        {"source": "shaul_cohen", "target": "moshe_levi", "relation": "RELATED_TO"},
        {"source": "oron_cohen", "target": "moshe_levi", "relation": "RELATED_TO"},
    ]

    resolved_nodes, _, report = await _resolve(nodes, edges)

    assert "shaul_cohen" in resolved_nodes
    assert "oron_cohen" in resolved_nodes
    assert report["merge_group_count"] == 0


@pytest.mark.asyncio
async def test_does_not_merge_person_into_bribery_offense():
    nodes = [
        {"id": "yisrael_israeli", "label": "ישראל ישראלי", "type": "OTHER"},
        {"id": "bribery_offer", "label": "הצעת שוחד", "type": "OTHER"},
        {"id": "moshe_levi", "label": "משה לוי", "type": "OTHER"},
    ]
    edges = [
        {"source": "yisrael_israeli", "target": "moshe_levi", "relation": "RELATED_TO"},
        {"source": "bribery_offer", "target": "moshe_levi", "relation": "RELATED_TO"},
    ]

    resolved_nodes, _, report = await _resolve(nodes, edges)

    assert "yisrael_israeli" in resolved_nodes
    assert "bribery_offer" in resolved_nodes
    assert report["merge_group_count"] == 0


@pytest.mark.asyncio
async def test_does_not_merge_distinct_numbered_cases():
    nodes = [
        {"id": "bribery_case_1000", "label": "פרשת 1000", "type": "OTHER"},
        {"id": "bribery_case_2000", "label": "פרשת 2000", "type": "OTHER"},
        {"id": "bribery_case_4000", "label": "פרשת 4000", "type": "OTHER"},
    ]

    resolved_nodes, _, report = await _resolve(nodes)

    assert set(resolved_nodes) == {
        "bribery_case_1000",
        "bribery_case_2000",
        "bribery_case_4000",
    }
    assert report["merge_group_count"] == 0


@pytest.mark.asyncio
async def test_does_not_merge_bezeq_org_with_bezeq_yes_deal_event():
    nodes = [
        {"id": "bezeq", "label": "בזק", "type": "OTHER"},
        {"id": "bezeq_yes_deal", "label": "אישור עסקת בזק-יס", "type": "OTHER"},
    ]

    resolved_nodes, _, report = await _resolve(nodes)

    assert "bezeq" in resolved_nodes
    assert "bezeq_yes_deal" in resolved_nodes
    assert report["merge_group_count"] == 0


@pytest.mark.asyncio
async def test_still_merges_clear_organization_aliases():
    nodes = [
        {"id": "walla", "label": "וואלה", "type": "OTHER"},
        {"id": "walla_news", "label": "וואלה", "type": "OTHER"},
    ]

    resolved_nodes, _, report = await _resolve(nodes)

    assert list(resolved_nodes) == ["canonical_walla"]
    assert report["merge_group_count"] == 1
    assert report["merged_alias_count"] == 1
