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
from src.storage.factory import get_artifact_store
from src.storage.factory import get_cosmos_manager
from src.utils.config import settings
from src.operations.dependency_checker import DependencyChecker
from src.query_agent.query_agent import QueryAgent
from src.models.models import SCHEMA_CONFIG  # Forces loading schema into memory cache during worker init

# Initialize the Durable Functions App (Python V2 Model)
app = df.DFApp(http_auth_level=func.AuthLevel.ANONYMOUS)

_DUPLICATE_PREFLIGHT_CACHE: Dict[str, Dict[str, Any]] = {}
_DUPLICATE_PREFLIGHT_TTL_SECONDS = 300


def _batched(items: List[str], batch_size: int) -> List[List[str]]:
    safe_batch_size = max(1, int(batch_size))
    return [
        items[i : i + safe_batch_size]
        for i in range(0, len(items), safe_batch_size)
    ]


def _json_response(data: Any, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
    )


def _compact_error(exc: Exception | str, *, limit: int = 3000) -> str:
    message = str(exc)
    if len(message) <= limit:
        return message
    return f"{message[:limit]}... [truncated; original length={len(message)} chars]"


def _doc_id_from_filename(filename: str) -> str:
    safe_name = os.path.basename((filename or "").strip())
    return os.path.splitext(safe_name)[0]


def _legacy_encoded_doc_id(doc_id: str) -> str:
    if not doc_id or "%" in doc_id:
        return ""

    encoded = urllib.parse.quote(doc_id, safe="")
    return encoded if encoded != doc_id else ""


def _graph_payload_has_data(payload: Dict[str, Any]) -> bool:
    return bool(payload.get("nodes") or payload.get("edges"))


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
        }

    now = time.time()
    cached = _DUPLICATE_PREFLIGHT_CACHE.get(doc_id)
    if cached and now - float(cached.get("cached_at", 0)) < _DUPLICATE_PREFLIGHT_TTL_SECONDS:
        payload = dict(cached["payload"])
        payload["cache_hit"] = True
        return payload

    cosmos_mgr = get_cosmos_manager()
    query = (
        "g.V().has('partitionKey', pk).limit(1)"
        ".project('id', 'label', 'properties')"
        ".by(id()).by(label()).by(valueMap(true))"
    )
    matched_by = "partitionKey"
    vertices = await cosmos_mgr.execute_query(query, {"pk": doc_id})

    if not vertices:
        query = (
            "g.V().has('source_file', source_file).limit(1)"
            ".project('id', 'label', 'properties')"
            ".by(id()).by(label()).by(valueMap(true))"
        )
        matched_by = "source_file"
        vertices = await cosmos_mgr.execute_query(query, {"source_file": doc_id})

    if vertices:
        sample_node = _sample_node_from_vertex(vertices[0])
        props = vertices[0].get("properties") or {}
        matched_doc_id = (
            _property_value(props, "partitionKey")
            or _property_value(props, "pk")
            or _property_value(props, "source_file")
            or doc_id
        )
        payload = {
            "status": "duplicate",
            "doc_id": doc_id,
            "matched_doc_id": str(matched_doc_id),
            "matched_by": matched_by,
            "sample_node": sample_node,
            "cache_hit": False,
        }
    else:
        payload = {
            "status": "new",
            "doc_id": doc_id,
            "matched_doc_id": None,
            "matched_by": "none",
            "sample_node": None,
            "cache_hit": False,
        }

    if payload.get("status") == "duplicate":
        _DUPLICATE_PREFLIGHT_CACHE[doc_id] = {"cached_at": now, "payload": payload}
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
    }

# --------------------------------------------------------------------------------
# HTTP INGRESS (For Testing/Triggering)
# --------------------------------------------------------------------------------


