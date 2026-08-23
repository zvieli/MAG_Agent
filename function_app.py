import json
import logging
import re
import uuid
import os
import asyncio
import time
import urllib.parse
from datetime import datetime
from typing import List, Dict, Any 

try:
    import azure.durable_functions as df
    import azure.functions as func
except Exception as e:
    logging.error(f"Failed to import Azure modules: {e}")

from src.processing import orchestration_activities as pipeline_activities
from src.graph.cosmos_manager import DUPLICATE_MATCH_FIELDS, duplicate_lookup_query
from src.storage.factory import get_artifact_store
from src.storage.factory import get_cosmos_manager
from src.utils.config import settings
from src.operations.dependency_checker import DependencyChecker
from src.query_agent.query_agent import QueryAgent
from src.models.models import SCHEMA_CONFIG  # noqa: F401 # Forces loading schema into memory cache during worker init
from src.utils.observability import calculate_estimated_cost, log_event, log_metric, trace_id_from_request
from src.auth.decorators import require_authenticated_scope
from src.utils.cors import cors_headers_for_request

# Initialize the Durable Functions App (Python V2 Model)
app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

def _batched(items: List[str], batch_size: int) -> List[List[str]]:
    safe_batch_size = max(1, int(batch_size))
    return [
        items[i : i + safe_batch_size]
        for i in range(0, len(items), safe_batch_size)
    ]


def _get_cors_headers_for_req(req: func.HttpRequest | None = None) -> Dict[str, str]:
    return cors_headers_for_request(req)


def _cors_response(
    body: Any = "", status_code: int = 200, mimetype: str | None = None, req: func.HttpRequest | None = None
) -> func.HttpResponse:
    return func.HttpResponse(
        body,
        status_code=status_code,
        mimetype=mimetype,
        headers=_get_cors_headers_for_req(req),
    )


def _handle_options_request(req: func.HttpRequest | None = None) -> func.HttpResponse:
    return func.HttpResponse("", status_code=204, headers=_get_cors_headers_for_req(req))


def _check_status_response_with_cors(
    client: df.DurableOrchestrationClient, req: func.HttpRequest, instance_id: str
) -> func.HttpResponse:
    res = client.create_check_status_response(req, instance_id)
    for k, v in _get_cors_headers_for_req(req).items():
        res.headers[k] = v
    return res


def _json_response(
    data: Any,
    status_code: int = 200,
    req: func.HttpRequest | None = None,
    headers: Dict[str, str] | None = None,
) -> func.HttpResponse:
    response_headers = _get_cors_headers_for_req(req)
    response_headers.update(headers or {})
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
        headers=response_headers,
    )


