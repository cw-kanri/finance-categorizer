import unittest
from pathlib import Path
from uuid import uuid4

from accounting_pdf_sorter import (
    PreparedAttachment,
    build_new_filename,
    classify_document,
    create_run_output_dir,
    execute_plan,
    extract_common_details,
    iter_pdf_files,
    iter_source_files,
    merge_details,
    parse_azure_invoice_result,
)

TEST_TEMP_ROOT = Path("test_materials") / "unit_tests"


def temporary_workspace(name: str) -> Path:
    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = TEST_TEMP_ROOT / f"{name}_{uuid4().hex}"
    workspace.mkdir(parents=True)
    return workspace


class AccountingPdfSorterTest(unittest.TestCase):
    def test_bank_transfer_details_are_extracted_from_labeled_text(self):
        text = """
        振込日 2026年4月13日
        振込先名 株式会社サンプル
        振込金額 123,456円
        振込手数料 330円
        """

        details = extract_common_details(text, Path("0125_PJ34_0000426_202604131.pdf"))

        self.assertEqual(
            details,
            {
                "date": "2026-04-13",
                "payee": "株式会社サンプル",
                "amount": "123456",
                "fee": "330",
            },
        )

    def test_bank_transfer_summary_totals_are_extracted(self):
        text = """
        本支店宛 1 967120 220
        他行宛 1 66000 550
        他行宛 1 760320 550
        他行宛 1 1617000 550
        """

        details = extract_common_details(text, Path("0125_PJ34_0000426_202604131.pdf"))

        self.assertEqual(details["date"], "2026-04-13")
        self.assertIsNone(details["payee"])
        self.assertEqual(details["amount"], "3410440")
        self.assertEqual(details["fee"], "1870")

    def test_unknown_values_are_not_guessed_except_filename_date(self):
        details = extract_common_details("本文に表示ラベルがない", Path("sample_20260413.pdf"))

        self.assertEqual(details["date"], "2026-04-13")
        self.assertIsNone(details["payee"])
        self.assertIsNone(details["amount"])
        self.assertIsNone(details["fee"])

    def test_cloud_details_take_priority_over_local_hints(self):
        details = extract_common_details(
            "請求日 2026年4月1日 請求金額 10,000円",
            Path("sample_20260413.pdf"),
            {"date": "2026-04-30", "payee": "Azure Vendor", "amount": "25000", "fee": None},
        )

        self.assertEqual(details["date"], "2026-04-30")
        self.assertEqual(details["payee"], "Azure Vendor")
        self.assertEqual(details["amount"], "25000")

    def test_document_classification_uses_keywords(self):
        document_type, confidence = classify_document("請求書 請求金額 10,000円 Invoice Total")

        self.assertEqual(document_type, "invoice")
        self.assertGreater(confidence, 0)

    def test_filename_contains_sorting_parts(self):
        filename = build_new_filename(
            Path("original.pdf"),
            "invoice",
            {"date": "2026-04-13", "payee": "株式会社サンプル", "amount": "123456", "fee": "330"},
        )

        self.assertEqual(filename, "2026-04-13_株式会社サンプル_123456_invoice_original.pdf")

    def test_iter_pdf_files_reads_all_pdfs_under_input(self):
        root = temporary_workspace("iter_all")
        input_dir = root / "test_materials" / "input"
        output_dir = root / "test_materials" / "output"
        nested_dir = input_dir / "nested"
        nested_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        top_pdf = input_dir / "top.pdf"
        nested_pdf = nested_dir / "nested.pdf"
        ignored_text = input_dir / "memo.txt"
        output_pdf = output_dir / "already_sorted.pdf"

        top_pdf.write_bytes(b"%PDF-1.4\n")
        nested_pdf.write_bytes(b"%PDF-1.4\n")
        ignored_text.write_text("not a PDF", encoding="utf-8")
        output_pdf.write_bytes(b"%PDF-1.4\n")

        self.assertEqual(iter_pdf_files(input_dir, output_dir), sorted([top_pdf, nested_pdf]))

    def test_iter_source_files_reads_supported_images(self):
        root = temporary_workspace("iter_images")
        input_dir = root / "input"
        output_dir = root / "output"
        input_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        pdf = input_dir / "receipt.pdf"
        image = input_dir / "経費申請画面_明細.png"
        ignored = input_dir / "memo.txt"
        pdf.write_bytes(b"%PDF-1.4\n")
        image.write_bytes(b"not a real image")
        ignored.write_text("ignore", encoding="utf-8")

        self.assertEqual(iter_source_files(input_dir, output_dir), sorted([pdf, image]))

    def test_iter_pdf_files_skips_output_inside_input(self):
        root = temporary_workspace("skip_output")
        input_dir = root / "input"
        output_dir = input_dir / "output"
        output_dir.mkdir(parents=True)

        source_pdf = input_dir / "source.pdf"
        sorted_pdf = output_dir / "sorted.pdf"
        source_pdf.write_bytes(b"%PDF-1.4\n")
        sorted_pdf.write_bytes(b"%PDF-1.4\n")

        self.assertEqual(iter_pdf_files(input_dir, output_dir), [source_pdf])

    def test_execute_plan_copies_without_removing_source(self):
        root = temporary_workspace("copy")
        source = root / "input.pdf"
        destination = root / "output" / "renamed.pdf"
        source.write_bytes(b"%PDF-1.4\n")
        plan = PreparedAttachment(
            source=str(source),
            destination=str(destination),
            document_type="receipt",
            new_name=destination.name,
            confidence=1.0,
            page=None,
            page_count=1,
            extracted={"date": None, "payee": None, "amount": None, "fee": None},
            journal_hint={"date": None, "debit_account": None, "credit_account": None},
        )

        execute_plan(plan, dry_run=False)

        self.assertTrue(source.exists())
        self.assertEqual(destination.read_bytes(), b"%PDF-1.4\n")

    def test_create_run_output_dir_uses_unique_timestamp_folder(self):
        root = temporary_workspace("run_dir")
        output_root = root / "output"

        first = create_run_output_dir(output_root, "20260520_104500")
        second = create_run_output_dir(output_root, "20260520_104500")

        self.assertEqual(first.name, "20260520_104500")
        self.assertEqual(second.name, "20260520_104500_001")
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())

    def test_parse_azure_invoice_result_maps_invoice_fields(self):
        result = parse_azure_invoice_result(
            {
                "status": "succeeded",
                "analyzeResult": {
                    "content": "Invoice text",
                    "documents": [
                        {
                            "confidence": 0.93,
                            "fields": {
                                "InvoiceDate": {"valueDate": "2026-04-13"},
                                "VendorName": {"valueString": "Contoso Japan"},
                                "InvoiceTotal": {"valueCurrency": {"amount": 123456, "currencyCode": "JPY"}},
                            },
                        }
                    ],
                },
            }
        )

        self.assertEqual(result.extractor, "azure-document-intelligence")
        self.assertEqual(result.cloud_document_type, "invoice")
        self.assertEqual(result.cloud_details["date"], "2026-04-13")
        self.assertEqual(result.cloud_details["payee"], "Contoso Japan")
        self.assertEqual(result.cloud_details["amount"], "123456")
        self.assertEqual(result.cloud_confidence, 0.93)

    def test_merge_details_fills_missing_values(self):
        merged = merge_details(
            {"date": "2026-04-13", "payee": None, "amount": "1000", "fee": None},
            {"date": "2026-04-01", "payee": "Fallback", "amount": "999", "fee": "110"},
        )

        self.assertEqual(merged, {"date": "2026-04-13", "payee": "Fallback", "amount": "1000", "fee": "110"})


if __name__ == "__main__":
    unittest.main()
