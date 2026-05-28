import argparse
import csv
import importlib.util
import json
import logging
import os
import re
import sys
import time
import unicodedata
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

DEFAULT_INPUT_DIR = Path("test_materials") / "input"
DEFAULT_OUTPUT_DIR = Path("test_materials") / "output"
ATTACHMENT_DIR_NAME = "statements"
REQUIRED_PYTHON_MODULES = ["pdfplumber", "pypdf"]
AZURE_API_VERSION = "2024-11-30"

FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")
ACCOUNT_TYPES = {"ﾌ", "ト", "ﾄ", "チ", "ﾁ", "ソ", "ｿ"}


@dataclass
class PageText:
    text: str
    extractor: str


@dataclass
class ShichijushichiRecord:
    source: str
    destination: str
    new_name: str
    transfer_date: str | None
    recipient: str | None
    amount: str | None
    fee: str | None
    statement_type: str
    page: int
    page_count: int
    extractor: str


def setup_logging(verbose: bool = False) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("pdfminer").setLevel(logging.ERROR)


def normalize_digits(value: str) -> str:
    return value.translate(FULLWIDTH_DIGIT_TRANSLATION)


def normalize_recipient(value: str | None) -> str | None:
    if not value:
        return None
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("-", "ー")
    normalized = normalized.replace("(", "（").replace(")", "）")
    normalized = re.sub(r"\s+", "", normalized).strip()
    return normalized or None


def sanitize_filename_part(value: str | None, fallback: str) -> str:
    part = value or fallback
    part = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", part)
    part = re.sub(r"\s+", "_", part).strip("._ ")
    return part or fallback


def format_date_for_filename(value: str | None) -> str:
    if not value:
        return "date_unknown"
    return value.replace("-", "")


