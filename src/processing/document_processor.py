import hashlib
import re
import os
import io
import logging
import random
import tiktoken
import asyncio
from pathlib import Path
from typing import Any
from pypdf import PdfReader, PdfWriter

try:
    from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential
    from azure.core.exceptions import HttpResponseError, ClientAuthenticationError
except ImportError:
    DocumentIntelligenceClient = None
    AzureKeyCredential = None
    HttpResponseError = None
    ClientAuthenticationError = None

# Mock settings and SCHEMA_CONFIG if needed, or import them
# Assuming they are available via src.utils.config and src.models.models
from src.utils.config import settings
from src.models.models import SCHEMA_CONFIG
from src.storage.interfaces import IArtifactStore


logger = logging.getLogger(__name__)

DI_CONCURRENCY = max(1, int(os.getenv("DOCUMENT_INTELLIGENCE_CONCURRENCY", "5")))
DI_MAX_ATTEMPTS = max(1, int(os.getenv("DOCUMENT_INTELLIGENCE_MAX_ATTEMPTS", "5")))
DI_BACKOFF_BASE = max(0.1, float(os.getenv("DOCUMENT_INTELLIGENCE_BACKOFF_BASE", "2")))
DI_BACKOFF_MAX = max(DI_BACKOFF_BASE, float(os.getenv("DOCUMENT_INTELLIGENCE_BACKOFF_MAX", "60")))


def _extract_pages_via_pypdf(file_stream: bytes) -> list[dict[str, Any]]:
    """
    Local fallback: extract text from a PDF using pypdf.

    Used when Azure Document Intelligence is unavailable (e.g., 403 quota
    exceeded, network errors, or missing credentials).  The quality is lower
    than DI (no OCR, no table/layout understanding), but it guarantees the
    pipeline can still produce non-empty chunks.

    Args:
        file_stream: Raw PDF bytes.

    Returns:
        A list of page dicts with 'page_number' and 'text' keys.
    """
    try:
        reader = PdfReader(io.BytesIO(file_stream))
    except Exception as exc:
        logger.error("[DOCUMENT_PROCESSOR] pypdf failed to open PDF: %s", exc)
        return [{"page_number": 1, "text": ""}]

    pages: list[dict[str, Any]] = []
    for idx, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:
            logger.warning(
                "[DOCUMENT_PROCESSOR] pypdf could not extract text from page %d: %s",
                idx, exc,
            )
            text = ""
        pages.append({"page_number": idx, "text": text})

    if not pages:
        pages.append({"page_number": 1, "text": ""})

    logger.info(
        "[DOCUMENT_PROCESSOR] pypdf fallback extracted %d pages (%d non-empty).",
        len(pages),
        sum(1 for p in pages if p["text"]),
    )
    return pages


def _has_extracted_text(pages: list[dict[str, Any]]) -> bool:
    return any((page.get("text") or "").strip() for page in pages)


def _fallback_pages_or_raise(
    *,
    chunk_bytes: bytes,
    start_page: int,
    original_error: Exception,
) -> list[dict[str, Any]]:
    chunk_pages = _extract_pages_via_pypdf(chunk_bytes)
    for fallback_idx, fp in enumerate(chunk_pages):
        fp["page_number"] = start_page + fallback_idx + 1

    if not _has_extracted_text(chunk_pages):
        raise RuntimeError(
            "Document Intelligence failed and local pypdf fallback extracted no text "
            f"for pages {start_page + 1}-{start_page + len(chunk_pages)}. "
            "This PDF appears to require OCR; verify Azure Document Intelligence "
            f"configuration. Original error: {type(original_error).__name__}: {original_error}"
        ) from original_error

    return chunk_pages


def _retry_after_seconds(headers: Any) -> float | None:
    if not headers:
        return None
    retry_after_val = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after_val is None:
        return None
    try:
        return max(0.0, float(retry_after_val))
    except (TypeError, ValueError):
        return None


