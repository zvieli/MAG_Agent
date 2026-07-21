from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from azure.core.exceptions import ResourceNotFoundError

from scripts import backfill_protocol_completeness as backfill


class MemoryArtifactStore:
    def __init__(self):
        self.files = {}

    def read_bytes(self, path: str) -> bytes:
        return self.files[path]

    def write_bytes(self, path: str, data: bytes) -> None:
        self.files[path] = data

    def exists(self, path: str) -> bool:
        return path in self.files

    def list_files(self, prefix: str):
        return [path for path in self.files if path.startswith(prefix)]

    def delete(self, path: str) -> None:
        self.files.pop(path, None)

    def delete_folder(self, prefix: str) -> None:
        for path in list(self.files):
            if path.startswith(prefix):
                del self.files[path]


def test_vertex_and_edge_conversion_preserves_analyzer_fields():
    vertex = {
        "id": "suspect_1",
        "label": "SUSPECT",
        "properties": {
            "label": ["פלוני"],
            "type": ["SUSPECT"],
            "meta_forensic_summary": ["החשוד נחקר באזהרה."],
            "source_location": ["עמוד 3"],
        },
    }
    edge = {
        "id": "edge_1",
        "label": "SEIZED_FROM",
        "source": "event_1",
        "target": "phone_1",
        "properties": {
            "confidence": ["FACT"],
            "meta_forensic_summary": ["נתפס טלפון נייד."],
        },
    }

    node = backfill._vertex_to_node(vertex)
    record = backfill._edge_to_record(edge)

    assert node["id"] == "suspect_1"
    assert node["type"] == "SUSPECT"
    assert node["label"] == "פלוני"
    assert node["forensic_summary"] == "החשוד נחקר באזהרה."
    assert node["source_location"] == "עמוד 3"
    assert record["relation"] == "SEIZED_FROM"
    assert record["forensic_summary"] == "נתפס טלפון נייד."
    assert record["confidence"] == "FACT"


@pytest.mark.asyncio
async def test_fetch_graph_from_cosmos_builds_expected_payload():
    cosmos_mgr = SimpleNamespace()
    cosmos_mgr.execute_query = AsyncMock(
        side_effect=[
            [
                {
                    "id": "doc_1",
                    "label": "DOCUMENT",
                    "properties": {"label": ["דו\"ח פעולה"], "type": ["DOCUMENT"]},
                }
            ],
            [
                {
                    "id": "edge_1",
                    "label": "RELATED_TO",
                    "source": "doc_1",
                    "target": "event_1",
                    "properties": {"source_location": ["עמוד 1"]},
                }
            ],
        ]
    )

    graph = await backfill.fetch_graph_from_cosmos(cosmos_mgr, "case_1", limit=10)

    assert graph["metadata"]["doc_id"] == "case_1"
    assert graph["metadata"]["node_count"] == 1
    assert graph["metadata"]["edge_count"] == 1
    assert graph["nodes"][0]["label"] == "דו\"ח פעולה"
    assert graph["edges"][0]["relation"] == "RELATED_TO"


@pytest.mark.asyncio
async def test_fetch_raw_text_from_search_uses_exact_sanitized_chunk_ids(monkeypatch):
    requested_keys = []

    class FakeSearchClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get_document(self, key: str):
            requested_keys.append(key)
            if key.endswith("_1"):
                return {"text": "תלונה ראשונית"}
            if key.endswith("_2"):
                return {"text": "סיכום חקירה"}
            raise ResourceNotFoundError("missing")

    monkeypatch.setattr(backfill, "SearchClient", FakeSearchClient)
    monkeypatch.setattr(backfill.settings, "azure_search_index", "graphify-chunks")
    search_mgr = SimpleNamespace(endpoint="https://search.example", credential=object())

    raw_text, metadata = await backfill.fetch_raw_text_from_search(
        search_mgr,
        "תיק בדיקה",
        max_chunks=10,
        max_empty_chunk_misses=2,
    )

    assert "תלונה ראשונית" in raw_text
    assert "סיכום חקירה" in raw_text
    assert metadata["chunksFound"] == 2
    assert metadata["chunksAttempted"] == 4
    assert requested_keys[:2] == [
        backfill.SearchManager._sanitize_id("תיק בדיקה_1"),
        backfill.SearchManager._sanitize_id("תיק בדיקה_2"),
    ]


def test_write_report_artifacts_writes_endpoint_candidate_paths():
    store = MemoryArtifactStore()
    report = {"docId": "case_1", "summary": {"present": 1}, "items": []}

    paths = backfill.write_report_artifacts(store, "case_1", report)

    assert paths == [
        "graph/case_1/protocol_completeness.json",
        "graph/latest/protocol_completeness.json",
        "protocol_completeness.json",
    ]
    for path in paths:
        assert store.exists(path)
        assert b'"case_1"' in store.read_bytes(path)
