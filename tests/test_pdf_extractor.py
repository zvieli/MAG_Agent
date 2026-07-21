from pathlib import Path
import pytest
from scripts.extract_protocols_text import format_pdf_content

def test_format_pdf_content():
    original_path = Path("investigation_protocols/1/test_doc.pdf")
    pages_text = ["Hello Page One", "שלוש ארבע חמש", "Page Three Content"]
    timestamp = "2026-07-21 17:00:00"
    
    expected_output = (
        "---\n"
        "original_file: investigation_protocols/1/test_doc.pdf\n"
        "total_pages: 3\n"
        "extracted_at: 2026-07-21 17:00:00\n"
        "---\n\n"
        "## Page 1\n"
        "Hello Page One\n\n"
        "## Page 2\n"
        "שלוש ארבע חמש\n\n"
        "## Page 3\n"
        "Page Three Content\n"
    )
    
    result = format_pdf_content(original_path, pages_text, timestamp)
    assert result == expected_output
