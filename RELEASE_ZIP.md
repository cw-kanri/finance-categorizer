# zipリリース手順

この手順は、開発者または配布担当者が利用者向けのzipファイルを作るためのものです。利用者にはリポジトリをクローンしてもらわず、この手順で作ったzipを渡します。

## 配布物に含めるもの

zipには次のものだけを入れます。

- `accounting_pdf_sorter.py`
- `requirements.txt`
- `WINDOWS_SETUP.md`
- `test_materials\input`
- `test_materials\output`

次のものは入れません。

- `.git`
- `.venv`
- `__pycache__`
- `test_materials\output` の過去の実行結果
- テスト用PDFや社内データ
- `test_accounting_pdf_sorter.py`

## リリース前の確認

リポジトリのルートで次を実行します。

```powershell
uv run python -m unittest -v
```

テストが通ったら、配布物に実データが混ざっていないか確認します。

```powershell
Get-ChildItem .\test_materials -Recurse
```

## zipを作る

リポジトリのルートで次を実行します。`v1.0.0` は実際のバージョン名に置き換えてください。

```powershell
$version = "v1.0.0"
$releaseRoot = ".\release"
$packageName = "finance-categorizer-$version"
$packageDir = Join-Path $releaseRoot $packageName

New-Item -ItemType Directory -Force $packageDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $packageDir "test_materials\input") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $packageDir "test_materials\output") | Out-Null

Copy-Item .\accounting_pdf_sorter.py -Destination $packageDir -Force
Copy-Item .\requirements.txt -Destination $packageDir -Force
Copy-Item .\WINDOWS_SETUP.md -Destination $packageDir -Force

$zipPath = Join-Path $releaseRoot "$packageName.zip"
Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -Force
```

`-Force` の後ろには何も付けません。もし `-Force]` のように `]` が付いていると、PowerShellでエラーになります。

作成されるzip:

```text
release\finance-categorizer-v1.0.0.zip
```

## zipの中身を確認する

```powershell
Expand-Archive .\release\finance-categorizer-v1.0.0.zip .\release\check -Force
Get-ChildItem .\release\check -Recurse
```

次のような構成になっていればOKです。

```text
release\check
  accounting_pdf_sorter.py
  requirements.txt
  WINDOWS_SETUP.md
  test_materials
    input
    output
```

## よくあるエラー

### `A parameter cannot be found that matches parameter name 'Force]'`

コマンド末尾に余分な `]` が入っています。次のように、最後を `-Force` で終わらせてください。

```powershell
Compress-Archive -Path (Join-Path $packageDir "*") -DestinationPath $zipPath -Force
```

## 利用者へ案内すること

利用者には次のように伝えます。

```text
添付の finance-categorizer-v1.0.0.zip を展開し、中にある WINDOWS_SETUP.md の手順に沿ってセットアップしてください。
Gitのインストールやリポジトリのクローンは不要です。
```

## GitHub Releasesにzipを追加する

GitHubでリポジトリを開き、次の手順でzipを公開します。

1. 右側、または上部付近にある `Releases` を開きます。
2. `Draft a new release` を選びます。
3. `Choose a tag` にバージョン名を入れます。例: `v1.0.0`
4. 新しいタグの場合は `Create new tag: v1.0.0 on publish` を選びます。
5. `Release title` に分かりやすい名前を入れます。例: `七十七銀行 明細PDF仕分けツール v1.0.0`
6. 説明欄に変更内容や利用者向け案内を書きます。
7. `Attach binaries by dropping them here or selecting them` の場所へ、作成したzipをドラッグ&ドロップします。
8. `Publish release` を押します。

添付するファイル:

```text
release\finance-categorizer-v1.0.0.zip
```

説明欄の例:

```text
七十七銀行の明細PDFを、仕訳添付しやすい名前のPDFとCSV一覧に整理するツールです。

使い始める方は、下の Assets から finance-categorizer-v1.0.0.zip をダウンロードし、展開後に WINDOWS_SETUP.md を読んでください。
Gitのインストールやリポジトリのクローンは不要です。
```

公開後、利用者にはGitHub ReleasesのページURLを共有します。利用者は `Assets` の中にあるzipファイルをダウンロードします。

## バージョンを更新するとき

修正を入れたら、もう一度テストを実行してから新しいバージョン名でzipを作ります。

例:

```powershell
$version = "v1.0.1"
```

利用者には古いフォルダを残したまま新しいzipを展開してもらうと、過去の出力結果と混ざりにくくなります。
