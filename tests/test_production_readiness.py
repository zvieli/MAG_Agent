import asyncio

import pytest

from src.operations.dependency_checker import DependencyChecker


@pytest.mark.asyncio
async def test_dependency_checker_reports_required_failure():
    checker = DependencyChecker(timeout_seconds=1)

    async def healthy():
        return "ok"

    async def failing():
        raise RuntimeError("dns failed")

    checker._check_storage_config = healthy
    checker._check_openai = failing
    checker._check_document_intelligence = healthy

    result = await checker.check_all(include_optional=False)

    assert result["status"] == "Unhealthy"
    assert result["dependencies"]["openai"]["status"] == "Unhealthy"
    assert "dns failed" in result["dependencies"]["openai"]["message"]
    assert "document_intelligence" in result["configuration"]


@pytest.mark.asyncio
async def test_dependency_checker_times_out_probe():
    checker = DependencyChecker(timeout_seconds=0.01)

    async def slow():
        await asyncio.sleep(1)
        return "late"

    checker._check_storage_config = slow
    checker._check_openai = slow
    checker._check_document_intelligence = slow

    result = await checker.check_all(include_optional=False)

    assert result["status"] == "Unhealthy"
    assert result["dependencies"]["storage_config"]["status"] == "Unhealthy"
    assert "Timed out" in result["dependencies"]["storage_config"]["message"]


@pytest.mark.asyncio
async def test_dependency_checker_reports_document_intelligence_config(monkeypatch):
    checker = DependencyChecker(timeout_seconds=1)

    async def healthy():
        return "ok"

    checker._check_storage_config = healthy
    checker._check_openai = healthy
    checker._check_document_intelligence = healthy

    from src.operations import dependency_checker

    monkeypatch.setattr(
        dependency_checker.settings,
        "azure_document_intelligence_endpoint",
        "https://di-example.cognitiveservices.azure.com/",
    )
    monkeypatch.setattr(
        dependency_checker.settings,
        "azure_document_intelligence_key",
        "abcd1234secret5678",
    )
    monkeypatch.setattr(
        dependency_checker.settings,
        "azure_document_intelligence_model",
        "prebuilt-layout",
    )
    monkeypatch.setattr(
        dependency_checker.settings,
        "document_intelligence_concurrency",
        7,
    )

    result = await checker.check_all(include_optional=False)
    config = result["configuration"]["document_intelligence"]

    assert config["endpoint"] == "https://di-example.cognitiveservices.azure.com/"
    assert config["endpoint_host"] == "di-example.cognitiveservices.azure.com"
    assert config["key_configured"] is True
    assert config["key_preview"] == "abcd...5678"
    assert config["key_preview"] != "abcd1234secret5678"
    assert config["model"] == "prebuilt-layout"
    assert config["concurrency"] == 7


def test_orchestrator_batching_helper():
    from function_app import _batched

    assert _batched([], 4) == []
    assert _batched(["a"], 4) == [["a"]]
    assert _batched(["a", "b", "c", "d"], 4) == [["a", "b", "c", "d"]]
    assert _batched(["a", "b", "c", "d", "e"], 4) == [
        ["a", "b", "c", "d"],
        ["e"],
    ]
    assert _batched(["a", "b"], 0) == [["a"], ["b"]]


@pytest.mark.asyncio
async def test_embedding_generation_times_out(monkeypatch):
    from src.processing import orchestration_activities as activities

    class SlowEmbeddings:
        async def create(self, **kwargs):
            await asyncio.sleep(1)

    class SlowClient:
        embeddings = SlowEmbeddings()

    monkeypatch.setattr(activities, "_build_azure_client", lambda: SlowClient())
    monkeypatch.setattr(activities.settings, "llm_request_timeout_seconds", 0.01)

    with pytest.raises(TimeoutError, match="OpenAI embedding request timed out"):
        await activities._generate_embeddings(["hello"])
