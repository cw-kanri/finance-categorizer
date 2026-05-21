# Windows向け 初回セットアップ手順

この手順書は、バックオフィス担当者がWindows PCでPDF請求書仕分けツールを使い始めるためのものです。

## 1. 前提

対応OSは、いったんWindowsのみです。

使うもの:

- Windows 10 または Windows 11
- PowerShell
- Git
- uv
- Azure Document Intelligence Free F0プラン

## 2. Gitをインストールする

1. 次のページを開きます。

   https://git-scm.com/download/win

2. Windows用インストーラーをダウンロードします。
3. インストーラーを実行します。
4. 基本的には既定のまま `Next` で進めてインストールします。
5. PowerShellを新しく開き、次を実行します。

```powershell
git --version
```

バージョンが表示されればOKです。

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

## 4. リポジトリをクローンする

作業用フォルダへ移動します。例として `Documents` に置く場合:

```powershell
cd $HOME\Documents
```

リポジトリをクローンします。

```powershell
git clone <リポジトリURL>
```

作成されたフォルダへ移動します。

```powershell
cd finance-categorizer
```

`<リポジトリURL>` は、GitHubなどで共有されたURLに置き換えてください。

## 5. Python環境を作る

リポジトリのフォルダで、次を実行します。

```powershell
uv venv
uv pip install -r requirements.txt
```

初回だけ少し時間がかかります。

## 6. Azure Free Planを準備する

このツールは、Azure Document Intelligence の請求書読み取り機能を使えます。Free F0プランでは月500ページまで無料枠があります。

### 6.1 Azureリソースを作る

1. Azure Portalを開きます。

   https://portal.azure.com/

2. 検索窓で `Document Intelligence` を検索します。
3. `作成` を選びます。
4. 次のように設定します。

```text
Subscription: 自分のサブスクリプション
Resource group: 新規作成でOK 例 invoice-ocr-rg
Region: Japan East など近い場所
Name: 任意 例 invoice-ocr-free
Pricing tier: Free F0
```

必ず `Pricing tier` は `Free F0` を選んでください。

### 6.2 EndpointとKeyを取得する

作成したDocument Intelligenceリソースを開き、`Keys and Endpoint` を開きます。

次の2つを控えます。

```text
Endpoint
Key 1
```

### 6.3 Windowsに環境変数として保存する

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

## 7. PDF請求書を入れる

処理したいPDFや画像を次のフォルダに入れます。

```text
test_materials\input
```

対応ファイル:

- PDF
- PNG
- JPG/JPEG
- TIFF
- BMP

## 8. まず確認実行する

いきなりファイルをコピー・分割せず、確認だけする場合:

```powershell
uv run python .\accounting_pdf_sorter.py --dry-run
```

Azureの環境変数が設定されていれば、自動でAzureを使います。設定されていなければ、ローカル抽出に切り替わります。

## 9. 本番実行する

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
attachments
attachment_index.csv
attachment_index.json
```

通常は `attachment_index.csv` をExcelで開いて確認します。

## 10. 日常運用

毎回やることは基本的にこれだけです。

1. `test_materials\input` にPDF請求書を入れる
2. PowerShellでリポジトリへ移動する
3. 確認実行する
4. 本番実行する
5. `attachment_index.csv` を確認する

コマンド例:

```powershell
cd $HOME\Documents\finance-categorizer
uv run python .\accounting_pdf_sorter.py --dry-run
uv run python .\accounting_pdf_sorter.py
```

## 11. よくある困りごと

### `git` が認識されない

Gitをインストールした後、PowerShellを開き直してください。

```powershell
git --version
```

で確認できます。

### `uv` が認識されない

uvをインストールした後、PowerShellを開き直してください。

```powershell
uv --version
```

で確認できます。

### Azureが使われているか確認したい

出力された `attachment_index.csv` の `extractor` 列を確認してください。

Azureで処理された場合:

```text
azure-document-intelligence
```

ローカル処理の場合:

```text
pdfplumber
tesseract
local-no-tesseract
local-no-poppler
```

### Azureだけを強制して接続確認したい

```powershell
uv run python .\accounting_pdf_sorter.py --dry-run --extractor azure
```

これで失敗する場合は、EndpointまたはKeyの設定を見直してください。

### 無料枠を超えないか心配

Azure Document IntelligenceのFree F0は月500ページまでの無料枠です。課金はファイル数ではなくページ数で数えます。

例:

```text
1ページPDF 10個 -> 10ページ
5ページPDF 10個 -> 50ページ
```

使用量はAzure PortalのCost Managementやリソースのメトリックで確認してください。

## 12. 注意

請求書PDFをAzureに送信します。社内ルール上、クラウドサービスへ請求書を送ってよいか確認してから使ってください。

このツールは、最終的な会計判断を自動で確定するものではありません。日付・取引先・金額を一覧にして、人が確認しやすくするための補助ツールです。
