# 開発・継続運用メモ

この文書は、このリポジトリを保守する開発者向けの入口です。利用者向けの配布物は zip で渡す前提なので、利用者に Git やリポジトリ構成を説明する必要はありません。利用者向け手順は [WINDOWS_SETUP.md](WINDOWS_SETUP.md)、zip 作成手順は [RELEASE_ZIP.md](RELEASE_ZIP.md) に分けています。

ここでは、個別の作業チケットではなく、継続運用のたびに立ち返るべき前提と確認観点をまとめます。

## 基本方針

このツールの役割は、七十七銀行の明細 PDF を、仕訳添付しやすい PDF と一覧 CSV/JSON に整理することです。会計判断を自動化するものではなく、明細の確認を人間がしやすくする補助ツールとして扱います。

保守時は、次の性質を壊さないことを優先します。

- 入力 PDF は直接書き換えない
- 出力は実行ごとのフォルダに分け、過去の出力と混ざらないようにする
- Excel で開きやすい UTF-8 BOM 付き CSV を維持する
- Azure Document Intelligence が使える場合でも、未設定時や失敗時にローカル抽出へ戻れるようにする
- 実データ、テスト用 PDF、認証情報をリポジトリや配布 zip に混ぜない

## リポジトリをクローンする

開発者は、まず GitHub からリポジトリを自分の PC にクローンします。利用者向け zip とは違い、開発者は Git の履歴を含む作業用フォルダを持つ前提です。

Git が入っているか確認します。

```powershell
git --version
```

バージョンが表示されれば Git は使えます。表示されない場合は、Git for Windows をインストールしてから PowerShell を開き直します。

作業用フォルダへ移動します。場所は社内ルールや個人の運用に合わせて構いませんが、例として `Documents` の下に置く場合は次のようにします。

```powershell
cd $HOME\Documents
```

GitHub のリポジトリ URL を使ってクローンします。

```powershell
git clone <repository-url>
```

`<repository-url>` は、GitHub のリポジトリ画面にある `Code` ボタンからコピーした URL に置き換えます。HTTPS の例は次の形です。

```powershell
git clone https://github.com/<owner>/<repository>.git
```

クローンできたら、作成されたフォルダへ移動します。

```powershell
cd .\finance-categorizer
```

正しい場所にいるか確認します。

```powershell
Get-ChildItem
git status
```

`accounting_pdf_sorter.py`、`requirements.txt`、`README.md` などが表示され、`git status` で現在のブランチ情報が表示されれば、開発作業を始められる状態です。

## クローン後に確認すること

まず、リポジトリルートで作業していることを確認します。主なファイルは次の通りです。

```text
accounting_pdf_sorter.py       本体
test_accounting_pdf_sorter.py  ユニットテスト
requirements.txt               Python 依存関係
README.md                      プロジェクト概要
WINDOWS_SETUP.md               利用者向けセットアップ
RELEASE_ZIP.md                 配布 zip 作成手順
```

開発環境は `uv` を前提にします。環境を作る時は、グローバル Python に直接依存関係を入れず、リポジトリ内の仮想環境を使います。

```powershell
uv venv
uv pip install -r requirements.txt
```

この時点で重要なのは、コマンドを覚えることよりも「このリポジトリの実行結果は、現在の Python、`requirements.txt`、入力 PDF の組み合わせで決まる」と把握することです。不具合を調べる時は、この3つを分けて確認します。

## 実行と出力の前提

既定の入力は `test_materials/input`、既定の出力は `test_materials/output/<実行時刻>/` です。

```powershell
uv run python .\accounting_pdf_sorter.py
```

PDF 分割をせず、抽出結果だけを CSV/JSON で確認したい場合は `--dry-run` を使います。

```powershell
uv run python .\accounting_pdf_sorter.py --dry-run
```

任意のフォルダを処理する場合も、入力と出力を明示して、入力フォルダの中に出力フォルダを混ぜないようにします。

```powershell
uv run python .\accounting_pdf_sorter.py ".\input" --output-dir ".\output"
```

出力された CSV の `抽出方法` 列を見ると、`pdfplumber` と `azure-document-intelligence` のどちらで読まれたかを確認できます。抽出精度の差や失敗原因を追う時は、この列を最初に見ます。

## 依存関係の不具合を見る時の考え方

依存関係の問題は、将来的に起こりやすい運用トラブルです。特に PDF 処理ライブラリは、Python のバージョン、PDF の作られ方、ライブラリ側の更新の影響を受けます。

まず、問題を次のどれに近いかで分けます。

- 環境が作れない: `uv`、Python、仮想環境、ネットワーク、パッケージ取得の問題
- 起動できない: `pdfplumber` や `pypdf` が import できない、依存ライブラリのバージョン不整合
- 読み取れない: PDF 内のテキスト構造、Azure 設定、OCR 精度、銀行側 PDF レイアウト変更
- 出力が期待と違う: ファイル名生成、日付や金額のパース、CSV の文字コード、出力先の混在

