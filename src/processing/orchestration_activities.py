import json
import logging
import asyncio
import os
import hashlib
from pathlib import Path
from typing import Any, Dict, List

from src.utils.config import settings
from src.storage.factory import (
    get_artifact_store,
    get_search_manager,
    get_cosmos_manager,
)
from src.storage.interfaces import IArtifactStore
from src.extraction.extraction_engine import _build_azure_client
from src.extraction.extraction_engine import extract_from_chunk
from src.extraction.extraction_engine import _estimate_token_count, _schema_version_for
from src.extraction.provider_adapter import ExtractionOutcomeCode, ProviderOutcomeError
from src.extraction.wire_schema import (
    create_wire_extraction_model,
    schema_fingerprint,
)
from src.processing.document_processor import (
    _load_document_pages,
    _load_document_pages_via_di,
    _build_discrete_chunks,
)
from src.models.models import RawGraphExtraction, SCHEMA_CONFIG
from src.storage.search_manager import SearchManager
from src.processing.strategies import DefaultEntityResolver, DefaultReporter
from src.graph.local_graphify import build_from_json, cluster
from src.reporting.report_generator import (
    fallback_community_summaries,
    summarize_communities,
)
from src.reporting.protocol_completeness import analyze_protocol_completeness
from src.processing.persistence_manager import PersistenceManager
from src.storage.blob_storage import AzureBlobStorage
from src.utils.observability import log_event, log_metric


def _compact_error(exc: Exception | str, *, limit: int = 3000) -> str:
    message = str(exc)
    if len(message) <= limit:
        return message
    return f"{message[:limit]}... [truncated; original length={len(message)} chars]"


def _get_artifact_store() -> IArtifactStore:
    return get_artifact_store()


MERGE_ARTIFACT_CONCURRENCY = max(
    1, int(os.getenv("MERGE_ARTIFACT_CONCURRENCY", "8"))
)


def _doc_id_from_blob_path(blob_path: str) -> str:
    return Path(blob_path).stem


def _chunk_manifest_path(doc_id: str) -> str:
    return f"chunks/{doc_id}/list.json"


def _chunk_artifact_path(doc_id: str, chunk_id: str) -> str:
    return f"chunks/{doc_id}/{chunk_id}.json"


def _extraction_artifact_path(doc_id: str, chunk_id: str) -> str:
    return f"extracted/{doc_id}/{chunk_id}.json"


def _partial_graph_path(doc_id: str) -> str:
    return f"graph/{doc_id}/partial.json"


def _final_graph_path(doc_id: str) -> str:
    return f"graph/{doc_id}/final.json"


def _ingestion_manifest_path(doc_id: str) -> str:
    return f"graph/{doc_id}/ingestion_manifest.json"


def _extracted_text_path(doc_id: str) -> str:
    return f"extracted_text/{doc_id}.md"


def _processed_text_path(doc_id: str) -> str:
    return f"extracted_text/{doc_id}.processed.md"


async def _build_community_summary_payload(
    G: Any,
    communities: Dict[str, List[str]],
    hierarchy: Dict[str, str],
) -> tuple[dict[str, str], dict[str, str], dict[str, Any], bool]:
    enable_llm_summaries = bool(
        SCHEMA_CONFIG.graph_analysis.enable_llm_community_summaries
    )
    community_labels: dict[str, str] = {}
    if enable_llm_summaries:
        summaries = await summarize_communities(
            G,
            communities,
            community_labels,
            enable_llm=True,
        )
    else:
        logging.info(
            "[ACTIVITY][FINAL_INDEXING] LLM community summaries disabled; using deterministic fallbacks."
        )
        log_metric("community_summary_llm_skipped_count", 1)
        summaries = fallback_community_summaries(G, communities, community_labels)

    payload = {
        "summaries": summaries,
        "hierarchy": hierarchy,
        "llmEnabled": enable_llm_summaries,
        "summaryMode": "llm" if enable_llm_summaries else "fallback",
    }
    return summaries, community_labels, payload, enable_llm_summaries


class _ArtifactPersistence:
    def __init__(self, artifact_store: IArtifactStore):
        self.artifact_store = artifact_store

    async def save_json(self, data: Any, filename: str, indent: int = 2):
        await asyncio.to_thread(
            self.artifact_store.write_bytes,
            filename,
            json.dumps(data, ensure_ascii=False, indent=indent).encode("utf-8"),
        )


