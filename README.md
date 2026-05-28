# 七十七銀行 明細PDF仕分けツール

七十七銀行からダウンロードした明細表PDFを、仕訳添付しやすい形に整えるための小さなツールです。

## できること

- 七十七銀行のPDFだけを対象に処理
- 複数ページPDFを1ページずつのPDFへ分割
- 各ページから振込日、振込先、振込金額、手数料を抽出
- `20260410_オオタカツヒコ_967120_220.pdf` のように内容が分かる名前で保存
- Excelで開きやすいUTF-8 BOM付きCSVを自動出力
- Azure Document Intelligenceの設定がある場合はクラウド抽出を優先し、未設定ならPDF埋め込みテキストをローカル抽出

## 対応している明細

- 総合振込明細表兼振込手数料のお知らせ

給与のように1ページに複数人分がまとまる明細は、振込先を `給与` として合計金額・合計手数料を出力します。1ページ1振込の明細は、ページごとに振込先を読み取ります。

## セットアップ

```powershell
uv venv
uv pip install -r requirements.txt
```

## 使い方

既定では `test_materials/input` のPDFを読み、`test_materials/output/<実行時刻>/` に出力します。

```powershell
uv run python .\accounting_pdf_sorter.py
```

任意のフォルダを処理する場合:

```powershell
uv run python .\accounting_pdf_sorter.py ".\input" --output-dir ".\output"
```

PDFを作らず、CSV/JSONだけ確認する場合:

```powershell
uv run python .\accounting_pdf_sorter.py ".\input" --dry-run
```

Azure Document Intelligenceを明示的に使う場合:

```powershell
$env:AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT="https://<your-resource>.cognitiveservices.azure.com"
$env:AZURE_DOCUMENT_INTELLIGENCE_KEY="<your-key>"
uv run python .\accounting_pdf_sorter.py ".\input" --extractor azure
```

通常は `--extractor auto` のままで構いません。Azureの環境変数があればAzureの `prebuilt-layout` を使い、なければ `pdfplumber` でローカル抽出します。

## 出力

```text
test_materials/output/
  20260528_103000/
    statements/
      20260410_オオタカツヒコ_967120_220.pdf
      20260410_ライトアーム（カ_66000_550.pdf
    shichijushichi_index.csv
    shichijushichi_index.json
```

CSVの主な列:

- 元ファイル名
- 新ファイル名
- 振込日
- 振込先
- 振込金額
- 手数料
- 明細種別
- ページ
- 総ページ数
- 抽出方法

## テスト

```powershell
uv run python -m unittest -v
```
