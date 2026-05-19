import argparse
import json
import logging
import re
import sys
from pathlib import Path


logger = logging.getLogger(__name__)


EMPTY_TRANSFER_DETAILS = {
    "date": None,
    "payee": None,
    "amount": None,
    "fee": None,
}


FULLWIDTH_DIGIT_TRANSLATION = str.maketrans("０１２３４５６７８９", "0123456789")


def setup_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def extract_text_with_pdfplumber(pdf_path: Path) -> str:
    import pdfplumber

    page_texts: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
                page_texts.append(text)
            except Exception:
                logger.exception("pdfplumber extraction failed on page %s", page_number)
                page_texts.append("")

    return "\n".join(text for text in page_texts if text).strip()


def extract_text_with_ocr(pdf_path: Path, lang: str, dpi: int) -> str:
    import pytesseract
    from pdf2image import convert_from_path

    page_texts: list[str] = []

    try:
        images = convert_from_path(str(pdf_path), dpi=dpi)
    except Exception:
        logger.exception("failed to convert PDF pages to images")
        raise

    for page_number, image in enumerate(images, start=1):
        try:
            text = pytesseract.image_to_string(image, lang=lang)
            page_texts.append(text.strip())
        except Exception:
            logger.exception("OCR failed on page %s", page_number)
            page_texts.append("")

    return "\n".join(text for text in page_texts if text).strip()


def extract_pdf_text(pdf_file: str | Path, lang: str = "jpn+eng", dpi: int = 300) -> dict[str, str]:
    pdf_path = Path(pdf_file)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")

    try:
        text = extract_text_with_pdfplumber(pdf_path)
        if text:
            logger.info("extracted text with pdfplumber: %s", pdf_path)
            return {"text": text}

        logger.info("no embedded text found; starting OCR: %s", pdf_path)
        return {"text": extract_text_with_ocr(pdf_path, lang=lang, dpi=dpi)}
    except Exception:
        logger.exception("PDF text extraction failed: %s", pdf_path)
        raise


def clean_extracted_value(value: str) -> str | None:
    cleaned = value.strip()
    cleaned = re.sub(r"^[：:\s]+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def normalize_digits(value: str) -> str:
    return value.translate(FULLWIDTH_DIGIT_TRANSLATION)


def find_labeled_value(text: str, labels: list[str], value_pattern: str) -> str | None:
    joined_labels = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    pattern = re.compile(
        rf"(?:{joined_labels})\s*[：:\s]*({value_pattern})",
        flags=re.IGNORECASE,
    )

    match = pattern.search(text)
    if not match:
        return None

    return clean_extracted_value(match.group(1))


def find_transfer_statement_date(text: str) -> str | None:
    normalized = normalize_digits(text)
    match = re.search(r"令和\s*(\d{1,2})年\s*(\d{1,2})月\s*(\d{1,2})日\s*振込分", normalized)
    if not match:
        return None

    era_year, month, day = match.groups()
    western_year = 2018 + int(era_year)
    return f"{western_year}年{int(month)}月{int(day)}日"


def extract_transfer_summary_totals(text: str) -> tuple[str | None, str | None]:
    total_amount = 0
    total_fee = 0

    for line in normalize_digits(text).splitlines():
        normalized_line = re.sub(r"\s+", " ", line).strip()
        match = re.search(r"(?:本支店仕向|他行仕向)\s+(\d+)件?\s+([\d,]+)\s+([\d,]+)", normalized_line)
        if not match:
            continue

        count, amount, fee = match.groups()
        if int(count) == 0:
            continue

        total_amount += int(amount.replace(",", ""))
        total_fee += int(fee.replace(",", ""))

    return (
        str(total_amount) if total_amount else None,
        str(total_fee) if total_fee else None,
    )


def extract_bank_transfer_details(text: str) -> dict[str, str | None]:
    try:
        total_amount, total_fee = extract_transfer_summary_totals(text)
        return {
            "date": find_labeled_value(
                text,
                ["振込日"],
                r"(?:\d{4}[年/-]\d{1,2}[月/-]\d{1,2}日?|\d{1,2}[月/-]\d{1,2}日?)",
            )
            or find_transfer_statement_date(text),
            "payee": find_labeled_value(
                text,
                ["支払先", "振込先", "振込先名", "受取人", "受取人名", "先方名"],
                r"[^\r\n]+",
            ),
            "amount": find_labeled_value(
                text,
                ["振込金額"],
                r"(?:￥|¥)?\s*[\d,]+(?:\s*円)?",
            )
            or total_amount,
            "fee": find_labeled_value(
                text,
                ["手数料", "振込手数料"],
                r"(?:￥|¥)?\s*[\d,]+(?:\s*円)?",
            )
            or total_fee,
        }
    except Exception:
        logger.exception("bank transfer detail extraction failed")
        return EMPTY_TRANSFER_DETAILS.copy()


def main() -> int:
    setup_logging()

    parser = argparse.ArgumentParser(description="Extract text from one PDF file as JSON.")
    parser.add_argument("pdf_file", help="Path to a PDF file")
    parser.add_argument("--lang", default="jpn+eng", help="Tesseract OCR language, e.g. jpn or jpn+eng")
    parser.add_argument("--dpi", type=int, default=300, help="DPI used when converting image PDFs for OCR")
    parser.add_argument(
        "--extract",
        choices=["text", "bank-transfer"],
        default="text",
        help="Output full text or bank transfer details",
    )
    args = parser.parse_args()

    try:
        pdf_result = extract_pdf_text(args.pdf_file, lang=args.lang, dpi=args.dpi)
        if args.extract == "bank-transfer":
            result = extract_bank_transfer_details(pdf_result["text"])
        else:
            result = pdf_result
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        logger.error("failed to extract PDF text: %s", exc)
        if args.extract == "bank-transfer":
            print(json.dumps(EMPTY_TRANSFER_DETAILS, ensure_ascii=False))
        else:
            print(json.dumps({"text": ""}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