async def _generate_embeddings(texts: List[str]) -> List[List[float]]:
    import random
    try:
        from openai import RateLimitError
    except ImportError:
        RateLimitError = None

    if not texts:
        return []
    client = _build_azure_client()
    model = (
        settings.azure_openai_embedding_deployment
        or settings.azure_openai_embedding_model
        or settings.openai_embedding_model
    )
    
    max_attempts = 5
    backoff = 1.0
    for attempt in range(max_attempts):
        try:
            response = await asyncio.wait_for(
                client.embeddings.create(input=texts, model=model),
                timeout=settings.llm_request_timeout_seconds,
            )
            return [item.embedding for item in response.data]
        except (asyncio.TimeoutError, Exception) as e:
            if attempt == max_attempts - 1:
                logging.error(f"Failed to generate embeddings after {max_attempts} attempts: {e}")
                if isinstance(e, asyncio.TimeoutError):
                    raise TimeoutError(
                        f"OpenAI embedding request timed out after {settings.llm_request_timeout_seconds:.1f}s"
                    ) from e
                raise
            
            is_rate_limit = False
            if RateLimitError and isinstance(e, RateLimitError):
                is_rate_limit = True
            elif hasattr(e, "status_code") and e.status_code == 429:
                is_rate_limit = True

            if is_rate_limit:
                sleep_time = min(30.0, 2.0 * (2 ** attempt)) + random.uniform(0, 1.0)
                logging.warning(f"Embedding call rate limited (429): {e}. Retrying in {sleep_time:.1f}s (attempt {attempt + 1}/{max_attempts})...")
            else:
                sleep_time = backoff * (2 ** attempt) + random.uniform(0, 0.5)
                logging.warning(f"Embedding call failed: {e}. Retrying in {sleep_time:.1f}s (attempt {attempt + 1}/{max_attempts})...")
            
            await asyncio.sleep(sleep_time)


async def _index_edge_labels(
    graph_data: Dict[str, Any], filename: str, persistence: Any
) -> None:
    logging.info("[ACTIVITY][FINAL_INDEXING] Edge label indexing to AI Search is disabled per design.")
    return


async def _index_chunks(doc_id: str, chunks: List[Dict[str, Any]]) -> None:
    if (
        not getattr(settings, "use_azure_search", False)
        or not settings.azure_search_endpoint
    ):
        return

    logging.info("[ACTIVITY][CHUNKING] Indexing %d chunks to AI Search", len(chunks))
    
    batch_size = max(1, int(getattr(settings, "azure_search_batch_size", 20)))
    search_mgr = get_search_manager()
    await search_mgr.ensure_investigation_indices()

    semaphore = asyncio.Semaphore(5)

    async def index_batch(batch_idx: int, chunk_batch: List[Dict[str, Any]]) -> None:
        async with semaphore:
            texts = [c["text"] for c in chunk_batch]
            embeddings = await _generate_embeddings(texts)
            if not embeddings:
                logging.warning(f"Failed to generate embeddings for batch {batch_idx}")
                return

            documents = []
            for j, chunk in enumerate(chunk_batch):
                documents.append(
                    {
                        "id": SearchManager._sanitize_id(f"{doc_id}_{chunk['chunk_index']}"),
                        "text": chunk["text"],
                        "embedding": embeddings[j],
                        "source_file": doc_id,
                    }
                )
            await search_mgr.upload_documents(settings.azure_search_index, documents)

    tasks = []
    for i in range(0, len(chunks), batch_size):
        chunk_batch = chunks[i : i + batch_size]
        batch_idx = (i // batch_size) + 1
        tasks.append(index_batch(batch_idx, chunk_batch))

    if tasks:
        await asyncio.gather(*tasks)


async def _delete_indexed_chunks(doc_id: str, chunk_ids: List[str]) -> None:
    """Ensure newly blocked chunks cannot remain in the semantic retrieval index."""
    if (
        not chunk_ids
        or not getattr(settings, "use_azure_search", False)
        or not settings.azure_search_endpoint
    ):
        return

    search_mgr = get_search_manager()
    await search_mgr.ensure_investigation_indices()
    documents = [
        {
            "id": SearchManager._sanitize_id(
                f"{doc_id}_{int(chunk_id) if str(chunk_id).isdigit() else chunk_id}"
            )
        }
        for chunk_id in chunk_ids
    ]
    client = search_mgr.get_search_client(settings.azure_search_index)
    await client.delete_documents(documents=documents)


async def _index_nodes(doc_id: str, nodes: List[Dict[str, Any]]) -> None:
    logging.info("[ACTIVITY][FINAL_INDEXING] Node indexing to AI Search is disabled per design.")
    return


async def run_evaluation_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"status": "success"}


