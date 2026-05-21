# PDF Invoice Sorter

PDF/画像の請求書を読み取り、仕訳前に人が確認しやすいファイル名と一覧表へ整理する小さなPythonツールです。

## できること

- `test_materials/input` 以下の PDF/PNG/JPG/TIFF/BMP をまとめて処理
- PDFに埋め込みテキストがあれば `pdfplumber` で抽出
- 画像PDFや画像ファイルはローカルOCR、またはAzure Document Intelligenceで抽出
- 日付、取引先、金額、手数料、文書種別を推定
- 元ファイルを消さず、`test_materials/output/<実行時刻>/attachments` にコピー
- `attachment_index.csv` と `attachment_index.json` を出力

## セットアップ

Windowsで初めて使う人向けの詳しい手順は [WINDOWS_SETUP.md](WINDOWS_SETUP.md) を見てください。

`uv` がまだ入っていない場合は、PowerShellで次を実行します。

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

PowerShellを開き直して、インストールを確認します。

```powershell
uv --version
```

その後、リポジトリのフォルダでPython環境を作ります。

```powershell
uv venv
uv pip install -r requirements.txt
```

ローカルOCRを使う場合は、Pythonライブラリに加えて以下も必要です。

- Tesseract OCR
- Tesseractの日本語データ `jpn`
- Poppler (`pdf2image` がPDFを画像化するために使用)

クラウドOCRを使う場合、ローカルOCR用の外部コマンドは不要です。

## まず動かす

既定では `--extractor auto` で動きます。Azureの環境変数が設定されていればAzure Document Intelligenceを使い、未設定ならローカル抽出に自動で切り替わります。

仕分けしたいPDF請求書や画像ファイルは、実行前に `test_materials/input` に入れてください。このフォルダはリポジトリに含めていますが、中に入れた請求書ファイルはGit管理しない想定です。

```powershell
uv run python .\accounting_pdf_sorter.py
```

確認だけしたい場合:

```powershell
uv run python .\accounting_pdf_sorter.py --dry-run
```

別フォルダを処理する場合:

```powershell
uv run python .\accounting_pdf_sorter.py ".\invoices" --output-dir ".\sorted"
```

複数ページPDFを分割したくない場合:

```powershell
uv run python .\accounting_pdf_sorter.py --no-split-pages
```

## OCR/抽出エンジン

既定は `--extractor auto` です。通常運用では引数指定なしで構いません。

```powershell
uv run python .\accounting_pdf_sorter.py --extractor auto
```

ローカルだけで動かす:

```powershell
uv run python .\accounting_pdf_sorter.py --extractor local
```

Azure Document Intelligenceだけを使う:

```powershell
$env:AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT="https://<your-resource>.cognitiveservices.azure.com"
$env:AZURE_DOCUMENT_INTELLIGENCE_KEY="<your-key>"
uv run python .\accounting_pdf_sorter.py --extractor azure
```

Azure設定後の通常実行:

```powershell
uv run python .\accounting_pdf_sorter.py
```

Azureでは `prebuilt-invoice` モデルを使います。請求書OCRは専用モデルが日付・取引先・合計金額を返すため、Tesseractだけで日本語ラベルを拾うより運用が楽になりやすいです。

## 出力例

`test_materials/output` は事前に作らなくても構いません。実行時に自動生成されます。

```text
test_materials/output/
  20260521_103000/
    attachments/
      2026-04-13_株式会社サンプル_123456_invoice_original.pdf
    attachment_index.csv
    attachment_index.json
```

CSVには、会計確認で見たい最低限の項目を出します。

- `source_file`
- `output_file`
- `document_type`
- `date`
- `counterparty`
- `amount`
- `fee`
- `page`
- `page_count`
- `confidence`
- `extractor`

## 実装メモ

実行コードは `accounting_pdf_sorter.py` に一本化しています。以前の単独テキスト抽出スクリプトは統合し、テストは `test_accounting_pdf_sorter.py` に置いています。

このツールは最終的な勘定科目を自動確定するものではありません。目的は、PDF請求書を「人が探す・開く・転記する」前段階で、日付・取引先・金額つきの一覧にして確認負荷を下げることです。

## テスト

```powershell
uv run python -m unittest -v
```
