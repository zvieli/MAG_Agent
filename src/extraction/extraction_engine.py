from src.utils.bidi_utils import fix_text
import hashlib
import json
import asyncio
import threading
import os
import random
import re
import socket
import subprocess
import logging
from contextlib import contextmanager, nullcontext
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse
from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncAzureOpenAI,
    AsyncOpenAI,
    AuthenticationError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import ValidationError
from src.utils.config import settings
from src.models.models import RawGraphExtraction, SCHEMA_CONFIG
from src.extraction.provider_adapter import (
    ExtractionOutcomeCode,
    ProviderClassification,
    ProviderOutcomeError,
    classify_provider_exception,
    classify_returned_response,
)
from src.extraction.wire_schema import (
    create_wire_extraction_model,
    lint_structured_output_schema,
    schema_fingerprint,
    to_graph_dto,
)
from src.storage.factory import get_artifact_store
from src.storage.interfaces import IArtifactStore
from src.utils.observability import log_metric

logger = logging.getLogger(__name__)

DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
OPENAI_RATE_LIMIT_MAX_ATTEMPTS = max(
    1, int(os.getenv("OPENAI_RATE_LIMIT_MAX_ATTEMPTS", "6"))
)
OPENAI_RATE_LIMIT_BACKOFF_BASE = max(
    0.1, float(os.getenv("OPENAI_RATE_LIMIT_BACKOFF_BASE", "1.5"))
)
OPENAI_RATE_LIMIT_BACKOFF_MAX = max(
    OPENAI_RATE_LIMIT_BACKOFF_BASE,
    float(os.getenv("OPENAI_RATE_LIMIT_BACKOFF_MAX", "30")),
)


def _get_artifact_store() -> IArtifactStore:
    return get_artifact_store()

try:
    import anyio._core._sockets as anyio_sockets

    ANYIO_PATCH_AVAILABLE = True
except ImportError:
    ANYIO_PATCH_AVAILABLE = False
    logger.warning(
        "[WARNING] anyio._core._sockets not found. DNS override for anyio will be disabled."
    )

_ONTOLOGY_CACHE: Dict[str, Dict[str, Any]] = {}


def _normalize_artifact_component(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value).strip()) or "default"


def _schema_version_for(schema_config: Any) -> str:
    version = getattr(schema_config, "schema_version", None) or getattr(
        schema_config, "version", None
    )
    if version:
        return str(version)
    try:
        payload = json.dumps(
            schema_config.model_dump() if hasattr(schema_config, "model_dump") else {},
            sort_keys=True,
            ensure_ascii=False,
        )
    except Exception:
        payload = str(schema_config)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _extraction_cache_key(
    model_name: str, schema_version: str, chunk_hash: str, contract_fingerprint: str = "legacy"
) -> str:
    return (
        f"extraction_cache/{_normalize_artifact_component(model_name)}/"
        f"{_normalize_artifact_component(schema_version)}/"
        f"{_normalize_artifact_component(contract_fingerprint)}/{chunk_hash}.json"
    )


def _extraction_text_key(
    model_name: str, schema_version: str, chunk_hash: str, contract_fingerprint: str = "legacy"
) -> str:
    return (
        f"extraction_cache/{_normalize_artifact_component(model_name)}/"
        f"{_normalize_artifact_component(schema_version)}/"
        f"{_normalize_artifact_component(contract_fingerprint)}/{chunk_hash}.txt"
    )