async def run_chunking(
    payload: Dict[str, Any], artifact_store: IArtifactStore | None = None
) -> Dict[str, Any]:
    store = artifact_store or _get_artifact_store()
    input_blob_path = payload["input_blob_path"]
    doc_id = _doc_id_from_blob_path(input_blob_path)

    if str(input_blob_path).lower().endswith(".pdf"):
        file_bytes = await asyncio.to_thread(store.read_bytes, input_blob_path)
        logging.info(
            "[ACTIVITY][CHUNKING] read bytes | blob_path=%s | file_size_bytes=%d",
            input_blob_path,
            len(file_bytes) if file_bytes else 0,
        )

        if file_bytes and not file_bytes.startswith(b"%PDF"):
            logging.error(
                "[ACTIVITY][CHUNKING] Payload is not a valid PDF! First 10 bytes: %s",
                file_bytes[:10],
            )

        pdf_hash = hashlib.sha256(file_bytes).hexdigest() if file_bytes else "empty"
        di_cache_path = f"di_cache/{pdf_hash}.json"

        pages = None
        if await asyncio.to_thread(store.exists, di_cache_path):
            try:
                cached_bytes = await asyncio.to_thread(store.read_bytes, di_cache_path)
                pages = json.loads(cached_bytes.decode("utf-8"))
                logging.info("[ACTIVITY][CHUNKING] Document Intelligence layout cache hit: %s", di_cache_path)
            except Exception as exc:
                logging.warning("[ACTIVITY][CHUNKING] Failed to read DI layout cache: %s", exc)

        if pages is None:
            pages = await _load_document_pages_via_di(file_bytes)
            try:
                cache_payload = json.dumps(pages, ensure_ascii=False, indent=2).encode("utf-8")
                await asyncio.to_thread(store.write_bytes, di_cache_path, cache_payload)
                logging.info("[ACTIVITY][CHUNKING] Saved Document Intelligence layout cache: %s", di_cache_path)
            except Exception as exc:
                logging.warning("[ACTIVITY][CHUNKING] Failed to write DI layout cache: %s", exc)
    else:
        pages = await asyncio.to_thread(_load_document_pages, input_blob_path, store)

    chunks = _build_discrete_chunks(pages)

    # 1. Generate and save Unified Markdown Artifact (Ground Truth)
    md_content = []
    for p in pages:
        md_content.append(f"## Page {p['page_number']}\n\n{p['text']}\n")

    full_md_text = "\n".join(md_content)
    md_path = _extracted_text_path(doc_id)
    await asyncio.to_thread(store.write_bytes, md_path, full_md_text.encode("utf-8"))
    logging.info("[ACTIVITY][CHUNKING] Unified Markdown created: %s", md_path)

    # 2. Inject doc_id and source_blob_path into chunks for in-memory flow
    for chunk in chunks:
        chunk["doc_id"] = doc_id
        chunk["source_blob_path"] = input_blob_path

    # Claim-check: Save individual chunk payloads and manifest to storage
    logging.info(f"[ACTIVITY][CHUNKING] Starting materialization for {len(chunks)} chunks")
    persistence = _ArtifactPersistence(store)
    semaphore = asyncio.Semaphore(5)

    async def save_chunk_with_sem(chunk_data, path):
        async with semaphore:
            await persistence.save_json(chunk_data, path)

    save_tasks = []
    chunk_ids = []
    for chunk in chunks:
        chunk_id = str(chunk["chunk_index"]).zfill(5)
        chunk_ids.append(chunk_id)
        save_tasks.append(
            save_chunk_with_sem(chunk, _chunk_artifact_path(doc_id, chunk_id))
        )
    
    # Save manifest list
    manifest = [
        {"chunk_id": cid, "chunk_index": idx}
        for idx, cid in enumerate(chunk_ids, start=1)
    ]
    save_tasks.append(
        save_chunk_with_sem(manifest, _chunk_manifest_path(doc_id))
    )

    if save_tasks:
        await asyncio.gather(*save_tasks)

    return {
        "doc_id": doc_id,
        "chunk_ids": chunk_ids,
        "total_chunks": len(chunks)
    }


async def activity_chunking(blob_path: str) -> Dict[str, Any]:
    try:
        store = _get_artifact_store()
        doc_id = _doc_id_from_blob_path(blob_path)
        logging.info(
            "[ACTIVITY][CHUNKING] start | blob_path=%s | doc_id=%s", blob_path, doc_id
        )
        chunk_meta = await run_chunking({"input_blob_path": blob_path}, store)

        logging.info(
            "[ACTIVITY][CHUNKING] complete | doc_id=%s | chunks=%d",
            doc_id,
            chunk_meta["total_chunks"],
        )
        return chunk_meta
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_chunking: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


