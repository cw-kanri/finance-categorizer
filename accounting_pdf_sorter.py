import argparse
import json
import logging
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from pdf_text_extractor import extract_bank_transfer_details, extract_pdf_text, normalize_digits


logger = logging.getLogger(__name__)


DOCUMENT_RULES = [
    ("bank_transfer", ["振込日", "振込金額", "振込先", "受取人名", "振込受付", "振込明細"]),
    ("invoice", ["請求書", "御請求書", "請求金額", "お支払期限", "インボイス"]),
    ("receipt", ["領収書", "領収証", "レシート", "但し"]),
    ("quote", ["見積書", "御見積書", "見積金額"]),
    ("purchase_order", ["注文書", "発注書", "注文番号"]),
    ("statement", ["利用明細", "取引明細", "ご利用明細", "入出金明細"]),
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
]


@dataclass
class SortPlan:
    source: str
    destination: str
    document_type: str
    new_name: str
    confidence: float
    action: str
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


def date_from_filename(path: Path) -> str | None:
    match = re.search(r"(20\d{2})(\d{2})(\d{2})", normalize_digits(path.stem))
    if not match:
        return None

    year, month, day = match.groups()
    try:
        return datetime(int(year), int(month), int(day)).strftime("%Y-%m-%d")
    except ValueError:
        return None


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


def extract_common_details(text: str, source_path: Path) -> dict[str, str | None]:
    bank_details = extract_bank_transfer_details(text)

    date = parse_date(bank_details.get("date"))
    if not date:
        date = parse_date(find_labeled_value(text, DATE_LABELS))
    if not date:
        date = date_from_filename(source_path)

    payee = bank_details.get("payee") or find_labeled_value(text, PAYEE_LABELS)

    amount = clean_amount(bank_details.get("amount"))
    if not amount:
        amount = clean_amount(find_labeled_value(text, AMOUNT_LABELS, r"(?:￥|¥)?\s*[\d,]+(?:\s*円)?"))

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


def build_new_filename(source_path: Path, document_type: str, details: dict[str, str | None]) -> str:
    date_part = sanitize_filename_part(details.get("date"), "date_unknown", 10)
    type_part = sanitize_filename_part(document_type, "unknown", 30)
    payee_part = sanitize_filename_part(details.get("payee"), "payee_unknown", 40)
    amount_part = sanitize_filename_part(details.get("amount"), "amount_unknown", 20)
    original_id = sanitize_filename_part(source_path.stem, "original", 30)
    return f"{date_part}_{type_part}_{payee_part}_{amount_part}_{original_id}{source_path.suffix.lower()}"


def unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    for index in range(1, 1000):
        candidate = path.with_name(f"{path.stem}_{index:03d}{path.suffix}")
        if not candidate.exists():
            return candidate

    raise FileExistsError(f"Could not create unique destination for: {path}")


def iter_pdf_files(input_dir: Path, recursive: bool, output_dir: Path) -> list[Path]:
    pattern = "**/*.pdf" if recursive else "*.pdf"
    files = []
    resolved_output = output_dir.resolve()

    for path in sorted(input_dir.glob(pattern)):
        if not path.is_file():
            continue
        if resolved_output in path.resolve().parents:
            continue
        files.append(path)

    return files


def plan_pdf_sort(
    pdf_path: Path,
    output_dir: Path,
    lang: str,
    dpi: int,
    action: str,
) -> SortPlan:
    logger.info("processing PDF: %s", pdf_path)
    try:
        text = extract_pdf_text(pdf_path, lang=lang, dpi=dpi)["text"]
    except Exception:
        logger.exception("text extraction failed; continuing as unknown: %s", pdf_path)
        text = ""

    document_type, confidence = classify_document(text)
    details = extract_common_details(text, pdf_path)
    new_name = build_new_filename(pdf_path, document_type, details)
    period = details["date"][:7] if details.get("date") else "date_unknown"
    destination_dir = output_dir / period / document_type
    destination = unique_destination(destination_dir / new_name)

    return SortPlan(
        source=str(pdf_path),
        destination=str(destination),
        document_type=document_type,
        new_name=destination.name,
        confidence=confidence,
        action=action,
        extracted=details,
        journal_hint=build_journal_hint(document_type, details),
    )


def execute_plan(plan: SortPlan, apply: bool) -> None:
    if not apply:
        return

    source = Path(plan.source)
    destination = Path(plan.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if plan.action == "copy":
        shutil.copy2(source, destination)
    elif plan.action == "move":
        shutil.move(str(source), str(destination))
    else:
        raise ValueError(f"Unsupported action: {plan.action}")


def write_manifest(output_dir: Path, plans: list[SortPlan], apply: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / ("sort_manifest.json" if apply else "sort_manifest_dry_run.json")
    payload = {
        "applied": apply,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(plans),
        "results": [asdict(plan) for plan in plans],
    }
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Classify, rename, and sort accounting PDFs.")
    parser.add_argument("input_dir", help="Folder containing PDF files")
    parser.add_argument("--output-dir", help="Destination root folder. Default: input_dir/sorted_pdfs")
    parser.add_argument("--recursive", action="store_true", help="Find PDFs recursively")
    parser.add_argument("--apply", action="store_true", help="Actually copy or move files")
    parser.add_argument("--action", choices=["move", "copy"], default="move", help="Action used with --apply")
    parser.add_argument("--lang", default="jpn+eng", help="Tesseract OCR language")
    parser.add_argument("--dpi", type=int, default=300, help="DPI for OCR conversion")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    args = parser.parse_args()

    setup_logging(args.verbose)

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        logger.error("input folder not found: %s", input_dir)
        return 1

    output_dir = Path(args.output_dir).resolve() if args.output_dir else input_dir / "sorted_pdfs"
    pdf_files = iter_pdf_files(input_dir, args.recursive, output_dir)
    logger.info("found %s PDF file(s)", len(pdf_files))

    plans = [
        plan_pdf_sort(pdf_file, output_dir, args.lang, args.dpi, args.action)
        for pdf_file in pdf_files
    ]

    try:
        for plan in plans:
            execute_plan(plan, args.apply)
        manifest_path = write_manifest(output_dir, plans, args.apply)
    except Exception:
        logger.exception("failed to execute PDF sorting")
        return 1

    print(json.dumps({"applied": args.apply, "count": len(plans), "manifest": str(manifest_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