def _di_retry_delay(attempt: int, headers: Any = None) -> float:
    retry_after = _retry_after_seconds(headers)
    if retry_after is not None:
        return retry_after + random.uniform(0, 0.5)
    exponential = DI_BACKOFF_BASE * (2 ** max(0, attempt - 1))
    return min(DI_BACKOFF_MAX, exponential) + random.uniform(0, 1.0)


def _contains_arabic(text: str) -> bool:
    if not isinstance(text, str) or not text:
        return False
    # Check for Arabic script characters
    # Range \u0600-\u06FF: Arabic
    # Range \u0750-\u077F: Arabic Supplement
    # Range \u08A0-\u08FF: Arabic Extended-A
    # Range \uFB50-\uFDFF: Arabic Presentation Forms-A
    # Range \uFE70-\uFEFF: Arabic Presentation Forms-B
    for char in text:
        if (
            '\u0600' <= char <= '\u06FF' or
            '\u0750' <= char <= '\u077F' or
            '\u08A0' <= char <= '\u08FF' or
            '\uFB50' <= char <= '\uFDFF' or
            '\uFE70' <= char <= '\uFEFF'
        ):
            return True
    return False


def _contains_hebrew(text: str) -> bool:
    if not isinstance(text, str) or not text:
        return False
    for char in text:
        if '\u0590' <= char <= '\u05FF':
            return True
    return False


def _strip_arabic_lines(text: str) -> str:
    if not isinstance(text, str) or not text:
        return text
    lines = text.split("\n")
    clean_lines = []
    stripped_count = 0
    for line in lines:
        if _contains_arabic(line):
            stripped_count += 1
            continue
        clean_lines.append(line)
    if stripped_count > 0:
        logger.info(f"[DOCUMENT_PROCESSOR] Stripped {stripped_count} Arabic lines from page.")
    return "\n".join(clean_lines)


