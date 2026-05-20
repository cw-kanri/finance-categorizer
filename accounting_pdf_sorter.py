import argparse
import csv
import importlib.util
import json
import logging
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from pdf_text_extractor import (
    extract_bank_transfer_details,
    extract_text_with_ocr,
    extract_text_with_pdfplumber,
    normalize_digits,
)


logger = logging.getLogger(__name__)

DEFAULT_INPUT_DIR = Path("test_materials") / "input"
DEFAULT_OUTPUT_DIR = Path("test_materials") / "output"
ATTACHMENT_DIR_NAME = "attachments"
REQUIRED_PYTHON_MODULES = ["pdfplumber", "pytesseract", "pdf2image", "PIL", "pypdf"]
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


DOCUMENT_RULES = [
    ("bank_transfer", ["振込日", "振込金額", "振込先", "受取人名", "振込受付", "振込明細"]),
    ("invoice", ["請求書", "御請求書", "請求金額", "お支払期限", "インボイス", "Invoice number"]),
    ("receipt", ["領収書", "領収証", "レシート", "但し", "Receipt", "Amount paid"]),
    ("expense_claim", ["経費申請", "立替経費", "外注費", "出張宿泊費", "旅費交通費"]),
    ("credit_card_statement", ["クレジットカード", "カード利用", "ご利用明細", "楽天カード", "ご利用代金請求明細書"]),
    ("payment_notice", ["検収通知書", "支払通知書", "入金予定", "検収", "支払通知"]),
    ("quote", ["見積書", "御見積書", "見積金額"]),
    ("purchase_order", ["注文書", "発注書", "注文番号"]),
    ("statement", ["利用明細", "取引明細", "ご利用明細", "入出金明細", "通帳写し"]),
    ("tax", ["適格請求書", "登録番号", "消費税", "税率"]),
    ("payroll", ["給与", "賃金", "源泉所得税", "社会保険料"]),
    ("contract", ["契約書", "業務委託契約", "秘密保持契約"]),
]


DATE_LABELS = [
    "振込日",
    "発行日",
    "請求日",
    "領収日",
    "取引日",
    "支払日",
    "ご利用日",
    "作成日",
]


PAYEE_LABELS = [
    "支払先",
    "振込先",
    "振込先名",
    "受取人",
    "受取人名",
    "宛先",
    "取引先",
    "請求元",
    "発行元",
]


AMOUNT_LABELS = [
    "振込金額",
    "請求金額",
    "合計金額",
    "税込金額",
    "領収金額",
    "お支払金額",
    "金額",
    "Amount paid",
    "Total",
]


@dataclass
class PreparedAttachment:
    source: str
    destination: str
    document_type: str
    new_name: str
    confidence: float
    page: int | None
    page_count: int | None
    extracted: dict[str, str | None]
    journal_hint: dict[str, str | None]


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


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def sanitize_filename_part(value: str | None, fallback: str = "unknown", max_length: int = 40) -> str:
    if not value:
        return fallback

    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    sanitized = re.sub(r"\s+", "_", sanitized).strip("._ ")
    return (sanitized or fallback)[:max_length]


def clean_amount(value: str | None) -> str | None:
    if not value:
        return None

    match = re.search(r"[\d,]+", normalize_digits(value))
    if not match:
        return None

    return match.group(0).replace(",", "")