環境が疑わしい場合は、仮想環境を作り直す前に、現在の前提を記録します。少なくとも次を見ます。

```powershell
uv --version
uv run python --version
uv pip list
```

依存関係を更新する場合は、まず `requirements.txt` の意図を確認します。現在は次の2つが直接依存です。

```text
pdfplumber  PDF から埋め込みテキストを読む
pypdf       PDF をページ単位に分割して書き出す
```

不具合対応で依存関係を上げる場合も下げる場合も、変更後に必ず同じ観点で比較します。

- ユニットテストが通るか
- `--dry-run` の CSV/JSON が期待通りか
- 実際に分割された PDF が1ページずつになっているか
- Excel で CSV を開いた時に文字化けしないか
- Azure 未設定時にローカル抽出で動くか

依存関係のエラーをコードのバグと決めつけないことが大切です。逆に、依存関係のせいにしてパース仕様の変化を見逃さないことも大切です。環境、入力 PDF、コード変更を切り分けてから判断します。

## Azure Document Intelligence の扱い

通常の実行は `--extractor auto` です。次の環境変数が両方ある場合は Azure の `prebuilt-layout` を使い、なければ `pdfplumber` によるローカル抽出を使います。

```powershell
$env:AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT
$env:AZURE_DOCUMENT_INTELLIGENCE_KEY
```

Azure を明示的に確認する時は、`--extractor azure` を使います。

```powershell
uv run python .\accounting_pdf_sorter.py --dry-run --extractor azure
```

Azure は読み取り精度を上げるための選択肢ですが、常に使える前提にしません。認証情報、料金、社内ルール、ネットワーク、クラウド送信可否の影響を受けるためです。保守時は、Azure なしでも最低限の運用確認ができる状態を残します。

## 変更時に守る確認観点

コードを変更したら、まずユニットテストを実行します。

```powershell
uv run python -m unittest -v
```

ただし、このツールではテストが通るだけでは十分ではありません。PDF の抽出は入力ファイルの作られ方に依存するため、代表的な明細 PDF で `--dry-run` を行い、CSV/JSON を人間が確認します。

見るべき項目は固定です。

- 振込日が西暦 `YYYY-MM-DD` になっているか
- 振込先がファイル名に使える形へ正規化されているか
- 振込金額と手数料が入れ替わっていないか
- 給与明細のような複数人ページが `給与` として集約されているか
- 1ページ1振込の明細で、ページごとに別 PDF が出ているか
- 同名ファイルが発生した時に `_001` のように重複回避されるか
- 出力先に過去の結果や入力 PDF が混ざっていないか

銀行側の PDF レイアウトが変わった場合は、パース処理だけでなくテストデータの意図も更新します。`test_accounting_pdf_sorter.py` には、日付変換、合計行、振込先行、給与明細、出力ディレクトリ、PDF 分割の基本仕様が入っています。仕様を変える時は、先に「何を正しい結果とするか」をテストで表現します。

## 配布前に考えること

利用者にはリポジトリではなく zip を渡します。配布前は [RELEASE_ZIP.md](RELEASE_ZIP.md) に従い、テストの通過と配布物の中身を確認します。

配布 zip に含めるのは、利用者が実行に必要な最小限のファイルです。開発用ファイル、仮想環境、Git 履歴、過去の出力、実データは含めません。

バージョンを更新する時は、利用者が古いフォルダを残したまま新しい zip を展開できるようにします。同じフォルダへ上書きさせる運用にすると、過去の出力や入力 PDF と混ざり、原因調査が難しくなります。

## トラブル調査で残すべき情報

再現性を保つため、問い合わせや不具合調査では次の情報を残します。

- 実行したコマンド
- `uv --version` と `uv run python --version`
- `requirements.txt` の内容
- Azure を使ったかどうか
- 出力 CSV の `抽出方法`
- 問題が出た PDF の種類とページ数
- 期待した値と実際に出た値
- エラーメッセージ全文

認証情報や明細 PDF そのものを共有する場合は、社内ルールに従います。Azure を使う場合、PDF の内容がクラウドサービスへ送信される点を必ず意識します。

## ドキュメントの役割分担

ドキュメントを更新する時は、読者ごとに置き場所を分けます。

- `README.md`: ツールの概要と入口
- `DEVELOPMENT.md`: 開発者の保守方針、環境、調査観点
- `WINDOWS_SETUP.md`: zip を受け取った利用者の初回セットアップ
- `RELEASE_ZIP.md`: 開発者・配布担当者の zip 作成手順

README にすべてを戻すと、利用者向け情報と開発者向け情報が混ざります。運用中に迷った時は、「誰が読む文書か」を先に決めてから追記します。