def _preflight_json_response(
    data: Any, status_code: int = 200, req: func.HttpRequest | None = None
) -> func.HttpResponse:
    return _json_response(
        data,
        status_code=status_code,
        req=req,
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


def _compact_error(exc: Exception | str, *, limit: int = 3000) -> str:
    message = str(exc)
    if len(message) <= limit:
        return message
    return f"{message[:limit]}... [truncated; original length={len(message)} chars]"


def _is_debug_logs_enabled() -> bool:
    if os.getenv("MAG_ENABLE_DEBUG_LOGS", "").lower() in {"1", "true", "yes"}:
        return True
    return os.getenv("AZURE_FUNCTIONS_ENVIRONMENT", "").lower() in {"", "local", "development"}


def _pipeline_input_payload(input_value: Any) -> Dict[str, Any]:
    if isinstance(input_value, dict):
        blob_path = str(input_value.get("blob_path") or input_value.get("blobPath") or "")
        doc_id = str(input_value.get("doc_id") or input_value.get("docId") or "")
        trace_id = str(input_value.get("trace_id") or input_value.get("traceId") or "")
    else:
        blob_path = str(input_value or "")
        doc_id = ""
        trace_id = ""
    doc_id = doc_id or os.path.splitext(os.path.basename(blob_path))[0]
    return {
        "blob_path": blob_path,
        "doc_id": doc_id,
        "trace_id": trace_id,
    }


def _pipeline_status_payload(
    *,
    phase: str,
    message: str,
    blob_path: str,
    doc_id: str,
    trace_id: str,
    total_chunks: int,
    completed_chunks: int,
    current_batch: int,
    last_error: str | None,
    blocked_chunks: int = 0,
    failed_chunks: int = 0,
    coverage_ratio: float | None = None,
    blocked_page_ranges: List[List[int]] | None = None,
    failed_page_ranges: List[List[int]] | None = None,
    manifest_path: str | None = None,
    review_required: bool = False,
) -> Dict[str, Any]:
    return {
        "phase": phase,
        "message": message,
        "blob_path": blob_path,
        "doc_id": doc_id,
        "trace_id": trace_id,
        "total_chunks": total_chunks,
        "completed_chunks": completed_chunks,
        "current_batch": current_batch,
        "last_error": last_error,
        "blocked_chunks": blocked_chunks,
        "failed_chunks": failed_chunks,
        "coverage_ratio": coverage_ratio,
        "blocked_page_ranges": blocked_page_ranges or [],
        "failed_page_ranges": failed_page_ranges or [],
        "manifest_path": manifest_path,
        "review_required": review_required,
    }


def _durable_instance_id(context: Any) -> str:
    return str(getattr(context, "instance_id", "") or getattr(context, "instanceId", "") or "")


def _doc_id_from_filename(filename: str) -> str:
    safe_name = os.path.basename((filename or "").strip())
    return os.path.splitext(safe_name)[0]


def _normalize_upload_filename(raw_filename: Any) -> str:
    decoded = urllib.parse.unquote(str(raw_filename or "").strip()).strip('"')
    basename = decoded.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not basename or basename in {".", ".."} or "\x00" in basename:
        raise ValueError("A valid PDF filename is required")
    if not basename.lower().endswith(".pdf"):
        basename += ".pdf"
    if not _doc_id_from_filename(basename):
        raise ValueError("A valid PDF filename is required")
    return basename


def _content_disposition_filename(content_disposition: str) -> str:
    if not content_disposition:
        return ""
    extended = re.search(
        r"filename\*\s*=\s*(?:UTF-8'')?([^;]+)",
        content_disposition,
        flags=re.IGNORECASE,
    )
    if extended:
        return extended.group(1).strip().strip('"')
    regular = re.search(
        r'filename\s*=\s*"?([^";]+)"?',
        content_disposition,
        flags=re.IGNORECASE,
    )
    return regular.group(1).strip() if regular else ""


def _resolve_upload_filename(req: func.HttpRequest) -> str:
    candidates = {
        "query": req.params.get("filename") or "",
        "header": req.headers.get("x-mag-file-name") or "",
        "content_disposition": _content_disposition_filename(
            req.headers.get("Content-Disposition", "")
        ),
    }
    normalized = {
        source: _normalize_upload_filename(value)
        for source, value in candidates.items()
        if str(value or "").strip()
    }
    if not normalized:
        raise ValueError("Missing PDF filename")
    filenames = set(normalized.values())
    if len(filenames) != 1:
        logging.warning(
            "[PIPELINE_START] Conflicting filename metadata sources=%s",
            sorted(normalized),
        )
        raise ValueError("Conflicting PDF filename metadata")
    return next(iter(filenames))


def _legacy_encoded_doc_id(doc_id: str) -> str:
    if not doc_id or "%" in doc_id:
        return ""

    encoded = urllib.parse.quote(doc_id, safe="")
    return encoded if encoded != doc_id else ""


def _graph_payload_has_data(payload: Dict[str, Any]) -> bool:
    return bool(payload.get("nodes") or payload.get("edges"))


def _case_coverage_metadata(doc_id: str) -> Dict[str, Any]:
    """Read the authoritative ingestion manifest without exposing blocked text."""
    try:
        store = get_artifact_store()
        path = f"graph/{doc_id}/ingestion_manifest.json"
        if store.exists(path):
            payload = json.loads(store.read_bytes(path).decode("utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception as exc:
        logging.warning("[COVERAGE] Failed reading manifest for %s: %s", doc_id, exc)
    return {
        "is_partial": False,
        "review_required": False,
        "coverage_ratio": 1.0,
        "blocked_chunks": 0,
        "blocked_page_ranges": [],
    }


def _partial_coverage_notice(metadata: Dict[str, Any]) -> str:
    ranges = metadata.get("blocked_page_ranges") or []
    coverage = float(metadata.get("coverage_ratio") or 0.0) * 100
    return (
        "⚠️ Incomplete source coverage: provider-blocked page ranges "
        f"{ranges} were excluded (token-weighted coverage {coverage:.1f}%). "
        "The answer applies only to processed source ranges and does not establish "
        "that an entity, relationship, or offense is absent from the entire document. "
        f"Review trace ID: {metadata.get('trace_id') or 'unavailable'}."
    )


def _property_value(properties: Dict[str, Any], key: str, default: Any = None) -> Any:
    value = properties.get(key, default)
    if isinstance(value, list) and value:
        first = value[0]
        if isinstance(first, dict) and "value" in first:
            return first.get("value")
        return first
    return value


def _primitive_properties(properties: Dict[str, Any]) -> Dict[str, Any]:
    clean = {}
    for key, value in properties.items():
        normalized = _property_value(properties, key)
        if isinstance(normalized, (str, int, float, bool)) or normalized is None:
            clean[key] = normalized
    return clean


def _readable_identifier(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"^(canonical|suspect|offense|org|actor|event|location)_", "", text, flags=re.IGNORECASE)
    text = text.replace("_", " ").strip()
    return text or str(value or "")


def _display_label_from_properties(properties: Dict[str, Any], fallback: Any) -> str:
    label = (
        _property_value(properties, "label")
        or _property_value(properties, "name")
        or _property_value(properties, "display_name")
        or _property_value(properties, "title")
    )
    return str(label or _readable_identifier(fallback))


def _sample_node_from_vertex(vertex: Dict[str, Any]) -> Dict[str, Any]:
    props = vertex.get("properties") or {}
    return {
        "id": str(vertex.get("id") or ""),
        "label": str(
            _property_value(props, "label")
            or _property_value(props, "name")
            or vertex.get("label")
            or vertex.get("id")
            or ""
        ),
        "type": str(
            _property_value(props, "type")
            or _property_value(props, "label")
            or vertex.get("label")
            or ""
        ),
    }


async def _case_duplicate_preflight(filename_or_doc_id: str) -> Dict[str, Any]:
    doc_id = _doc_id_from_filename(filename_or_doc_id)
    if not doc_id:
        return {
            "status": "new",
            "doc_id": "",
            "matched_doc_id": None,
            "matched_by": "none",
            "sample_node": None,
            "cache_hit": False,
            "cache_source": "none",
            "live_query": False,
            "data_source_id": None,
        }

    # A partial case must remain reprocessable.  The manifest is authoritative,
    # even when graph vertices already exist from an earlier partial run.
    try:
        store = get_artifact_store()
        manifest_path = f"graph/{doc_id}/ingestion_manifest.json"
        if store.exists(manifest_path):
            manifest = json.loads(store.read_bytes(manifest_path).decode("utf-8"))
            if manifest.get("is_partial") or manifest.get("status") == "content_blocked":
                return {
                    "status": "new",
                    "doc_id": doc_id,
                    "matched_doc_id": None,
                    "matched_by": "none",
                    "sample_node": None,
                    "cache_hit": False,
                    "cache_source": "none",
                    "live_query": False,
                    "data_source_id": None,
                    "reprocess_partial": True,
                }
    except Exception as exc:
        logging.warning("[DUPLICATE_PREFLIGHT] Manifest check failed for %s: %s", doc_id, exc)

    cosmos_mgr = get_cosmos_manager()
    identity = getattr(cosmos_mgr, "data_source_identity", None)
    data_source_id = (
        identity.get("data_source_id") if isinstance(identity, dict) else None
    )
    matched_by = "none"
    matched_vertex: Dict[str, Any] | None = None
    warnings: List[str] = []

    for property_name in DUPLICATE_MATCH_FIELDS:
        vertices = await cosmos_mgr.execute_query(
            duplicate_lookup_query(property_name),
            {"requested_doc_id": doc_id},
        )
        if not vertices:
            continue

        candidate = vertices[0]
        if not isinstance(candidate, dict):
            warnings.append(f"invalid_lookup_result:{property_name}")
            logging.error(
                "[DUPLICATE_PREFLIGHT] Invalid lookup result shape for doc_id=%s field=%s data_source_id=%s",
                doc_id,
                property_name,
                data_source_id,
            )
            continue

        matched_value = _property_value(candidate, "matched_value")
        if str(matched_value or "") != doc_id:
            warnings.append(f"lookup_value_mismatch:{property_name}")
            logging.error(
                "[DUPLICATE_PREFLIGHT] Lookup value mismatch for doc_id=%s field=%s data_source_id=%s",
                doc_id,
                property_name,
                data_source_id,
            )
            continue

        matched_by = property_name
        matched_vertex = candidate
        break

    if matched_vertex is not None:
        payload = {
            "status": "duplicate",
            "doc_id": doc_id,
            "matched_doc_id": doc_id,
            "matched_by": matched_by,
            "sample_node": _sample_node_from_vertex(matched_vertex),
            "cache_hit": False,
            "cache_source": "none",
            "live_query": True,
            "data_source_id": data_source_id,
        }
    else:
        payload = {
            "status": "new",
            "doc_id": doc_id,
            "matched_doc_id": None,
            "matched_by": "none",
            "sample_node": None,
            "cache_hit": False,
            "cache_source": "none",
            "live_query": True,
            "data_source_id": data_source_id,
        }
    if warnings:
        payload["warning"] = ",".join(warnings)
    return payload


async def _graph_payload_from_gremlin(doc_id: str, limit: int = 250) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit or 250), 1000))
    edge_limit = max(safe_limit * 2, safe_limit)
    cosmos_mgr = get_cosmos_manager()

    vertex_query = (
        "g.V().or(has('partitionKey', pk), has('pk', pk), has('source_file', pk), has('doc_id', pk)).limit(limit)"
        ".project('id', 'label', 'properties', 'degree')"
        ".by(id()).by(label()).by(valueMap(true)).by(bothE().count())"
    )
    edge_query = (
        "g.E().or(has('partitionKey', pk), has('pk', pk), has('source_file', pk), has('doc_id', pk)).limit(limit)"
        ".project('id', 'label', 'source', 'target', 'sourceProperties', 'targetProperties', 'properties')"
        ".by(id()).by(label()).by(outV().id()).by(inV().id()).by(outV().valueMap(true)).by(inV().valueMap(true)).by(valueMap(true))"
    )

    vertices, edges = await asyncio.gather(
        cosmos_mgr.execute_query(vertex_query, {"pk": doc_id, "limit": safe_limit}),
        cosmos_mgr.execute_query(edge_query, {"pk": doc_id, "limit": edge_limit}),
    )

    ui_nodes = []
    node_types = set()
    for vertex in vertices:
        props = vertex.get("properties") or {}
        node_type = (
            _property_value(props, "type")
            or _property_value(props, "label")
            or vertex.get("label")
            or "Entity"
        )
        node_label = _display_label_from_properties(props, vertex.get("id"))
        node_types.add(str(node_type))
        ui_nodes.append(
            {
                "id": str(vertex.get("id")),
                "label": str(node_label),
                "type": str(node_type),
                "description": _property_value(props, "description")
                or _property_value(props, "meta_forensic_summary")
                or "",
                "communityId": str(_property_value(props, "community") or ""),
                "degree": vertex.get("degree") or 0,
                "properties": _primitive_properties(props),
            }
        )

    ui_edges = []
    for edge in edges:
        props = edge.get("properties") or {}
        source_props = edge.get("sourceProperties") or {}
        target_props = edge.get("targetProperties") or {}
        edge_id = edge.get("id") or f"{edge.get('source')}_{edge.get('label')}_{edge.get('target')}"
        ui_edges.append(
            {
                "id": str(edge_id),
                "source": str(edge.get("source")),
                "target": str(edge.get("target")),
                "sourceLabel": _display_label_from_properties(source_props, edge.get("source")),
                "targetLabel": _display_label_from_properties(target_props, edge.get("target")),
                "relation": str(edge.get("label") or _property_value(props, "relation") or "related_to"),
                "description": _property_value(props, "description")
                or _property_value(props, "forensic_summary")
                or "",
                "confidence": _property_value(props, "confidence_score")
                or _property_value(props, "confidence"),
                "sourceLocation": _property_value(props, "source_location") or "",
                "properties": _primitive_properties(props),
            }
        )

    return {
        "docId": doc_id,
        "source": "gremlin",
        "nodes": ui_nodes,
        "edges": ui_edges,
        "metadata": {
            "nodeCount": len(ui_nodes),
            "edgeCount": len(ui_edges),
            "nodeTypes": sorted(node_types),
            "generatedAt": datetime.utcnow().isoformat() + "Z",
            **_case_coverage_metadata(doc_id),
        },
    }


