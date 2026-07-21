import json
from pathlib import Path

from src.models.base import SchemaConfigModel
from src.reporting.protocol_completeness import analyze_protocol_completeness


def _schema() -> SchemaConfigModel:
    data = json.loads(Path("schemas/ontology_schema.json").read_text(encoding="utf-8"))
    return SchemaConfigModel.model_validate(data)


def _item(report, item_id):
    return next(item for item in report["items"] if item["id"] == item_id)


def test_schema_loads_protocol_completeness_section():
    schema = _schema()

    assert schema.protocol_completeness["version"] == "1.2"
    item_ids = {
        item["id"]
        for item in schema.protocol_completeness["checklist_items"]
    }
    assert "investigation_material_list" in item_ids
    assert "exhibit_description_or_seizure" in item_ids
    assert "exhibit_chain_of_custody" in item_ids


def test_analyzer_marks_universal_missing_and_present_items():
    schema = _schema()
    raw_text = "הוגשה תלונה ראשונית. סיכום חקירה הועבר לעיון התביעה."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_1",
    )

    assert _item(report, "opening_initial_report")["status"] == "present"
    assert _item(report, "opening_initial_report")["evidenceStrength"] == "strong"
    assert _item(report, "case_summary_recommendation")["status"] == "present"
    assert _item(report, "investigation_material_list")["status"] == "missing"
    assert report["summary"]["criticalMissing"] >= 1
    assert "criticalReviewRequired" in report["summary"]


def test_analyzer_flags_suspect_rights_when_suspect_appears_without_warning():
    schema = _schema()
    raw_text = "החשוד מסר גרסה במסגרת חקירת מצח."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [{"id": "s1", "type": "SUSPECT", "label": "פלוני"}], "edges": []},
        raw_text=raw_text,
        doc_id="case_2",
    )

    assert _item(report, "rights_warning_form")["status"] == "conditional_missing"
    assert _item(report, "suspect_interrogation_statement")["status"] == "conditional_missing"


def test_analyzer_uses_graph_inventory_for_exhibit_description_signal():
    schema = _schema()
    raw_text = "נתפס טלפון נייד של החשוד."
    graph_data = {
        "nodes": [{"id": "phone", "type": "EXHIBIT", "label": "טלפון נייד"}],
        "edges": [{"source": "event", "target": "phone", "relation": "SEIZED_FROM"}],
    }

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data=graph_data,
        raw_text=raw_text,
        doc_id="case_3",
    )

    seizure_item = _item(report, "exhibit_description_or_seizure")
    assert seizure_item["status"] == "present"
    assert seizure_item["evidence"][0]["source"] == "raw_text"

    exhibit_item = _item(report, "exhibit_chain_of_custody")
    assert exhibit_item["status"] == "conditional_missing"
    assert exhibit_item["evidenceStrength"] == "weak"
    assert exhibit_item["reviewNote"]


def test_analyzer_does_not_treat_displayed_information_as_exhibit():
    schema = _schema()
    raw_text = "תדפיס מידע פלילי המידע הפלילי המוצג לפניך הוא בהיקף אותו אתה זכאי לקבל."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_displayed",
    )

    assert _item(report, "exhibit_description_or_seizure")["status"] == "not_applicable"
    assert _item(report, "exhibit_chain_of_custody")["status"] == "not_applicable"


def test_analyzer_uses_regex_for_numbered_exhibit_description():
    schema = _schema()
    raw_text = "אני מציג לך פריט 10 פריט 11 פריט 12. פריט 10 הוא סרטון מהאירוע."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_numbered_exhibit",
    )

    seizure_item = _item(report, "exhibit_description_or_seizure")
    assert seizure_item["status"] == "present"
    assert seizure_item["evidence"][0]["matchType"] == "regex"
    assert "פריט 10" in seizure_item["evidence"][0]["signal"]


def test_analyzer_uses_graph_seized_from_when_raw_exhibit_text_is_excluded():
    schema = _schema()
    raw_text = "תדפיס מידע פלילי המידע הפלילי המוצג לפניך."
    graph_data = {
        "nodes": [{"id": "phone", "type": "EXHIBIT", "label": "טלפון נייד"}],
        "edges": [{"source": "event", "target": "phone", "relation": "SEIZED_FROM"}],
    }

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data=graph_data,
        raw_text=raw_text,
        doc_id="case_graph_exhibit",
    )

    seizure_item = _item(report, "exhibit_description_or_seizure")
    assert seizure_item["status"] == "present"
    assert seizure_item["evidence"][0]["source"] == "graph_inventory"
    assert seizure_item["evidence"][0]["matchType"] == "keyword"