def parse_date(value: str | None) -> str | None:
    if not value:
        return None

    normalized = normalize_digits(value)
    english_match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
        normalized,
        flags=re.IGNORECASE,
    )
    if english_match:
        month_name, day, year = english_match.groups()
        try:
            return datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            return None

    patterns = [
        (r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", "%Y-%m-%d"),
        (r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", "%Y-%m-%d"),
        (r"令和\s*(\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日", "reiwa"),
        (r"(\d{1,2})月\s*(\d{1,2})日", None),
        (r"(\d{1,2})[/-](\d{1,2})", None),
    ]

    for pattern, output_format in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue

        if output_format == "reiwa":
            era_year, month, day = match.groups()
            year = str(2018 + int(era_year))
        elif output_format:
            year, month, day = match.groups()
        else:
            year = str(datetime.now().year)
            month, day = match.groups()

        try:
            return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def find_english_date(text: str) -> str | None:
    match = re.search(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),\s*(\d{4})",
        normalize_digits(text),
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    month_name, day, year = match.groups()
    try:
        return datetime.strptime(f"{month_name} {day} {year}", "%B %d %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def date_from_filename(path: Path) -> str | None:
    name = normalize_digits(path.stem)
    patterns = [
        (r"(20\d{2})(\d{2})(\d{2})", True),
        (r"(20\d{2})[-_/年](\d{1,2})[-_/月](\d{1,2})", True),
        (r"(?<!\d)(\d{2})(\d{2})(?:発行|利用|分|_|$)", False),
        (r"(?<!\d)(\d{1,2})月(\d{1,2})日", False),
    ]

    for pattern, has_year in patterns:
        match = re.search(pattern, name)
        if not match:
            continue

        if has_year:
            year, month, day = match.groups()
        else:
            year = str(datetime.now().year)
            month, day = match.groups()

        try:
            return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            continue

    month_match = re.search(r"(?<!\d)(\d{1,2})月分", name)
    if month_match:
        month = int(month_match.group(1))
        if 1 <= month <= 12:
            return f"{datetime.now().year}-{month:02d}"

    return None


def amount_from_filename(path: Path) -> str | None:
    match = re.search(r"([\d,]+)\s*円", normalize_digits(path.stem))
    if not match:
        return None
    return match.group(1).replace(",", "")


def payee_from_filename(path: Path) -> str | None:
    stem = normalize_digits(path.stem)
    stem = re.sub(r"【([^】]*)】", r" \1 ", stem)
    stem = re.sub(r"\(([^)]*)\)", r" \1 ", stem)
    stem = re.sub(r"(20\d{2})[-_/年]?\d{1,2}[-_/月]?\d{1,2}日?", " ", stem)
    stem = re.sub(r"(?<!\d)\d{4}(?:発行|利用|分)?", " ", stem)
    stem = re.sub(r"[\d,]+\s*円", " ", stem)
    stem = re.sub(r"(請求書|兼|差異総括表|検収通知書|支払通知書|領収書|Receipt|発行|利用分|明細|画面|入金予定)", " ", stem, flags=re.IGNORECASE)

    parts = [part.strip(" _-・　") for part in re.split(r"[_\s]+", stem) if part.strip(" _-・　")]
    company_parts = [part for part in parts if "株式会社" in part or "有限会社" in part or "合同会社" in part]
    if company_parts:
        return company_parts[-1]

    generic_parts = {"sample", "original", "receipt", "pj", "no"}
    business_descriptors = {"経理", "通帳写し", "明細", "画面", "外注費申請", "立替経費申請"}
    for part in parts:
        lowered = part.lower()
        if lowered in generic_parts or part in business_descriptors:
            continue
        if re.fullmatch(r"[A-Za-z]*\d+[A-Za-z\d-]*", part):
            continue
        if re.fullmatch(r"[A-Za-z]{1,3}\d*", part):
            continue
        if re.search(r"[ぁ-んァ-ヶ一-龥]", part) or re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]{3,}", part):
            return part
    return None


def is_noisy_payee(value: str | None) -> bool:
    if not value:
        return True
    noisy_terms = ["における", "以下", "合計", "金額", "残高", "請求", "明細", "税込", "税抜"]
    return any(term in value for term in noisy_terms) or len(value) > 50


def find_labeled_value(text: str, labels: list[str], value_pattern: str = r"[^\r\n]+") -> str | None:
    joined_labels = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    pattern = re.compile(rf"(?:{joined_labels})\s*[：:\s]*({value_pattern})", flags=re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return None

    value = re.sub(r"\s+", " ", match.group(1)).strip(" ：:")
    return value or None


def classify_document(text: str) -> tuple[str, float]:
    normalized = normalize_text(text)
    scores: list[tuple[str, int]] = []

    for document_type, keywords in DOCUMENT_RULES:
        score = sum(1 for keyword in keywords if keyword in normalized)
        if score:
            scores.append((document_type, score))

    if not scores:
        return "unknown", 0.0

    document_type, score = max(scores, key=lambda item: item[1])
    max_keywords = len(dict(DOCUMENT_RULES)[document_type])
    confidence = min(1.0, score / max_keywords + 0.2)
    return document_type, round(confidence, 2)


def classify_source_document(text: str, source_path: Path) -> tuple[str, float]:
    path_hint = " ".join(source_path.parts[-4:])
    document_type, confidence = classify_document(f"{path_hint} {text}")
    if document_type == "unknown" and date_from_filename(source_path) and amount_from_filename(source_path):
        return "receipt", 0.2
    return document_type, confidence


def extract_common_details(text: str, source_path: Path) -> dict[str, str | None]:
    bank_details = extract_bank_transfer_details(text)

    date = parse_date(bank_details.get("date"))
    if not date:
        date = parse_date(find_labeled_value(text, DATE_LABELS))
    if not date:
        date = find_english_date(text)
    if not date:
        date = date_from_filename(source_path)

    payee = bank_details.get("payee") or find_labeled_value(text, PAYEE_LABELS)
    if is_noisy_payee(payee):
        payee = payee_from_filename(source_path)

    amount = clean_amount(bank_details.get("amount"))
    if not amount:
        amount = clean_amount(find_labeled_value(text, AMOUNT_LABELS, r"(?:￥|¥|\$)?\s*[\d,]+(?:\.\d+)?(?:\s*円)?"))
    if not amount:
        yen_match = re.search(r"(?:￥|¥)\s*([\d,]+)", normalize_digits(text))
        if yen_match:
            amount = yen_match.group(1).replace(",", "")
    if not amount:
        amount = amount_from_filename(source_path)

    fee = clean_amount(bank_details.get("fee"))

    return {
        "date": date,
        "payee": payee,
        "amount": amount,
        "fee": fee,
    }


def build_journal_hint(document_type: str, details: dict[str, str | None]) -> dict[str, str | None]:
    if document_type == "bank_transfer":
        return {
            "date": details.get("date"),
            "debit_account": None,
            "credit_account": "普通預金",
            "counterparty": details.get("payee"),
            "amount": details.get("amount"),
            "fee": details.get("fee"),
            "memo": "銀行振込明細から抽出。勘定科目は未推測。",
        }

    return {
        "date": details.get("date"),
        "debit_account": None,
        "credit_account": None,
        "counterparty": details.get("payee"),
        "amount": details.get("amount"),
        "fee": details.get("fee"),
        "memo": "勘定科目は未推測。",
    }


def build_new_filename(
    source_path: Path,
    document_type: str,
    details: dict[str, str | None],
    page: int | None = None,
    page_count: int | None = None,
) -> str:
    date_part = sanitize_filename_part(details.get("date"), "date_unknown", 10)
    payee_part = sanitize_filename_part(details.get("payee"), "payee_unknown", 40)
    amount_part = sanitize_filename_part(details.get("amount"), "amount_unknown", 20)
    type_part = sanitize_filename_part(document_type, "unknown", 30)
    original_id = sanitize_filename_part(source_path.stem, "original", 30)
    page_part = f"_p{page:02d}" if page and page_count and page_count > 1 else ""
    return f"{date_part}_{payee_part}_{amount_part}_{type_part}_{original_id}{page_part}{source_path.suffix.lower()}"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise FileExistsError(f"Could not create unique destination for: {path}")


def iter_pdf_files(input_dir: Path, output_dir: Path) -> list[Path]:
    return iter_source_files(input_dir, output_dir)


def iter_source_files(input_dir: Path, output_dir: Path) -> list[Path]:
    files = []
    resolved_output = output_dir.resolve()

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
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


def extract_source_text(source_path: Path, lang: str, dpi: int) -> str:
    if source_path.suffix.lower() == ".pdf":
        text = extract_text_with_pdfplumber(source_path)
        if text:
            logger.info("extracted text with pdfplumber: %s", source_path)
            return text

        if not shutil.which("pdftoppm"):
            logger.warning("PDF OCR skipped because Poppler is not in PATH: %s", source_path)
            return ""
        if not shutil.which("tesseract"):
            logger.warning("PDF OCR skipped because Tesseract is not in PATH: %s", source_path)
            return ""

        try:
            logger.info("no embedded text found; starting OCR: %s", source_path)
            return extract_text_with_ocr(source_path, lang=lang, dpi=dpi)
        except Exception as exc:
            logger.warning("PDF OCR failed; continuing with filename hints: %s (%s)", source_path, exc)
            return ""

    try:
        import pytesseract
        from PIL import Image

        if not shutil.which("tesseract"):
            logger.warning("image OCR skipped because Tesseract is not in PATH: %s", source_path)
            return ""

        with Image.open(source_path) as image:
            return pytesseract.image_to_string(image, lang=lang).strip()
    except Exception as exc:
        logger.warning("image OCR failed; continuing with filename hints: %s (%s)", source_path, exc)
        return ""


def get_pdf_page_count(pdf_path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        logger.exception("failed to read page count: %s", pdf_path)
        return None


def plan_source_preparation(
    source_path: Path,
    output_dir: Path,
    lang: str,
    dpi: int,
) -> PreparedAttachment:
    logger.info("processing source: %s", source_path)
    try:
        text = extract_source_text(source_path, lang=lang, dpi=dpi)
    except Exception:
        logger.exception("text extraction failed; continuing with filename hints: %s", source_path)
        text = ""

    document_type, confidence = classify_source_document(text, source_path)
    details = extract_common_details(text, source_path)
    page_count = get_pdf_page_count(source_path) if source_path.suffix.lower() == ".pdf" else 1
    new_name = build_new_filename(source_path, document_type, details, page_count=page_count)
    destination_dir = output_dir / ATTACHMENT_DIR_NAME
    destination = unique_destination(destination_dir / new_name)

    return PreparedAttachment(
        source=str(source_path),
        destination=str(destination),
        document_type=document_type,
        new_name=destination.name,
        confidence=confidence,
        page=None,
        page_count=page_count,
        extracted=details,
        journal_hint=build_journal_hint(document_type, details),
    )


def plan_pdf_preparation(pdf_path: Path, output_dir: Path, lang: str, dpi: int) -> PreparedAttachment:
    return plan_source_preparation(pdf_path, output_dir, lang, dpi)


def expand_pages(plan: PreparedAttachment, split_pages: bool) -> list[PreparedAttachment]:
    if Path(plan.source).suffix.lower() != ".pdf":
        return [plan]

    page_count = plan.page_count or 1
    if not split_pages or page_count <= 1:
        return [plan]

    source = Path(plan.source)
    destination = Path(plan.destination)
    expanded = []
    for page in range(1, page_count + 1):
        new_name = build_new_filename(
            source,
            plan.document_type,
            plan.extracted,
            page=page,
            page_count=page_count,
        )
        page_destination = unique_destination(destination.parent / new_name)
        expanded.append(
            PreparedAttachment(
                source=plan.source,
                destination=str(page_destination),
                document_type=plan.document_type,
                new_name=page_destination.name,
                confidence=plan.confidence,
                page=page,
                page_count=page_count,
                extracted=plan.extracted,
                journal_hint=plan.journal_hint,
            )
        )
    return expanded


def execute_plan(plan: PreparedAttachment, dry_run: bool) -> None:
    if dry_run:
        return

    source = Path(plan.source)
    destination = Path(plan.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if source.suffix.lower() == ".pdf" and plan.page and plan.page_count and plan.page_count > 1:
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(source))
        writer = PdfWriter()
        writer.add_page(reader.pages[plan.page - 1])
        with destination.open("wb") as output_file:
            writer.write(output_file)
        return

    from shutil import copy2

    copy2(source, destination)


def write_json_manifest(output_dir: Path, plans: list[PreparedAttachment], dry_run: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / ("attachment_index_dry_run.json" if dry_run else "attachment_index.json")
    payload = {
        "dry_run": dry_run,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_output_dir": str(output_dir),
        "count": len(plans),
        "results": [asdict(plan) for plan in plans],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def write_csv_index(output_dir: Path, plans: list[PreparedAttachment], dry_run: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / ("attachment_index_dry_run.csv" if dry_run else "attachment_index.csv")
    fieldnames = [
        "source_file",
        "output_file",
        "document_type",
        "date",
        "counterparty",
        "amount",
        "fee",
        "page",
        "page_count",
        "confidence",
        "run_output_dir",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for plan in plans:
            writer.writerow(
                {
                    "source_file": plan.source,
                    "output_file": plan.destination,
                    "document_type": plan.document_type,
                    "date": plan.extracted.get("date"),
                    "counterparty": plan.extracted.get("payee"),
                    "amount": plan.extracted.get("amount"),
                    "fee": plan.extracted.get("fee"),
                    "page": plan.page,
                    "page_count": plan.page_count,
                    "confidence": plan.confidence,
                    "run_output_dir": str(output_dir),
                }
            )
    return csv_path


def find_missing_python_modules() -> list[str]:
    return [module for module in REQUIRED_PYTHON_MODULES if importlib.util.find_spec(module) is None]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read accounting PDFs and prepare renamed attachment files plus an index.")
    parser.add_argument(
        "input_dir",
        nargs="?",
        default=str(DEFAULT_INPUT_DIR),
        help=f"Folder containing PDF files. Default: {DEFAULT_INPUT_DIR}",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Destination root folder. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only write the index without copying or splitting files")
    parser.add_argument("--no-split-pages", action="store_true", help="Keep multi-page PDFs as one file")
    parser.add_argument("--run-name", help="Output subfolder name. Default: current timestamp")
    parser.add_argument("--lang", default="jpn+eng", help="Tesseract OCR language")
    parser.add_argument("--dpi", type=int, default=300, help="DPI for OCR conversion")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    args = parser.parse_args()

    setup_logging(args.verbose)

    missing_modules = find_missing_python_modules()
    if missing_modules:
        logger.error(
            "missing Python module(s): %s. Install dependencies with: uv pip install -r requirements.txt",
            ", ".join(missing_modules),
        )
        return 1

    input_dir = Path(args.input_dir).resolve()
    input_dir.mkdir(parents=True, exist_ok=True)
    if not input_dir.is_dir():
        logger.error("input folder not found: %s", input_dir)
        return 1

    output_root = Path(args.output_dir).resolve()
    output_dir = create_run_output_dir(output_root, args.run_name)
    source_files = iter_source_files(input_dir, output_root)
    logger.info("found %s source file(s)", len(source_files))

    source_plans = [plan_source_preparation(source_file, output_dir, args.lang, args.dpi) for source_file in source_files]
    plans = [page_plan for plan in source_plans for page_plan in expand_pages(plan, not args.no_split_pages)]

    try:
        for plan in plans:
            execute_plan(plan, args.dry_run)
        json_manifest_path = write_json_manifest(output_dir, plans, args.dry_run)
        csv_index_path = write_csv_index(output_dir, plans, args.dry_run)
    except Exception:
        logger.exception("failed to prepare PDF attachments")
        return 1

    print(
        json.dumps(
            {
                "dry_run": args.dry_run,
                "count": len(plans),
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
