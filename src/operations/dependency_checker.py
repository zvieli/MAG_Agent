from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from src.utils.config import settings


@dataclass
class DependencyProbe:
    name: str
    status: str
    latency_ms: int
    message: str = ""
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "latency_ms": self.latency_ms,
            "message": self.message,
            "required": self.required,
        }


class DependencyChecker:
    """Reusable runtime dependency probes for health, preflight, and CLI checks."""

    def __init__(self, timeout_seconds: float | None = None):
        self.timeout_seconds = timeout_seconds or settings.connectivity_probe_timeout_seconds

    async def check_all(self, *, include_optional: bool = True) -> dict[str, Any]:
        probes = [
            await self._run_probe("storage_config", self._check_storage_config),
            await self._run_probe("openai", self._check_openai),
            await self._run_probe(
                "document_intelligence", self._check_document_intelligence
            ),
        ]

        if include_optional and settings.use_azure_search:
            probes.append(await self._run_probe("azure_search", self._check_search))

        if include_optional and settings.cosmos_gremlin_endpoint:
            probes.append(await self._run_probe("cosmos_db", self._check_cosmos))

        status = "Healthy"
        if any(p.required and p.status != "Healthy" for p in probes):
            status = "Unhealthy"
        elif any(p.status != "Healthy" for p in probes):
            status = "Degraded"

        return {
            "status": status,
            "dependencies": {probe.name: probe.as_dict() for probe in probes},
            "configuration": {
                "storage_config": self._storage_config_info(),
                "openai": self._openai_config(),
                "document_intelligence": self._document_intelligence_config(),
                "azure_search": self._azure_search_config(),
                "cosmos_db": self._cosmos_db_config(),
            },
        }

    async def ensure_ready(self) -> dict[str, Any]:
        result = await self.check_all(include_optional=True)
        failures = []
        for name, probe in result["dependencies"].items():
            if probe["required"] and probe["status"] != "Healthy":
                failures.append(
                    f"{name} (Status: {probe['status']}, Error: {probe['message']})"
                )
        if failures:
            import json
            import logging

            logging.error(
                "[DEPENDENCY_CHECKER] Preflight checks failed! Full Report:\n%s",
                json.dumps(result, indent=2),
            )
            raise RuntimeError(
                f"Preflight dependency check failed: {', '.join(failures)}"
            )
        return result

    async def _run_probe(
        self, name: str, probe_func, *, required: bool = True
    ) -> DependencyProbe:
        start = time.perf_counter()
        try:
            message = await asyncio.wait_for(probe_func(), timeout=self.timeout_seconds)
            status = "Healthy"
        except asyncio.TimeoutError:
            status = "Unhealthy"
            message = f"Timed out after {self.timeout_seconds:.1f}s"
        except Exception as exc:
            status = "Unhealthy"
            message = str(exc)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return DependencyProbe(
            name=name,
            status=status,
            latency_ms=latency_ms,
            message=message,
            required=required,
        )

    async def _check_storage_config(self) -> str:
        from src.utils.config_manager import config_manager

        await config_manager.get_config("ontology_schema.json", category="schemas")

        local_path = config_manager._resolve_local_path(
            "ontology_schema.json", "schemas"
        )
        if local_path is not None:
            return f"Read schemas/ontology_schema.json from local filesystem ({local_path})"
        return f"Read schemas/ontology_schema.json from cloud ({config_manager.container_name})"

    async def _check_openai(self) -> str:
        from src.extraction.extraction_engine import _build_azure_client

        client = _build_azure_client()
        model = (
            settings.azure_openai_embedding_deployment
            or settings.azure_openai_embedding_model
            or settings.openai_embedding_model
        )
        response = await client.embeddings.create(input=["ping"], model=model)
        if not getattr(response, "data", None):
            raise RuntimeError("OpenAI embedding probe returned no data")
        return f"Embedding probe succeeded with {model}"

    async def _check_document_intelligence(self) -> str:
        if not settings.azure_document_intelligence_endpoint:
            raise RuntimeError("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT is missing")
        if not settings.azure_document_intelligence_key:
            raise RuntimeError("AZURE_DOCUMENT_INTELLIGENCE_KEY is missing")

        parsed = urlparse(settings.azure_document_intelligence_endpoint)
        host = parsed.hostname
        if not host:
            raise RuntimeError("Document Intelligence endpoint host is invalid")
        await asyncio.to_thread(socket.getaddrinfo, host, 443)

        from azure.ai.documentintelligence.aio import (
            DocumentIntelligenceAdministrationClient,
        )
        from azure.core.credentials import AzureKeyCredential

        async with DocumentIntelligenceAdministrationClient(
            endpoint=settings.azure_document_intelligence_endpoint,
            credential=AzureKeyCredential(settings.azure_document_intelligence_key),
            api_version="2024-11-30",
        ) as client:
            await client.get_resource_details()
        return f"Document Intelligence probe succeeded for {host}"

    def _document_intelligence_config(self) -> dict[str, Any]:
        endpoint = settings.azure_document_intelligence_endpoint
        parsed = urlparse(endpoint) if endpoint else None
        key = settings.azure_document_intelligence_key
        return {
            "endpoint": endpoint,
            "endpoint_host": parsed.hostname if parsed else None,
            "key_configured": bool(key),
            "key_preview": self._mask_secret(key),
            "model": settings.azure_document_intelligence_model,
            "timeout_seconds": settings.document_intelligence_timeout_seconds,
            "concurrency": settings.document_intelligence_concurrency,
            "max_attempts": settings.document_intelligence_max_attempts,
            "backoff_base_seconds": settings.document_intelligence_backoff_base,
            "backoff_max_seconds": settings.document_intelligence_backoff_max,
        }

    def _storage_config_info(self) -> dict[str, Any]:
        from src.utils.config_manager import config_manager
        conn_str = config_manager.connection_string
        return {
            "container_name": config_manager.container_name,
            "connection_string_configured": bool(conn_str),
            "connection_string_preview": self._mask_secret(conn_str),
            "cloud_only": config_manager.cloud_only,
            "ttl_seconds": config_manager.ttl_seconds,
            "base_directory": str(config_manager._BASE_DIR),
        }

    def _openai_config(self) -> dict[str, Any]:
        azure_key = settings.azure_openai_api_key
        openai_key = settings.openai_api_key
        return {
            "azure_endpoint": settings.azure_openai_endpoint,
            "azure_api_version": settings.azure_openai_api_version,
            "azure_key_configured": bool(azure_key),
            "azure_key_preview": self._mask_secret(azure_key),
            "openai_api_key_configured": bool(openai_key),
            "openai_api_key_preview": self._mask_secret(openai_key),
            "openai_base_url": settings.openai_base_url,
            "openai_api_version": settings.openai_api_version,
            "graphify_extraction_model": settings.graphify_extraction_model,
            "graphify_agent_model": settings.graphify_agent_model,
            "azure_openai_embedding_deployment": settings.azure_openai_embedding_deployment,
            "azure_openai_embedding_model": settings.azure_openai_embedding_model,
            "openai_embedding_model": settings.openai_embedding_model,
            "temperature": settings.llm_temperature,
            "fallback_temperature": settings.llm_fallback_temperature,
            "timeout_seconds": settings.llm_request_timeout_seconds,
        }

    def _azure_search_config(self) -> dict[str, Any]:
        endpoint = settings.azure_search_endpoint
        parsed = urlparse(endpoint) if endpoint else None
        key = settings.azure_search_key
        return {
            "endpoint": endpoint,
            "endpoint_host": parsed.hostname if parsed else None,
            "key_configured": bool(key),
            "key_preview": self._mask_secret(key),
            "use_azure_search": settings.use_azure_search,
            "index": settings.azure_search_index,
            "index_nodes": settings.azure_search_index_nodes,
            "index_edges": settings.azure_search_index_edges,
        }

    def _cosmos_db_config(self) -> dict[str, Any]:
        endpoint = settings.cosmos_gremlin_endpoint
        parsed = urlparse(endpoint) if endpoint else None
        key = settings.cosmos_gremlin_key
        return {
            "gremlin_endpoint": endpoint,
            "endpoint_host": parsed.hostname if parsed else None,
            "key_configured": bool(key),
            "key_preview": self._mask_secret(key),
            "database": settings.cosmos_database,
            "graph": settings.cosmos_graph,
            "batch_size": settings.cosmos_batch_size,
        }

    @staticmethod
    def _mask_secret(value: str | None) -> str | None:
        if not value:
            return None
        if len(value) <= 8:
            return "***"
        return f"{value[:4]}...{value[-4:]}"

    async def _check_search(self) -> str:
        from src.storage.factory import get_search_manager

        search_mgr = get_search_manager()
        await search_mgr.index_client.get_index(settings.azure_search_index)
        return f"Search index reachable: {settings.azure_search_index}"

    async def _check_cosmos(self) -> str:
        from src.storage.factory import get_cosmos_manager

        cosmos_mgr = get_cosmos_manager()
        await cosmos_mgr.execute_query("g.V().limit(1).count()")
        return f"Cosmos DB connection verified for {settings.cosmos_database}/{settings.cosmos_graph}"

    async def close(self) -> None:
        """Closes any cached client connections cleanly."""
        from src.storage.factory import _SEARCH_MANAGER

        if _SEARCH_MANAGER is not None:
            try:
                await _SEARCH_MANAGER.close()
            except Exception:
                pass

        from src.extraction.extraction_engine import close_openai_client

        try:
            await close_openai_client()
        except Exception:
            pass
