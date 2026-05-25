#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return False

try:
    from mistralai.client import Mistral
except ImportError:
    try:
        from mistralai import Mistral
    except ImportError:
        Mistral = None

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None


DEFAULT_MODEL = "mistral-ocr-latest"


def natural_key(path: Path):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def page_count(pdf: Path) -> int:
    if PdfReader is None:
        raise SystemExit("pypdf is missing. Install OCR dependencies with: python3 -m pip install -r requirements.txt")
    return len(PdfReader(str(pdf)).pages)


def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(total))

    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s), int(end_s)
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))

    invalid = [page for page in pages if page < 0 or page >= total]
    if invalid:
        raise ValueError(f"Page indexes out of range for {total}-page PDF: {invalid[:10]}")

    return sorted(pages)


def ranges(pages: list[int], chunk_pages: int):
    for offset in range(0, len(pages), chunk_pages):
        chunk = pages[offset : offset + chunk_pages]
        start = prev = chunk[0]
        parts = []
        for page in chunk[1:]:
            if page == prev + 1:
                prev = page
                continue
            parts.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = page
        parts.append(f"{start}-{prev}" if start != prev else str(start))
        yield ",".join(parts)


def markdown_document(pdf: Path, pages, model: str, usage_totals: dict[str, int]):
    chunks = [
        "---",
        f'source_pdf: "{pdf.name}"',
        f'ocr_model: "{model}"',
        f"pages_ocrd: {len(pages)}",
    ]

    for key, value in sorted(usage_totals.items()):
        chunks.append(f"{key}: {value}")

    chunks.extend(["---", "", f"# {pdf.stem}", ""])

    for page in sorted(pages, key=lambda p: p["index"]):
        page_num = page["index"] + 1
        markdown = page["markdown"].strip()
        chunks.append(f"<!-- page {page_num} -->")
        chunks.append("")
        chunks.append(markdown)
        chunks.append("")

    return "\n".join(chunks).rstrip() + "\n"


def response_to_dict(response):
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    return json.loads(response.json())


def usage_dict(response_dict: dict) -> dict[str, int]:
    usage = response_dict.get("usage_info") or {}
    return {key: value for key, value in usage.items() if isinstance(value, int)}


def add_usage(total: dict[str, int], usage: dict[str, int]):
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value


def get_signed_url_with_retry(client: Any, file_id: str, timeout_ms: int):
    last_exc = None
    for attempt in range(1, 7):
        try:
            return client.files.get_signed_url(file_id=file_id, expiry=24, timeout_ms=timeout_ms)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt == 6:
                break
            time.sleep(min(2**attempt, 10))
    raise last_exc


def ocr_pdf(client: Any, pdf: Path, out_md: Path, out_json: Path, page_spec: str | None, chunk_pages: int, timeout_ms: int, keep_upload: bool, model: str):
    total_pages = page_count(pdf)
    requested_pages = parse_pages(page_spec, total_pages)
    if not requested_pages:
        raise ValueError("No pages requested")

    print(f"Uploading {pdf.name} ({total_pages} pages)")
    with pdf.open("rb") as handle:
        uploaded = client.files.upload(
            file={
                "file_name": pdf.name,
                "content": handle,
                "content_type": "application/pdf",
            },
            purpose="ocr",
            timeout_ms=timeout_ms,
        )

    all_pages = []
    raw_responses = []
    usage_totals: dict[str, int] = {}
    processed_indexes: set[int] = set()

    try:
        signed_url = get_signed_url_with_retry(client, uploaded.id, timeout_ms)
        document = {
            "type": "document_url",
            "document_url": signed_url.url,
            "document_name": pdf.name,
        }

        chunk_ranges = list(ranges(requested_pages, chunk_pages))
        for chunk_index, chunk_range in enumerate(chunk_ranges, start=1):
            print(f"OCR chunk {chunk_index}/{len(chunk_ranges)} pages {chunk_range}")
            started = time.time()
            response = client.ocr.process(
                model=model,
                document=document,
                pages=chunk_range,
                include_image_base64=False,
                table_format="markdown",
                extract_header=True,
                extract_footer=True,
                timeout_ms=timeout_ms,
            )
            elapsed = time.time() - started
            response_dict = response_to_dict(response)
            raw_responses.append({"pages": chunk_range, "response": response_dict})
            add_usage(usage_totals, usage_dict(response_dict))

            for page in response_dict.get("pages", []):
                processed_indexes.add(page["index"])
                all_pages.append({"index": page["index"], "markdown": page.get("markdown", "")})

            out_md.parent.mkdir(parents=True, exist_ok=True)
            out_json.parent.mkdir(parents=True, exist_ok=True)
            out_md.write_text(markdown_document(pdf, all_pages, model, usage_totals), encoding="utf-8")
            out_json.write_text(
                json.dumps(
                    {
                        "source_pdf": pdf.name,
                        "model": model,
                        "requested_pages": requested_pages,
                        "processed_pages": sorted(processed_indexes),
                        "usage_totals": usage_totals,
                        "chunks": raw_responses,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(f"Chunk done in {elapsed:.1f}s; wrote {out_md}")
    finally:
        if not keep_upload:
            try:
                client.files.delete(file_id=uploaded.id, timeout_ms=timeout_ms)
            except Exception as exc:  # noqa: BLE001
                print(f"Warning: could not delete uploaded file {uploaded.id}: {exc}", file=sys.stderr)

    missing = sorted(set(requested_pages) - processed_indexes)
    if missing:
        raise RuntimeError(f"OCR finished with missing pages: {missing[:20]}")

    print(f"Finished {pdf.name}: {len(processed_indexes)} pages -> {out_md}")


def main():
    parser = argparse.ArgumentParser(description="Run Mistral OCR on PDFs and write page-marked Markdown.")
    parser.add_argument("pdfs", nargs="*", type=Path, help="PDF files. Defaults to all PDFs in cwd.")
    parser.add_argument("--out-dir", type=Path, default=Path("mistral_ocr_md"))
    parser.add_argument("--raw-dir", type=Path, default=Path("mistral_ocr_md/raw"))
    parser.add_argument("--pages", help="Zero-based page indexes/ranges, e.g. '10' or '0-9,20'.")
    parser.add_argument("--chunk-pages", type=int, default=100)
    parser.add_argument("--timeout-ms", type=int, default=600_000)
    parser.add_argument("--keep-upload", action="store_true")
    args = parser.parse_args()

    load_dotenv()
    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        raise SystemExit("MISTRAL_API_KEY is missing. Put it in .env or the environment.")
    if Mistral is None:
        raise SystemExit("mistralai is missing. Install OCR dependencies with: python3 -m pip install -r requirements.txt")
    model = os.environ.get("MISTRAL_OCR_MODEL", DEFAULT_MODEL)

    pdfs = args.pdfs or sorted(Path.cwd().glob("*.pdf"), key=natural_key)
    if not pdfs:
        raise SystemExit("No PDFs found.")

    client = Mistral(api_key=api_key, timeout_ms=args.timeout_ms)

    for pdf in pdfs:
        suffix = "_pages_" + args.pages.replace(",", "_").replace("-", "-") if args.pages else ""
        out_md = args.out_dir / f"{pdf.stem}{suffix}.md"
        out_json = args.raw_dir / f"{pdf.stem}{suffix}.json"
        ocr_pdf(client, pdf, out_md, out_json, args.pages, args.chunk_pages, args.timeout_ms, args.keep_upload, model)


if __name__ == "__main__":
    main()
