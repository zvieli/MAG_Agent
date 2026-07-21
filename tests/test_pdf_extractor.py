from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from scripts.extract_protocols_text import format_pdf_content, extract_text_from_pdf, process_directory

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

@patch("scripts.extract_protocols_text.pypdf.PdfReader")
def test_extract_text_from_pdf(mock_pdf_reader):
    mock_page1 = MagicMock()
    mock_page1.extract_text.return_value = "Page 1 content"
    mock_page2 = MagicMock()
    mock_page2.extract_text.return_value = None  # None should fallback to empty string

    mock_reader = MagicMock()
    mock_reader.pages = [mock_page1, mock_page2]
    mock_pdf_reader.return_value = mock_reader

    result = extract_text_from_pdf(Path("dummy.pdf"))
    assert result == ["Page 1 content", ""]

@patch("scripts.extract_protocols_text.extract_text_from_pdf")
def test_process_directory(mock_extract, tmp_path):
    # Setup temporary directory structure
    sub_dir = tmp_path / "investigation_protocols" / "1"
    sub_dir.mkdir(parents=True)
    pdf_file = sub_dir / "sample.pdf"
    pdf_file.write_bytes(b"%PDF-mock")

    mock_extract.return_value = ["Sample text"]

    process_directory(sub_dir)

    md_file = sub_dir / "sample.md"
    assert md_file.exists()
    content = md_file.read_text(encoding="utf-8")
    assert "original_file: 1/sample.pdf" in content
    assert "## Page 1" in content
    assert "Sample text" in content