async def _timeline_payload_from_gremlin(doc_id: str, limit: int = 1000) -> Dict[str, Any]:
    safe_limit = max(1, min(int(limit or 1000), 2000))
    edge_limit = safe_limit
    cosmos_mgr = get_cosmos_manager()

    vertex_query = (
        "g.V().or(has('partitionKey', pk), has('pk', pk), has('source_file', pk), has('doc_id', pk))"
        ".or(has('date'), has('event_date'), has('timestamp'), has('occurrence_date'), has('type', 'EVENT'))"
        ".limit(limit)"
        ".project('id', 'label', 'properties', 'degree')"
        ".by(id()).by(label()).by(valueMap(true)).by(bothE().count())"
    )
    edge_query = (
        "g.E().or(has('partitionKey', pk), has('pk', pk), has('source_file', pk), has('doc_id', pk))"
        ".or(has('date'), has('event_date'), has('timestamp'), has('occurrence_date'))"
        ".limit(limit)"
        ".project('id', 'label', 'source', 'target', 'sourceProperties', 'targetProperties', 'properties')"
        ".by(id()).by(label()).by(outV().id()).by(inV().id()).by(outV().valueMap(true)).by(inV().valueMap(true)).by(valueMap(true))"
    )

    vertices, edges = await asyncio.gather(
        cosmos_mgr.execute_query(vertex_query, {"pk": doc_id, "limit": safe_limit}),
        cosmos_mgr.execute_query(edge_query, {"pk": doc_id, "limit": edge_limit}),
    )

    ui_nodes = []
    node_types = set()
    for vertex in vertices:
        props = vertex.get("properties") or {}
        node_type = (
            _property_value(props, "type")
            or _property_value(props, "label")
            or vertex.get("label")
            or "Entity"
        )
        node_label = _display_label_from_properties(props, vertex.get("id"))
        node_types.add(str(node_type))
        ui_nodes.append(
            {
                "id": str(vertex.get("id")),
                "label": str(node_label),
                "type": str(node_type),
                "description": _property_value(props, "description")
                or _property_value(props, "forensic_summary")
                or _property_value(props, "meta_forensic_summary")
                or "",
                "communityId": str(_property_value(props, "community") or ""),
                "degree": vertex.get("degree") or 0,
                "properties": _primitive_properties(props),
            }
        )

    ui_edges = []
    for edge in edges:
        props = edge.get("properties") or {}
        source_props = edge.get("sourceProperties") or {}
        target_props = edge.get("targetProperties") or {}
        edge_id = edge.get("id") or f"{edge.get('source')}_{edge.get('label')}_{edge.get('target')}"
        ui_edges.append(
            {
                "id": str(edge_id),
                "source": str(edge.get("source")),
                "target": str(edge.get("target")),
                "sourceLabel": _display_label_from_properties(source_props, edge.get("source")),
                "targetLabel": _display_label_from_properties(target_props, edge.get("target")),
                "relation": str(edge.get("label") or _property_value(props, "relation") or "related_to"),
                "description": _property_value(props, "description")
                or _property_value(props, "forensic_summary")
                or "",
                "confidence": _property_value(props, "confidence"),
                "sourceLocation": edge.get("sourceLocation") or _property_value(props, "source_location") or "",
                "properties": _primitive_properties(props),
            }
        )

    return {
        "docId": doc_id,
        "source": "graph",
        "nodes": ui_nodes,
        "edges": ui_edges,
        "nodeTypes": list(node_types),
        "metadata": _case_coverage_metadata(doc_id),
    }

# --------------------------------------------------------------------------------
# HTTP INGRESS (For Testing/Triggering)
# --------------------------------------------------------------------------------


