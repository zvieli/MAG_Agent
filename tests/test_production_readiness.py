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


@pytest.mark.asyncio
async def test_dependency_checker_reports_all_configs(monkeypatch):
    checker = DependencyChecker(timeout_seconds=1)

    async def healthy():
        return "ok"

    checker._check_storage_config = healthy
    checker._check_openai = healthy
    checker._check_document_intelligence = healthy
    checker._check_search = healthy
    checker._check_cosmos = healthy

    from src.operations import dependency_checker

    # Setup monkeypatch config values
    monkeypatch.setattr(dependency_checker.settings, "azure_openai_endpoint", "https://api.openai.com")
    monkeypatch.setattr(dependency_checker.settings, "azure_openai_api_key", "sk-proj-12345678openai")
    monkeypatch.setattr(dependency_checker.settings, "azure_search_endpoint", "https://my-search.search.windows.net")
    monkeypatch.setattr(dependency_checker.settings, "azure_search_key", "searchkey-12345678")
    monkeypatch.setattr(dependency_checker.settings, "cosmos_gremlin_endpoint", "https://my-cosmos.gremlin.cosmos.azure.com:443/")
    monkeypatch.setattr(dependency_checker.settings, "cosmos_gremlin_key", "cosmoskey-12345678")

    from src.utils.config_manager import config_manager
    monkeypatch.setattr(config_manager, "connection_string", "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=mykey;")

    result = await checker.check_all(include_optional=True)
    configs = result["configuration"]

    # Verify storage_config
    assert "storage_config" in configs
    assert configs["storage_config"]["container_name"] == config_manager.container_name
    assert configs["storage_config"]["connection_string_configured"] is True
    assert configs["storage_config"]["connection_string_preview"] == "Defa...key;"

    # Verify openai
    assert "openai" in configs
    assert configs["openai"]["azure_endpoint"] == "https://api.openai.com"
    assert configs["openai"]["azure_key_configured"] is True
    assert configs["openai"]["azure_key_preview"] == "sk-p...enai"

    # Verify azure_search
    assert "azure_search" in configs
    assert configs["azure_search"]["endpoint"] == "https://my-search.search.windows.net"
    assert configs["azure_search"]["endpoint_host"] == "my-search.search.windows.net"
    assert configs["azure_search"]["key_configured"] is True
    assert configs["azure_search"]["key_preview"] == "sear...5678"

    # Verify cosmos_db
    assert "cosmos_db" in configs
    assert configs["cosmos_db"]["gremlin_endpoint"] == "https://my-cosmos.gremlin.cosmos.azure.com:443/"
    assert configs["cosmos_db"]["endpoint_host"] == "my-cosmos.gremlin.cosmos.azure.com"
    assert configs["cosmos_db"]["key_configured"] is True
    assert configs["cosmos_db"]["key_preview"] == "cosm...5678"

