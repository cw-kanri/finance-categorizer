# Windows向け 初回セットアップ手順

この手順書は、zipファイルで配布された「七十七銀行 明細PDF仕分けツール」をWindows PCで使い始めるためのものです。Gitやリポジトリのクローンは不要です。

## 1. 前提

対応OSは、いったんWindowsのみです。

使うもの:

- Windows 10 または Windows 11
- PowerShell
- uv
- 必要に応じて Azure Document Intelligence Free F0プラン

## 2. zipファイルを展開する

1. 共有された `finance-categorizer-<バージョン>.zip` をダウンロードします。
2. zipファイルを右クリックして `すべて展開` を選びます。
3. 展開先は、例として次の場所にします。

```text
C:\Users\<ユーザー名>\Documents\finance-categorizer
```

展開後、フォルダの中に次のファイルがあることを確認してください。

```text
accounting_pdf_sorter.py
requirements.txt
WINDOWS_SETUP.md
test_materials
```

## 3. uvをインストールする

PowerShellを開き、次を実行します。

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

インストール後、PowerShellを開き直して確認します。

```powershell
uv --version
```

バージョンが表示されればOKです。

## 4. ツールのフォルダへ移動する

PowerShellで、zipを展開したフォルダへ移動します。

```powershell
cd $HOME\Documents\'finance-categorizer-v1.0.0'
```

展開先を変えた場合は、その場所に合わせて `cd` の後ろを書き換えてください。

## 5. Python環境を作る

初回だけ、ツールのフォルダで次を実行します。

```powershell
uv venv
uv pip install -r requirements.txt
```

少し時間がかかることがあります。

## 6. 明細PDFを入れる

処理したい七十七銀行の明細PDFを次のフォルダに入れます。

```text
test_materials\input
```

フォルダがない場合は作成してください。

```powershell
New-Item -ItemType Directory -Force .\test_materials\input
```

## 7. まず確認実行する

いきなりPDFを分割せず、CSV/JSONだけ確認する場合:

```powershell
uv run python .\accounting_pdf_sorter.py --dry-run
```

結果は次のようなフォルダに出ます。

```text
test_materials\output\YYYYMMDD_HHMMSS
```

確認実行では、主に `shichijushichi_index_dry_run.csv` をExcelで開いて確認します。

## 8. 本番実行する

問題なさそうなら次を実行します。

```powershell
uv run python .\accounting_pdf_sorter.py
```

結果は次のようなフォルダに出ます。

```text
test_materials\output\YYYYMMDD_HHMMSS
```

中身:

```text
statements
shichijushichi_index.csv
shichijushichi_index.json
```

通常は `shichijushichi_index.csv` をExcelで開いて確認します。分割されたPDFは `statements` フォルダに入ります。

## 9. 日常運用

毎回やることは基本的にこれだけです。

1. `test_materials\input` に七十七銀行の明細PDFを入れる
2. PowerShellでツールのフォルダへ移動する
3. 確認実行する
4. 本番実行する
5. `shichijushichi_index.csv` と `statements` フォルダを確認する

コマンド例:

```powershell
cd $HOME\Documents\finance-categorizer
uv run python .\accounting_pdf_sorter.py --dry-run
uv run python .\accounting_pdf_sorter.py
```

## 10. Azure Document Intelligenceを使う場合

Azureを設定しなくても、PDFに埋め込みテキストがあればローカル抽出で動きます。読み取り精度を上げたい場合やローカル抽出でうまく読めない場合は、Azure Document Intelligenceを設定してください。

### 10.1 Azureリソースを作る

1. Azure Portalを開きます。

   https://portal.azure.com/

2. 検索窓で `Document Intelligence` を検索します。
3. `作成` を選びます。
4. 次のように設定します。

```text
Subscription: 自分のサブスクリプション
Resource group: 新規作成でOK 例 shichijushichi-ocr-rg
Region: Japan East など近い場所
Name: 任意 例 shichijushichi-ocr-free
Pricing tier: Free F0
```

必ず `Pricing tier` は `Free F0` を選んでください。

### 10.2 EndpointとKeyを取得する

作成したDocument Intelligenceリソースを開き、`Keys and Endpoint` を開きます。

次の2つを控えます。

```text
Endpoint
Key 1
```

### 10.3 Windowsに環境変数として保存する

PowerShellで次を実行します。

```powershell
setx AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT "https://xxxx.cognitiveservices.azure.com"
setx AZURE_DOCUMENT_INTELLIGENCE_KEY "ここにKey1"
```

`https://xxxx.cognitiveservices.azure.com` と `ここにKey1` は、自分のAzure画面に表示されている値に置き換えてください。

実行後、PowerShellを開き直します。

確認:

```powershell
echo $env:AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
```

Endpointが表示されればOKです。

### 10.4 Azureだけを強制して接続確認する

```powershell
uv run python .\accounting_pdf_sorter.py --dry-run --extractor azure
```

これで失敗する場合は、EndpointまたはKeyの設定を見直してください。

## 11. よくある困りごと

### `uv` が認識されない

uvをインストールした後、PowerShellを開き直してください。

```powershell
uv --version
```

で確認できます。

### PDFを入れたのに件数が0になる

PDFが `test_materials\input` に入っているか確認してください。サブフォルダの中に入れても処理できますが、出力先の `test_materials\output` に入れたPDFは処理対象から外れます。

### Azureが使われているか確認したい

出力された `shichijushichi_index.csv` の `抽出方法` 列を確認してください。

Azureで処理された場合:

```text
azure-document-intelligence
```

ローカル処理の場合:

```text
pdfplumber
```

### 無料枠を超えないか心配

Azure Document IntelligenceのFree F0は月500ページまでの無料枠です。課金はファイル数ではなくページ数で数えます。

例:

```text
1ページPDF 10個 -> 10ページ
5ページPDF 10個 -> 50ページ
```

使用量はAzure PortalのCost Managementやリソースのメトリックで確認してください。

## 12. 注意

Azureを使う場合、明細PDFの内容をAzureに送信します。社内ルール上、クラウドサービスへ明細を送ってよいか確認してから使ってください。

このツールは、最終的な会計判断を自動で確定するものではありません。日付・振込先・金額を一覧にして、人が確認しやすくするための補助ツールです。