@app.route(route="ping", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
def ping(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    return _cors_response("Healthy", status_code=200, req=req)


@app.route(route="debug_logs", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@require_authenticated_scope
def debug_logs(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    if not _is_debug_logs_enabled():
        return _cors_response("Debug logs endpoint is disabled.", status_code=404)

    import glob
    
    logs = []
    log_dirs = ["/home/LogFiles", "/home/LogFiles/Application/Functions/Host", "/home/LogFiles/Application/Functions/Function", "/tmp"]
    
    file_param = req.params.get("file")
    if file_param:
        allowed = any(file_param.startswith(d) for d in log_dirs)
        if allowed and os.path.isfile(file_param):
            try:
                with open(file_param, "r", errors="ignore") as f:
                    return func.HttpResponse(f.read()[-50000:], mimetype="text/plain")
            except Exception as e:
                return func.HttpResponse(f"Error: {e}", status_code=500)
        return func.HttpResponse("File not found or access denied", status_code=400)

    for log_dir in log_dirs:
        if os.path.exists(log_dir):
            files = glob.glob(os.path.join(log_dir, "**/*"), recursive=True)
            for f in files:
                if os.path.isfile(f):
                    try:
                        size = os.path.getsize(f)
                        mtime = os.path.getmtime(f)
                        logs.append({"path": f, "size": size, "mtime": mtime})
                    except Exception:
                        pass
                        
    logs.sort(key=lambda x: x["mtime"], reverse=True)
    
    content = ""
    latest_path = ""
    if logs:
        latest_path = logs[0]["path"]
        try:
            with open(latest_path, "r", errors="ignore") as f:
                content = f.read()[-30000:]
        except Exception as e:
            content = f"Error reading log: {e}"
            
    response_data = {
        "files": logs[:30],
        "latest_file": latest_path,
        "content": content
    }
    return _json_response(response_data)


@app.route(route="health", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@require_authenticated_scope
async def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """
    Deep health check that verifies connectivity to all critical dependencies:
    Storage, Config, OpenAI, Cosmos DB, Search, and Document Intelligence.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    trace_id = trace_id_from_request(req, prefix="health")
    started_at = time.perf_counter()
    result = await DependencyChecker().check_all(include_optional=True)
    health_status = {
        **result,
        "timestamp": uuid.uuid4().hex,
        "environment": os.getenv("AZURE_FUNCTIONS_ENVIRONMENT", "local"),
        "trace_id": trace_id,
    }

    status_code = 200 if health_status["status"] != "Unhealthy" else 503
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    log_event(
        "health.checked",
        trace_id=trace_id,
        status=health_status["status"],
        duration_ms=elapsed_ms,
        dependency_count=len(health_status.get("dependencies", {})),
    )
    log_metric("health_check_duration_ms", elapsed_ms, unit="ms", status=health_status["status"])
    for dep_name, dep in (health_status.get("dependencies") or {}).items():
        log_metric(
            "dependency_probe_latency_ms",
            dep.get("latency_ms", 0),
            unit="ms",
            dependency=dep_name,
            status=dep.get("status"),
            required=dep.get("required"),
        )
    return _json_response(health_status, status_code=status_code)


@app.route(route="cases/{docId}/graph", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@require_authenticated_scope
async def get_case_graph(req: func.HttpRequest) -> func.HttpResponse:
    """
    UI graph endpoint backed by Cosmos DB Gremlin results.
    Never reads graph/{doc_id}/final.json; the doc_id is used only as partitionKey.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    raw_doc_id = req.params.get("pk") or req.route_params.get("docId") or ""
    doc_id = urllib.parse.unquote(raw_doc_id)
    if not doc_id:
        return _cors_response("Missing route parameter: docId", status_code=400)

    try:
        limit = int(req.params.get("limit", "250"))
    except ValueError:
        return _cors_response("Invalid limit parameter.", status_code=400)

    try:
        payload = await _graph_payload_from_gremlin(doc_id, limit=limit)
        legacy_doc_id = _legacy_encoded_doc_id(doc_id)
        if not _graph_payload_has_data(payload) and legacy_doc_id:
            logging.info(
                "[HTTP][CASE_GRAPH] Empty graph for doc_id=%s; retrying legacy partitionKey=%s",
                doc_id,
                legacy_doc_id,
            )
            legacy_payload = await _graph_payload_from_gremlin(legacy_doc_id, limit=limit)
            if _graph_payload_has_data(legacy_payload):
                payload = legacy_payload
        return _json_response(payload)
    except Exception as exc:
        logging.error(
            "[HTTP][CASE_GRAPH] Failed to load graph for doc_id=%s: %s",
            doc_id,
            exc,
            exc_info=True,
        )
        return _json_response(
            {"available": False, "error": str(exc), "docId": doc_id},
            status_code=500,
        )


@app.route(route="cases/{docId}/timeline", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@require_authenticated_scope
async def get_case_timeline(req: func.HttpRequest) -> func.HttpResponse:
    """
    UI timeline endpoint backed by dated Cosmos DB Gremlin results.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    raw_doc_id = req.params.get("pk") or req.route_params.get("docId") or ""
    doc_id = urllib.parse.unquote(raw_doc_id)
    if not doc_id:
        return _cors_response("Missing route parameter: docId", status_code=400)

    try:
        limit = int(req.params.get("limit", "1000"))
    except ValueError:
        return _cors_response("Invalid limit parameter.", status_code=400)

    try:
        payload = await _timeline_payload_from_gremlin(doc_id, limit=limit)
        legacy_doc_id = _legacy_encoded_doc_id(doc_id)
        if not _graph_payload_has_data(payload) and legacy_doc_id:
            logging.info(
                "[HTTP][CASE_TIMELINE] Empty timeline for doc_id=%s; retrying legacy partitionKey=%s",
                doc_id,
                legacy_doc_id,
            )
            legacy_payload = await _timeline_payload_from_gremlin(legacy_doc_id, limit=limit)
            if _graph_payload_has_data(legacy_payload):
                payload = legacy_payload
        return _json_response(payload)
    except Exception as exc:
        logging.error(
            "[HTTP][CASE_TIMELINE] Failed to load timeline graph for doc_id=%s: %s",
            doc_id,
            exc,
            exc_info=True,
        )
        return _json_response(
            {"available": False, "error": str(exc), "docId": doc_id},
            status_code=500,
        )


async def _ensure_case_report_artifacts(doc_id: str) -> None:
    """
    On-demand generator for GRAPH_REPORT.md, community_summaries.json, and protocol_completeness.json.
    Queries Cosmos DB Gremlin for the case graph, clusters communities, analyzes completeness,
    renders markdown report, and persists all 3 artifacts to Blob Storage.
    """
    store = get_artifact_store()
    report_path = f"graph/{doc_id}/GRAPH_REPORT.md"
    comm_path = f"graph/{doc_id}/community_summaries.json"
    comp_path = f"graph/{doc_id}/protocol_completeness.json"

    if store.exists(report_path) and store.exists(comm_path) and store.exists(comp_path):
        return

    try:
        logging.info("[CASE_ARTIFACTS] Auto-generating missing report artifacts for doc_id=%s", doc_id)
        graph_data = await _graph_payload_from_gremlin(doc_id, limit=1000)
        nodes = graph_data.get("nodes", [])
        if not nodes:
            logging.warning("[CASE_ARTIFACTS] No nodes found in Cosmos DB for doc_id=%s", doc_id)
            return

        from src.graph.local_graphify import build_from_json, cluster
        from src.reporting.protocol_completeness import analyze_protocol_completeness
        from src.processing.strategies import DefaultReporter
        from src.processing.persistence_manager import PersistenceManager
        from src.storage.blob_storage import AzureBlobStorage
        from src.models.models import SCHEMA_CONFIG
        from src.processing.orchestration_activities import _build_community_summary_payload

        G = build_from_json(graph_data)
        communities, hierarchy = await asyncio.to_thread(cluster, G)

        summaries, community_labels, payload, enable_llm = await _build_community_summary_payload(G, communities, hierarchy)
        G.graph["communities"] = communities
        G.graph["community_summaries"] = summaries
        G.graph["llm_community_summaries_enabled"] = enable_llm

        summaries_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        store.write_bytes(comm_path, summaries_bytes)

        protocol_report = analyze_protocol_completeness(
            schema_config=SCHEMA_CONFIG,
            graph_data=graph_data,
            raw_text="",
            doc_id=doc_id,
        )
        protocol_bytes = json.dumps(protocol_report, ensure_ascii=False, indent=2).encode("utf-8")
        store.write_bytes(comp_path, protocol_bytes)

        conn_str = settings.azure_storage_connection_string or "UseDevelopmentStorage=true"
        container_name = settings.azure_storage_container or "graphify-out"
        blob_storage = AzureBlobStorage(connection_string=conn_str, container_name=container_name)

        persistence = PersistenceManager(blob_storage, base_blob_path=f"graph/{doc_id}")
        reporter = DefaultReporter(persistence)
        await reporter.generate(
            G,
            communities,
            graph_data,
            [{"text": ""}],
            doc_id,
            protocol_completeness=protocol_report,
        )
        logging.info("[CASE_ARTIFACTS] Successfully auto-generated report artifacts for doc_id=%s", doc_id)
    except Exception as exc:
        logging.error("[CASE_ARTIFACTS] Failed auto-generating report artifacts for doc_id=%s: %s", doc_id, exc, exc_info=True)


@app.route(route="cases/{docId}/report", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@require_authenticated_scope
async def get_case_report(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns the Markdown report generated during activity_final_indexing or on-demand auto-generation.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    raw_doc_id = req.route_params.get("docId") or ""
    doc_id = urllib.parse.unquote(raw_doc_id)
    if not doc_id:
        return _cors_response("Missing route parameter: docId", status_code=400)

    store = get_artifact_store()
    candidates = [
        f"graph/{doc_id}/GRAPH_REPORT.md",
        f"graph/{raw_doc_id}/GRAPH_REPORT.md",
    ]

    for path in candidates:
        try:
            if store.exists(path):
                markdown = store.read_bytes(path).decode("utf-8")
                coverage = _case_coverage_metadata(doc_id)
                if coverage.get("is_partial") and "Incomplete source coverage" not in markdown:
                    markdown = _partial_coverage_notice(coverage) + "\n\n" + markdown
                return _json_response(
                    {
                        "docId": doc_id,
                        "markdown": markdown,
                        "source": "blob",
                        "updatedAt": datetime.utcnow().isoformat() + "Z",
                        "coverage": coverage,
                    }
                )
        except Exception as exc:
            logging.warning(
                "[HTTP][CASE_REPORT] Failed reading candidate report path=%s doc_id=%s: %s",
                path,
                doc_id,
                exc,
            )

    # Attempt on-demand auto-generation if not found
    await _ensure_case_report_artifacts(doc_id)

    for path in candidates:
        try:
            if store.exists(path):
                markdown = store.read_bytes(path).decode("utf-8")
                coverage = _case_coverage_metadata(doc_id)
                if coverage.get("is_partial") and "Incomplete source coverage" not in markdown:
                    markdown = _partial_coverage_notice(coverage) + "\n\n" + markdown
                return _json_response(
                    {
                        "docId": doc_id,
                        "markdown": markdown,
                        "source": "blob",
                        "updatedAt": datetime.utcnow().isoformat() + "Z",
                        "coverage": coverage,
                    }
                )
        except Exception as exc:
            pass

    return _json_response(
        {
            "docId": doc_id,
            "markdown": "",
            "source": "blob",
            "updatedAt": datetime.utcnow().isoformat() + "Z",
            "error": "GRAPH_REPORT.md not found for case.",
        },
        status_code=200,
    )


@app.route(route="cases/{docId}/communities", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@require_authenticated_scope
async def get_case_communities(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns the community summaries generated during activity_final_indexing or on-demand auto-generation.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    raw_doc_id = req.route_params.get("docId") or ""
    doc_id = urllib.parse.unquote(raw_doc_id)
    if not doc_id:
        return _cors_response("Missing route parameter: docId", status_code=400)

    store = get_artifact_store()
    candidates = [
        f"graph/{doc_id}/community_summaries.json",
        f"graph/{raw_doc_id}/community_summaries.json",
    ]

    for path in candidates:
        try:
            if store.exists(path):
                payload = json.loads(store.read_bytes(path).decode("utf-8"))
                summaries = payload.get("summaries", {})
                hierarchy = payload.get("hierarchy", {})
                if not isinstance(summaries, dict):
                    summaries = {}
                if not isinstance(hierarchy, dict):
                    hierarchy = {}
                return _json_response(
                    {
                        "docId": doc_id,
                        "summaries": summaries,
                        "hierarchy": hierarchy,
                        "source": "blob",
                        "updatedAt": datetime.utcnow().isoformat() + "Z",
                    }
                )
        except Exception as exc:
            logging.warning(
                "[HTTP][CASE_COMMUNITIES] Failed reading candidate path=%s doc_id=%s: %s",
                path,
                doc_id,
                exc,
            )

    # Attempt on-demand auto-generation if not found
    await _ensure_case_report_artifacts(doc_id)

    for path in candidates:
        try:
            if store.exists(path):
                payload = json.loads(store.read_bytes(path).decode("utf-8"))
                summaries = payload.get("summaries", {})
                hierarchy = payload.get("hierarchy", {})
                if not isinstance(summaries, dict):
                    summaries = {}
                if not isinstance(hierarchy, dict):
                    hierarchy = {}
                return _json_response(
                    {
                        "docId": doc_id,
                        "summaries": summaries,
                        "hierarchy": hierarchy,
                        "source": "blob",
                        "updatedAt": datetime.utcnow().isoformat() + "Z",
                    }
                )
        except Exception as exc:
            pass

    return _json_response(
        {
            "docId": doc_id,
            "summaries": {},
            "hierarchy": {},
            "source": "blob",
            "updatedAt": datetime.utcnow().isoformat() + "Z",
            "error": "community_summaries.json not found for case.",
        },
        status_code=200,
    )


@app.route(route="cases/{docId}/completeness", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@require_authenticated_scope
async def get_case_completeness(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns protocol completeness analysis generated during activity_final_indexing or on-demand auto-generation.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    raw_doc_id = req.route_params.get("docId") or ""
    doc_id = urllib.parse.unquote(raw_doc_id)
    if not doc_id:
        return _cors_response("Missing route parameter: docId", status_code=400)

    store = get_artifact_store()
    candidates = [
        f"graph/{doc_id}/protocol_completeness.json",
        f"graph/{raw_doc_id}/protocol_completeness.json",
    ]

    for path in candidates:
        try:
            if store.exists(path):
                payload = json.loads(store.read_bytes(path).decode("utf-8"))
                if not isinstance(payload, dict):
                    payload = {}
                payload.setdefault("docId", doc_id)
                payload.setdefault("source", "blob")
                payload.setdefault("updatedAt", datetime.utcnow().isoformat() + "Z")
                return _json_response(payload)
        except Exception as exc:
            logging.warning(
                "[HTTP][CASE_COMPLETENESS] Failed reading candidate path=%s doc_id=%s: %s",
                path,
                doc_id,
                exc,
            )

    # Attempt on-demand auto-generation if not found
    await _ensure_case_report_artifacts(doc_id)

    for path in candidates:
        try:
            if store.exists(path):
                payload = json.loads(store.read_bytes(path).decode("utf-8"))
                if not isinstance(payload, dict):
                    payload = {}
                payload.setdefault("docId", doc_id)
                payload.setdefault("source", "blob")
                payload.setdefault("updatedAt", datetime.utcnow().isoformat() + "Z")
                return _json_response(payload)
        except Exception as exc:
            pass

    return _json_response(
        {
            "docId": doc_id,
            "source": "blob",
            "updatedAt": datetime.utcnow().isoformat() + "Z",
            "summary": {},
            "items": [],
            "error": "protocol_completeness.json not found for case.",
        },
        status_code=200,
    )



@app.route(route="cases/{docId}/completeness/verify", methods=["POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@app.durable_client_input(client_name="client")
@require_authenticated_scope
async def verify_case_completeness_async(
    req: func.HttpRequest, client: df.DurableOrchestrationClient
) -> func.HttpResponse:
    """
    On-demand asynchronous verification of protocol completeness missing items using QueryAgent.
    Returns HTTP 202 Accepted response with status check URI to prevent browser timeouts.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    raw_doc_id = req.route_params.get("docId") or ""
    doc_id = urllib.parse.unquote(raw_doc_id)
    if not doc_id:
        return _cors_response("Missing route parameter: docId", status_code=400)


    trace_id = trace_id_from_request(req, prefix="completeness_verify")
    instance_id = await client.start_new(
        "completeness_verification_orchestrator",
        client_input={
            "doc_id": doc_id,
            "trace_id": trace_id,
            "critical_only": False,
            "max_items": 15,
        },
    )
    logging.info(
        f"[HTTP][COMPLETENESS_VERIFY] Started verification orchestration for doc_id {doc_id}. Instance ID: {instance_id}"
    )
    return _check_status_response_with_cors(client, req, instance_id)


@app.orchestration_trigger(context_name="context")
def completeness_verification_orchestrator(context: df.DurableOrchestrationContext):
    """
    Durable Orchestrator to execute background protocol completeness agent verification.
    """
    input_data = context.get_input() or {}
    doc_id = input_data.get("doc_id")
    if not context.is_replaying:
        logging.info(f"[ORCHESTRATOR][COMPLETENESS_VERIFY] Starting verification for doc_id: {doc_id}")

    result = yield context.call_activity("activity_verify_protocol_completeness", input_data)
    return result



@app.route(route="pipelines", methods=["POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@app.durable_client_input(client_name="client")
@require_authenticated_scope
async def start_pipeline_async(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """
    Production-ready asynchronous pipeline entry point.
    Starts the orchestration and returns the instance ID.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    trace_id = trace_id_from_request(req, prefix="pipeline")
    started_at = time.perf_counter()
    # 1. Validation
    MAX_SIZE_BYTES = 50 * 1024 * 1024
    file_bytes = req.get_body()
    if not file_bytes:
        return _cors_response("Missing PDF body.", status_code=400)
    
    if len(file_bytes) > MAX_SIZE_BYTES:
        return _cors_response("File too large.", status_code=413)

    if not file_bytes.startswith(b"%PDF"):
        return _cors_response("Invalid file type. Only PDF supported.", status_code=415)

    try:
        filename = _resolve_upload_filename(req)
    except ValueError as exc:
        return _cors_response(str(exc), status_code=400, req=req)

    try:
        duplicate = await _case_duplicate_preflight(filename)
        if duplicate.get("status") == "duplicate":
            log_event(
                "pipeline.duplicate",
                trace_id=trace_id,
                doc_id=duplicate.get("doc_id"),
                matched_doc_id=duplicate.get("matched_doc_id"),
                matched_by=duplicate.get("matched_by"),
                data_source_id=duplicate.get("data_source_id"),
                live_query=duplicate.get("live_query"),
            )
            log_metric("pipeline_duplicate_count", 1, doc_id=duplicate.get("doc_id"))
            logging.info(
                "[HTTP][PIPELINE_START][DUPLICATE] filename=%s doc_id=%s matched_doc_id=%s matched_by=%s",
                filename,
                duplicate.get("doc_id"),
                duplicate.get("matched_doc_id"),
                duplicate.get("matched_by"),
            )
            return _preflight_json_response(duplicate)
    except Exception as exc:
        log_event(
            "pipeline.preflight_failed",
            level=logging.ERROR,
            trace_id=trace_id,
            doc_id=_doc_id_from_filename(filename),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        log_metric("pipeline_failure_count", 1, phase="preflight")
        logging.error(
            "[HTTP][PIPELINE_START][PREFLIGHT_ERROR] filename=%s error=%s",
            filename,
            exc,
            exc_info=True,
        )
        return _json_response(
            {"status": "error", "error": str(exc), "doc_id": _doc_id_from_filename(filename)},
            status_code=500,
        )
    
    blob_path = f"uploads/{filename}"
    doc_id = _doc_id_from_filename(filename)
    store = get_artifact_store()
    store.write_bytes(blob_path, file_bytes)

    # 3. Start Orchestration
    instance_id = await client.start_new(
        "pipeline_orchestrator",
        client_input={"blob_path": blob_path, "doc_id": doc_id, "trace_id": trace_id},
    )
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    log_event(
        "pipeline.started",
        trace_id=trace_id,
        instance_id=instance_id,
        doc_id=doc_id,
        blob_path=blob_path,
        file_size_bytes=len(file_bytes),
        duration_ms=elapsed_ms,
    )
    log_metric("pipeline_start_count", 1, doc_id=doc_id)
    
    logging.info(f"[HTTP] Started orchestration for {blob_path}. ID: {instance_id}")
    
    return _check_status_response_with_cors(client, req, instance_id)


@app.route(route="pipeline-preflight", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@require_authenticated_scope
async def pipeline_duplicate_preflight(req: func.HttpRequest) -> func.HttpResponse:
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    trace_id = trace_id_from_request(req, prefix="pipeline-preflight")
    raw_filename = req.params.get("filename") or req.params.get("doc_id") or ""
    filename = urllib.parse.unquote(raw_filename)
    if not filename:
        return _preflight_json_response(
            {"status": "error", "error": "Missing query parameter: filename"},
            status_code=400,
            req=req,
        )

    try:
        result = await _case_duplicate_preflight(filename)
        result["trace_id"] = trace_id
        log_event(
            "pipeline.preflight_checked",
            trace_id=trace_id,
            doc_id=result.get("doc_id"),
            status=result.get("status"),
            matched_doc_id=result.get("matched_doc_id"),
            matched_by=result.get("matched_by"),
            data_source_id=result.get("data_source_id"),
            live_query=result.get("live_query"),
            warning=result.get("warning"),
        )
        return _preflight_json_response(result, req=req)
    except Exception as exc:
        doc_id = _doc_id_from_filename(filename)
        log_event(
            "pipeline.preflight_failed",
            level=logging.ERROR,
            trace_id=trace_id,
            doc_id=doc_id,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        logging.error(
            "[HTTP][PIPELINE_PREFLIGHT][ERROR] filename=%s error=%s",
            filename,
            exc,
            exc_info=True,
        )
        return _preflight_json_response(
            {
                "status": "new",
                "doc_id": doc_id,
                "matched_doc_id": None,
                "matched_by": "none",
                "sample_node": None,
                "cache_hit": False,
                "cache_source": "none",
                "live_query": False,
                "data_source_id": None,
                "trace_id": trace_id,
                "warning": f"Preflight check error: {exc}",
            },
            status_code=200,
            req=req,
        )



@app.route(route="pipelines/{instanceId}", methods=["GET", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@app.durable_client_input(client_name="client")
@require_authenticated_scope
async def get_pipeline_status(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """
    Retrieves the status of a specific pipeline run.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    instance_id = req.route_params.get("instanceId")
    status = await client.get_status(instance_id)
    
    if not status:
        return _cors_response("Pipeline instance not found.", status_code=404)

    response_data = {
        "instanceId": instance_id,
        "runtimeStatus": str(status.runtime_status),
        "createdTime": status.created_time.isoformat() if status.created_time else None,
        "lastUpdatedTime": status.last_updated_time.isoformat() if status.last_updated_time else None,
        "output": status.output,
        "customStatus": status.custom_status
    }

    return _json_response(response_data)


# --------------------------------------------------------------------------------
# PRODUCER: Blob Trigger to Queue
# --------------------------------------------------------------------------------


@app.blob_trigger(
    arg_name="blob", path="graphify-out/raw/{name}", connection="AzureStorageConnection"
)
@app.queue_output(
    arg_name="msg", queue_name="pipeline-queue", connection="AzureStorageConnection"
)
def blob_to_queue(blob: func.InputStream, msg: func.Out[str]) -> None:
    """
    Triggers when a new blob is uploaded to the 'raw' container.
    Writes the blob path to the 'pipeline-queue' to initiate processing.
    """
    blob_path = blob.name
    if blob_path.startswith("graphify-out/"):
        blob_path = blob_path[len("graphify-out/") :]
    logging.info(f"[PRODUCER] Blob detected: {blob.name}. Writing to pipeline-queue.")
    msg.set(blob_path)


# --------------------------------------------------------------------------------
# CONSUMER: Queue Trigger to Orchestrator
# --------------------------------------------------------------------------------


@app.queue_trigger(
    arg_name="msg", queue_name="pipeline-queue", connection="AzureStorageConnection"
)
@app.durable_client_input(client_name="client")
async def queue_trigger(
    msg: func.QueueMessage, client: df.DurableOrchestrationClient
) -> None:
    """
    Consumes a blob path from the 'pipeline-queue' and starts the Durable Orchestrator.
    """
    blob_path = msg.get_body().decode("utf-8")
    logging.info(f"[CONSUMER] Received path: {blob_path}. Starting orchestrator.")

    instance_id = await client.start_new(
        "pipeline_orchestrator", client_input=blob_path
    )
    logging.info(f"[CONSUMER] Orchestrator started. Instance ID: {instance_id}")


# --------------------------------------------------------------------------------
# ORCHESTRATOR: Map-Reduce Pipeline
# --------------------------------------------------------------------------------


@app.orchestration_trigger(context_name="context")
def pipeline_orchestrator(context: df.DurableOrchestrationContext):
    """
    Durable Orchestrator implementing the Map-Reduce pattern.
    - Map: Split document into chunks.
    - Parallel Process: Extract entities/relationships from each chunk (Fan-Out).
    - Reduce: Merge results into a unified graph (Fan-In).
    """
    pipeline_input = _pipeline_input_payload(context.get_input())
    blob_path = pipeline_input["blob_path"]
    doc_id = pipeline_input["doc_id"]
    trace_id = pipeline_input["trace_id"]
    total_chunks = 0
    completed_chunks = 0
    current_batch = 0
    blocked_chunks = 0

    if not context.is_replaying:
        log_event(
            "pipeline.orchestrator_started",
            trace_id=trace_id,
            doc_id=doc_id,
            blob_path=blob_path,
            instance_id=_durable_instance_id(context),
        )
        logging.info(f"[ORCHESTRATOR][START] Processing blob: {blob_path}")

    try:
        context.set_custom_status(
            _pipeline_status_payload(
                phase="preflight",
                message="Validating runtime dependencies",
                blob_path=blob_path,
                doc_id=doc_id,
                trace_id=trace_id,
                total_chunks=0,
                completed_chunks=0,
                current_batch=0,
                last_error=None,
            )
        )
        yield context.call_activity("activity_preflight_check", None)

        context.set_custom_status(
            _pipeline_status_payload(
                phase="chunking",
                message="Extracting document text and materializing chunks",
                blob_path=blob_path,
                doc_id=doc_id,
                trace_id=trace_id,
                total_chunks=0,
                completed_chunks=0,
                current_batch=0,
                last_error=None,
            )
        )
        chunk_meta: Dict[str, Any] = yield context.call_activity("activity_chunking", blob_path)

        if not chunk_meta or chunk_meta.get("total_chunks", 0) == 0:
            logging.warning(f"[ORCHESTRATOR][END] No chunks materialized for {blob_path}. Ending orchestration.")
            context.set_custom_status(
                _pipeline_status_payload(
                    phase="completed",
                    message="No chunks were produced",
                    blob_path=blob_path,
                    doc_id=doc_id,
                    trace_id=trace_id,
                    total_chunks=0,
                    completed_chunks=0,
                    current_batch=0,
                    last_error=None,
                )
            )
            return {
                "status": "completed_no_content",
                "blob_path": blob_path,
                "final_graph_path": None,
                "trace_id": trace_id,
            }

        batch_size = max(1, int(settings.orchestrator_extract_batch_size))
        extraction_results: List[Dict[str, Any]] = []
        chunk_ids = chunk_meta.get("chunk_ids", [])
        total_chunks = len(chunk_ids)
        batches = _batched(chunk_ids, batch_size)

        for batch_index, batch in enumerate(batches, start=1):
            context.set_custom_status(
                _pipeline_status_payload(
                    phase="extracting",
                    message=f"Extracting batch {batch_index} of {len(batches)}",
                    blob_path=blob_path,
                    doc_id=doc_id,
                    trace_id=trace_id,
                    total_chunks=total_chunks,
                    completed_chunks=completed_chunks,
                    current_batch=batch_index,
                    last_error=None,
                )
            )
            current_batch = batch_index
            extraction_tasks = [
                context.call_activity("activity_extract_chunk", {"doc_id": doc_id, "chunk_id": cid})
                for cid in batch
            ]
            batch_result_payloads: List[Dict[str, Any]] = yield context.task_all(extraction_tasks)
            extraction_results.extend(batch_result_payloads)
            completed_chunks = len(extraction_results)
            blocked_chunks = sum(
                1
                for item in extraction_results
                if item.get("outcome") in ("content_blocked", "extraction_failed")
            )
            context.set_custom_status(
                _pipeline_status_payload(
                    phase="extracting",
                    message=f"Completed batch {batch_index} of {len(batches)}",
                    blob_path=blob_path,
                    doc_id=doc_id,
                    trace_id=trace_id,
                    total_chunks=total_chunks,
                    completed_chunks=completed_chunks,
                    current_batch=current_batch,
                    last_error=None,
                )
            )

        context.set_custom_status(
            _pipeline_status_payload(
                phase="merging",
                message="Merging partial graph extractions",
                blob_path=blob_path,
                doc_id=doc_id,
                trace_id=trace_id,
                total_chunks=total_chunks,
                completed_chunks=completed_chunks,
                current_batch=current_batch,
                last_error=None,
            )
        )
        merge_result: Dict[str, Any] = yield context.call_activity(
            "activity_merge_partials",
            {
                "results": extraction_results,
                "doc_id": doc_id,
                "trace_id": trace_id,
                "total_chunks": total_chunks,
            },
        )

        if merge_result.get("status") == "content_blocked":
            if not context.is_replaying:
                log_event(
                    "pipeline.content_blocked",
                    trace_id=trace_id,
                    doc_id=doc_id,
                    total_chunks=total_chunks,
                    blocked_chunks=merge_result.get("blocked_chunks", blocked_chunks),
                    coverage_ratio=merge_result.get("coverage_ratio", 0.0),
                    manifest_path=merge_result.get("manifest_path"),
                )
                log_metric("pipeline_content_blocked_documents_count", 1, doc_id=doc_id)
            context.set_custom_status(
                _pipeline_status_payload(
                    phase="content_blocked",
                    message="All usable graph content was blocked by provider policy",
                    blob_path=blob_path,
                    doc_id=doc_id,
                    trace_id=trace_id,
                    total_chunks=total_chunks,
                    completed_chunks=completed_chunks,
                    current_batch=current_batch,
                    last_error=None,
                    blocked_chunks=merge_result.get("blocked_chunks", blocked_chunks),
                    failed_chunks=merge_result.get("failed_chunks", 0),
                    coverage_ratio=merge_result.get("coverage_ratio", 0.0),
                    blocked_page_ranges=merge_result.get("blocked_page_ranges", []),
                    failed_page_ranges=merge_result.get("failed_page_ranges", []),
                    manifest_path=merge_result.get("manifest_path"),
                    review_required=True,
                )
            )
            return {
                "status": "content_blocked",
                "blob_path": blob_path,
                "final_graph_path": None,
                "trace_id": trace_id,
                "manifest_path": merge_result.get("manifest_path"),
                "coverage_ratio": merge_result.get("coverage_ratio", 0.0),
                "blocked_count": merge_result.get("blocked_chunks", blocked_chunks),
                "failed_count": merge_result.get("failed_chunks", 0),
                "blocked_page_ranges": merge_result.get("blocked_page_ranges", []),
                "failed_page_ranges": merge_result.get("failed_page_ranges", []),
                "review_required": True,
            }

        merged_graph_path = merge_result["merged_graph_path"]

        context.set_custom_status(
            _pipeline_status_payload(
                phase="indexing",
                message="Saving graph and updating downstream indexes",
                blob_path=blob_path,
                doc_id=doc_id,
                trace_id=trace_id,
                total_chunks=total_chunks,
                completed_chunks=completed_chunks,
                current_batch=current_batch,
                last_error=None,
            )
        )
        final_path: str = yield context.call_activity(
            "activity_final_indexing", merged_graph_path
        )

        if not context.is_replaying:
            total_prompt_tokens = sum(
                r.get("token_usage", {}).get("input_tokens", r.get("token_usage", {}).get("prompt_tokens", 0))
                for r in extraction_results
                if isinstance(r, dict)
            )
            total_completion_tokens = sum(
                r.get("token_usage", {}).get("output_tokens", r.get("token_usage", {}).get("completion_tokens", 0))
                for r in extraction_results
                if isinstance(r, dict)
            )
            total_tokens = total_prompt_tokens + total_completion_tokens
            model_name = getattr(settings, "graphify_agent_model", "gpt-4o") or "gpt-4o"
            total_cost_usd = calculate_estimated_cost(total_prompt_tokens, total_completion_tokens, model_name=model_name)

            log_event(
                "pipeline.ingestion.summary",
                trace_id=trace_id,
                doc_id=doc_id,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                total_tokens=total_tokens,
                total_cost_usd=total_cost_usd,
                chunks_processed_count=len(extraction_results),
                blocked_chunks=merge_result.get("blocked_chunks", 0),
                coverage_ratio=merge_result.get("coverage_ratio", 1.0),
                is_partial=merge_result.get("is_partial", False),
            )

            log_event(
                "pipeline.completed",
                trace_id=trace_id,
                doc_id=doc_id,
                instance_id=_durable_instance_id(context),
                total_chunks=total_chunks,
                completed_chunks=completed_chunks,
                final_graph_path=final_path,
            )
            log_metric("pipeline_success_count", 1, doc_id=doc_id)
            log_metric("pipeline_chunks_total", total_chunks, doc_id=doc_id)
            log_metric(
                "pipeline_ingestion_coverage_ratio",
                merge_result.get("coverage_ratio", 1.0),
                unit="ratio",
                doc_id=doc_id,
            )
            if merge_result.get("is_partial"):
                log_metric("pipeline_partial_documents_count", 1, doc_id=doc_id)
            logging.info(
                f"[ORCHESTRATOR][COMPLETE] Pipeline finished. Final graph: {final_path}"
            )

        context.set_custom_status(
            _pipeline_status_payload(
                phase=merge_result.get("status", "completed"),
                message=(
                    "Pipeline completed with excluded source ranges"
                    if merge_result.get("is_partial")
                    else "Pipeline completed"
                ),
                blob_path=blob_path,
                doc_id=doc_id,
                trace_id=trace_id,
                total_chunks=total_chunks,
                completed_chunks=completed_chunks,
                current_batch=current_batch,
                last_error=None,
                blocked_chunks=merge_result.get("blocked_chunks", 0),
                failed_chunks=merge_result.get("failed_chunks", 0),
                coverage_ratio=merge_result.get("coverage_ratio", 1.0),
                blocked_page_ranges=merge_result.get("blocked_page_ranges", []),
                failed_page_ranges=merge_result.get("failed_page_ranges", []),
                manifest_path=merge_result.get("manifest_path"),
                review_required=merge_result.get("review_required", False),
            )
        )
        return {
            "status": merge_result.get("status", "completed"),
            "blob_path": blob_path,
            "final_graph_path": final_path,
            "trace_id": trace_id,
            "manifest_path": merge_result.get("manifest_path"),
            "coverage_ratio": merge_result.get("coverage_ratio", 1.0),
            "blocked_count": merge_result.get("blocked_chunks", 0),
            "failed_count": merge_result.get("failed_chunks", 0),
            "blocked_page_ranges": merge_result.get("blocked_page_ranges", []),
            "failed_page_ranges": merge_result.get("failed_page_ranges", []),
            "review_required": merge_result.get("review_required", False),
        }
    except Exception as exc:
        import traceback
        if not context.is_replaying:
            log_event(
                "pipeline.failed",
                level=logging.ERROR,
                trace_id=trace_id,
                doc_id=doc_id,
                instance_id=_durable_instance_id(context),
                phase="failed",
                error_type=type(exc).__name__,
                error=_compact_error(exc),
            )
            log_metric("pipeline_failure_count", 1, doc_id=doc_id)
        logging.error(f"[CRITICAL][ORCHESTRATOR] Pipeline failed: {str(exc)}")
        logging.error(traceback.format_exc())
        context.set_custom_status(
            _pipeline_status_payload(
                phase="failed",
                message="Pipeline failed",
                blob_path=blob_path,
                doc_id=doc_id,
                trace_id=trace_id,
                total_chunks=total_chunks,
                completed_chunks=completed_chunks,
                current_batch=current_batch,
                last_error=_compact_error(exc),
            )
        )
        try:
            yield context.call_activity("activity_cleanup_failed_run", {"doc_id": doc_id})
        except Exception as cleanup_exc:
            logging.error(f"[CRITICAL][ORCHESTRATOR] Cleanup activity failed: {cleanup_exc}")
        raise exc


# --------------------------------------------------------------------------------
# ACTIVITIES: Path-Only Atomic Operations
# --------------------------------------------------------------------------------


@app.activity_trigger(input_name="unused")
async def activity_preflight_check(unused: Any) -> dict:
    """Validates required runtime dependencies before pipeline work starts."""
    logging.info("[ACTIVITY][PREFLIGHT][START] Checking runtime dependencies")
    try:
        result = await DependencyChecker().ensure_ready()
        logging.info("[ACTIVITY][PREFLIGHT][END] Dependencies are ready")
        return result
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_preflight_check: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


@app.activity_trigger(input_name="blobPath")
async def activity_chunking(blobPath: str) -> Dict[str, Any]:
    """Wraps the chunking logic. Input: blob path, Output: chunk metadata dict."""
    started_at = time.perf_counter()
    doc_id = os.path.splitext(os.path.basename(blobPath))[0]
    logging.info(f"[ACTIVITY][CHUNKING][START] Processing: {blobPath}")
    try:
        chunk_meta = await pipeline_activities.activity_chunking(blobPath)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        log_event(
            "pipeline.chunking.completed",
            doc_id=doc_id,
            blob_path=blobPath,
            duration_ms=elapsed_ms,
            total_chunks=chunk_meta.get("total_chunks", 0),
        )
        log_metric("pipeline_chunking_duration_ms", elapsed_ms, unit="ms", doc_id=doc_id)
        log_metric("pipeline_chunks_total", chunk_meta.get("total_chunks", 0), doc_id=doc_id)
        logging.info(
            f"[ACTIVITY][CHUNKING][END] Chunks generated: {chunk_meta.get('total_chunks', 0)}"
        )
        return chunk_meta
    except Exception as e:
        import traceback
        log_metric("pipeline_failure_count", 1, phase="chunking", doc_id=doc_id)
        error_msg = _compact_error(
            f"Error in activity_chunking: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


@app.activity_trigger(input_name="chunk")
async def activity_extract_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts entities from a single chunk in-memory. Input: chunk dict/reference, Output: extraction result dict."""
    started_at = time.perf_counter()
    doc_id = str(chunk.get("doc_id") or "unknown")
    chunk_idx = chunk.get("chunk_index", "unknown") if "text" in chunk else chunk.get("chunk_id", "unknown")
    logging.info(f"[ACTIVITY][EXTRACT_CHUNK][START] Chunk: {chunk_idx}")
    try:
        result_payload = await pipeline_activities.activity_extract_chunk(chunk)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        log_event(
            "pipeline.extraction.chunk_completed",
            doc_id=doc_id,
            chunk_id=chunk_idx,
            duration_ms=elapsed_ms,
        )
        log_metric("pipeline_extraction_duration_ms", elapsed_ms, unit="ms", doc_id=doc_id)
        logging.info(
            f"[ACTIVITY][EXTRACT_CHUNK][END] Extraction complete for chunk: {chunk_idx}"
        )
        return result_payload
    except Exception as e:
        import traceback
        log_metric("pipeline_failure_count", 1, phase="extracting", doc_id=doc_id)
        error_msg = _compact_error(
            f"Error in activity_extract_chunk: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


@app.activity_trigger(input_name="extractions")
async def activity_merge_partials(
    extractions: Any,
) -> Any:
    """Merges partial graphs. Input: List of extraction result dicts, Output: merged graph path (Claim-Check)."""
    if not extractions:
        logging.warning("[ACTIVITY][MERGE_PARTIALS] Received empty or None extractions. Skipping merge.")
        return None

    started_at = time.perf_counter()
    result_items = extractions.get("results", []) if isinstance(extractions, dict) else extractions
    doc_id = str(
        (extractions.get("doc_id") if isinstance(extractions, dict) else None)
        or ((result_items[0] or {}).get("doc_id") if result_items else None)
        or "unknown"
    )
    logging.info(
        f"[ACTIVITY][MERGE_PARTIALS][START] Merging {len(result_items)} results in-memory."
    )
    try:
        merged_graph_path = await pipeline_activities.activity_merge_partials(
            extractions
        )
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        log_event(
            "pipeline.merge.completed",
            doc_id=doc_id,
            extraction_count=len(result_items),
            merged_graph_path=merged_graph_path,
            duration_ms=elapsed_ms,
        )
        log_metric("pipeline_merge_duration_ms", elapsed_ms, unit="ms", doc_id=doc_id)
        logging.info(
            f"[ACTIVITY][MERGE_PARTIALS][END] Merged graph materialized to: {merged_graph_path}"
        )
        return merged_graph_path
    except Exception as e:
        import traceback
        log_metric("pipeline_failure_count", 1, phase="merging", doc_id=doc_id)
        error_msg = _compact_error(
            f"Error in activity_merge_partials: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


@app.activity_trigger(input_name="mergedGraphPath")
async def activity_final_indexing(mergedGraphPath: str) -> str:
    """Performs final indexing and cleanup. Input: merged graph path, Output: final graph path (cosmos_uri)."""
    started_at = time.perf_counter()
    parts = str(mergedGraphPath or "").split("/")
    doc_id = parts[1] if len(parts) > 2 and parts[0] == "graph" else "unknown"
    logging.info(
        f"[ACTIVITY][FINAL_INDEXING][START] Indexing graph from: {mergedGraphPath}"
    )
    try:
        final_path = await pipeline_activities.activity_final_indexing(mergedGraphPath)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        coverage = _case_coverage_metadata(doc_id)
        log_event(
            "pipeline.final_indexing.completed",
            doc_id=doc_id,
            merged_graph_path=mergedGraphPath,
            final_graph_path=final_path,
            duration_ms=elapsed_ms,
        )
        log_metric(
            "pipeline_final_indexing_duration_ms",
            elapsed_ms,
            unit="ms",
            doc_id=doc_id,
            is_partial=coverage.get("is_partial", False),
        )
        logging.info(f"[ACTIVITY][FINAL_INDEXING][END] Final graph indexing complete: {final_path}")
        return final_path
    except Exception as e:
        import traceback
        log_metric("pipeline_failure_count", 1, phase="indexing", doc_id=doc_id)
        error_msg = _compact_error(
            f"Error in activity_final_indexing: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


@app.activity_trigger(input_name="payload")
async def activity_cleanup_failed_run(payload: dict) -> None:
    """Cleans up temporary resources for failed runs. Input: {"doc_id": str}"""
    logging.info(f"[ACTIVITY][CLEANUP_FAILED_RUN][START] doc_id: {payload.get('doc_id')}")
    try:
        await pipeline_activities.activity_cleanup_failed_run(payload)
        logging.info(f"[ACTIVITY][CLEANUP_FAILED_RUN][END] doc_id: {payload.get('doc_id')}")
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_cleanup_failed_run: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


@app.activity_trigger(input_name="payload")
async def activity_verify_protocol_completeness(payload: dict) -> dict:
    """Audits missing protocol items using QueryAgent / Azure Search. Input: {"doc_id": str}"""
    logging.info(f"[ACTIVITY][VERIFY_COMPLETENESS][START] doc_id: {payload.get('doc_id')}")
    try:
        res = await pipeline_activities.activity_verify_protocol_completeness(payload)
        logging.info(f"[ACTIVITY][VERIFY_COMPLETENESS][END] doc_id: {payload.get('doc_id')}")
        return res or {}
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_verify_protocol_completeness: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)



# --------------------------------------------------------------------------------
# INVESTIGATION QUERY ENGINE (Stateless Activities & Endpoint)
# --------------------------------------------------------------------------------


@app.route(route="query", methods=["POST", "OPTIONS"], auth_level=func.AuthLevel.ANONYMOUS)
@app.durable_client_input(client_name="client")
@require_authenticated_scope
async def start_query_async(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """
    Stateless investigative query entry point.
    Starts the query orchestration and returns status check details.
    """
    if req.method == "OPTIONS":
        return _handle_options_request(req)
    trace_id = trace_id_from_request(req, prefix="query")
    started_at = time.perf_counter()
    try:
        req_body = req.get_json()
    except ValueError:
        return _cors_response("Invalid JSON body.", status_code=400)

    question = req_body.get("question") or ""
    doc_id = req_body.get("doc_id")
    state = req_body.get("state")
    autonomous_mode = req_body.get("autonomous_mode", False)


    if not question and not state:
        return _cors_response("Missing required field: question", status_code=400)
    if not doc_id:
        return _cors_response("Missing required field: doc_id", status_code=400)

    instance_id = await client.start_new(
        "query_orchestrator", 
        client_input={
            "question": question,
            "doc_id": doc_id,
            "state": state,
            "trace_id": trace_id,
            "autonomous_mode": autonomous_mode,
        }
    )
    elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    log_event(
        "query.started",
        trace_id=trace_id,
        instance_id=instance_id,
        doc_id=doc_id,
        question_length=len(str(question)),
        duration_ms=elapsed_ms,
    )
    log_metric("query_start_count", 1, doc_id=doc_id)
    
    logging.info(f"[HTTP] Started query orchestration for doc_id {doc_id}. ID: {instance_id}")
    return _check_status_response_with_cors(client, req, instance_id)


@app.orchestration_trigger(context_name="context")
def query_orchestrator(context: df.DurableOrchestrationContext):
    """
    Durable Orchestrator to route and execute a query activity.
    """
    query_input = context.get_input()
    if not context.is_replaying:
        log_event(
            "query.orchestrator_started",
            trace_id=query_input.get("trace_id"),
            doc_id=query_input.get("doc_id"),
            instance_id=_durable_instance_id(context),
        )
        logging.info(f"[ORCHESTRATOR][QUERY] Starting query orchestrator for doc_id: {query_input.get('doc_id')}")

    try:
        context.set_custom_status(
            {
                "phase": "querying",
                "message": "Answering investigative query",
                "doc_id": query_input.get("doc_id"),
                "trace_id": query_input.get("trace_id"),
                "last_error": None,
            }
        )
        result = yield context.call_activity("activity_query_investigation", query_input)
        context.set_custom_status(
            {
                "phase": "completed",
                "message": "Query completed",
                "doc_id": query_input.get("doc_id"),
                "trace_id": query_input.get("trace_id"),
                "last_error": None,
            }
        )
        return result
    except Exception as exc:
        import traceback
        context.set_custom_status(
            {
                "phase": "failed",
                "message": "Query failed",
                "doc_id": query_input.get("doc_id"),
                "trace_id": query_input.get("trace_id"),
                "last_error": _compact_error(exc),
            }
        )
        if not context.is_replaying:
            log_event(
                "query.failed",
                level=logging.ERROR,
                trace_id=query_input.get("trace_id"),
                doc_id=query_input.get("doc_id"),
                instance_id=_durable_instance_id(context),
                error_type=type(exc).__name__,
                error=_compact_error(exc),
            )
            log_metric("query_failure_count", 1, doc_id=query_input.get("doc_id"))
        logging.error(f"[CRITICAL][ORCHESTRATOR] Query orchestrator failed: {str(exc)}")
        logging.error(traceback.format_exc())
        raise exc


@app.activity_trigger(input_name="queryInput")
async def activity_query_investigation(queryInput: dict) -> dict:
    """
    Stateless activity to route and answer an investigative query.
    Input format: {"question": str, "doc_id": str, "state": dict}
    """
    question = queryInput.get("question") or ""
    doc_id = queryInput["doc_id"]
    state_dict = queryInput.get("state")
    trace_id = queryInput.get("trace_id")
    autonomous_mode = queryInput.get("autonomous_mode", False)

    started_at = time.perf_counter()
    
    logging.info(f"[ACTIVITY][QUERY][START] doc_id: {doc_id} | question: {question[:50]}...")
    try:
        coverage = _case_coverage_metadata(doc_id)
        agent_question = question
        if coverage.get("is_partial"):
            agent_question = (
                f"{question}\n\n"
                "COVERAGE CONSTRAINT: Some source page ranges were excluded by provider policy. "
                "Answer only from processed evidence. Never state or imply that an entity, "
                "relationship, offense, or fact is absent from the entire document; qualify any "
                "non-finding as applying only to processed ranges."
            )
        async with QueryAgent(doc_id=doc_id, stateless=True) as agent:
            result_state = await agent.answer_question_stateless(
                agent_question,
                state_dict,
                trace_id=trace_id,
                autonomous_mode=autonomous_mode,
            )
            elapsed_ms = int((time.perf_counter() - started_at) * 1000)
            answer = result_state.get("final_answer")
            if coverage.get("is_partial"):
                answer = f"{_partial_coverage_notice(coverage)}\n\n{answer or ''}".strip()
                result_state["final_answer"] = answer
            log_event(
                "query.completed",
                trace_id=trace_id,
                doc_id=doc_id,
                duration_ms=elapsed_ms,
                answer_length=len(str(answer or "")),
            )
            log_metric("query_success_count", 1, doc_id=doc_id)
            log_metric("query_duration_ms", elapsed_ms, unit="ms", doc_id=doc_id)
            logging.info(f"[ACTIVITY][QUERY][END] doc_id: {doc_id} query finished successfully.")
            payload = {
                "state": result_state,
                "answer": answer,
                "trace_id": trace_id,
                "coverage": coverage,
            }
            json.dumps(payload, ensure_ascii=False)
            return payload
    except Exception as e:
        import traceback
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
        log_event(
            "query.failed",
            level=logging.ERROR,
            trace_id=trace_id,
            doc_id=doc_id,
            duration_ms=elapsed_ms,
            error_type=type(e).__name__,
            error=_compact_error(e),
        )
        log_metric("query_failure_count", 1, doc_id=doc_id)
        error_msg = _compact_error(
            f"Error in activity_query_investigation: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)