def _get_ontology(schema_config: Any = None) -> Dict[str, Any]:
    """
    Retrieve the lightweight ontology from the investigation schema.

    Returns:
        Dict[str, Any]: The loaded ontology dictionary.
    """
    schema = schema_config or SCHEMA_CONFIG
    schema_version = _schema_version_for(schema)
    if schema_version in _ONTOLOGY_CACHE:
        return _ONTOLOGY_CACHE[schema_version]

    ontology = {
        "legal_role_map": getattr(schema, "legal_role_map", {}) or {},
        "entity_types": getattr(schema, "entity_types", {}) or {},
        "relation_types": getattr(schema, "relation_types", {}) or {},
        "confidence_levels": getattr(schema, "confidence_levels", {}) or {},
        "extraction_guidelines": getattr(schema, "extraction_guidelines", []) or [],
        "schema_version": schema_version,
    }
    _ONTOLOGY_CACHE[schema_version] = ontology
    return ontology


_AZURE_CLIENT: Optional[Union[AsyncAzureOpenAI, AsyncOpenAI]] = None
_AZURE_CLIENT_LOCK = threading.Lock()

def _build_azure_client(
    *, default_api_version: str = "2024-02-01"
) -> Union[AsyncAzureOpenAI, AsyncOpenAI]:
    """
    Construct an OpenAI or Azure OpenAI client using environment configuration.
    Enforces explicit key-based authentication and fails fast if required settings are missing.
    Caches the client instance to prevent connection/session leaks.

    Args:
        default_api_version (str): Fallback API version if not specified in settings.
            Defaults to "2024-08-01-preview".

    Returns:
        Union[AsyncAzureOpenAI, AsyncOpenAI]: The configured async client.

    Raises:
        RuntimeError: If required endpoint or API key is missing.
    """
    global _AZURE_CLIENT
    if _AZURE_CLIENT is None:
        with _AZURE_CLIENT_LOCK:
            if _AZURE_CLIENT is None:
                azure_endpoint = settings.azure_openai_endpoint
                azure_api_key = settings.azure_openai_api_key
                openai_api_key = settings.openai_api_key
                openai_base_url = settings.openai_base_url
                
                api_version = (
                    settings.azure_openai_api_version
                    or settings.openai_api_version
                    or default_api_version
                )

                # Determine if we are in Azure mode based on the presence of an Azure endpoint
                is_azure = bool(azure_endpoint) or (openai_base_url and "openai.azure.com" in openai_base_url)

                if is_azure:
                    endpoint = (azure_endpoint or openai_base_url).rstrip("/")
                    if not endpoint:
                        raise RuntimeError("AZURE_OPENAI_ENDPOINT is required for Azure OpenAI mode.")
                        
                    if azure_api_key:
                        _AZURE_CLIENT = AsyncAzureOpenAI(
                            api_key=azure_api_key,
                            azure_endpoint=endpoint,
                            api_version=api_version,
                            timeout=120.0,
                        )
                    else:
                        try:
                            from azure.identity.aio import DefaultAzureCredential
                            from azure.identity import get_bearer_token_provider
                            token_provider = get_bearer_token_provider(
                                DefaultAzureCredential(),
                                "https://cognitiveservices.azure.com/.default"
                            )
                            _AZURE_CLIENT = AsyncAzureOpenAI(
                                azure_ad_token_provider=token_provider,
                                azure_endpoint=endpoint,
                                api_version=api_version,
                                timeout=120.0,
                            )
                            logging.info("[EXTRACTION] Authenticated Azure OpenAI using Managed Identity token provider.")
                        except (ImportError, Exception) as e:
                            raise RuntimeError(
                                f"AZURE_OPENAI_API_KEY is missing and DefaultAzureCredential authentication failed: {e}"
                            )
                else:
                    # Standard OpenAI mode
                    if not openai_api_key:
                        raise RuntimeError("OPENAI_API_KEY is required for standard OpenAI mode.")

                    _AZURE_CLIENT = AsyncOpenAI(
                        api_key=openai_api_key,
                        base_url=openai_base_url.rstrip("/") if openai_base_url else None,
                        timeout=120.0,
                    )
    return _AZURE_CLIENT


async def close_openai_client() -> None:
    """Closes the cached async OpenAI client session safely."""
    global _AZURE_CLIENT
    if _AZURE_CLIENT is not None:
        await _AZURE_CLIENT.close()
        _AZURE_CLIENT = None