async def _load_document_pages_via_di(file_stream: bytes) -> list[dict[str, Any]]:
    """
    Load document content and split into logical pages using Azure Document Intelligence.
    To avoid payload size limits, large PDFs are split into 2-page chunks.

    Args:
        file_stream (bytes): The binary content of the PDF file.

    Returns:
        list[dict[str, Any]]: A list of page objects containing 'page_number' and 'text'.
    """
    if DocumentIntelligenceClient is None:
        raise RuntimeError(
            "azure-ai-documentintelligence is required but not installed."
        )

    endpoint = settings.azure_document_intelligence_endpoint
    key = settings.azure_document_intelligence_key

    if not endpoint or not key:
        raise RuntimeError(
            "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY are required. "
            "Managed Identity and implicit authentication are not supported."
        )

    # 1. Split PDF into 2-page chunks using pypdf
    try:
        reader = PdfReader(io.BytesIO(file_stream))
        total_pages = len(reader.pages)
        logging.info(
            f"[DOCUMENT_PROCESSOR] PDF detected. Total pages: {total_pages}. Splitting into 2-page chunks."
        )
        logging.info(f"PDF Page count: {total_pages}")
    except Exception as e:
        raise RuntimeError(f"Failed to parse PDF for splitting: {e}")

    chunk_size = 2
    tasks = []
    
    # Use a Semaphore to control concurrency and prevent hitting rate limits
    semaphore = asyncio.Semaphore(DI_CONCURRENCY)

    async def analyze_chunk(start_page: int, end_page: int) -> list[dict[str, Any]]:
        async with semaphore:
            logger.info(
                f"[DOCUMENT_PROCESSOR] Processing pages {start_page + 1} to {end_page}..."
            )
            print(f"DEBUG: Processing pages {start_page + 1} to {end_page}...")

            # Check which pages contain Arabic digital text. Skip DI for them.
            pages_to_di = []
            chunk_pages = []
            
            for i in range(start_page, end_page):
                try:
                    digital_text = reader.pages[i].extract_text() or ""
                except Exception as exc:
                    logger.warning(
                        "[DOCUMENT_PROCESSOR] pypdf could not extract text from page %d: %s",
                        i + 1, exc,
                    )
                    digital_text = ""
                
                if _contains_arabic(digital_text) and not _contains_hebrew(digital_text):
                    logger.info(
                        f"[DOCUMENT_PROCESSOR] Page {i + 1} contains Arabic script and no Hebrew. Skipping Document Intelligence OCR."
                    )
                    chunk_pages.append(
                        {"page_number": i + 1, "text": digital_text}
                    )
                else:
                    pages_to_di.append(i)

            if not pages_to_di:
                return chunk_pages

            # Create a new PDF containing only the pages scheduled for DI
            writer = PdfWriter()
            for i in pages_to_di:
                writer.add_page(reader.pages[i])

            chunk_io = io.BytesIO()
            writer.write(chunk_io)
            chunk_bytes = chunk_io.getvalue()

            max_attempts = DI_MAX_ATTEMPTS
            attempt = 0
            success = False

            while attempt < max_attempts and not success:
                attempt += 1
                try:
                    async with DocumentIntelligenceClient(
                        endpoint=endpoint,
                        credential=AzureKeyCredential(key),
                        api_version="2024-11-30",
                        retry_total=0  # Disable built-in synchronous retries
                    ) as client:
                        model_id = os.environ.get(
                            "AZURE_DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout"
                        )
                        poller = await asyncio.wait_for(
                            client.begin_analyze_document(
                                model_id, body=chunk_bytes, content_type="application/octet-stream"
                            ),
                            timeout=settings.document_intelligence_timeout_seconds,
                        )
                        result = await asyncio.wait_for(
                            poller.result(),
                            timeout=settings.document_intelligence_timeout_seconds,
                        )
                        msg = f"[DOCUMENT_PROCESSOR] DI result received for pages {start_page + 1}-{end_page}. Has pages: {bool(hasattr(result, 'pages'))}, Content length: {len(getattr(result, 'content', ''))}"
                        logger.info(msg)
                        print(f"DEBUG: {msg}")

                        if hasattr(result, "pages") and result.pages:
                            for idx, page in enumerate(result.pages):
                                page_text = ""
                                if hasattr(page, "lines") and page.lines:
                                    page_text = "\n".join([line.content for line in page.lines])
                                elif hasattr(page, "content"):
                                    page_text = page.content
                                
                                if not page_text and hasattr(result, "content") and hasattr(page, "spans"):
                                    # Fallback: slice top-level content using page spans
                                    for span in page.spans:
                                        page_text += result.content[span.offset : span.offset + span.length]

                                orig_page_num = pages_to_di[idx] + 1
                                msg_page = f"[DOCUMENT_PROCESSOR] Page {orig_page_num} text length: {len(page_text)}"
                                logger.info(msg_page)
                                chunk_pages.append(
                                    {"page_number": orig_page_num, "text": page_text or ""}
                                )
                        elif hasattr(result, "content"):
                            orig_page_num = pages_to_di[0] + 1
                            chunk_pages.append(
                                {"page_number": orig_page_num, "text": result.content}
                            )
                            for idx in range(1, len(pages_to_di)):
                                chunk_pages.append(
                                    {"page_number": pages_to_di[idx] + 1, "text": ""}
                                )
                        success = True
                except asyncio.TimeoutError as e:
                    raise TimeoutError(
                        f"Document Intelligence timed out after {settings.document_intelligence_timeout_seconds:.1f}s "
                        f"for pages {start_page + 1}-{end_page}"
                    ) from e
                except Exception as e:
                    print(f"DEBUG ERROR: {type(e).__name__}: {str(e)}")
                    if HttpResponseError and isinstance(e, HttpResponseError):
                        if e.status_code == 429 and attempt < max_attempts:
                            headers = (
                                e.response.headers
                                if e.response and hasattr(e.response, "headers")
                                else None
                            )
                            retry_after_sec = _di_retry_delay(attempt, headers)
                            logger.warning(
                                f"[DOCUMENT_PROCESSOR] Received 429 for pages {start_page + 1}-{end_page}. "
                                f"Retrying in {retry_after_sec:.2f} seconds (Attempt {attempt}/{max_attempts})..."
                            )
                            await asyncio.sleep(retry_after_sec)
                        else:
                            logger.warning(
                                "[DOCUMENT_PROCESSOR] DI API failed for pages %d-%d (status %s). "
                                "Falling back to local pypdf extraction.",
                                start_page + 1, end_page, e.status_code,
                            )
                            fallback_pages = _fallback_pages_or_raise(
                                chunk_bytes=chunk_bytes,
                                start_page=0,
                                original_error=e,
                            )
                            for f_idx, fp in enumerate(fallback_pages):
                                orig_page_num = pages_to_di[f_idx] + 1
                                chunk_pages.append(
                                    {"page_number": orig_page_num, "text": fp["text"]}
                                )
                            success = True
                    else:
                        logger.warning(
                            "[DOCUMENT_PROCESSOR] Non-HTTP DI error for pages %d-%d: %s. "
                            "Falling back to local pypdf extraction.",
                            start_page + 1, end_page, e,
                        )
                        fallback_pages = _fallback_pages_or_raise(
                            chunk_bytes=chunk_bytes,
                            start_page=0,
                            original_error=e,
                        )
                        for f_idx, fp in enumerate(fallback_pages):
                            orig_page_num = pages_to_di[f_idx] + 1
                            chunk_pages.append(
                                {"page_number": orig_page_num, "text": fp["text"]}
                            )
                        success = True
            return chunk_pages

    for start_page in range(0, total_pages, chunk_size):
        end_page = min(start_page + chunk_size, total_pages)
        tasks.append(analyze_chunk(start_page, end_page))

    # Run chunks concurrently in batches of size DI_CONCURRENCY to throttle memory consumption
    chunk_results = []
    for i in range(0, len(tasks), DI_CONCURRENCY):
        batch = tasks[i : i + DI_CONCURRENCY]
        batch_results = await asyncio.gather(*batch)
        chunk_results.extend(batch_results)
    
    # Flatten the results and sort by page number
    all_pages = []
    for chunk_res in chunk_results:
        all_pages.extend(chunk_res)
    
    all_pages.sort(key=lambda x: x["page_number"])

    if not _has_extracted_text(all_pages):
        raise RuntimeError(
            "Document Intelligence completed but extracted no text from the PDF. "
            "This document cannot be chunked until OCR returns content."
        )

    # Post-process all pages to strip Arabic lines
    for page in all_pages:
        page["text"] = _strip_arabic_lines(page["text"])

    return all_pages or [{"page_number": 1, "text": ""}]


