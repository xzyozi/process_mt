# 依存関係の管理フロー

このプロジェクトでは、Pythonの依存関係を管理するために `pip-tools` を使用します。
これにより、開発環境の再現性を高め、依存関係をクリーンに保ちます。

## 概要

依存関係は2つのファイルで管理されます。

-   `requirements.in`: プロジェクトが**直接**必要とするライブラリを記述するファイルです。**手で編集するのはこのファイルだけです。**
-   `requirements.txt`: `pip-compile`によって**自動生成**されるファイルです。プロジェクトの全依存ライブラリ（間接的なものも含む）とそのバージョンが固定されています。このファイルは手で編集しないでください。

---

## 新しいライブラリを追加する手順

1.  **`requirements.in` にライブラリを追加**
    -   プロジェクトのルートにある `requirements.in` ファイルを開き、追加したいライブラリ名を追記します。バージョンを指定することも可能ですが、通常は指定せずに最新の互換バージョンを自動で選択させます。

    ```
    # requirements.in

    flask
    requests
    # 新しいライブラリを追記
    new-library
    ```

2.  **`requirements.txt` を更新**
    -   ターミナルで以下のコマンドを実行し、`requirements.txt` を再生成します。

    ```bash
    pip-compile requirements.in
    ```

3.  **ライブラリのインストール**
    -   更新された `requirements.txt` を使して、ライブラリをインストールします。

    ```bash
    pip install -r requirements.txt
    ```

4.  **ファイルをコミット**
    -   変更された `requirements.in` と `requirements.txt` の両方をGitにコミットしてください。

---

## 新しい開発環境をセットアップする手順

1.  **リポジトリをクローン**
    -   `git clone ...`

2.  **仮想環境の作成と有効化** (推奨)
    ```bash
    python -m venv .venv
    source .venv/bin/activate  # Linux/macOS
    # .venv\Scripts\activate  # Windows
    ```

3.  **依存ライブラリのインストール**
    -   `requirements.txt` を使って、プロジェクトに必要な全てのライブラリをインストールします。

    ```bash
    pip install -r requirements.txt
    ```

4.  **初期セットアップの実行**
    -   データベースのマイグレーションや、`esbuild`のセットアップを行います。

    ```bash
    python setup.py
    ```

これで、開発を開始する準備が整います。