def test_analyzer_marks_chain_of_custody_present_only_for_chain_language():
    schema = _schema()
    raw_text = "נערך טופס מוצג הכולל סימון המוצג והעברת מוצג לחזקת היחידה."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_3b",
    )

    exhibit_item = _item(report, "exhibit_chain_of_custody")
    assert exhibit_item["status"] == "present"
    assert exhibit_item["evidenceStrength"] == "strong"
    assert exhibit_item["evidence"][0]["source"] == "raw_text"


def test_analyzer_treats_media_exhibit_description_as_digital_evidence():
    schema = _schema()
    raw_text = "אני מציג לך פריט 10, סרטון המתעד את האירוע."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_5",
    )

    digital_item = _item(report, "digital_evidence_outputs")
    assert digital_item["status"] == "present"
    assert digital_item["evidenceStrength"] == "strong"
    assert digital_item["evidence"][0]["matchType"] == "regex"


def test_analyzer_marks_suspect_statement_present_for_warning_statement_form():
    schema = _schema()
    raw_text = "טופס הודעה על זכויות חשוד טרם חקירה נלווה להודעה באזהרה. מוסר ההודעה נוכח. ש. ספר מה קרה. ת. מסרתי גרסה."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_6",
    )

    statement_item = _item(report, "suspect_interrogation_statement")
    assert statement_item["status"] == "present"
    assert statement_item["evidenceStrength"] == "strong"


def test_analyzer_does_not_trigger_witness_testimony_from_generic_involved_person():
    schema = _schema()
    raw_text = "שם מעורב: פלוני. פרטים כלליים בתיק."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_7",
    )

    assert _item(report, "witness_testimony")["status"] == "not_applicable"


def test_analyzer_marks_criminal_record_present_for_printout():
    schema = _schema()
    raw_text = "תדפיס מידע פלילי המידע הופק על ידי היחידה החוקרת."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_record",
    )

    record_item = _item(report, "criminal_or_disciplinary_record")
    assert record_item["status"] == "present"
    assert record_item["evidenceStrength"] == "strong"
    assert record_item["evidence"][0]["matchType"] == "regex"


def test_analyzer_marks_victim_rights_not_applicable_without_trigger():
    schema = _schema()
    raw_text = "הוגשה תלונה ראשונית. סיכום חקירה ורשימת חומר צורפו."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_4",
    )

    assert _item(report, "victim_rights_notification")["status"] == "not_applicable"


def test_summary_splits_hard_critical_missing_from_review_required():
    schema = _schema()
    raw_text = "החשוד נחקר. נתפס טלפון נייד."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_summary_split",
    )

    assert _item(report, "investigation_material_list")["status"] == "missing"
    assert _item(report, "exhibit_chain_of_custody")["status"] == "conditional_missing"
    assert report["summary"]["criticalMissing"] >= 2
    assert report["summary"]["criticalReviewRequired"] >= 1


def test_conditional_items_include_applicability_evidence():
    schema = _schema()
    raw_text = "המתלונן דיווח כי החזיק חומר החשוד כסם."

    report = analyze_protocol_completeness(
        schema_config=schema,
        graph_data={"nodes": [], "edges": []},
        raw_text=raw_text,
        doc_id="case_applicability",
    )

    complainant_item = _item(report, "complainant_or_victim_testimony")
    forensic_item = _item(report, "forensic_or_lab_report")

    assert complainant_item["status"] == "conditional_missing"
    assert complainant_item["applicability"] == "keyword:מתלונן"
    assert complainant_item["applicabilityEvidence"]["signal"] == "מתלונן"
    assert "המתלונן" in complainant_item["applicabilityEvidence"]["snippet"]

    assert forensic_item["status"] == "conditional_missing"
    assert forensic_item["applicability"] == "keyword:סם"
    assert forensic_item["applicabilityEvidence"]["signal"] == "סם"
    assert "סם" in forensic_item["applicabilityEvidence"]["snippet"]