def parse_reiwa_transfer_date(text: str) -> str | None:
    normalized = normalize_digits(text)
    match = re.search(r"令和\s*(\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*振込分", normalized)
    if not match:
        return None

    era_year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(2018 + era_year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def date_from_filename(path: Path) -> str | None:
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", normalize_digits(path.stem))
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_summary_totals(text: str) -> tuple[int | None, str | None, str | None]:
    normalized = normalize_digits(text)
    matches = re.findall(r"合\s*計\s+(\d+)\s+([\d,]+)\s+合\s*計\s+([\d,]+)", normalized)
    if not matches:
        return None, None, None

    count, amount, fee = matches[-1]
    return int(count), amount.replace(",", ""), fee.replace(",", "")


def parse_recipient_rows(text: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for raw_line in normalize_digits(text).splitlines():
        line = raw_line.strip()
        if not line or "合 計" in line or "合計" in line:
            continue
        tokens = line.split()
        account_index = next((index for index, token in enumerate(tokens) if token in ACCOUNT_TYPES), None)
        if account_index is None or account_index + 2 >= len(tokens):
            continue
        if not re.fullmatch(r"\d{5,8}", tokens[account_index + 1]):
            continue

        remainder = tokens[account_index + 2 :]
        for index, token in enumerate(remainder):
            if not re.fullmatch(r"\d+", token):
                continue
            if index + 2 >= len(remainder):
                continue
            if not re.fullmatch(r"\d{4}", remainder[index + 1]):
                continue
            if not re.fullmatch(r"\d{3}", remainder[index + 2]):
                continue

            recipient = normalize_recipient(" ".join(remainder[:index]))
            if recipient:
                rows.append((recipient, token))
            break
    return rows


def classify_statement(text: str, rows: list[tuple[str, str]], total_count: int | None) -> str:
    if "総合振込明細表" in text:
        return "general_transfer"
    if "給与振込明細表" in text and total_count and total_count > 1:
        return "payroll"
    if "給与振込明細表" in text:
        return "reimbursement_or_single_transfer"
    return "unknown"


def extract_record_details(page_text: str, source_path: Path) -> dict[str, str | None]:
    transfer_date = parse_reiwa_transfer_date(page_text) or date_from_filename(source_path)
    total_count, total_amount, total_fee = parse_summary_totals(page_text)
    rows = parse_recipient_rows(page_text)
    statement_type = classify_statement(page_text, rows, total_count)

    if statement_type == "payroll":
        recipient = "給与"
        amount = total_amount
    elif len(rows) == 1:
        recipient, amount = rows[0]
    else:
        recipient = rows[0][0] if rows else None
        amount = total_amount or (rows[0][1] if rows else None)

    return {
        "transfer_date": transfer_date,
        "recipient": recipient,
        "amount": amount,
        "fee": total_fee,
        "statement_type": statement_type,
    }


def build_new_filename(details: dict[str, str | None], suffix: str = ".pdf") -> str:
    date_part = format_date_for_filename(details.get("transfer_date"))
    recipient_part = sanitize_filename_part(details.get("recipient"), "recipient_unknown")
    amount_part = sanitize_filename_part(details.get("amount"), "amount_unknown")
    fee_part = sanitize_filename_part(details.get("fee"), "fee_unknown")
    return f"{date_part}_{recipient_part}_{amount_part}_{fee_part}{suffix.lower()}"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not create unique destination for: {path}")


def iter_pdf_files(input_dir: Path, output_dir: Path) -> list[Path]:
    files = []
    resolved_output = output_dir.resolve()
    for path in sorted(input_dir.rglob("*.pdf")):
        if not path.is_file():
            continue
        resolved_path = path.resolve()
        if resolved_path == resolved_output or resolved_output in resolved_path.parents:
            continue
        files.append(path)
    return files


def create_run_output_dir(output_root: Path, run_name: str | None = None) -> Path:
    base_name = run_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = output_root / base_name
    if not candidate.exists():
        candidate.mkdir(parents=True)
        return candidate
    for index in range(1, 1000):
        indexed_candidate = output_root / f"{base_name}_{index:03d}"
        if not indexed_candidate.exists():
            indexed_candidate.mkdir(parents=True)
            return indexed_candidate
    raise FileExistsError(f"Could not create run output folder under: {output_root}")


def extract_page_texts_with_pdfplumber(pdf_path: Path) -> list[PageText]:
    import pdfplumber

    page_texts: list[PageText] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_texts.append(PageText(text=page.extract_text() or "", extractor="pdfplumber"))
    return page_texts


def content_type_for(path: Path) -> str:
    return "application/pdf" if path.suffix.lower() == ".pdf" else "application/octet-stream"


def parse_azure_layout_result(payload: dict[str, Any]) -> list[PageText]:
    analyze_result = payload.get("analyzeResult") or {}
    pages = analyze_result.get("pages") or []
    page_texts: list[PageText] = []
    for page in pages:
        lines = page.get("lines") or []
        text = "\n".join(str(line.get("content", "")) for line in lines if line.get("content"))
        page_texts.append(PageText(text=text, extractor="azure-document-intelligence"))

    if page_texts:
        return page_texts

    content = analyze_result.get("content") or ""
    return [PageText(text=content, extractor="azure-document-intelligence")] if content else []


def analyze_with_azure_document_intelligence(source_path: Path, timeout_seconds: int = 120) -> list[PageText]:
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "").rstrip("/")
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")
    if not endpoint or not key:
        raise RuntimeError("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY are required")

    analyze_url = f"{endpoint}/documentintelligence/documentModels/prebuilt-layout:analyze?api-version={AZURE_API_VERSION}"
    request = urllib.request.Request(
        analyze_url,
        data=source_path.read_bytes(),
        headers={
            "Content-Type": content_type_for(source_path),
            "Ocp-Apim-Subscription-Key": key,
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        operation_location = response.headers.get("Operation-Location")

    if not operation_location:
        raise RuntimeError("Azure response did not include Operation-Location")

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        poll_request = urllib.request.Request(
            operation_location,
            headers={"Ocp-Apim-Subscription-Key": key},
            method="GET",
        )
        with urllib.request.urlopen(poll_request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))

        status = payload.get("status")
        if status == "succeeded":
            return parse_azure_layout_result(payload)
        if status == "failed":
            raise RuntimeError(json.dumps(payload.get("error", payload), ensure_ascii=False))
        time.sleep(2)

    raise TimeoutError(f"Azure analysis did not finish within {timeout_seconds} seconds")


def extract_page_texts(source_path: Path, extractor: str) -> list[PageText]:
    if extractor in {"azure", "auto"}:
        azure_configured = bool(os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT") and os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY"))
        if extractor == "azure" or azure_configured:
            try:
                logger.info("starting Azure Document Intelligence extraction: %s", source_path)
                return analyze_with_azure_document_intelligence(source_path)
            except Exception as exc:
                if extractor == "azure":
                    raise
                logger.warning("Azure extraction failed; falling back to pdfplumber: %s (%s)", source_path, exc)
    return extract_page_texts_with_pdfplumber(source_path)


def plan_pdf(pdf_path: Path, output_dir: Path, extractor: str) -> list[ShichijushichiRecord]:
    logger.info("processing PDF: %s", pdf_path)
    page_texts = extract_page_texts(pdf_path, extractor)
    page_count = len(page_texts)
    destination_dir = output_dir / ATTACHMENT_DIR_NAME
    records: list[ShichijushichiRecord] = []

    for page_number, page_text in enumerate(page_texts, start=1):
        details = extract_record_details(page_text.text, pdf_path)
        new_name = build_new_filename(details)
        destination = unique_destination(destination_dir / new_name)
        records.append(
            ShichijushichiRecord(
                source=str(pdf_path),
                destination=str(destination),
                new_name=destination.name,
                transfer_date=details.get("transfer_date"),
                recipient=details.get("recipient"),
                amount=details.get("amount"),
                fee=details.get("fee"),
                statement_type=details.get("statement_type") or "unknown",
                page=page_number,
                page_count=page_count,
                extractor=page_text.extractor,
            )
        )
    return records


def execute_record(record: ShichijushichiRecord, dry_run: bool) -> None:
    if dry_run:
        return

    from pypdf import PdfReader, PdfWriter

    source = Path(record.source)
    destination = Path(record.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(source))
    writer = PdfWriter()
    writer.add_page(reader.pages[record.page - 1])
    with destination.open("wb") as output_file:
        writer.write(output_file)


def write_json_manifest(output_dir: Path, records: list[ShichijushichiRecord], dry_run: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / ("shichijushichi_index_dry_run.json" if dry_run else "shichijushichi_index.json")
    payload = {
        "dry_run": dry_run,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_output_dir": str(output_dir),
        "count": len(records),
        "results": [asdict(record) for record in records],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def write_csv_index(output_dir: Path, records: list[ShichijushichiRecord], dry_run: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / ("shichijushichi_index_dry_run.csv" if dry_run else "shichijushichi_index.csv")
    fieldnames = [
        "元ファイル名",
        "新ファイル名",
        "振込日",
        "振込先",
        "振込金額",
        "手数料",
        "明細種別",
        "ページ",
        "総ページ数",
        "抽出方法",
        "元ファイルパス",
        "出力ファイルパス",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "元ファイル名": Path(record.source).name,
                    "新ファイル名": record.new_name,
                    "振込日": record.transfer_date,
                    "振込先": record.recipient,
                    "振込金額": record.amount,
                    "手数料": record.fee,
                    "明細種別": record.statement_type,
                    "ページ": record.page,
                    "総ページ数": record.page_count,
                    "抽出方法": record.extractor,
                    "元ファイルパス": record.source,
                    "出力ファイルパス": record.destination,
                }
            )
    return csv_path


def find_missing_python_modules() -> list[str]:
    return [module for module in REQUIRED_PYTHON_MODULES if importlib.util.find_spec(module) is None]


def main() -> int:
    parser = argparse.ArgumentParser(description="Split and rename 七十七銀行 statement PDFs, then write an Excel-friendly CSV index.")
    parser.add_argument("input_dir", nargs="?", default=str(DEFAULT_INPUT_DIR), help=f"Source PDF folder. Default: {DEFAULT_INPUT_DIR}")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help=f"Destination root. Default: {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--dry-run", action="store_true", help="Write indexes without creating split PDFs")
    parser.add_argument("--run-name", help="Output subfolder name. Default: current timestamp")
    parser.add_argument(
        "--extractor",
        choices=["auto", "local", "azure"],
        default="auto",
        help="Extraction engine. auto uses Azure when credentials are set, otherwise pdfplumber.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    args = parser.parse_args()

    setup_logging(args.verbose)

    missing_modules = find_missing_python_modules()
    if missing_modules:
        logger.error("missing Python module(s): %s. Install dependencies with: uv pip install -r requirements.txt", ", ".join(missing_modules))
        return 1

    input_dir = Path(args.input_dir).resolve()
    input_dir.mkdir(parents=True, exist_ok=True)
    output_root = Path(args.output_dir).resolve()
    output_dir = create_run_output_dir(output_root, args.run_name)

    pdf_files = iter_pdf_files(input_dir, output_root)
    logger.info("found %s PDF file(s)", len(pdf_files))
    records = [record for pdf_file in pdf_files for record in plan_pdf(pdf_file, output_dir, args.extractor)]

    try:
        for record in records:
            execute_record(record, args.dry_run)
        json_manifest_path = write_json_manifest(output_dir, records, args.dry_run)
        csv_index_path = write_csv_index(output_dir, records, args.dry_run)
    except Exception:
        logger.exception("failed to prepare 七十七銀行 statements")
        return 1

    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "count": len(records),
                "run_output_dir": str(output_dir),
                "json_index": str(json_manifest_path),
                "csv_index": str(csv_index_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