def _estimate_token_count(text: str) -> int:
    """
    Estimate the number of tokens in a text string using a simple word/punctuation regex.

    Args:
        text (str): The input text to estimate.

    Returns:
        int: The estimated token count.
    """
    if not text:
        return 0
    return len(re.findall(r"\w+|[^\w\s]", text))


def _response_token_usage(
    response: Any, prompt_text: str, completion_text: str
) -> Dict[str, int]:
    """
    Calculate token usage from an LLM response or fall back to estimation.

    Args:
        response (Any): The OpenAI API response object.
        prompt_text (str): The original prompt text.
        completion_text (str): The completion text from the response.

    Returns:
        Dict[str, int]: A dictionary with 'input_tokens', 'output_tokens', 'cached_tokens', and 'total_tokens'.
    """
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "prompt_tokens", None) or _estimate_token_count(
        prompt_text
    )
    output_tokens = getattr(usage, "completion_tokens", None) or _estimate_token_count(
        completion_text
    )
    prompt_tokens_details = getattr(usage, "prompt_tokens_details", None)
    cached_tokens = 0
    if prompt_tokens_details:
        cached_tokens = getattr(prompt_tokens_details, "cached_tokens", 0) or 0

    return {
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cached_tokens": int(cached_tokens),
        "total_tokens": int(input_tokens) + int(output_tokens),
    }


def is_connection_like_error(exc: Exception) -> bool:
    """
    Check if an exception is related to network or connection issues.

    Args:
        exc (Exception): The exception to check.

    Returns:
        bool: True if it's a connection-like error, False otherwise.
    """
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "name or service not known",
            "could not resolve host",
            "temporary failure in name resolution",
            "connection error",
            "connecterror",
            "timeout",
        )
    )


def _retry_after_seconds(exc: Exception) -> Optional[float]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after is None:
        return None
    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        return None


def _rate_limit_delay(attempt: int, exc: Exception) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return retry_after + random.uniform(0, 0.5)
    exponential = OPENAI_RATE_LIMIT_BACKOFF_BASE * (2 ** max(0, attempt - 1))
    return min(OPENAI_RATE_LIMIT_BACKOFF_MAX, exponential) + random.uniform(0, 1.0)


