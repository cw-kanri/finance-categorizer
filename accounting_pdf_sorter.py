import argparse
import csv
import importlib.util
import json
import logging
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

DEFAULT_INPUT_DIR = Path("test_materials") / "input"
DEFAULT_OUTPUT_DIR = Path("test_materials") / "output"
ATTACHMENT_DIR_NAME = "attachments"
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}
REQUIRED_PYTHON_MODULES = ["pdfplumber", "pypdf"]
LOCAL_OCR_MODULES = ["pytesseract", "pdf2image", "PIL"]
AZURE_API_VERSION = "2024-11-30"

EMPTY_DETAILS = {"date": None, "payee": None, "amount": None, "fee": None}
FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")

DOCUMENT_RULES = [
    ("invoice", ["請求書", "御請求書", "請求金額", "インボイス", "Invoice", "Invoice Number", "Invoice Total"]),
    ("receipt", ["領収書", "領収証", "レシート", "Receipt", "Amount paid"]),
    ("bank_transfer", ["振込日", "振込金額", "振込先", "受取人名", "振込明細", "振込受付"]),
    ("credit_card_statement", ["クレジットカード", "カード利用", "ご利用明細", "楽天カード", "利用代金明細"]),
    ("payment_notice", ["支払通知書", "入金予定", "支払予定", "Payment Notice"]),
    ("expense_claim", ["経費申請", "立替経費", "出張旅費", "交通費"]),
    ("quote", ["見積書", "御見積書", "見積金額", "Quotation"]),
    ("purchase_order", ["注文書", "発注書", "注文番号", "Purchase Order"]),
    ("statement", ["利用明細", "取引明細", "入出金明細", "通帳", "Statement"]),
    ("tax", ["適格請求書", "登録番号", "消費税", "税率"]),
    ("payroll", ["給与", "賞与", "源泉所得税", "社会保険料"]),
    ("contract", ["契約書", "業務委託契約", "秘密保持契約"]),
]

DATE_LABELS = [
    "請求日",
    "発行日",
    "日付",
    "領収日",
    "取引日",
    "支払日",
    "振込日",
    "ご利用日",
    "Invoice Date",
    "Due Date",
]

PAYEE_LABELS = [
    "請求元",
    "発行元",
    "支払先",
    "振込先",
    "振込先名",
    "受取人",
    "受取人名",
    "取引先",
    "宛先",
    "Vendor",
    "Supplier",
]

AMOUNT_LABELS = [
    "請求金額",
    "合計金額",
    "ご請求額",
    "税込金額",
    "領収金額",
    "振込金額",
    "金額",
    "Invoice Total",
    "Amount Due",
    "Amount paid",
    "Total",
]


@dataclass
class TextExtractionResult:
    text: str = ""
    extractor: str = "local"
    cloud_details: dict[str, str | None] = field(default_factory=lambda: EMPTY_DETAILS.copy())
    cloud_document_type: str | None = None
    cloud_confidence: float | None = None


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
    extractor: str = "local"


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


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", normalize_digits(text)).strip()


def sanitize_filename_part(value: str | None, fallback: str = "unknown", max_length: int = 40) -> str:
    if not value:
        return fallback

    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    sanitized = re.sub(r"\s+", "_", sanitized).strip("._ ")
    return (sanitized or fallback)[:max_length]


def clean_amount(value: str | None) -> str | None:
    if not value:
        return None

    normalized = normalize_digits(str(value))
    match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", normalized)
    if not match:
        return None

    amount = match.group(0).replace(",", "")
    if amount.endswith(".0"):
        amount = amount[:-2]
    return amount


