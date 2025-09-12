import sys, json
from pathlib import Path
from app.gpt_extractor import extract_offer_from_pdf_bytes, ExtractionError

def main():
  if len(sys.argv) < 2:
    print("Usage: python run_all_pdfs.py <folder-with-pdfs>")
    sys.exit(1)
  folder = Path(sys.argv[1]).expanduser().resolve()
  if not folder.exists() or not folder.is_dir():
    print(f"Folder not found: {folder}")
    sys.exit(1)

  out_dir = folder / "json_out"
  out_dir.mkdir(exist_ok=True)

  pdfs = sorted(folder.glob("*.pdf"))
  if not pdfs:
    print(f"No PDFs in {folder}")
    sys.exit(0)

  for pdf_path in pdfs:
    print(f"[+] Processing {pdf_path.name} ...")
    try:
      data = pdf_path.read_bytes()
      payload = extract_offer_from_pdf_bytes(data, document_id=pdf_path.name)
      out_path = out_dir / (pdf_path.stem + ".json")
      out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
      print(f"    -> {out_path}")
    except ExtractionError as e:
      print(f"    [WARN] {pdf_path.name}: {e}")
    except Exception as e:
      print(f"    [ERROR] {pdf_path.name}: {e}")

if __name__ == "__main__":
  main()
