import unittest
from pathlib import Path

from accounting_pdf_sorter import (
    build_new_filename,
    classify_document,
    extract_common_details,
)


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
        令和 ８年 ４月１０日振込分の総合・給与振込の明細
        本支店仕向 1 967120 220
        他行仕向 1 66000 550
        他行仕向 1 760320 550
        他行仕向 1 1617000 550
        """

        details = extract_common_details(text, Path("0125_PJ34_0000426_202604131.pdf"))

        self.assertEqual(details["date"], "2026-04-10")
        self.assertIsNone(details["payee"])
        self.assertEqual(details["amount"], "3410440")
        self.assertEqual(details["fee"], "1870")

    def test_unknown_values_are_not_guessed_except_filename_date(self):
        details = extract_common_details("本文に明示ラベルなし", Path("sample_20260413.pdf"))

        self.assertEqual(details["date"], "2026-04-13")
        self.assertIsNone(details["payee"])
        self.assertIsNone(details["amount"])
        self.assertIsNone(details["fee"])

    def test_document_classification_uses_keywords(self):
        document_type, confidence = classify_document("振込日 2026年4月13日 振込金額 10,000円 受取人名 A社")

        self.assertEqual(document_type, "bank_transfer")
        self.assertGreater(confidence, 0)

    def test_filename_contains_sorting_parts(self):
        filename = build_new_filename(
            Path("original.pdf"),
            "bank_transfer",
            {"date": "2026-04-13", "payee": "株式会社サンプル", "amount": "123456", "fee": "330"},
        )

        self.assertEqual(filename, "2026-04-13_bank_transfer_株式会社サンプル_123456_original.pdf")


if __name__ == "__main__":
    unittest.main()