def parse_date(value: str | None) -> str | None:
    if not value:
        return None

    normalized = normalize_digits(str(value))
    normalized = normalized.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = re.sub(r"\s+", " ", normalized)

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

    reiwa_match = re.search(r"令和\s*(\d{1,2})\s*[-/]\s*(\d{1,2})\s*[-/]\s*(\d{1,2})", normalized)
    if reiwa_match:
        era_year, month, day = reiwa_match.groups()
        try:
            return datetime(2018 + int(era_year), int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    patterns = [
        r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})",
        r"(\d{4})\s+(\d{1,2})\s+(\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        year, month, day = match.groups()
        try:
            return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    month_day_match = re.search(r"(?<!\d)(\d{1,2})[-/](\d{1,2})(?!\d)", normalized)
    if month_day_match:
        month, day = month_day_match.groups()
        try:
            return datetime(datetime.now().year, int(month), int(day)).strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def date_from_filename(path: Path) -> str | None:
    name = normalize_digits(path.stem)
    patterns = [
        (r"(20\d{2})(\d{2})(\d{2})", True),
        (r"(20\d{2})[-_/年](\d{1,2})[-_/月](\d{1,2})", True),
        (r"(?<!\d)(\d{2})(\d{2})(?:発行|利用|_|$)", False),
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
    stem = re.sub(r"(?<!\d)\d{4}(?:発行|利用|分|_|$)", " ", stem)
    stem = re.sub(r"[\d,]+\s*円", " ", stem)
    stem = re.sub(
        r"(請求書|領収書|領収証|レシート|明細|支払通知書|入金予定|経費申請|振込|invoice|receipt)",
        " ",
        stem,
        flags=re.IGNORECASE,
    )
    parts = [part.strip(" _-・　") for part in re.split(r"[_\s]+", stem) if part.strip(" _-・　")]
    generic_parts = {"sample", "original", "receipt", "invoice", "pj", "no"}

    company_parts = [part for part in parts if any(token in part for token in ["株式会社", "有限会社", "合同会社", "Inc", "LLC"])]
    if company_parts:
        return company_parts[-1]

    for part in parts:
        lowered = part.lower()
        if lowered in generic_parts:
            continue
        if re.fullmatch(r"[A-Za-z]*\d+[A-Za-z\d-]*", part):
            continue
        if len(part) >= 2:
            return part
    return None


def is_noisy_payee(value: str | None) -> bool:
    if not value:
        return True
    noisy_terms = ["合計", "金額", "請求", "明細", "税込", "税率", "以下", "ページ"]
    return any(term in value for term in noisy_terms) or len(value) > 50


def clean_extracted_value(value: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", value).strip(" ：:・\t")
    return cleaned or None


def find_labeled_value(text: str, labels: list[str], value_pattern: str = r"[^\r\n]+") -> str | None:
    joined_labels = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    pattern = re.compile(rf"(?:{joined_labels})\s*[：:・\s]*({value_pattern})", flags=re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return None
    return clean_extracted_value(match.group(1))


def extract_transfer_summary_totals(text: str) -> tuple[str | None, str | None]:
    total_amount = 0
    total_fee = 0

    for line in normalize_digits(text).splitlines():
        normalized_line = re.sub(r"\s+", " ", line).strip()
        match = re.search(r"(?:本支店宛|他行宛)\s+(\d+)件?\s+([\d,]+)\s+([\d,]+)", normalized_line)
        if not match:
            continue
        count, amount, fee = match.groups()
        if int(count) == 0:
            continue
        total_amount += int(amount.replace(",", ""))
        total_fee += int(fee.replace(",", ""))

    return str(total_amount) if total_amount else None, str(total_fee) if total_fee else None


def extract_bank_transfer_details(text: str) -> dict[str, str | None]:
    total_amount, total_fee = extract_transfer_summary_totals(text)
    return {
        "date": find_labeled_value(
            text,
            ["振込日"],
            r"(?:\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?|\d{1,2}[月/-]\d{1,2}日?)",
        ),
        "payee": find_labeled_value(text, ["支払先", "振込先", "振込先名", "受取人", "受取人名"], r"[^\r\n]+"),
        "amount": find_labeled_value(text, ["振込金額"], r"(?:¥|￥)?\s*[\d,]+(?:\s*円)?") or total_amount,
        "fee": find_labeled_value(text, ["手数料", "振込手数料"], r"(?:¥|￥)?\s*[\d,]+(?:\s*円)?") or total_fee,
    }


def classify_document(text: str) -> tuple[str, float]:
    normalized = normalize_text(text)
    scores: list[tuple[str, int]] = []

    for document_type, keywords in DOCUMENT_RULES:
        score = sum(1 for keyword in keywords if keyword.lower() in normalized.lower())
        if score:
            scores.append((document_type, score))

    if not scores:
        return "unknown", 0.0

    document_type, score = max(scores, key=lambda item: item[1])
    max_keywords = len(dict(DOCUMENT_RULES)[document_type])
    confidence = min(1.0, score / max_keywords + 0.2)
    return document_type, round(confidence, 2)


def classify_source_document(text: str, source_path: Path, cloud_type: str | None = None, cloud_confidence: float | None = None) -> tuple[str, float]:
    if cloud_type:
        return cloud_type, round(cloud_confidence or 0.9, 2)

    path_hint = " ".join(source_path.parts[-4:])
    document_type, confidence = classify_document(f"{path_hint} {text}")
    if document_type == "unknown" and date_from_filename(source_path) and amount_from_filename(source_path):
        return "receipt", 0.2
    return document_type, confidence


def merge_details(primary: dict[str, str | None], fallback: dict[str, str | None]) -> dict[str, str | None]:
    return {key: primary.get(key) or fallback.get(key) for key in EMPTY_DETAILS}


def extract_common_details(text: str, source_path: Path, cloud_details: dict[str, str | None] | None = None) -> dict[str, str | None]:
    bank_details = extract_bank_transfer_details(text)

    date = parse_date(bank_details.get("date"))
    if not date:
        date = parse_date(find_labeled_value(text, DATE_LABELS))
    if not date:
        date = date_from_filename(source_path)

    payee = bank_details.get("payee") or find_labeled_value(text, PAYEE_LABELS)
    if is_noisy_payee(payee):
        payee = payee_from_filename(source_path)

    amount = clean_amount(bank_details.get("amount"))
    if not amount:
        amount = clean_amount(find_labeled_value(text, AMOUNT_LABELS, r"(?:¥|￥|\$)?\s*[\d,]+(?:\.\d+)?(?:\s*円)?"))
    if not amount:
        yen_match = re.search(r"(?:¥|￥)\s*([\d,]+)", normalize_digits(text))
        if yen_match:
            amount = yen_match.group(1).replace(",", "")
    if not amount:
        amount = amount_from_filename(source_path)

    details = {
        "date": date,
        "payee": payee,
        "amount": amount,
        "fee": clean_amount(bank_details.get("fee")),
    }
    if cloud_details:
        return merge_details(cloud_details, details)
    return details


def build_journal_hint(document_type: str, details: dict[str, str | None]) -> dict[str, str | None]:
    memo = "クラウド/ローカル抽出結果。勘定科目は人が確認してください。"
    if document_type == "bank_transfer":
        memo = "銀行振込明細から抽出。借方科目は人が確認してください。"

    return {
        "date": details.get("date"),
        "debit_account": None,
        "credit_account": "普通預金" if document_type == "bank_transfer" else None,
        "counterparty": details.get("payee"),
        "amount": details.get("amount"),
        "fee": details.get("fee"),
        "memo": memo,
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


def iter_source_files(input_dir: Path, output_dir: Path) -> list[Path]:
    files = []
    resolved_output = output_dir.resolve()

    for path in sorted(input_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        resolved_path = path.resolve()
        if resolved_path == resolved_output or resolved_output in resolved_path.parents:
            continue
        files.append(path)

    return files


def iter_pdf_files(input_dir: Path, output_dir: Path) -> list[Path]:
    return [path for path in iter_source_files(input_dir, output_dir) if path.suffix.lower() == ".pdf"]


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


def extract_text_with_pdfplumber(pdf_path: Path) -> str:
    import pdfplumber

    page_texts: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                page_texts.append(page.extract_text() or "")
            except Exception:
                logger.exception("pdfplumber extraction failed on page %s", page_number)
                page_texts.append("")

    return "\n".join(text for text in page_texts if text).strip()


def extract_text_with_ocr(pdf_path: Path, lang: str, dpi: int) -> str:
    import pytesseract
    from pdf2image import convert_from_path

    page_texts: list[str] = []
    images = convert_from_path(str(pdf_path), dpi=dpi)
    for page_number, image in enumerate(images, start=1):
        try:
            page_texts.append(pytesseract.image_to_string(image, lang=lang).strip())
        except Exception:
            logger.exception("OCR failed on page %s", page_number)
            page_texts.append("")

    return "\n".join(text for text in page_texts if text).strip()


def extract_local_text(source_path: Path, lang: str, dpi: int) -> TextExtractionResult:
    if source_path.suffix.lower() == ".pdf":
        text = extract_text_with_pdfplumber(source_path)
        if text:
            logger.info("extracted text with pdfplumber: %s", source_path)
            return TextExtractionResult(text=text, extractor="pdfplumber")

        if not shutil.which("pdftoppm"):
            logger.warning("PDF OCR skipped because Poppler is not in PATH: %s", source_path)
            return TextExtractionResult(extractor="local-no-poppler")
        if not shutil.which("tesseract"):
            logger.warning("PDF OCR skipped because Tesseract is not in PATH: %s", source_path)
            return TextExtractionResult(extractor="local-no-tesseract")

        logger.info("no embedded text found; starting local OCR: %s", source_path)
        return TextExtractionResult(text=extract_text_with_ocr(source_path, lang=lang, dpi=dpi), extractor="tesseract")

    import pytesseract
    from PIL import Image

    if not shutil.which("tesseract"):
        logger.warning("image OCR skipped because Tesseract is not in PATH: %s", source_path)
        return TextExtractionResult(extractor="local-no-tesseract")

    with Image.open(source_path) as image:
        return TextExtractionResult(text=pytesseract.image_to_string(image, lang=lang).strip(), extractor="tesseract")


def content_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".bmp": "image/bmp",
    }.get(suffix, "application/octet-stream")


def azure_field_content(fields: dict[str, Any], name: str) -> str | None:
    field_value = fields.get(name)
    if not isinstance(field_value, dict):
        return None
    for key in ["valueString", "valueDate", "valueCurrency", "valueNumber", "content"]:
        value = field_value.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            amount = value.get("amount")
            if amount is not None:
                return str(amount)
        return str(value)
    return None


def parse_azure_invoice_result(payload: dict[str, Any]) -> TextExtractionResult:
    analyze_result = payload.get("analyzeResult") or {}
    documents = analyze_result.get("documents") or []
    invoice = documents[0] if documents else {}
    fields = invoice.get("fields") or {}

    details = {
        "date": parse_date(azure_field_content(fields, "InvoiceDate") or azure_field_content(fields, "DueDate")),
        "payee": azure_field_content(fields, "VendorName") or azure_field_content(fields, "CustomerName"),
        "amount": clean_amount(azure_field_content(fields, "InvoiceTotal") or azure_field_content(fields, "AmountDue")),
        "fee": None,
    }
    confidence = invoice.get("confidence")
    return TextExtractionResult(
        text=analyze_result.get("content") or "",
        extractor="azure-document-intelligence",
        cloud_details=details,
        cloud_document_type="invoice",
        cloud_confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
    )


def analyze_with_azure_document_intelligence(source_path: Path, timeout_seconds: int = 120) -> TextExtractionResult:
    endpoint = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "").rstrip("/")
    key = os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")
    if not endpoint or not key:
        raise RuntimeError("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY are required")

    analyze_url = f"{endpoint}/documentintelligence/documentModels/prebuilt-invoice:analyze?api-version={AZURE_API_VERSION}"
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
            return parse_azure_invoice_result(payload)
        if status == "failed":
            raise RuntimeError(json.dumps(payload.get("error", payload), ensure_ascii=False))
        time.sleep(2)

    raise TimeoutError(f"Azure analysis did not finish within {timeout_seconds} seconds")


def extract_source_text(source_path: Path, extractor: str, lang: str, dpi: int) -> TextExtractionResult:
    if extractor in {"azure", "auto"}:
        azure_configured = bool(os.getenv("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT") and os.getenv("AZURE_DOCUMENT_INTELLIGENCE_KEY"))
        if extractor == "azure" or azure_configured:
            try:
                logger.info("starting Azure Document Intelligence extraction: %s", source_path)
                return analyze_with_azure_document_intelligence(source_path)
            except Exception as exc:
                if extractor == "azure":
                    raise
                logger.warning("Azure extraction failed; falling back to local extraction: %s (%s)", source_path, exc)

    try:
        return extract_local_text(source_path, lang=lang, dpi=dpi)
    except Exception as exc:
        logger.warning("local text extraction failed; continuing with filename hints: %s (%s)", source_path, exc)
        return TextExtractionResult(extractor="local-failed")


def get_pdf_page_count(pdf_path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(pdf_path)).pages)
    except Exception:
        logger.exception("failed to read page count: %s", pdf_path)
        return None


def plan_source_preparation(source_path: Path, output_dir: Path, extractor: str, lang: str, dpi: int) -> PreparedAttachment:
    logger.info("processing source: %s", source_path)
    extraction = extract_source_text(source_path, extractor=extractor, lang=lang, dpi=dpi)
    document_type, confidence = classify_source_document(
        extraction.text,
        source_path,
        extraction.cloud_document_type,
        extraction.cloud_confidence,
    )
    details = extract_common_details(extraction.text, source_path, extraction.cloud_details)
    page_count = get_pdf_page_count(source_path) if source_path.suffix.lower() == ".pdf" else 1
    destination_dir = output_dir / ATTACHMENT_DIR_NAME
    new_name = build_new_filename(source_path, document_type, details, page_count=page_count)
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
        extractor=extraction.extractor,
    )


def plan_pdf_preparation(pdf_path: Path, output_dir: Path, lang: str, dpi: int) -> PreparedAttachment:
    return plan_source_preparation(pdf_path, output_dir, "local", lang, dpi)


def expand_pages(plan: PreparedAttachment, split_pages: bool) -> list[PreparedAttachment]:
    if Path(plan.source).suffix.lower() != ".pdf":
        return [plan]

    page_count = plan.page_count or 1
    if not split_pages or page_count <= 1:
        return [plan]

    source = Path(plan.source)
    expanded = []
    for page in range(1, page_count + 1):
        new_name = build_new_filename(source, plan.document_type, plan.extracted, page=page, page_count=page_count)
        page_destination = unique_destination(Path(plan.destination).parent / new_name)
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
                extractor=plan.extractor,
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

    shutil.copy2(source, destination)


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
        "extractor",
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
                    "extractor": plan.extractor,
                    "run_output_dir": str(output_dir),
                }
            )
    return csv_path