async def activity_extract_chunk(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        store = _get_artifact_store()
        
        if "text" in payload:
            # Backward compatibility / Direct chunk dictionary passed
            chunk_data = payload
            doc_id = chunk_data.get("doc_id", "unknown")
            chunk_id = str(chunk_data.get("chunk_index", "0")).zfill(5)
        else:
            # Claim-Check reference pattern
            doc_id = payload["doc_id"]
            chunk_id = payload["chunk_id"]

            # Load chunk data from store
            chunk_path = _chunk_artifact_path(doc_id, chunk_id)
            bytes_data = await asyncio.to_thread(store.read_bytes, chunk_path)
            chunk_data = json.loads(bytes_data.decode("utf-8"))

        text = chunk_data["text"]
        chunk_idx = chunk_data.get("chunk_index", "0")

        try:
            extraction, token_usage = await extract_from_chunk(
                None,
                text,
                SCHEMA_CONFIG,
                chunk_label=str(chunk_idx).zfill(5),
                artifact_store=store,
            )
        except ProviderOutcomeError as exc:
            classification = exc.classification
            if classification.code == ExtractionOutcomeCode.CONTENT_BLOCKED:
                outcome_key = "content_blocked"
                event_name = "pipeline.extraction.chunk_blocked"
                metric_name = "pipeline_filtered_chunks_count"
            elif classification.code in {
                ExtractionOutcomeCode.OUTPUT_LOGIC_INVALID,
                ExtractionOutcomeCode.OUTPUT_SCHEMA_INVALID,
            }:
                outcome_key = "extraction_failed"
                event_name = "pipeline.extraction.chunk_failed"
                metric_name = "pipeline_failed_chunks_count"
            else:
                raise

            token_count = int(chunk_data.get("token_count") or _estimate_token_count(text))
            blocked_payload = {
                "outcome": outcome_key,
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "page_range": list(chunk_data.get("page_range") or []),
                "token_count": token_count,
                "request_id": classification.request_id,
                "provider_signal": classification.provider_signal,
                "reason": classification.sanitized_reason,
            }
            log_event(
                event_name,
                doc_id=doc_id,
                chunk_id=chunk_id,
                page_range=blocked_payload["page_range"],
                provider_signal=classification.provider_signal,
                request_id=classification.request_id,
                status_code=classification.status_code,
                discovered_codes=classification.discovered_codes,
                body_key_shape=classification.body_key_shape,
            )
            log_metric(metric_name, 1, doc_id=doc_id)
            return blocked_payload

        result_payload = {
            "outcome": "success",
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "extraction": extraction.model_dump(),
            "token_usage": token_usage,
            "token_count": int(chunk_data.get("token_count") or _estimate_token_count(text)),
            "page_range": list(chunk_data.get("page_range") or []),
            "source_label": chunk_data.get("label", f"Chunk {chunk_id}"),
        }

        return result_payload
    except ProviderOutcomeError as e:
        classification = e.classification
        logging.error(
            "[ACTIVITY][EXTRACT_CHUNK] fail_closed code=%s status=%s request_id=%s "
            "signals=%s body_shape=%s",
            classification.code.value,
            classification.status_code,
            classification.request_id,
            classification.discovered_codes,
            classification.body_key_shape,
        )
        log_event(
            "pipeline.extraction.fail_closed",
            level=logging.ERROR,
            doc_id=locals().get("doc_id", "unknown"),
            chunk_id=locals().get("chunk_id", "unknown"),
            outcome=classification.code.value,
            status_code=classification.status_code,
            request_id=classification.request_id,
            discovered_codes=classification.discovered_codes,
            body_key_shape=classification.body_key_shape,
        )
        log_metric(
            "pipeline_extraction_outcome_count",
            1,
            doc_id=locals().get("doc_id", "unknown"),
            outcome=classification.code.value,
        )
        raise RuntimeError(str(e)) from None
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_extract_chunk: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


async def activity_merge_partials(
    extraction_results: List[Dict[str, Any]] | Dict[str, Any],
) -> str | Dict[str, Any]:
    try:
        store = _get_artifact_store()
        legacy_return = isinstance(extraction_results, list) and not any(
            isinstance(item, dict) and item.get("outcome") for item in extraction_results
        )
        envelope = extraction_results if isinstance(extraction_results, dict) else {}
        results = (
            list(envelope.get("results") or [])
            if isinstance(extraction_results, dict)
            else extraction_results
        )
        trace_id = str(envelope.get("trace_id") or "")
        expected_doc_id = str(envelope.get("doc_id") or "unknown")
        logging.info("[ACTIVITY][MERGE_PARTIALS] input_count=%s", len(results))
        all_nodes: list[dict[str, Any]] = []
        all_edges: list[dict[str, Any]] = []
        doc_id = expected_doc_id
        blocked: list[dict[str, Any]] = []
        successful: list[dict[str, Any]] = []

        for result_data in results:
            doc_id = result_data.get("doc_id", doc_id)
            outcome = result_data.get("outcome")
            if outcome not in {None, "success", "content_blocked", "extraction_failed"}:
                raise ValueError(f"Unsupported chunk extraction outcome: {outcome}")
            if outcome in {"content_blocked", "extraction_failed"}:
                blocked.append(result_data)
                continue
            successful.append(result_data)
            chunk_id = result_data.get("chunk_id", "unknown")
            # Use page-based label if available, fall back to chunk ID
            source_label = result_data.get("source_label") or f"Chunk {chunk_id}"
            extraction = RawGraphExtraction.model_validate(
                result_data.get("extraction", {})
            )
            for node in extraction.nodes:
                d = node.model_dump()
                d["source_file"] = doc_id
                d["source_location"] = source_label
                all_nodes.append(d)
            for edge in extraction.edges:
                d = edge.model_dump()
                d["source_file"] = doc_id
                d["source_location"] = source_label
                all_edges.append(d)

        failed = [item for item in blocked if item.get("outcome") == "extraction_failed"]
        policy_blocked = [item for item in blocked if item.get("outcome") == "content_blocked"]

        total_tokens = sum(max(0, int(item.get("token_count") or 0)) for item in results)
        successful_tokens = sum(
            max(0, int(item.get("token_count") or 0)) for item in successful
        )
        coverage_ratio = successful_tokens / total_tokens if total_tokens else (
            len(successful) / len(results) if results else 0.0
        )
        wire_model = create_wire_extraction_model(SCHEMA_CONFIG)
        manifest_path = _ingestion_manifest_path(doc_id)
        manifest = {
            "doc_id": doc_id,
            "status": "completed_partial" if blocked else "completed",
            "is_partial": bool(blocked),
            "review_required": bool(blocked),
            "total_chunks": len(results),
            "successful_chunks": len(successful),
            "blocked_chunks": len(blocked),
            "failed_chunks": len(failed),
            "policy_blocked_chunks": len(policy_blocked),
            "coverage_ratio": coverage_ratio,
            "coverage_method": "token_weighted",
            "total_source_tokens": total_tokens,
            "successful_source_tokens": successful_tokens,
            "blocked_chunk_ids": [str(item.get("chunk_id")) for item in policy_blocked],
            "blocked_page_ranges": [list(item.get("page_range") or []) for item in policy_blocked],
            "blocked_ranges": [
                {
                    "chunk_id": str(item.get("chunk_id")),
                    "page_range": list(item.get("page_range") or []),
                    "request_id": item.get("request_id"),
                    "provider_signal": item.get("provider_signal"),
                    "reason": item.get("reason"),
                }
                for item in policy_blocked
            ],
            "failed_chunk_ids": [str(item.get("chunk_id")) for item in failed],
            "failed_page_ranges": [list(item.get("page_range") or []) for item in failed],
            "failed_ranges": [
                {
                    "chunk_id": str(item.get("chunk_id")),
                    "page_range": list(item.get("page_range") or []),
                    "request_id": item.get("request_id"),
                    "provider_signal": item.get("provider_signal"),
                    "reason": item.get("reason"),
                }
                for item in failed
            ],
            "model_deployment": settings.graphify_extraction_model,
            "ontology_version": _schema_version_for(SCHEMA_CONFIG),
            "schema_fingerprint": schema_fingerprint(wire_model),
            "contract": "strict_structured_outputs_v1",
            "trace_id": trace_id,
            "processed_source_path": _processed_text_path(doc_id),
        }

        await _delete_indexed_chunks(
            doc_id, [str(item.get("chunk_id")) for item in blocked]
        )

        if blocked and not all_nodes and not all_edges:
            manifest["status"] = "content_blocked"
            await asyncio.to_thread(
                store.write_bytes,
                manifest_path,
                json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
            )
            return {
                "status": "content_blocked",
                "merged_graph_path": None,
                "manifest_path": manifest_path,
                **manifest,
            }

        resolver = DefaultEntityResolver(SCHEMA_CONFIG)
        resolved_nodes, resolved_edges, report = await resolver.resolve(all_nodes, all_edges)

        if blocked and not resolved_nodes and not resolved_edges:
            manifest["status"] = "content_blocked"
            await asyncio.to_thread(
                store.write_bytes,
                manifest_path,
                json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
            )
            return {
                "status": "content_blocked",
                "merged_graph_path": None,
                "manifest_path": manifest_path,
                **manifest,
            }

        successful_chunk_payloads: list[dict[str, Any]] = []
        if not legacy_return:
            for item in successful:
                chunk_path = _chunk_artifact_path(doc_id, str(item.get("chunk_id")))
                chunk_bytes = await asyncio.to_thread(store.read_bytes, chunk_path)
                successful_chunk_payloads.append(json.loads(chunk_bytes.decode("utf-8")))

            processed_text = "\n\n".join(
                f"## {chunk.get('label') or 'Processed source range'}\n\n{chunk.get('text') or ''}"
                for chunk in successful_chunk_payloads
            )
            await asyncio.to_thread(
                store.write_bytes,
                _processed_text_path(doc_id),
                processed_text.encode("utf-8"),
            )
            await _index_chunks(doc_id, successful_chunk_payloads)

        graph_data = {
            "nodes": resolved_nodes,
            "edges": resolved_edges,
            "metadata": {
                "doc_id": doc_id,
                "entity_resolution_report": report,
                **manifest,
                "manifest_path": manifest_path,
            },
        }

        merged_graph_path = f"graph/{doc_id}/partial.json"
        graph_bytes = json.dumps(graph_data, indent=2, ensure_ascii=False).encode("utf-8")
        await asyncio.to_thread(store.write_bytes, merged_graph_path, graph_bytes)
        if not legacy_return:
            await asyncio.to_thread(
                store.write_bytes,
                manifest_path,
                json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8"),
            )

        logging.info(f"[ACTIVITY][MERGE_PARTIALS] Merged graph written to {merged_graph_path}")
        if legacy_return:
            return merged_graph_path
        return {
            "status": manifest["status"],
            "merged_graph_path": merged_graph_path,
            "manifest_path": manifest_path,
            **manifest,
        }
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_merge_partials: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


async def activity_final_indexing(merged_graph_path: str) -> str:
    try:
        store = _get_artifact_store()

        # Load merged graph from blob (Claim-Check)
        logging.info(f"[ACTIVITY][FINAL_INDEXING] Loading merged graph from {merged_graph_path}")
        graph_bytes = await asyncio.to_thread(store.read_bytes, merged_graph_path)
        graph_data = json.loads(graph_bytes.decode("utf-8"))

        doc_id = (
            graph_data.get("metadata", {}).get("doc_id")
            or graph_data.get("metadata", {}).get("source_file")
            or "unknown"
        )
        cosmos_uri = f"cosmos://{doc_id}"

        # 0. Run community detection first to populate "community" property on nodes
        communities = {}
        hierarchy = {}
        G = None
        try:
            logging.info("[ACTIVITY][FINAL_INDEXING] Running community detection before Cosmos DB sync...")
            G = build_from_json(graph_data)
            communities, hierarchy = await asyncio.to_thread(cluster, G)
            
            # Map node ID to community ID
            node_to_comm = {}
            for comm_id, members in communities.items():
                for member in members:
                    node_to_comm[str(member)] = comm_id
            
            # Update community property on nodes in graph_data
            for node in graph_data.get("nodes", []):
                nid = str(node.get("id"))
                if nid in node_to_comm:
                    node["community"] = node_to_comm[nid]
        except Exception as e:
            logging.error(f"[ACTIVITY][FINAL_INDEXING] Community pre-detection failed: {e}")

        # 1. Sync to Cosmos DB
        if settings.cosmos_gremlin_endpoint:
            cosmos_mgr = get_cosmos_manager()
            try:
                await cosmos_mgr.sync_graph(graph_data)
            except Exception as e:
                logging.error(f"[ACTIVITY][FINAL_INDEXING] Cosmos DB sync failed: {e}")
                raise

        # 2. Sync to AI Search (Nodes)
        await _index_nodes(doc_id, graph_data.get("nodes", []))

        # 3. Sync to AI Search (Edge Labels)
        await _index_edge_labels(
            graph_data,
            f"indexes/{doc_id}/edge_embeddings.json",
            _ArtifactPersistence(store),
        )

        # Persist the final enriched graph for diagnostics and replay. The partial
        # artifact is deleted later; final.json is intentionally retained.
        final_graph_path = _final_graph_path(doc_id)
        final_graph_bytes = json.dumps(graph_data, indent=2, ensure_ascii=False).encode("utf-8")
        await asyncio.to_thread(store.write_bytes, final_graph_path, final_graph_bytes)
        logging.info("[ACTIVITY][FINAL_INDEXING] Final graph written to %s", final_graph_path)

        # 4. Generate community summaries and markdown report
        try:
            logging.info("[ACTIVITY][FINAL_INDEXING] Generating community summaries and reports...")
            if G is None:
                G = build_from_json(graph_data)
                communities, hierarchy = await asyncio.to_thread(cluster, G)

            (
                summaries,
                community_labels,
                payload,
                enable_llm_summaries,
            ) = await _build_community_summary_payload(G, communities, hierarchy)
            G.graph["communities"] = communities
            G.graph["community_summaries"] = summaries
            G.graph["llm_community_summaries_enabled"] = enable_llm_summaries
            
            # Save summaries and hierarchy mapping
            summaries_json = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            await asyncio.to_thread(store.write_bytes, f"graph/{doc_id}/community_summaries.json", summaries_json)
            await asyncio.to_thread(store.write_bytes, "graph/latest/community_summaries.json", summaries_json)
            await asyncio.to_thread(store.write_bytes, "community_summaries.json", summaries_json)

            # Load raw text to count words
            raw_text = ""
            try:
                metadata = graph_data.get("metadata") or {}
                md_path = (
                    metadata.get("processed_source_path")
                    if metadata.get("is_partial")
                    else _extracted_text_path(doc_id)
                )
                if await asyncio.to_thread(store.exists, md_path):
                    md_bytes = await asyncio.to_thread(store.read_bytes, md_path)
                    raw_text = md_bytes.decode("utf-8")
            except Exception as e:
                logging.warning(f"Could not load extracted text for word count: {e}")

            pages = [{"text": raw_text}]
            protocol_report = analyze_protocol_completeness(
                schema_config=SCHEMA_CONFIG,
                graph_data=graph_data,
                raw_text=raw_text,
                doc_id=doc_id,
            )
            protocol_summary = protocol_report.get("summary") or {}
            log_event(
                "protocol_completeness.generated",
                doc_id=doc_id,
                coverage_percent=protocol_summary.get("coveragePercent"),
                critical_missing=protocol_summary.get("criticalMissing"),
                critical_review_required=protocol_summary.get("criticalReviewRequired"),
            )
            log_metric(
                "protocol_completeness_coverage_percent",
                protocol_summary.get("coveragePercent") or 0,
                unit="percent",
                doc_id=doc_id,
            )
            log_metric(
                "protocol_completeness_critical_missing",
                protocol_summary.get("criticalMissing") or 0,
                doc_id=doc_id,
            )
            protocol_json = json.dumps(
                protocol_report, ensure_ascii=False, indent=2
            ).encode("utf-8")
            await asyncio.to_thread(
                store.write_bytes,
                f"graph/{doc_id}/protocol_completeness.json",
                protocol_json,
            )
            await asyncio.to_thread(
                store.write_bytes,
                "graph/latest/protocol_completeness.json",
                protocol_json,
            )
            await asyncio.to_thread(
                store.write_bytes,
                "protocol_completeness.json",
                protocol_json,
            )

            conn_str = settings.azure_storage_connection_string or "UseDevelopmentStorage=true"
            container_name = settings.azure_storage_container or "graphify-out"
            blob_storage = AzureBlobStorage(connection_string=conn_str, container_name=container_name)

            # Generate report to root of container
            persistence_root = PersistenceManager(blob_storage, base_blob_path="")
            reporter_root = DefaultReporter(persistence_root)
            await reporter_root.generate(
                G,
                communities,
                graph_data,
                pages,
                doc_id,
                protocol_completeness=protocol_report,
            )

            # Copy report & HTML to doc_id folder for historical tracking
            persistence_nested = PersistenceManager(blob_storage, base_blob_path=f"graph/{doc_id}")
            reporter_nested = DefaultReporter(persistence_nested)
            await reporter_nested.generate(
                G,
                communities,
                graph_data,
                pages,
                doc_id,
                protocol_completeness=protocol_report,
            )
            
            logging.info("[ACTIVITY][FINAL_INDEXING] Successfully generated community summaries and markdown report.")
        except Exception as e:
            logging.error(f"[ACTIVITY][FINAL_INDEXING] Failed to run reporting and community summaries: {e}", exc_info=True)

        # Immediate Cleanup: Delete the partial merged graph now that indexing is complete
        try:
            await asyncio.to_thread(store.delete, merged_graph_path)
            logging.debug(f"[CLEANUP] Deleted partial merged graph: {merged_graph_path}")
        except Exception as e:
            logging.warning(f"[CLEANUP] Failed to delete merged graph {merged_graph_path}: {e}")

        # Idempotent cleanup of temporary chunk folders and uploads for stateless design
        try:
            for folder_prefix in [f"chunks/{doc_id}", "uploads"]:
                await asyncio.to_thread(store.delete_folder, folder_prefix)
            logging.info(f"Successfully cleaned up temporary artifacts for {doc_id}")
        except Exception as e:
            logging.warning(f"[CLEANUP] Failed to clean up temporary artifacts for {doc_id}: {e}")

        logging.info("[ACTIVITY][FINAL_INDEXING] complete | cosmos_uri=%s", cosmos_uri)
        return cosmos_uri
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_final_indexing: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


async def run_extraction(payload: Dict[str, Any]) -> Dict[str, Any]:
    logging.warning("[ACTIVITY][STUB] run_extraction called but not implemented. Returning empty dict.")
    return {}


async def run_resolution_and_inference(payload: Dict[str, Any]) -> str:
    logging.warning("[ACTIVITY][STUB] run_resolution_and_inference called but not implemented. Returning default path.")
    return "graph.json"


async def run_analysis_and_reporting(payload: Dict[str, Any]) -> Dict[str, Any]:
    logging.warning("[ACTIVITY][STUB] run_analysis_and_reporting called but not implemented. Returning default paths.")
    return {"graph_path": "graph.json", "report_path": "report.md"}


async def activity_cleanup_failed_run(payload: Dict[str, Any]) -> None:
    doc_id = payload["doc_id"]
    logging.info(f"[ACTIVITY][CLEANUP_FAILED_RUN][START] Cleaning up resources for failed pipeline doc_id: {doc_id}")
    
    # 1. Clean up temporary files in storage
    try:
        store = _get_artifact_store()
        # Delete temporary chunk files
        chunk_prefix = f"chunks/{doc_id}"
        await asyncio.to_thread(store.delete_folder, chunk_prefix)
        logging.info(f"[ACTIVITY][CLEANUP_FAILED_RUN] Cleaned up storage folder: {chunk_prefix}")
        
        # Delete partial graph if it exists
        partial_path = f"graph/{doc_id}/partial.json"
        if await asyncio.to_thread(store.exists, partial_path):
            await asyncio.to_thread(store.delete, partial_path)
            logging.info(f"[ACTIVITY][CLEANUP_FAILED_RUN] Deleted partial graph: {partial_path}")
    except Exception as e:
        logging.error(f"[ACTIVITY][CLEANUP_FAILED_RUN] Storage cleanup failed: {e}")

    # 2. Clean up partially synchronized data in Cosmos DB
    if settings.cosmos_gremlin_endpoint:
        try:
            cosmos_mgr = get_cosmos_manager()
            logging.info(f"[ACTIVITY][CLEANUP_FAILED_RUN] Deleting partially synced Cosmos DB nodes/edges for doc_id: {doc_id}")
            # Cosmos partition key is doc_id
            query = "g.V().has('pk', pk).drop()"
            await cosmos_mgr.execute_query(query, {"pk": doc_id})
            logging.info(f"[ACTIVITY][CLEANUP_FAILED_RUN] Dropped Gremlin partition for: {doc_id}")
        except Exception as e:
            logging.error(f"[ACTIVITY][CLEANUP_FAILED_RUN] Cosmos DB cleanup failed: {e}")


async def activity_verify_protocol_completeness(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Background activity to audit missing protocol items via QueryAgent / Azure Search."""
    from src.reporting.protocol_completeness import verify_missing_items_with_agent

    doc_id = payload.get("doc_id")
    critical_only = payload.get("critical_only", False)
    max_items = payload.get("max_items", 10)

    if not doc_id:
        logging.warning("[ACTIVITY][VERIFY_COMPLETENESS] Missing doc_id in payload.")
        return {}

    logging.info(f"[ACTIVITY][VERIFY_COMPLETENESS] Starting background verification for doc_id: {doc_id}")
    store = _get_artifact_store()
    path = f"graph/{doc_id}/protocol_completeness.json"

    if not await asyncio.to_thread(store.exists, path):
        path = "protocol_completeness.json"

    if not await asyncio.to_thread(store.exists, path):
        logging.warning(f"[ACTIVITY][VERIFY_COMPLETENESS] protocol_completeness.json not found for doc_id: {doc_id}")
        return {}

    try:
        raw_bytes = await asyncio.to_thread(store.read_bytes, path)
        report = json.loads(raw_bytes.decode("utf-8"))

        search_mgr = None
        try:
            from src.storage.factory import get_search_manager

            search_mgr = get_search_manager()
        except Exception as search_exc:
            logging.warning(f"[ACTIVITY][VERIFY_COMPLETENESS] SearchManager unavailable: {search_exc}")

        updated_report = await verify_missing_items_with_agent(
            report=report,
            doc_id=doc_id,
            search_manager=search_mgr,
            max_items=max_items,
            critical_only=critical_only,
        )

        updated_bytes = json.dumps(updated_report, ensure_ascii=False, indent=2).encode("utf-8")
        await asyncio.to_thread(store.write_bytes, f"graph/{doc_id}/protocol_completeness.json", updated_bytes)
        await asyncio.to_thread(store.write_bytes, "graph/latest/protocol_completeness.json", updated_bytes)
        await asyncio.to_thread(store.write_bytes, "protocol_completeness.json", updated_bytes)

        logging.info(f"[ACTIVITY][VERIFY_COMPLETENESS] Successfully updated protocol completeness for doc_id: {doc_id}")
        return updated_report
    except Exception as exc:
        logging.error(f"[ACTIVITY][VERIFY_COMPLETENESS] Verification failed for doc_id {doc_id}: {exc}", exc_info=True)
        return {}