def _load_document_pages(
    input_path: Path | str, artifact_store: IArtifactStore
) -> list[dict[str, Any]]:
    """
    Load document content and split into logical pages. Currently supports Markdown.
    PDF extraction is disabled here to enforce Document Intelligence usage.

    Args:
        input_path (Path | str): Path to the source document (MD).

    Returns:
        list[dict[str, Any]]: A list of page objects containing 'page_number'
            and 'text'.

    Raises:
        RuntimeError: If a PDF is provided (enforcing DI only).
        FileNotFoundError: If the input file does not exist.
    """
    source_path = Path(input_path)

    if source_path.suffix.lower() == ".pdf":
        logger.info(
            "[DOCUMENT_PROCESSOR] PDF detected in local loader. "
            "Extracting via pypdf (DI path was not used for this file)."
        )
        pdf_bytes = artifact_store.read_bytes(str(input_path))
        return _extract_pages_via_pypdf(pdf_bytes)

    text = artifact_store.read_bytes(str(input_path)).decode("utf-8")
    if "## Page" not in text:
        return [{"page_number": 1, "text": text.strip()}]

    pages: list[dict[str, Any]] = []
    for page_number, page_block in enumerate(
        re.split(r"(?m)^## Page\s*", text)[1:], start=1
    ):
        cleaned = page_block.strip()
        if not cleaned:
            continue

        lines = cleaned.splitlines()
        if lines and re.fullmatch(r"\d+", lines[0].strip()):
            lines = lines[1:]

        page_text = "\n".join(lines).strip()
        if page_text:
            pages.append({"page_number": page_number, "text": page_text})

    return pages or [{"page_number": 1, "text": text.strip()}]