def find_missing_python_modules(include_local_ocr: bool) -> list[str]:
    modules = REQUIRED_PYTHON_MODULES + (LOCAL_OCR_MODULES if include_local_ocr else [])
    return [module for module in modules if importlib.util.find_spec(module) is None]


def main() -> int:
    parser = argparse.ArgumentParser(description="Sort PDF/image invoices into renamed attachment files and indexes.")
    parser.add_argument("input_dir", nargs="?", default=str(DEFAULT_INPUT_DIR), help=f"Source folder. Default: {DEFAULT_INPUT_DIR}")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help=f"Destination root. Default: {DEFAULT_OUTPUT_DIR}")
    parser.add_argument("--dry-run", action="store_true", help="Write indexes without copying or splitting files")
    parser.add_argument("--no-split-pages", action="store_true", help="Keep multi-page PDFs as one file")
    parser.add_argument("--run-name", help="Output subfolder name. Default: current timestamp")
    parser.add_argument(
        "--extractor",
        choices=["auto", "local", "azure"],
        default="auto",
        help="Text/OCR engine. Default: auto uses Azure when credentials are set, otherwise local extraction.",
    )
    parser.add_argument("--lang", default="jpn+eng", help="Tesseract OCR language")
    parser.add_argument("--dpi", type=int, default=300, help="DPI for local OCR conversion")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    args = parser.parse_args()

    setup_logging(args.verbose)

    missing_modules = find_missing_python_modules(include_local_ocr=args.extractor in {"auto", "local"})
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

    source_plans = [
        plan_source_preparation(source_file, output_dir, args.extractor, args.lang, args.dpi) for source_file in source_files
    ]
    plans = [page_plan for plan in source_plans for page_plan in expand_pages(plan, not args.no_split_pages)]

    try:
        for plan in plans:
            execute_plan(plan, args.dry_run)
        json_manifest_path = write_json_manifest(output_dir, plans, args.dry_run)
        csv_index_path = write_csv_index(output_dir, plans, args.dry_run)
    except Exception:
        logger.exception("failed to prepare attachments")
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
