import sys
from datetime import datetime
from pathlib import Path
import pypdf

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

def extract_text_from_pdf(pdf_path: Path) -> list[str]:
    pages_text = []
    reader = pypdf.PdfReader(pdf_path)
    for page in reader.pages:
        text = page.extract_text() or ""
        pages_text.append(text)
    return pages_text

def process_directory(base_dir: Path):
    pdf_files = list(base_dir.rglob("*.pdf")) + list(base_dir.rglob("*.PDF"))
    pdf_files = sorted(list(set(pdf_files))) # Unique & sorted
    
    print(f"Found {len(pdf_files)} PDF files in {base_dir}")
    
    success_count = 0
    error_count = 0
    
    for idx, pdf_path in enumerate(pdf_files, 1):
        relative_path = pdf_path.relative_to(base_dir.parent)
        print(f"[{idx}/{len(pdf_files)}] Processing {relative_path}...", end="", flush=True)
        
        try:
            pages_text = extract_text_from_pdf(pdf_path)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            md_content = format_pdf_content(relative_path, pages_text, timestamp)
            
            md_path = pdf_path.with_suffix(".md")
            md_path.write_text(md_content, encoding="utf-8")
            
            print(f" Done ({len(pages_text)} pages)")
            success_count += 1
        except Exception as e:
            print(f" ERROR: {str(e)}")
            error_count += 1
            
    print(f"\nProcessing complete: {success_count} succeeded, {error_count} failed.")

if __name__ == "__main__":
    # Always target investigation_protocols directory by default
    target_dir = Path(__file__).resolve().parent.parent / "investigation_protocols"
    if not target_dir.exists():
        print(f"Error: Target directory {target_dir} does not exist.")
        sys.exit(1)
    process_directory(target_dir)

