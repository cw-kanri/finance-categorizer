import unittest
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader, PdfWriter

from accounting_pdf_sorter import (
    ShichijushichiRecord,
    build_new_filename,
    create_run_output_dir,
    execute_record,
    extract_record_details,
    iter_pdf_files,
    parse_recipient_rows,
    parse_reiwa_transfer_date,
    parse_summary_totals,
)


TEST_TEMP_ROOT = Path("test_materials") / "unit_tests"

GENERAL_TRANSFER_TEXT = """
203PJ340413
総合振込明細表兼振込手数料のお知らせ
令和 ８年 ４月１０日振込分の総合・給与振込の明細および振込手数料についてお知らせします。
お 振 込 先 お 受 取 人 銀 行 支店
金 額 受 取 人 番 号 備 考
銀 行 名 支 店 名 科目 口座番号 受 取 人 名 番 号 番号
ｼﾁｼﾞﾕｳｼﾁ ｾﾝﾀﾞｲﾋｶﾞｼｸﾞﾁ ﾌ 5278961 ｵｵﾀｶﾂﾋｺ 967120 0125 278
合 計 手 数 料 （金額）
同一店内仕向 0件 0円 0円
本支店仕向 1 967120 220
他行仕向 0 0 0
合 計 1 967120 合 計 220
"""

PAYROLL_TEXT = """
203PJ370427
給与振込明細表兼振込手数料のお知らせ
令和 ８年 ４月２４日振込分の総合・給与振込の明細および振込手数料についてお知らせします。
ｼﾁｼﾞﾕｳｼﾁ ｲﾁﾊﾞﾝﾁﾖｳ ﾌ 5356873 ﾄﾖｼﾏｵｻﾑ 242030 0125 205 2016
ｼﾁｼﾞﾕｳｼﾁ ｼﾝﾃﾝﾏﾁ ﾌ 9117857 ｱﾜｼﾞﾖｼｶｽﾞ 100000 0125 203 1001
合 計 13 2979275 合 計 1320
"""


def temporary_workspace(name: str) -> Path:
    TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    workspace = TEST_TEMP_ROOT / f"{name}_{uuid4().hex}"
    workspace.mkdir(parents=True)
    return workspace


class ShichijushichiStatementSorterTest(unittest.TestCase):
    def test_reiwa_transfer_date_is_converted_to_iso_date(self):
        self.assertEqual(parse_reiwa_transfer_date(GENERAL_TRANSFER_TEXT), "2026-04-10")

    def test_reiwa_transfer_date_allows_space_before_transfer_label(self):
        text = "令和　８年　４月２４日　振込分の総合・給与振込の明細"

        self.assertEqual(parse_reiwa_transfer_date(text), "2026-04-24")

    def test_summary_totals_are_extracted(self):
        self.assertEqual(parse_summary_totals(GENERAL_TRANSFER_TEXT), (1, "967120", "220"))

    def test_recipient_row_is_extracted_and_normalized(self):
        self.assertEqual(parse_recipient_rows(GENERAL_TRANSFER_TEXT), [("オオタカツヒコ", "967120")])

    def test_general_transfer_record_details_match_requested_filename_parts(self):
        details = extract_record_details(GENERAL_TRANSFER_TEXT, Path("0125_PJ34_0000426_202604131.pdf"))

        self.assertEqual(
            details,
            {
                "transfer_date": "2026-04-10",
                "recipient": "オオタカツヒコ",
                "amount": "967120",
                "fee": "220",
                "statement_type": "general_transfer",
            },
        )
        self.assertEqual(build_new_filename(details), "20260410_オオタカツヒコ_967120_220.pdf")

    def test_payroll_uses_summary_amount_and_payroll_recipient(self):
        details = extract_record_details(PAYROLL_TEXT, Path("0125_PJ37_0000555_202604271.pdf"))

        self.assertEqual(details["transfer_date"], "2026-04-24")
        self.assertEqual(details["recipient"], "給与")
        self.assertEqual(details["amount"], "2979275")
        self.assertEqual(details["fee"], "1320")
        self.assertEqual(details["statement_type"], "payroll")

    def test_iter_pdf_files_only_reads_pdf_and_skips_output_folder(self):
        root = temporary_workspace("iter_pdf")
        input_dir = root / "input"
        output_dir = input_dir / "output"
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

    def test_execute_record_writes_only_the_requested_page(self):
        root = temporary_workspace("split")
        source = root / "source.pdf"
        destination = root / "output" / "page2.pdf"

        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        writer.add_blank_page(width=144, height=144)
        with source.open("wb") as output_file:
            writer.write(output_file)

        record = ShichijushichiRecord(
            source=str(source),
            destination=str(destination),
            new_name=destination.name,
            transfer_date="2026-04-10",
            recipient="テスト",
            amount="100",
            fee="0",
            statement_type="general_transfer",
            page=2,
            page_count=2,
            extractor="pdfplumber",
        )

        execute_record(record, dry_run=False)

        reader = PdfReader(str(destination))
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(float(reader.pages[0].mediabox.width), 144.0)

    def test_create_run_output_dir_uses_unique_timestamp_folder(self):
        root = temporary_workspace("run_dir")
        output_root = root / "output"

        first = create_run_output_dir(output_root, "20260520_104500")
        second = create_run_output_dir(output_root, "20260520_104500")

        self.assertEqual(first.name, "20260520_104500")
        self.assertEqual(second.name, "20260520_104500_001")
        self.assertTrue(first.is_dir())
        self.assertTrue(second.is_dir())


if __name__ == "__main__":
    unittest.main()