@app.route(route="ping", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def ping(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse("Healthy", status_code=200)


@app.route(route="debug_logs", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def debug_logs(req: func.HttpRequest) -> func.HttpResponse:
    import glob
    import time
    
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
    return func.HttpResponse(json.dumps(response_data, indent=2), mimetype="application/json")


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def health_check(req: func.HttpRequest) -> func.HttpResponse:
    """
    Deep health check that verifies connectivity to all critical dependencies:
    Storage, Config, OpenAI, Cosmos DB, Search, and Document Intelligence.
    """
    result = await DependencyChecker().check_all(include_optional=True)
    health_status = {
        **result,
        "timestamp": uuid.uuid4().hex,
        "environment": os.getenv("AZURE_FUNCTIONS_ENVIRONMENT", "local"),
    }

    status_code = 200 if health_status["status"] != "Unhealthy" else 503
    return func.HttpResponse(
        json.dumps(health_status, indent=2),
        status_code=status_code,
        mimetype="application/json"
    )


@app.route(route="cases/{docId}/graph", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def get_case_graph(req: func.HttpRequest) -> func.HttpResponse:
    """
    UI graph endpoint backed by Cosmos DB Gremlin results.
    Never reads graph/{doc_id}/final.json; the doc_id is used only as partitionKey.
    """
    doc_id = req.params.get("pk") or req.route_params.get("docId")
    if not doc_id:
        return func.HttpResponse("Missing route parameter: docId", status_code=400)

    try:
        limit = int(req.params.get("limit", "250"))
    except ValueError:
        return func.HttpResponse("Invalid limit parameter.", status_code=400)

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


@app.route(route="cases/{docId}/timeline", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def get_case_timeline(req: func.HttpRequest) -> func.HttpResponse:
    """
    UI timeline endpoint backed by dated Cosmos DB Gremlin results.
    """
    doc_id = req.params.get("pk") or req.route_params.get("docId")
    if not doc_id:
        return func.HttpResponse("Missing route parameter: docId", status_code=400)

    try:
        limit = int(req.params.get("limit", "1000"))
    except ValueError:
        return func.HttpResponse("Invalid limit parameter.", status_code=400)

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


@app.route(route="cases/{docId}/report", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_case_report(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns the Markdown report generated during activity_final_indexing.
    """
    doc_id = req.route_params.get("docId")
    if not doc_id:
        return func.HttpResponse("Missing route parameter: docId", status_code=400)

    store = get_artifact_store()
    candidates = [
        f"graph/{doc_id}/GRAPH_REPORT.md",
        "GRAPH_REPORT.md",
    ]

    for path in candidates:
        try:
            if store.exists(path):
                markdown = store.read_bytes(path).decode("utf-8")
                return _json_response(
                    {
                        "docId": doc_id,
                        "markdown": markdown,
                        "source": "blob",
                        "updatedAt": datetime.utcnow().isoformat() + "Z",
                    }
                )
        except Exception as exc:
            logging.warning(
                "[HTTP][CASE_REPORT] Failed reading candidate report path=%s doc_id=%s: %s",
                path,
                doc_id,
                exc,
            )

    return _json_response(
        {
            "docId": doc_id,
            "markdown": "",
            "source": "blob",
            "updatedAt": datetime.utcnow().isoformat() + "Z",
            "error": "GRAPH_REPORT.md not found for case.",
        },
        status_code=404,
    )


@app.route(route="cases/{docId}/communities", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_case_communities(req: func.HttpRequest) -> func.HttpResponse:
    """
    Returns the community summaries generated during activity_final_indexing.
    """
    doc_id = req.route_params.get("docId")
    if not doc_id:
        return func.HttpResponse("Missing route parameter: docId", status_code=400)

    store = get_artifact_store()
    candidates = [
        f"graph/{doc_id}/community_summaries.json",
        "community_summaries.json",
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

    return _json_response(
        {
            "docId": doc_id,
            "summaries": {},
            "hierarchy": {},
            "source": "blob",
            "updatedAt": datetime.utcnow().isoformat() + "Z",
            "error": "community_summaries.json not found for case.",
        },
        status_code=404,
    )


@app.route(route="pipelines", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
@app.durable_client_input(client_name="client")
async def start_pipeline_async(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """
    Production-ready asynchronous pipeline entry point.
    Starts the orchestration and returns the instance ID.
    """
    # 1. Validation
    MAX_SIZE_BYTES = 50 * 1024 * 1024
    file_bytes = req.get_body()
    if not file_bytes:
        return func.HttpResponse("Missing PDF body.", status_code=400)
    
    if len(file_bytes) > MAX_SIZE_BYTES:
        return func.HttpResponse("File too large.", status_code=413)

    if not file_bytes.startswith(b"%PDF"):
        return func.HttpResponse("Invalid file type. Only PDF supported.", status_code=415)

    content_disposition = req.headers.get("Content-Disposition", "")
    match = re.search(r'filename="?([^";]+)"?', content_disposition)
    filename = match.group(1) if match else f"{uuid.uuid4()}.pdf"
    import urllib.parse
    filename = urllib.parse.unquote(filename)
    if not filename.lower().endswith(".pdf"):
        filename += ".pdf"

    try:
        duplicate = await _case_duplicate_preflight(filename)
        if duplicate.get("status") == "duplicate":
            logging.info(
                "[HTTP][PIPELINE_START][DUPLICATE] filename=%s doc_id=%s matched_doc_id=%s matched_by=%s",
                filename,
                duplicate.get("doc_id"),
                duplicate.get("matched_doc_id"),
                duplicate.get("matched_by"),
            )
            return _json_response(duplicate)
    except Exception as exc:
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
    store = get_artifact_store()
    store.write_bytes(blob_path, file_bytes)

    # 3. Start Orchestration
    instance_id = await client.start_new("pipeline_orchestrator", client_input=blob_path)
    
    logging.info(f"[HTTP] Started orchestration for {blob_path}. ID: {instance_id}")
    
    return client.create_check_status_response(req, instance_id)


@app.route(route="pipeline-preflight", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
async def pipeline_duplicate_preflight(req: func.HttpRequest) -> func.HttpResponse:
    filename = req.params.get("filename") or req.params.get("doc_id") or ""
    if not filename:
        return func.HttpResponse("Missing query parameter: filename", status_code=400)

    try:
        return _json_response(await _case_duplicate_preflight(filename))
    except Exception as exc:
        logging.error(
            "[HTTP][PIPELINE_PREFLIGHT][ERROR] filename=%s error=%s",
            filename,
            exc,
            exc_info=True,
        )
        return _json_response(
            {"status": "error", "error": str(exc), "doc_id": _doc_id_from_filename(filename)},
            status_code=500,
        )


@app.route(route="pipelines/{instanceId}", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@app.durable_client_input(client_name="client")
async def get_pipeline_status(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """
    Retrieves the status of a specific pipeline run.
    """
    instance_id = req.route_params.get("instanceId")
    status = await client.get_status(instance_id)
    
    if not status:
        return func.HttpResponse("Pipeline instance not found.", status_code=404)

    response_data = {
        "instanceId": instance_id,
        "runtimeStatus": str(status.runtime_status),
        "createdTime": status.created_time.isoformat() if status.created_time else None,
        "lastUpdatedTime": status.last_updated_time.isoformat() if status.last_updated_time else None,
        "output": status.output,
        "customStatus": status.custom_status
    }

    return func.HttpResponse(
        json.dumps(response_data),
        status_code=200,
        mimetype="application/json"
    )


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
    blob_path: str = context.get_input()
    doc_id = os.path.splitext(os.path.basename(blob_path))[0]
    total_chunks = 0
    completed_chunks = 0
    current_batch = 0

    if not context.is_replaying:
        logging.info(f"[ORCHESTRATOR][START] Processing blob: {blob_path}")

    try:
        context.set_custom_status(
            {
                "phase": "preflight",
                "message": "Validating runtime dependencies",
                "blob_path": blob_path,
                "doc_id": doc_id,
                "total_chunks": 0,
                "completed_chunks": 0,
                "current_batch": 0,
                "last_error": None,
            }
        )
        yield context.call_activity("activity_preflight_check", None)

        context.set_custom_status(
            {
                "phase": "chunking",
                "message": "Extracting document text and materializing chunks",
                "blob_path": blob_path,
                "doc_id": doc_id,
                "total_chunks": 0,
                "completed_chunks": 0,
                "current_batch": 0,
                "last_error": None,
            }
        )
        chunk_meta: Dict[str, Any] = yield context.call_activity("activity_chunking", blob_path)

        if not chunk_meta or chunk_meta.get("total_chunks", 0) == 0:
            logging.warning(f"[ORCHESTRATOR][END] No chunks materialized for {blob_path}. Ending orchestration.")
            context.set_custom_status(
                {
                    "phase": "completed",
                    "message": "No chunks were produced",
                    "blob_path": blob_path,
                    "doc_id": doc_id,
                    "total_chunks": 0,
                    "completed_chunks": 0,
                    "current_batch": 0,
                    "last_error": None,
                }
            )
            return {
                "status": "completed_no_content",
                "blob_path": blob_path,
                "final_graph_path": None,
            }

        batch_size = max(1, int(settings.orchestrator_extract_batch_size))
        extraction_results: List[Dict[str, Any]] = []
        chunk_ids = chunk_meta.get("chunk_ids", [])
        total_chunks = len(chunk_ids)
        batches = _batched(chunk_ids, batch_size)

        for batch_index, batch in enumerate(batches, start=1):
            context.set_custom_status(
                {
                    "phase": "extracting",
                    "message": f"Extracting batch {batch_index} of {len(batches)}",
                    "blob_path": blob_path,
                    "doc_id": doc_id,
                    "total_chunks": total_chunks,
                    "completed_chunks": completed_chunks,
                    "current_batch": batch_index,
                    "last_error": None,
                }
            )
            current_batch = batch_index
            extraction_tasks = [
                context.call_activity("activity_extract_chunk", {"doc_id": doc_id, "chunk_id": cid})
                for cid in batch
            ]
            batch_result_payloads: List[Dict[str, Any]] = yield context.task_all(extraction_tasks)
            extraction_results.extend(batch_result_payloads)
            completed_chunks = len(extraction_results)
            context.set_custom_status(
                {
                    "phase": "extracting",
                    "message": f"Completed batch {batch_index} of {len(batches)}",
                    "blob_path": blob_path,
                    "doc_id": doc_id,
                    "total_chunks": total_chunks,
                    "completed_chunks": completed_chunks,
                    "current_batch": current_batch,
                    "last_error": None,
                }
            )

        context.set_custom_status(
            {
                "phase": "merging",
                "message": "Merging partial graph extractions",
                "blob_path": blob_path,
                "doc_id": doc_id,
                "total_chunks": total_chunks,
                "completed_chunks": completed_chunks,
                "current_batch": current_batch,
                "last_error": None,
            }
        )
        merged_graph_path: str = yield context.call_activity(
            "activity_merge_partials", extraction_results
        )

        context.set_custom_status(
            {
                "phase": "indexing",
                "message": "Saving graph and updating downstream indexes",
                "blob_path": blob_path,
                "doc_id": doc_id,
                "total_chunks": total_chunks,
                "completed_chunks": completed_chunks,
                "current_batch": current_batch,
                "last_error": None,
            }
        )
        final_path: str = yield context.call_activity(
            "activity_final_indexing", merged_graph_path
        )

        if not context.is_replaying:
            logging.info(
                f"[ORCHESTRATOR][COMPLETE] Pipeline finished. Final graph: {final_path}"
            )

        context.set_custom_status(
            {
                "phase": "completed",
                "message": "Pipeline completed",
                "blob_path": blob_path,
                "doc_id": doc_id,
                "total_chunks": total_chunks,
                "completed_chunks": completed_chunks,
                "current_batch": current_batch,
                "last_error": None,
            }
        )
        return {
            "status": "completed",
            "blob_path": blob_path,
            "final_graph_path": final_path,
        }
    except Exception as exc:
        import traceback
        logging.error(f"[CRITICAL][ORCHESTRATOR] Pipeline failed: {str(exc)}")
        logging.error(traceback.format_exc())
        context.set_custom_status(
            {
                "phase": "failed",
                "message": "Pipeline failed",
                "blob_path": blob_path,
                "doc_id": doc_id,
                "total_chunks": total_chunks,
                "completed_chunks": completed_chunks,
                "current_batch": current_batch,
                "last_error": _compact_error(exc),
            }
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
    logging.info(f"[ACTIVITY][CHUNKING][START] Processing: {blobPath}")
    try:
        chunk_meta = await pipeline_activities.activity_chunking(blobPath)
        logging.info(
            f"[ACTIVITY][CHUNKING][END] Chunks generated: {chunk_meta.get('total_chunks', 0)}"
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


@app.activity_trigger(input_name="chunk")
async def activity_extract_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts entities from a single chunk in-memory. Input: chunk dict/reference, Output: extraction result dict."""
    chunk_idx = chunk.get("chunk_index", "unknown") if "text" in chunk else chunk.get("chunk_id", "unknown")
    logging.info(f"[ACTIVITY][EXTRACT_CHUNK][START] Chunk: {chunk_idx}")
    try:
        result_payload = await pipeline_activities.activity_extract_chunk(chunk)
        logging.info(
            f"[ACTIVITY][EXTRACT_CHUNK][END] Extraction complete for chunk: {chunk_idx}"
        )
        return result_payload
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_extract_chunk: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


@app.activity_trigger(input_name="extractions")
async def activity_merge_partials(
    extractions: List[Dict[str, Any]],
) -> str:
    """Merges partial graphs. Input: List of extraction result dicts, Output: merged graph path (Claim-Check)."""
    if not extractions:
        logging.warning("[ACTIVITY][MERGE_PARTIALS] Received empty or None extractions. Skipping merge.")
        return None

    logging.info(
        f"[ACTIVITY][MERGE_PARTIALS][START] Merging {len(extractions)} results in-memory."
    )
    try:
        merged_graph_path = await pipeline_activities.activity_merge_partials(
            extractions
        )
        logging.info(
            f"[ACTIVITY][MERGE_PARTIALS][END] Merged graph materialized to: {merged_graph_path}"
        )
        return merged_graph_path
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_merge_partials: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)


@app.activity_trigger(input_name="mergedGraphPath")
async def activity_final_indexing(mergedGraphPath: str) -> str:
    """Performs final indexing and cleanup. Input: merged graph path, Output: final graph path (cosmos_uri)."""
    logging.info(
        f"[ACTIVITY][FINAL_INDEXING][START] Indexing graph from: {mergedGraphPath}"
    )
    try:
        final_path = await pipeline_activities.activity_final_indexing(mergedGraphPath)
        logging.info(f"[ACTIVITY][FINAL_INDEXING][END] Final graph indexing complete: {final_path}")
        return final_path
    except Exception as e:
        import traceback
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


# --------------------------------------------------------------------------------
# INVESTIGATION QUERY ENGINE (Stateless Activities & Endpoint)
# --------------------------------------------------------------------------------


@app.route(route="query", methods=["POST"], auth_level=func.AuthLevel.ANONYMOUS)
@app.durable_client_input(client_name="client")
async def start_query_async(req: func.HttpRequest, client: df.DurableOrchestrationClient) -> func.HttpResponse:
    """
    Stateless investigative query entry point.
    Starts the query orchestration and returns status check details.
    """
    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse("Invalid JSON body.", status_code=400)

    question = req_body.get("question")
    doc_id = req_body.get("doc_id")

    if not question:
        return func.HttpResponse("Missing required field: question", status_code=400)
    if not doc_id:
        return func.HttpResponse("Missing required field: doc_id", status_code=400)

    instance_id = await client.start_new(
        "query_orchestrator", 
        client_input={"question": question, "doc_id": doc_id}
    )
    
    logging.info(f"[HTTP] Started query orchestration for doc_id {doc_id}. ID: {instance_id}")
    return client.create_check_status_response(req, instance_id)


@app.orchestration_trigger(context_name="context")
def query_orchestrator(context: df.DurableOrchestrationContext):
    """
    Durable Orchestrator to route and execute a query activity.
    """
    query_input = context.get_input()
    if not context.is_replaying:
        logging.info(f"[ORCHESTRATOR][QUERY] Starting query orchestrator for doc_id: {query_input.get('doc_id')}")

    try:
        result = yield context.call_activity("activity_query_investigation", query_input)
        return result
    except Exception as exc:
        import traceback
        logging.error(f"[CRITICAL][ORCHESTRATOR] Query orchestrator failed: {str(exc)}")
        logging.error(traceback.format_exc())
        raise exc


@app.activity_trigger(input_name="queryInput")
async def activity_query_investigation(queryInput: dict) -> dict:
    """
    Stateless activity to route and answer an investigative query.
    Input format: {"question": str, "doc_id": str}
    """
    question = queryInput["question"]
    doc_id = queryInput["doc_id"]
    
    logging.info(f"[ACTIVITY][QUERY][START] doc_id: {doc_id} | question: {question[:50]}...")
    try:
        async with QueryAgent(doc_id=doc_id, stateless=True) as agent:
            answer = await agent.answer_question_stateless(question)
            logging.info(f"[ACTIVITY][QUERY][END] doc_id: {doc_id} query finished successfully.")
            return {"answer": answer}
    except Exception as e:
        import traceback
        error_msg = _compact_error(
            f"Error in activity_query_investigation: {type(e).__name__} - {e}"
        )
        logging.error(error_msg)
        logging.error(traceback.format_exc())
        raise RuntimeError(error_msg)
