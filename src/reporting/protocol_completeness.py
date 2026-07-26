"""Protocol completeness analysis for investigation case files."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
import json
import logging
import re
from typing import Any



logger = logging.getLogger(__name__)


STATUS_PRESENT = "present"
STATUS_MISSING = "missing"
STATUS_CONDITIONAL_MISSING = "conditional_missing"
STATUS_NOT_APPLICABLE = "not_applicable"
STATUS_UNKNOWN = "unknown"
EVIDENCE_STRONG = "strong"
EVIDENCE_WEAK = "weak"
EVIDENCE_NONE = "none"


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _normalize_for_search(value: Any) -> str:
    return _clean_text(value).casefold()


def _contains_any(haystack: str, needles: list[Any]) -> bool:
    return any(_normalize_for_search(needle) in haystack for needle in needles if needle)


def _snippet(raw_text: str, start: int, end: int, *, context_chars: int = 90) -> str:
    snippet_start = max(0, start - context_chars)
    snippet_end = min(len(raw_text), end + context_chars)
    return raw_text[snippet_start:snippet_end].strip()


def _regex_search(pattern: Any, text: str) -> re.Match[str] | None:
    pattern_text = str(pattern or "")
    if not pattern_text:
        return None
    try:
        return re.search(pattern_text, text, flags=re.IGNORECASE)
    except re.error as exc:
        logger.warning(
            "[PROTOCOL_COMPLETENESS] Invalid regex skipped | pattern=%s error=%s",
            pattern_text,
            exc,
        )
        return None


def _is_excluded(snippet: str, exclude_patterns: list[Any]) -> bool:
    return any(_regex_search(pattern, snippet) for pattern in exclude_patterns)


def _first_pattern_match(
    text: str,
    patterns: list[Any],
    exclude_patterns: list[Any] | None = None,
) -> dict[str, str] | None:
    raw_text = _clean_text(text)
    excludes = exclude_patterns or []
    for pattern in patterns:
        match = _regex_search(pattern, raw_text)
        if not match:
            continue
        snippet = _snippet(raw_text, match.start(), match.end())
        if _is_excluded(snippet, excludes):
            continue
        pattern_text = str(pattern)
        return {
            "signal": match.group(0),
            "snippet": snippet,
            "matchType": "regex",
            "pattern": pattern_text,
        }
    return None


def _first_signal_match(
    text: str,
    signals: list[Any],
    exclude_patterns: list[Any] | None = None,
) -> dict[str, str] | None:
    raw_text = _clean_text(text)
    search_text = raw_text.casefold()
    excludes = exclude_patterns or []
    for signal in signals:
        signal_text = _normalize_for_search(signal)
        if not signal_text:
            continue
        start_at = 0
        while True:
            index = search_text.find(signal_text, start_at)
            if index < 0:
                break
            snippet = _snippet(raw_text, index, index + len(str(signal)))
            if not _is_excluded(snippet, excludes):
                return {
                    "signal": str(signal),
                    "snippet": snippet,
                    "matchType": "keyword",
                }
            start_at = index + max(1, len(signal_text))
    return None


def _find_signal_match(
    *,
    raw_text: str,
    graph_inventory: str,
    patterns: list[Any],
    signals: list[Any],
    exclude_patterns: list[Any] | None = None,
) -> dict[str, str] | None:
    exclude_patterns = exclude_patterns or []
    if isinstance(signals, dict):
        signals = []

    match = _first_pattern_match(raw_text, patterns, exclude_patterns)
    if match:
        match["source"] = "raw_text"
        return match

    match = _first_signal_match(raw_text, signals, exclude_patterns)
    if match:
        match["source"] = "raw_text"
        return match

    graph_match = _first_pattern_match(graph_inventory, patterns, exclude_patterns)
    if graph_match:
        graph_match["source"] = "graph_inventory"
        return graph_match

    graph_match = _first_signal_match(graph_inventory, signals, exclude_patterns)
    if graph_match:
        graph_match["source"] = "graph_inventory"
        return graph_match

    return None


def _inventory_from_graph(graph_data: dict[str, Any]) -> str:
    parts: list[str] = []
    for node in graph_data.get("nodes", []) or []:
        if not isinstance(node, dict):
            continue
        parts.extend(
            [
                node.get("id", ""),
                node.get("type", ""),
                node.get("label", ""),
                node.get("description", ""),
                node.get("forensic_summary", ""),
                node.get("source_location", ""),
            ]
        )
        metadata = node.get("metadata")
        if isinstance(metadata, dict):
            parts.extend(str(v) for v in metadata.values() if isinstance(v, (str, int, float)))

    for edge in graph_data.get("edges", []) or []:
        if not isinstance(edge, dict):
            continue
        parts.extend(
            [
                edge.get("relation", ""),
                edge.get("description", ""),
                edge.get("forensic_summary", ""),
                edge.get("source_location", ""),
                edge.get("confidence", ""),
            ]
        )
        metadata = edge.get("metadata")
        if isinstance(metadata, dict):
            parts.extend(str(v) for v in metadata.values() if isinstance(v, (str, int, float)))

    return "\n".join(_clean_text(part) for part in parts if _clean_text(part))


def _condition_applies(
    condition: dict[str, Any],
    raw_text: str,
    graph_inventory: str,
    *,
    applicability_patterns: list[Any] | None = None,
    applicability_exclude_patterns: list[Any] | None = None,
) -> tuple[bool, str, dict[str, str] | None]:
    if not condition or condition.get("always") is True:
        return True, "always", None

    exclude_patterns = applicability_exclude_patterns or []
    text_sources = [
        ("raw_text", _clean_text(raw_text)),
        ("graph_inventory", _clean_text(graph_inventory)),
    ]

    for source, clean_text in text_sources:
        for pattern in applicability_patterns or []:
            match = _regex_search(pattern, clean_text)
            if not match:
                continue
            snippet = _snippet(clean_text, match.start(), match.end())
            if not _is_excluded(snippet, exclude_patterns):
                return True, f"regex:{pattern}", {
                    "source": source,
                    "signal": match.group(0),
                    "snippet": snippet,
                    "matchType": "regex",
                    "pattern": str(pattern),
                }

    any_keywords = condition.get("any_keywords") or []
    for source, clean_text in text_sources:
        combined_search_text = clean_text.casefold()
        for keyword in any_keywords:
            keyword_text = _normalize_for_search(keyword)
            if not keyword_text:
                continue
            start_at = 0
            while True:
                index = combined_search_text.find(keyword_text, start_at)
                if index < 0:
                    break
                snippet = _snippet(clean_text, index, index + len(str(keyword)))
                if not _is_excluded(snippet, exclude_patterns):
                    return True, f"keyword:{keyword}", {
                        "source": source,
                        "signal": str(keyword),
                        "snippet": snippet,
                        "matchType": "keyword",
                    }
                start_at = index + max(1, len(keyword_text))

    all_keywords = condition.get("all_keywords") or []
    combined_text = "\n".join([raw_text or "", graph_inventory or ""])
    combined_search_text = _normalize_for_search(combined_text)
    if all_keywords and all(
        _normalize_for_search(keyword) in combined_search_text for keyword in all_keywords if keyword
    ):
        return True, "keyword:all", {
            "source": "combined",
            "signal": "all",
            "snippet": _snippet(_clean_text(combined_text), 0, 0),
            "matchType": "keyword",
        }

    return False, "condition_not_met", None


def analyze_protocol_completeness(
    *,
    schema_config: Any,
    graph_data: dict[str, Any] | None,
    raw_text: str = "",
    doc_id: str = "unknown",
) -> dict[str, Any]:
    """Evaluate uploaded case material against the configured protocol checklist."""
    protocol = getattr(schema_config, "protocol_completeness", None) or {}
    checklist = protocol.get("checklist_items") or []
    graph_inventory = _inventory_from_graph(graph_data or {})

    results: list[dict[str, Any]] = []
    for item in checklist:
        if not isinstance(item, dict):
            continue

        applies, applicability_reason, applicability_evidence = _condition_applies(
            item.get("required_when") or {},
            raw_text,
            graph_inventory,
            applicability_patterns=item.get("applicability_patterns") or [],
            applicability_exclude_patterns=item.get("applicability_exclude_patterns") or item.get("exclude_patterns") or [],
        )
        exclude_patterns = item.get("exclude_patterns") or []
        strong_patterns = item.get("strong_evidence_patterns") or []
        strong_signals = item.get("strong_evidence_signals") or item.get("evidence_signals") or []
        weak_patterns = item.get("weak_reference_patterns") or []
        weak_signals = item.get("weak_reference_signals") or []
        strong_match = _find_signal_match(
            raw_text=raw_text,
            graph_inventory=graph_inventory,
            patterns=strong_patterns,
            signals=strong_signals,
            exclude_patterns=exclude_patterns,
        )
        weak_match = None
        if not strong_match:
            weak_match = _find_signal_match(
                raw_text=raw_text,
                graph_inventory=graph_inventory,
                patterns=weak_patterns,
                signals=weak_signals,
                exclude_patterns=exclude_patterns,
            )

        if not applies:
            status = STATUS_NOT_APPLICABLE
            reason = "תנאי התחולה לא זוהו בתיק שהועלה."
        elif strong_match:
            status = STATUS_PRESENT
            reason = item.get("present_message_he") or "אותר סימן מתאים בתיק שהועלה."
        elif weak_match:
            status = STATUS_CONDITIONAL_MISSING
            reason = item.get("weak_reference_message_he") or item.get(
                "missing_message_he",
                "אותרה אינדיקציה חלקית בלבד; נדרשת בדיקה ידנית של רכיב הפרוטוקול.",
            )
        else:
            status = (
                STATUS_MISSING
                if (item.get("required_when") or {}).get("always") is True
                else STATUS_CONDITIONAL_MISSING
            )
            reason = item.get("missing_message_he") or "לא אותר סימן מתאים בתיק שהועלה."

        evidence = []
        evidence_strength = EVIDENCE_NONE
        review_note = ""
        if strong_match:
            evidence_strength = EVIDENCE_STRONG
            evidence_item = {
                "source": strong_match["source"],
                "signal": strong_match["signal"],
                "snippet": strong_match["snippet"],
                "strength": EVIDENCE_STRONG,
                "matchType": strong_match.get("matchType", "keyword"),
            }
            if strong_match.get("pattern"):
                evidence_item["pattern"] = strong_match["pattern"]
            evidence.append(evidence_item)
        elif weak_match:
            evidence_strength = EVIDENCE_WEAK
            review_note = item.get("review_note_he") or "אותרה אינדיקציה חלקית, אך לא אותר הרכיב המלא הנדרש."
            evidence_item = {
                "source": weak_match["source"],
                "signal": weak_match["signal"],
                "snippet": weak_match["snippet"],
                "strength": EVIDENCE_WEAK,
                "matchType": weak_match.get("matchType", "keyword"),
            }
            if weak_match.get("pattern"):
                evidence_item["pattern"] = weak_match["pattern"]
            evidence.append(evidence_item)

        results.append(
            {
                "id": item.get("id"),
                "pillar": item.get("pillar") or "general",
                "label": item.get("label_he") or item.get("id"),
                "description": item.get("description_he") or "",
                "severity": item.get("severity") or "info",
                "status": status,
                "reason": reason,
                "applicability": applicability_reason,
                "applicabilityEvidence": applicability_evidence,
                "evidenceStrength": evidence_strength,
                "reviewNote": review_note,
                "evidence": evidence,
                "sourceRefs": item.get("source_refs") or [],
            }
        )

    counts = Counter(result["status"] for result in results)
    critical_missing = sum(
        1
        for result in results
        if result["severity"] == "critical"
        and result["status"] == STATUS_MISSING
    )
    critical_review_required = sum(
        1
        for result in results
        if result["severity"] == "critical"
        and result["status"] == STATUS_CONDITIONAL_MISSING
    )
    applicable = [
        result
        for result in results
        if result["status"] != STATUS_NOT_APPLICABLE
    ]
    present = [result for result in applicable if result["status"] == STATUS_PRESENT]
    coverage = round((len(present) / len(applicable)) * 100, 1) if applicable else 100.0

    return {
        "docId": doc_id,
        "protocolVersion": protocol.get("version", "unknown"),
        "generatedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "disclaimer": "הבדיקה מזהה רכיבים שלא אותרו בחומר שהועלה ואינה קביעה משפטית לגבי קיומם או תחולתם.",
        "summary": {
            "totalItems": len(results),
            "applicableItems": len(applicable),
            "present": counts.get(STATUS_PRESENT, 0),
            "missing": counts.get(STATUS_MISSING, 0),
            "conditionalMissing": counts.get(STATUS_CONDITIONAL_MISSING, 0),
            "notApplicable": counts.get(STATUS_NOT_APPLICABLE, 0),
            "unknown": counts.get(STATUS_UNKNOWN, 0),
            "criticalMissing": critical_missing,
            "criticalReviewRequired": critical_review_required,
            "coveragePercent": coverage,
        },
        "items": results,
    }


def protocol_completeness_to_markdown(report: dict[str, Any]) -> str:
    """Render a compact Hebrew Markdown section for GRAPH_REPORT.md."""
    summary = report.get("summary") or {}
    items = report.get("items") or []
    problem_items = [
        item
        for item in items
        if item.get("status") in {STATUS_MISSING, STATUS_CONDITIONAL_MISSING, STATUS_UNKNOWN}
    ]

    lines = [
        "## בקרת שלמות תיק לפי פרוטוקול חקירה",
        "",
        str(report.get("disclaimer") or ""),
        "",
        f"- **כיסוי רכיבים רלוונטיים:** {summary.get('coveragePercent', 0)}%",
        f"- **רכיבים שאותרו:** {summary.get('present', 0)}",
        f"- **חוסרים קריטיים:** {summary.get('criticalMissing', 0)}",
        f"- **בדיקות קריטיות נדרשות:** {summary.get('criticalReviewRequired', 0)}",
        f"- **חוסרים/בדיקות מותנות:** {summary.get('conditionalMissing', 0)}",
        "",
    ]

    if not problem_items:
        lines.append("לא זוהו חוסרים ברכיבי הפרוטוקול הרלוונטיים לפי החומר שהועלה.")
        return "\n".join(lines)

    lines.extend(["### פריטים שלא אותרו", ""])
    for item in problem_items[:20]:
        status_label = {
            STATUS_MISSING: "חסר",
            STATUS_CONDITIONAL_MISSING: "חסר מותנה בבדיקת תחולה",
            STATUS_UNKNOWN: "לא ידוע",
        }.get(str(item.get("status")), str(item.get("status")))
        severity = item.get("severity") or "info"
        lines.append(f"- **{item.get('label')}** ({status_label}, {severity}): {item.get('reason')}")
        applicability_evidence = item.get("applicabilityEvidence") or {}
        if applicability_evidence.get("snippet"):
            lines.append(f"  - למה נבדק: {applicability_evidence.get('snippet')}")
        if item.get("reviewNote"):
            lines.append(f"  - הערת בדיקה: {item.get('reviewNote')}")

    return "\n".join(lines)


async def verify_missing_items_with_agent(
    report: dict[str, Any],
    doc_id: str,
    raw_text: str = "",
    search_manager: Any = None,
    max_items: int = 10,
    critical_only: bool = False,
) -> dict[str, Any]:
    """Audits missing/conditional protocol items using Azure AI Search & LLM grounded verification."""
    from pathlib import Path

    if not isinstance(report, dict):
        return report

    items = report.get("items") or []
    if not items:
        return report

    target_items = [
        item
        for item in items
        if item.get("status") in {STATUS_MISSING, STATUS_CONDITIONAL_MISSING}
        and (not critical_only or item.get("severity") == "critical")
    ][:max_items]

    if not target_items:
        return report

    system_prompt = "You are a constrained investigation-file completeness classifier and auditor."
    try:
        prompt_path = Path(__file__).resolve().parents[2] / "prompts" / "protocol_completeness_system.md"
        if prompt_path.exists():
            system_prompt = prompt_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("[PROTOCOL_COMPLETENESS] Could not read system prompt file: %s", exc)


    try:
        from src.query_agent.nodes import call_llm
    except ImportError:
        logger.warning("[PROTOCOL_COMPLETENESS] call_llm import failed, skipping agent verification.")
        return report

    updated_items = list(items)
    item_map = {item.get("id"): idx for idx, item in enumerate(updated_items)}

    for target in target_items:
        item_id = target.get("id")
        label = target.get("label") or item_id
        description = target.get("description") or ""

        candidate_snippets: list[dict[str, str]] = []

        if search_manager and hasattr(search_manager, "search_chunks"):
            try:
                search_query = f"{label} {description}"
                chunks = await search_manager.search_chunks(doc_id=doc_id, query=search_query, top=5)
                for chunk in chunks:
                    candidate_snippets.append({
                        "text": chunk.get("chunk_text") or chunk.get("text") or "",
                        "source_location": chunk.get("source_location") or chunk.get("id") or "raw_text",
                    })
            except Exception as search_err:
                logger.warning("[PROTOCOL_COMPLETENESS] Search retrieval error for item %s: %s", item_id, search_err)

        if not candidate_snippets and raw_text:
            clean_raw = _clean_text(raw_text)
            if clean_raw:
                candidate_snippets.append({
                    "text": clean_raw[:3000],
                    "source_location": f"doc_{doc_id}_extracted.txt",
                })

        if not candidate_snippets:
            continue

        snippets_prompt = "\n---\n".join(
            f"Source Location: {s['source_location']}\nText: {s['text']}"
            for s in candidate_snippets
        )

        user_prompt = (
            f"Checklist Item ID: {item_id}\n"
            f"Checklist Item Label: {label}\n"
            f"Checklist Item Description: {description}\n\n"
            f"Candidate Material Snippets:\n{snippets_prompt}\n\n"
            "Audit strictly according to system prompt rules. Return ONLY valid JSON."
        )

        try:
            llm_response = await call_llm(system_prompt, user_prompt, max_tokens=600, temperature=0.0)
            match_data = None
            if "```json" in llm_response:
                llm_response = llm_response.split("```json")[1].split("```")[0].strip()
            elif "```" in llm_response:
                llm_response = llm_response.split("```")[1].split("```")[0].strip()

            parsed = json.loads(llm_response)
            matches = parsed.get("matches") or []
            if matches and isinstance(matches, list):
                match_data = matches[0]

            idx = item_map.get(item_id)
            if idx is not None and match_data:
                status_str = str(match_data.get("status", "")).lower()
                verbatim_quote = _clean_text(match_data.get("verbatim_quote") or match_data.get("evidence_snippet") or "")
                source_loc = match_data.get("source_location") or "case_material"
                reason_text = match_data.get("reason") or ""

                is_grounded = False
                if verbatim_quote:
                    verbatim_lower = verbatim_quote.casefold()
                    for s in candidate_snippets:
                        if verbatim_lower in s["text"].casefold():
                            is_grounded = True
                            source_loc = s["source_location"]
                            break

                if status_str == "found" and is_grounded:
                    updated_items[idx]["status"] = STATUS_PRESENT
                    updated_items[idx]["evidenceStrength"] = "agent_verified"
                    updated_items[idx]["reason"] = f"אותר ואומת ע\"י סוכן חכם. {reason_text}".strip()
                    updated_items[idx]["agentVerification"] = {
                        "verifiedByAgent": True,
                        "verbatimQuote": verbatim_quote,
                        "sourceLocation": source_loc,
                        "explanation": reason_text,
                        "verifiedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    }
                    evidence_list = updated_items[idx].get("evidence") or []
                    evidence_list.append({
                        "source": "agent_verification",
                        "signal": "agent_verified",
                        "snippet": verbatim_quote,
                        "strength": "strong",
                        "matchType": "semantic",
                    })
                    updated_items[idx]["evidence"] = evidence_list
                else:
                    updated_items[idx]["agentVerification"] = {
                        "verifiedByAgent": False,
                        "explanation": reason_text or "נבדק ע\"י סוכן חכם ולא אותרה ראיה מפורשת בחומר התיק.",
                        "verifiedAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    }
        except Exception as llm_err:
            logger.warning("[PROTOCOL_COMPLETENESS] LLM verification error for item %s: %s", item_id, llm_err)

    counts = Counter(item["status"] for item in updated_items)
    critical_missing = sum(
        1 for item in updated_items
        if item["severity"] == "critical" and item["status"] == STATUS_MISSING
    )
    critical_review_required = sum(
        1 for item in updated_items
        if item["severity"] == "critical" and item["status"] == STATUS_CONDITIONAL_MISSING
    )
    applicable = [item for item in updated_items if item["status"] != STATUS_NOT_APPLICABLE]
    present = [item for item in applicable if item["status"] == STATUS_PRESENT]
    coverage = round((len(present) / len(applicable)) * 100, 1) if applicable else 100.0

    report["items"] = updated_items
    report["summary"] = {
        "totalItems": len(updated_items),
        "applicableItems": len(applicable),
        "present": counts.get(STATUS_PRESENT, 0),
        "missing": counts.get(STATUS_MISSING, 0),
        "conditionalMissing": counts.get(STATUS_CONDITIONAL_MISSING, 0),
        "notApplicable": counts.get(STATUS_NOT_APPLICABLE, 0),
        "unknown": counts.get(STATUS_UNKNOWN, 0),
        "criticalMissing": critical_missing,
        "criticalReviewRequired": critical_review_required,
        "coveragePercent": coverage,
    }
    report["agentVerificationCompletedAt"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    return report

