# PDF Text Extractor

1ファイルずつPDFを処理し、以下のJSON形式で全文テキストを出力します。

```json
{
  "text": "全文テキスト"
}
```

## セットアップ

```powershell
uv venv
uv pip install -r requirements.txt
```

画像PDFのOCRには、別途以下が必要です。

- Tesseract OCR
- 日本語言語データ `jpn`
- `pdf2image` 用の Poppler

## 実行例

```powershell
uv run python .\pdf_text_extractor.py .\test_materials\input\sample.pdf
```

日本語と英語をOCR対象にする設定がデフォルトです。

```powershell
uv run python .\pdf_text_extractor.py .\test_materials\input\sample.pdf --lang jpn+eng --dpi 300
```

ログは標準エラー、JSONは標準出力に出力します。

## 銀行振込明細の抽出

PDFから抽出したテキストをもとに、銀行振込明細の主要項目をJSONで出力できます。

```powershell
uv run python .\pdf_text_extractor.py .\test_materials\input\transfer.pdf --extract bank-transfer
```

出力形式:

```json
{
  "date": "2026年5月19日",
  "payee": "株式会社サンプル",
  "amount": "100,000円",
  "fee": "330円"
}
```

抽出できない項目は `null` になります。推測はせず、`振込日`、`振込金額`、`手数料`、`支払先`、`振込先`、`受取人名` などのラベルが見つかった場合だけ抽出します。

## 添付用PDFの準備

`test_materials\input` に入っているPDF・PNG・JPGをすべて解析し、内容から日付・相手先・金額などを読み取り、仕訳に添付しやすいファイル名へリネームしたコピーを作成します。サブフォルダ内のファイルも対象です。

PDF素材はリポジトリ内の `test_materials\input` に置きます。このフォルダはGit追跡外です。

通常実行では、`test_materials\input` を読み込み、実行時刻ごとのフォルダを `test_materials\output` の下に作成します。添付用ファイルはその中の `attachments` に出力します。元ファイルは削除しません。

```powershell
uv run python .\accounting_pdf_sorter.py
```

同時に、元ファイル名・出力ファイル名・日付・相手先・金額・ページ番号を一覧化した `attachment_index.csv` と `attachment_index.json` を実行時刻フォルダに出力します。

```text
test_materials/output/
  20260520_104909/
    attachments/
      renamed.pdf
      renamed.png
    attachment_index.csv
    attachment_index.json
```

コピーやページ分割をせず、一覧だけ確認したい場合は `--dry-run` を付けます。

```powershell
uv run python .\accounting_pdf_sorter.py --dry-run
```

確認用の一覧は実行時刻フォルダ内の `attachment_index_dry_run.csv` と `attachment_index_dry_run.json` に出力されます。

複数ページPDFは、デフォルトで1ページずつ分割して添付用PDFを作成します。分割せず元PDF単位でコピーしたい場合は `--no-split-pages` を指定します。

```powershell
uv run python .\accounting_pdf_sorter.py --no-split-pages
```

別フォルダで試したい場合だけ、入力フォルダと出力フォルダを明示します。

```powershell
uv run python .\accounting_pdf_sorter.py ".\another_input" --output-dir ".\another_output"
```

実行フォルダ名を固定したい場合は `--run-name` を指定します。

```powershell
uv run python .\accounting_pdf_sorter.py --run-name "2026-04月分_確認01"
```

### 読み取りロジック

PDF本文を `pdfplumber` で抽出し、文字が取れない画像PDFやPNG/JPGは `pytesseract` でOCRします。OCRできない場合でも、ファイル名と親フォルダ名から日付・相手先・金額・書類種別を補います。

- `bank_transfer`: `振込日`、`振込金額`、`振込先`、`受取人名`、`振込明細`
- `invoice`: `請求書`、`請求金額`、`お支払期限`、`インボイス`
- `receipt`: `領収書`、`領収証`、`レシート`
- `expense_claim`: `経費申請`、`立替経費`、`外注費`、`出張宿泊費`
- `credit_card_statement`: `クレジットカード`、`カード利用`、`楽天カード`
- `payment_notice`: `検収通知書`、`支払通知書`、`入金予定`
- `quote`: `見積書`
- `purchase_order`: `注文書`、`発注書`
- `statement`: `利用明細`、`取引明細`、`入出金明細`
- `tax`: `適格請求書`、`登録番号`、`消費税`
- `payroll`: `給与`、`源泉所得税`、`社会保険料`
- `contract`: `契約書`
- `unknown`: 明示キーワードが見つからないPDF

### 出力ロジック

出力先はデフォルトで `test_materials\output` です。実行ごとに時刻フォルダを作り、その中に添付用ファイルと一覧を作成します。

```text
test_materials/output/
  YYYYMMDD_HHMMSS/
    attachments/
      renamed.pdf
      renamed.png
    attachment_index.csv
    attachment_index.json
```

日付が取れない場合はファイル名に `date_unknown` を使います。

### リネームロジック

ファイル名は以下の形式です。

```text
YYYY-MM-DD_payee_amount_document_type_original-id.pdf
```

例:

```text
2026-04-13_株式会社サンプル_123456_bank_transfer_0125_PJ34_0000426_202604131.pdf
```

抽出できない値は `date_unknown`、`payee_unknown`、`amount_unknown` を使います。複数ページを分割した場合は `_p01`、`_p02` のページ番号を付けます。同名ファイルがある場合は `_001`、`_002` の連番を付けます。

総合振込明細のように1PDF内に複数の振込先がある場合、単一の支払先名は推測せず `payee_unknown` にします。金額と手数料は、明示された `本支店仕向`、`他行仕向` の合計欄から集計します。

### 仕訳に向けた出力

`attachment_index.csv` には、仕訳時に見たい `source_file`、`output_file`、`document_type`、`date`、`counterparty`、`amount`、`fee`、`page`、`page_count` を出力します。最終的な分類や勘定科目判断は人間が行う前提です。