def _count_words(text: str) -> int:
    """
    Estimate the number of words in a string.

    Args:
        text (str): The string to count words in.

    Returns:
        int: The estimated word count.
    """
    return len(re.findall(r"\S+", text or ""))


def _count_tokens(text: str, model: str = "gpt-4o") -> int:
    """
    Count tokens in a string using tiktoken.

    Args:
        text (str): The string to count tokens in.
        model (str): The model to use for tokenization.

    Returns:
        int: The number of tokens.
    """
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text or ""))


def _build_discrete_chunks(
    pages: list[dict[str, Any]],
    *,
    max_tokens: int = 1200,
) -> list[dict[str, Any]]:
    """
    Partition pages into discrete chunks based on paragraph boundaries and token count.

    Minimizes overlap to reduce redundant LLM calls, but preserves small sentence
    context via a sliding window overlap of leading sentences.

    Args:
        pages (list[dict[str, Any]]): List of page objects.
        max_tokens (int): Target maximum tokens per chunk. Defaults to 1200.

    Returns:
        list[dict[str, Any]]: A list of chunk objects containing 'text', 'label',
            and indices.
    """
    if not pages:
        return []

    overlap_count = SCHEMA_CONFIG.graph_analysis.chunk_overlap_sentences

    full_text = ""
    for p in pages:
        full_text += p.get("text", "") + "\n\n"

    paragraphs = full_text.split("\n\n")
    chunk_raw_texts: list[str] = []
    current_chunk_text_list = []
    current_token_count = 0

    extraction_model = getattr(settings, "graphify_extraction_model", "gpt-4o")

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        para_token_count = _count_tokens(para, extraction_model)

        if (
            current_token_count + para_token_count > max_tokens
            and current_chunk_text_list
        ):
            chunk_raw_texts.append("\n\n".join(current_chunk_text_list))
            current_chunk_text_list = []
            current_token_count = 0

        current_chunk_text_list.append(para)
        current_token_count += para_token_count

    if current_chunk_text_list:
        chunk_raw_texts.append("\n\n".join(current_chunk_text_list))
    final_chunks: list[dict[str, Any]] = []
    prev_chunk_text = ""

    for i, raw_text in enumerate(chunk_raw_texts):
        chunk_text = raw_text

        if i > 0 and overlap_count > 0:
            sentences = re.split(r"(?<=[.!?])\s+", prev_chunk_text)
            overlap_sentences = [s.strip() for s in sentences if s.strip()]

            if overlap_sentences:
                overlap_text = " ".join(overlap_sentences[-overlap_count:])
                chunk_text = f"{overlap_text} …\n\n{raw_text}"

        chunk_index = i + 1
        final_chunks.append(
            {
                "index": chunk_index,
                "chunk_index": chunk_index,
                "label": f"Chunk {chunk_index}",
                "text": chunk_text,
                "word_count": _count_words(chunk_text),
                "token_count": _count_tokens(chunk_text, extraction_model),
            }
        )
        prev_chunk_text = raw_text

    return final_chunks


def _materialize_chunk_cache(
    chunks: list[dict[str, Any]],
    source_file: str,
    artifact_store: IArtifactStore,
) -> dict[str, dict[str, Any]]:
    """
    Write chunks to disk for semantic caching and link them to their source files.

    Args:
        chunks (list[dict[str, Any]]): List of chunk objects.
        source_file (str): Path or identifier for the source document.

    Returns:
        dict[str, dict[str, Any]]: A mapping of cache file paths to chunk metadata.
    """
    chunk_by_cache_path: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        cache_key = hashlib.sha256(chunk["text"].strip().encode("utf-8")).hexdigest()
        cache_path = f"chunk_cache/{cache_key}.txt"
        artifact_store.write_bytes(cache_path, chunk["text"].encode("utf-8"))

        chunk_record = dict(chunk)
        chunk_record["source_file"] = source_file
        chunk_record["cache_path"] = cache_path
        chunk_record["cache_key"] = cache_key
        chunk_by_cache_path.setdefault(cache_path, chunk_record)

    return chunk_by_cache_path