def resolve_host_ip(hostname: str) -> Optional[str]:
    """
    Attempt to resolve a hostname to an IP address using multiple methods.

    Args:
        hostname (str): The hostname to resolve.

    Returns:
        Optional[str]: The resolved IP address, or None if resolution fails.
    """
    try:
        result = subprocess.run(
            ["getent", "hosts", hostname],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split()
                if parts:
                    return parts[0]
    except OSError:
        pass

    try:
        infos = socket.getaddrinfo(
            hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        if infos:
            return infos[0][4][0]
    except OSError:
        return None

    return None


@contextmanager
def temporary_dns_override(hostname: str, resolved_ip: Optional[str]):
    """
    Temporarily override DNS resolution for a specific hostname within a context.

    Args:
        hostname (str): The hostname to override.
        resolved_ip (Optional[str]): The IP address to map the hostname to.
    """
    if not DEBUG_MODE or not hostname or not resolved_ip:
        yield
        return

    original_getaddrinfo = socket.getaddrinfo
    original_anyio_getaddrinfo = None
    if ANYIO_PATCH_AVAILABLE:
        original_anyio_getaddrinfo = anyio_sockets.getaddrinfo

    def patched_getaddrinfo(host, port, *args, **kwargs):
        if host == hostname:
            return original_getaddrinfo(resolved_ip, port, *args, **kwargs)
        return original_getaddrinfo(host, port, *args, **kwargs)

    async def patched_anyio_getaddrinfo(
        host, port, *, family=0, type=0, proto=0, flags=0
    ):
        if host in {
            hostname,
            hostname.encode("ascii", "ignore") if isinstance(host, bytes) else hostname,
        }:
            return await original_anyio_getaddrinfo(
                resolved_ip,
                port,
                family=family,
                type=type,
                proto=proto,
                flags=flags,
            )
        return await original_anyio_getaddrinfo(
            host,
            port,
            family=family,
            type=type,
            proto=proto,
            flags=flags,
        )

    socket.getaddrinfo = patched_getaddrinfo
    if ANYIO_PATCH_AVAILABLE:
        anyio_sockets.getaddrinfo = patched_anyio_getaddrinfo
    try:
        yield
    finally:
        socket.getaddrinfo = original_getaddrinfo
        if ANYIO_PATCH_AVAILABLE:
            anyio_sockets.getaddrinfo = original_anyio_getaddrinfo


def _extract_deterministic_entities(
    text: str, schema_config: Any
) -> List[Dict[str, Any]]:
    """
    Extract structured data using generic Regex patterns from configuration.
    Used to pre-seed the LLM or reduce its extraction load.

    Args:
        text (str): The input text to search.
        schema_config (Any): Configuration object containing 'deterministic_patterns'.

    Returns:
        List[Dict[str, Any]]: A list of extracted entity dictionaries.
    """
    patterns = getattr(schema_config, "deterministic_patterns", [])
    if not patterns:
        return []

    findings = []
    seen = set()
    for pattern_str in patterns:
        try:
            matches = re.finditer(pattern_str, text)
            for m in matches:
                val = m.group(0).strip()
                if val and val not in seen:
                    findings.append({"label": val, "type": "DATE/AMOUNT/REF"})
                    seen.add(val)
        except Exception:
            continue
    return findings


async def _build_prompt(
    chunk_text: str,
    schema_config: Any,
    chunk_label: Optional[str] = None,
    *,
    deterministic_entities: Optional[List[dict]] = None,
) -> str:
    """
    Construct the full extraction prompt for the LLM.

    Incorporates ontology definitions, entity/relation tables, and deterministic findings.

    Args:
        chunk_text (str): The text content of the chunk to process.
        schema_config (Any): Configuration object for extraction rules.
        chunk_label (Optional[str]): A label for the chunk (e.g., 'Chunk 1').
        deterministic_entities (Optional[List[dict]]): Entities already found via regex.

    Returns:
        str: The complete prompt ready to be sent to the LLM.
    """
    # Use lightweight ontology for high token efficiency
    ontology = _get_ontology(schema_config)

    # Prepare dynamic components from ONTOLOGY and context
    chunk_context = f"CHUNK WINDOW: {chunk_label}" if chunk_label else ""

    role_map_str = json.dumps(
        ontology.get("legal_role_map", {}), indent=2, ensure_ascii=False
    )

    # 1. ENTITY TYPES TABLE
    entity_types_data = ontology.get("entity_types", {})
    allowed_entity_types = (
        ", ".join(entity_types_data.keys())
        if isinstance(entity_types_data, dict)
        else ""
    )
    entity_table = (
        "| TYPE | SEMANTIC DEFINITION & CONTEXTUAL CONSTRAINTS |\n| :--- | :--- |\n"
    )
    if isinstance(entity_types_data, dict):
        for t, desc in entity_types_data.items():
            entity_table += f"| {t} | {desc} |\n"

    # 2. RELATION TYPES TABLE
    relation_types_data = ontology.get("relation_types", {})
    allowed_relation_types = ", ".join(relation_types_data.keys())
    relation_table = (
        "| RELATION | SEMANTIC DEFINITION & INVESTIGATIVE CONTEXT |\n| :--- | :--- |\n"
    )
    if isinstance(relation_types_data, dict):
        for r, desc in relation_types_data.items():
            relation_table += f"| {r} | {desc} |\n"

    # 3. CONFIDENCE LEVELS TABLE
    confidence_levels_data = ontology.get("confidence_levels", {})
    allowed_confidence_levels = ", ".join(confidence_levels_data.keys())
    confidence_table = (
        "| LEVEL | LEGAL DEFINITION & TOPOLOGY RULES |\n| :--- | :--- |\n"
    )
    if isinstance(confidence_levels_data, dict):
        for c, desc in confidence_levels_data.items():
            confidence_table += f"| {c} | {desc} |\n"

    # Retrieve base prompt template from settings
    raw_template = await settings.get_prompt("extraction_prompt_full")
    prompt_source = "EXTRACTION_PROMPT_FULL"

    if not raw_template:
        logger.debug(
            fix_text(
                f"[DEBUG] Falling back to EXTRACTION_BASE_INSTRUCTION (Source: {prompt_source} was empty)"
            )
        )
        raw_template = await settings.get_prompt("extraction_base_instruction")
        prompt_source = "EXTRACTION_BASE_INSTRUCTION"
    else:
        logger.debug(fix_text(f"[DEBUG] Using base prompt template from: {prompt_source}"))

    # Populate placeholders in base template
    base_template = (
        raw_template.replace("{legal_role_map}", role_map_str)
        .replace("{ALLOWED_ENTITY_TYPES}", allowed_entity_types)
        .replace("{ALLOWED_RELATION_TYPES}", allowed_relation_types)
        .replace("{ALLOWED_CONFIDENCE_LEVELS}", allowed_confidence_levels)
    )

    # Schema instructions from settings
    schema_template = await settings.get_prompt("extraction_schema_instructions_full")
    schema_source = "EXTRACTION_SCHEMA_INSTRUCTIONS_FULL"

    if schema_template:
        logger.debug(fix_text(f"[DEBUG] Using schema instructions from: {schema_source}"))
        try:
            schema_instructions = schema_template.replace(
                "{entity_types_str}", allowed_entity_types
            ).replace("{ALLOWED_ENTITY_TYPES}", allowed_entity_types)
        except Exception as e:
            logger.error(fix_text(f"[DEBUG] Error populating {schema_source}: {e}"))
            schema_instructions = schema_template
    elif not base_template:
        logger.debug(
            fix_text(
                f"[DEBUG] Falling back to default schema instructions (Source: {schema_source} was empty)"
            )
        )
        schema_instructions = f"Extract entities of types: {allowed_entity_types}"
    else:
        schema_instructions = ""

    # Content-Aware Suppression
    if schema_instructions and (
        "EXPECTED JSON STRUCTURE" in base_template or '"nodes":' in base_template
    ):
        logger.debug(
            fix_text(
                "[DEBUG] Suppressing redundant schema instructions (Base template already defines JSON structure)"
            )
        )
        schema_instructions = ""

    guidelines_str = "\n".join(
        [f"- {g}" for g in ontology.get("extraction_guidelines", [])]
    )
    guidelines_instructions = (
        f"Global Behavioral Constraints:\n{guidelines_str}" if guidelines_str else ""
    )

    # Step 6: Inject deterministic findings
    deterministic_str = ""
    if deterministic_entities:
        findings_text = ", ".join([e["label"] for e in deterministic_entities])
        deterministic_str = f"PRE-EXTRACTED ENTITIES (Use these in your JSON output if relevant):\n{findings_text}\n"

    # JSON Format Instruction
    json_format = await settings.get_prompt("extraction_json_format")

    final_instruction = (
        f"{base_template}\n\n"
        f"### ENTITY TYPES DEFINITIONS\n{entity_table}\n\n"
        f"### RELATION TYPES DEFINITIONS\n{relation_table}\n\n"
        f"### CONFIDENCE LEVELS DEFINITIONS\n{confidence_table}\n\n"
        f"{schema_instructions}\n\n"
        f"{json_format}\n\n"
        f"{guidelines_instructions}\n\n"
        f"{deterministic_str}\n\n"
        f"CONTEXT: {chunk_context}\n\n"
        f"TEXT:\n{chunk_text}"
    )

    return f"json output required.\n\n{final_instruction}"


async def _json_response_messages(prompt: str) -> List[Dict[str, str]]:
    """
    Construct the message list for the chat completion API.

    Args:
        prompt (str): The user prompt.

    Returns:
        List[Dict[str, str]]: List of system and user messages.
    """
    system_msg = await settings.get_prompt("extraction_system_message")
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": prompt},
    ]


async def extract_from_chunk(
    client: Optional[Union[AsyncAzureOpenAI, AsyncOpenAI]],
    chunk_text: str,
    schema_config: Any,
    chunk_label: Optional[str] = None,
    artifact_store: Optional[IArtifactStore] = None,
) -> Tuple[RawGraphExtraction, Dict[str, int]]:
    """
    Perform graph extraction from a single text chunk using an LLM.

    Handles multi-tier caching, deterministic pre-extraction, DNS overrides,
    and multiple extraction strategies with fallback temperatures.

    Args:
        client (Optional[Union[AsyncAzureOpenAI, AsyncOpenAI]]): The LLM client.
        chunk_text (str): The text to extract from.
        schema_config (Any): Configuration for the extraction.
        chunk_label (Optional[str]): Human-readable label for the chunk.

    Returns:
        Tuple[RawGraphExtraction, Dict[str, int]]: The extracted graph and token usage stats.

    Raises:
        ValidationError: If the LLM output fails Pydantic validation.
    """
    store = artifact_store or _get_artifact_store()

    chunk_text_stripped = chunk_text.strip()
    chunk_hash = hashlib.sha256(chunk_text_stripped.encode("utf-8")).hexdigest()
    model = settings.graphify_extraction_model
    schema_version = _schema_version_for(schema_config)
    wire_model = create_wire_extraction_model(schema_config)
    wire_schema = wire_model.model_json_schema()
    lint_structured_output_schema(wire_schema)
    contract_fingerprint = schema_fingerprint(wire_model)
    cache_key = _extraction_cache_key(
        model, schema_version, chunk_hash, contract_fingerprint
    )
    text_cache_key = _extraction_text_key(
        model, schema_version, chunk_hash, contract_fingerprint
    )

    exists = await asyncio.to_thread(store.exists, cache_key)
    if exists:
        try:
            logger.debug(
                fix_text(
                    f"[DEBUG] Artifact cache hit for {chunk_label} ({chunk_hash[:8]})"
                )
            )
            data_bytes = await asyncio.to_thread(store.read_bytes, cache_key)
            cache_data = json.loads(data_bytes.decode("utf-8"))
            extraction_data = cache_data.get("extraction")
            if extraction_data:
                extraction = RawGraphExtraction.model_validate(extraction_data)
                token_usage = cache_data.get(
                    "token_usage",
                    {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                )
                return extraction, token_usage
        except Exception as e:
            logger.error(
                fix_text(f"[DEBUG] Error loading artifact cache for {chunk_label}: {e}")
            )

    client = client or _build_azure_client()

    # Step 6: Deterministic Extraction
    deterministic_entities = _extract_deterministic_entities(chunk_text, schema_config)

    endpoint = settings.azure_openai_endpoint or settings.openai_base_url or "<unset>"
    endpoint_host = ""
    if endpoint != "<unset>":
        parsed_endpoint = urlparse(endpoint)
        endpoint_host = parsed_endpoint.hostname or endpoint.split("/")[0]

    resolved_ip = (
        resolve_host_ip(endpoint_host) if (DEBUG_MODE and endpoint_host) else None
    )

    async def _call_llm(p, temp=0, m_tokens=None):
        if m_tokens is None:
            m_tokens = settings.extraction_max_tokens
        nonlocal resolved_ip
        for attempt in range(OPENAI_RATE_LIMIT_MAX_ATTEMPTS):
            try:
                dns_context = (
                    temporary_dns_override(endpoint_host, resolved_ip)
                    if (DEBUG_MODE and endpoint_host and resolved_ip)
                    else nullcontext()
                )
                with dns_context:
                    logger.info(
                        fix_text(
                            f"--- DEBUG: Sending request to OpenAI | Model/Deployment: {model} | Endpoint: {endpoint} ---"
                        )
                    )
                    response = await asyncio.wait_for(
                        client.chat.completions.parse(
                            model=model,
                            messages=await _json_response_messages(p),
                            temperature=temp,
                            max_tokens=m_tokens,
                            response_format=wire_model,
                        ),
                        timeout=settings.llm_request_timeout_seconds,
                    )
                    logger.debug(
                        fix_text(
                            f"--- DEBUG: Received response from OpenAI (Attempt {attempt + 1}) ---"
                        )
                    )
                return response
            except asyncio.TimeoutError as exc:
                if attempt < OPENAI_RATE_LIMIT_MAX_ATTEMPTS - 1:
                    await asyncio.sleep(
                        min(OPENAI_RATE_LIMIT_BACKOFF_MAX, 1 + attempt)
                    )
                    continue
                raise TimeoutError(
                    f"OpenAI chat completion timed out after {settings.llm_request_timeout_seconds:.1f}s "
                    f"for chunk {chunk_label}"
                ) from exc
            except RateLimitError as exc:
                if attempt < OPENAI_RATE_LIMIT_MAX_ATTEMPTS - 1:
                    delay = _rate_limit_delay(attempt + 1, exc)
                    logger.warning(
                        fix_text(
                            f"[RATE_LIMIT][OPENAI] 429 for chunk {chunk_label}; retrying in {delay:.2f}s "
                            f"(attempt {attempt + 1}/{OPENAI_RATE_LIMIT_MAX_ATTEMPTS})"
                        )
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
            except (APIConnectionError, APITimeoutError, OSError) as exc:
                if attempt < OPENAI_RATE_LIMIT_MAX_ATTEMPTS - 1:
                    if (
                        DEBUG_MODE
                        and is_connection_like_error(exc)
                        and endpoint_host
                        and resolved_ip is None
                    ):
                        resolved_ip = resolve_host_ip(endpoint_host)
                    await asyncio.sleep(min(OPENAI_RATE_LIMIT_BACKOFF_MAX, 1 + attempt))
                    continue
                raise
            except Exception as exc:
                classification = classify_provider_exception(exc)
                if classification is not None:
                    logger.error(
                        "[EXTRACTION][PROVIDER] code=%s status=%s request_id=%s "
                        "signals=%s body_shape=%s",
                        classification.code.value,
                        classification.status_code,
                        classification.request_id,
                        classification.discovered_codes,
                        classification.body_key_shape,
                    )
                    raise ProviderOutcomeError(classification) from exc
                raise
        return None

    # Extraction Strategy: Try Full, then retry with fallback temperature
    extraction = None
    last_response = None
    used_prompt = None

    strategies = [
        {"name": "STRICT", "temp": 0},
        {"name": "STRICT_RETRY", "temp": 0},
    ]
    last_validation_code = ExtractionOutcomeCode.OUTPUT_SCHEMA_INVALID

    for strategy in strategies:
        logger.debug(
            fix_text(
                f"[DEBUG] Attempting {strategy['name']} extraction for {chunk_label}..."
            )
        )
        used_prompt = await _build_prompt(
            chunk_text,
            schema_config,
            chunk_label,
            deterministic_entities=deterministic_entities,
        )

        logger.debug("[EXTRACTION] Strict request prepared for chunk %s", chunk_label)

        try:
            last_response = await _call_llm(used_prompt, temp=strategy["temp"])
            if not last_response:
                continue

            classification = classify_returned_response(last_response)
            if classification is not None:
                raise ProviderOutcomeError(classification)

            choice = last_response.choices[0]
            message = choice.message

            # Check for truncation
            if choice.finish_reason == "length":
                raise ProviderOutcomeError(
                    ProviderClassification(
                        code=ExtractionOutcomeCode.OUTPUT_INCOMPLETE,
                        request_id=getattr(last_response, "_request_id", None),
                        provider_signal="finish_reason.length",
                        sanitized_reason="Structured output was truncated by the token limit.",
                    )
                )

            parsed = getattr(message, "parsed", None)
            if parsed is None:
                raise ValidationError.from_exception_data(
                    "StrictGraphExtraction",
                    [{"type": "missing", "loc": ("message", "parsed"), "input": None}],
                )
            validated_wire = wire_model.model_validate(parsed)
            extraction = to_graph_dto(validated_wire)

            # If we reach here, validation succeeded
            break
        except ProviderOutcomeError as exc:
            if exc.classification.code in {
                ExtractionOutcomeCode.CONTENT_BLOCKED,
                ExtractionOutcomeCode.OUTPUT_INCOMPLETE,
                ExtractionOutcomeCode.PROVIDER_SCHEMA_UNSUPPORTED,
                ExtractionOutcomeCode.PROVIDER_ERROR_UNCLASSIFIED,
            }:
                raise
            last_validation_code = exc.classification.code
        except (AuthenticationError, PermissionDeniedError, RateLimitError, TimeoutError):
            # Propagate critical infrastructure errors immediately
            raise
        except ValidationError as exc:
            logic_error = any(
                error.get("type") == "value_error"
                and not tuple(error.get("loc") or ())[:-1]
                for error in exc.errors()
            )
            last_validation_code = (
                ExtractionOutcomeCode.OUTPUT_LOGIC_INVALID
                if logic_error
                else ExtractionOutcomeCode.OUTPUT_SCHEMA_INVALID
            )
            logger.warning(
                "[EXTRACTION] %s validation failed for chunk %s (attempt %d/2)",
                last_validation_code.value,
                chunk_label,
                strategies.index(strategy) + 1,
            )
            log_metric(
                "pipeline_extraction_validation_failure_count",
                1,
                outcome=last_validation_code.value,
            )
            if strategies.index(strategy) < len(strategies) - 1:
                log_metric(
                    "pipeline_extraction_validation_retry_count",
                    1,
                    outcome=last_validation_code.value,
                )
            continue

    if not extraction:
        raise ProviderOutcomeError(
            ProviderClassification(
                code=last_validation_code,
                request_id=getattr(last_response, "_request_id", None),
                provider_signal="pydantic_validation",
                sanitized_reason="Structured extraction failed strict validation after one retry.",
            )
        )

    token_usage = _response_token_usage(
        last_response,
        used_prompt,
        last_response.choices[0].message.content or "",
    )

    # Note: Normalization and Hebrew spec enforcement happen in the GraphNormalizer and Orchestrator.
    filtered = extraction

    try:
        cache_data = {
            "extraction": filtered.model_dump(),
            "token_usage": token_usage,
            "timestamp": str(asyncio.get_event_loop().time()),
            "model_name": model,
            "schema_version": schema_version,
            "schema_fingerprint": contract_fingerprint,
            "contract": "strict_structured_outputs_v1",
        }
        await asyncio.to_thread(store.write_bytes, text_cache_key, chunk_text.encode("utf-8"))
        await asyncio.to_thread(
            store.write_bytes,
            cache_key,
            json.dumps(cache_data, indent=2, ensure_ascii=False).encode("utf-8"),
        )
        logger.debug(
            fix_text(
                f"[DEBUG] Saved extraction cache to artifact store for {chunk_label}"
            )
        )
    except Exception as e:
        logger.error(fix_text(f"[DEBUG] Error saving artifact cache for {chunk_label}: {e}"))

    return filtered, token_usage
