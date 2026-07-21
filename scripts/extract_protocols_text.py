from pathlib import Path

def format_pdf_content(original_path: Path, pages_text: list[str], timestamp: str) -> str:
    lines = [
        "---",
        f"original_file: {original_path.as_posix()}",
        f"total_pages: {len(pages_text)}",
        f"extracted_at: {timestamp}",
        "---",
        ""
    ]
    for i, page_text in enumerate(pages_text, 1):
        lines.append(f"## Page {i}")
        lines.append(page_text.strip())
        lines.append("")
    return "\n".join(lines)
