# PDF Text Extractor

1ファイルずつPDFを処理し、以下のJSON形式で全文テキストを出力します。

```json
{
  "text": "全文テキスト"
}
```

## セットアップ

```powershell
pip install -r requirements.txt
```

画像PDFのOCRには、別途以下が必要です。

- Tesseract OCR
- 日本語言語データ `jpn`
- `pdf2image` 用の Poppler

## 実行例

```powershell
python .\pdf_text_extractor.py .\sample.pdf
```

日本語と英語をOCR対象にする設定がデフォルトです。

```powershell
python .\pdf_text_extractor.py .\sample.pdf --lang jpn+eng --dpi 300
```

ログは標準エラー、JSONは標準出力に出力します。

## 銀行振込明細の抽出

PDFから抽出したテキストをもとに、銀行振込明細の主要項目をJSONで出力できます。

```powershell
python .\pdf_text_extractor.py .\transfer.pdf --extract bank-transfer
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

## フォルダ内PDFの自動仕分け

フォルダ内のPDFを1ファイルずつ解析し、分類、リネーム、フォルダ作成、移動またはコピーを行います。

まずは実行計画だけ確認します。

```powershell
python .\accounting_pdf_sorter.py "C:\Users\インターン）奥谷豊\Downloads"
```

実行計画は `sorted_pdfs\sort_manifest_dry_run.json` に出力されます。実際に仕分ける場合は `--apply` を付けます。

```powershell
python .\accounting_pdf_sorter.py "C:\Users\インターン）奥谷豊\Downloads" --apply
```

元ファイルを残したい場合はコピーで仕分けできます。

```powershell
python .\accounting_pdf_sorter.py "C:\Users\インターン）奥谷豊\Downloads" --apply --action copy
```

### 仕分けロジック

PDF本文を `pdfplumber` で抽出し、文字が取れない画像PDFは `pytesseract` でOCRします。そのテキストに含まれる明示的なキーワードで分類します。

- `bank_transfer`: `振込日`、`振込金額`、`振込先`、`受取人名`、`振込明細`
- `invoice`: `請求書`、`請求金額`、`お支払期限`、`インボイス`
- `receipt`: `領収書`、`領収証`、`レシート`
- `quote`: `見積書`
- `purchase_order`: `注文書`、`発注書`
- `statement`: `利用明細`、`取引明細`、`入出金明細`
- `tax`: `適格請求書`、`登録番号`、`消費税`
- `payroll`: `給与`、`源泉所得税`、`社会保険料`
- `contract`: `契約書`
- `unknown`: 明示キーワードが見つからないPDF

### フォルダ作成ロジック

出力先はデフォルトで入力フォルダ配下の `sorted_pdfs` です。フォルダは以下の形式で作成します。

```text
sorted_pdfs/
  YYYY-MM/
    document_type/
      renamed.pdf
```

日付が取れない場合は `date_unknown` に入ります。

### リネームロジック

ファイル名は以下の形式です。

```text
YYYY-MM-DD_document_type_payee_amount_original-id.pdf
```

例:

```text
2026-04-13_bank_transfer_株式会社サンプル_123456_0125_PJ34_0000426_202604131.pdf
```

抽出できない値は `date_unknown`、`payee_unknown`、`amount_unknown` を使います。同名ファイルがある場合は `_001`、`_002` の連番を付けます。

総合振込明細のように1PDF内に複数の振込先がある場合、単一の支払先名は推測せず `payee_unknown` にします。金額と手数料は、明示された `本支店仕向`、`他行仕向` の合計欄から集計します。

### 仕訳に向けた出力

`sort_manifest.json` には `journal_hint` を出力します。銀行振込明細では貸方を `普通預金` として出しますが、借方勘定科目は推測せず `null` にします。最終的な自動仕訳では、取引先マスタや摘要ルールを追加して借方科目を決定します。
