# format: 汎用コマンド実行テンプレート

`format/` は、設定値だけを書き換えてスケジューラから実行するスクリプトの雛形置き場です。特定の製品や処理に固定せず、同じ雛形を複数の用途へコピーして使うことを目的にします。

## 収録ファイル

| ファイル | 役割 |
| --- | --- |
| `run_command.ps1` | 任意の外部コマンドを、作業ディレクトリ・引数・環境変数を指定して実行する汎用テンプレート |
| `svn_update.txt` | 既存のSVN更新例。汎用テンプレートではなく、必要な場合だけ参照するサンプル |

## 基本的な使い方

1. `format/run_command.ps1` を用途ごとにコピーする。
2. コピー先で設定値を書き換える。
3. `tool/` などの実行用ディレクトリへ配置する。
4. `process_schedule.csv` に配置先の `.ps1` を登録する。
5. 初回は `$DryRun = $true` のまま実行結果を確認する。
6. 問題がなければ `$DryRun = $false` に変更する。

例:

```text
format/run_command.ps1
        ↓ コピー・設定
 tool/run_project_sync.ps1
        ↓ 登録
scheduler.py → tool/run_project_sync.ps1
```

## 設定項目

### `WorkingDirectory`

コマンドを実行する場所です。絶対パス、または設定済みスクリプトの配置先を基準とした相対パスを指定できます。

```powershell
$WorkingDirectory = 'C:\Work\Project'
# または
$WorkingDirectory = '..\Project'
```

### `Command`

実行するコマンド名または実行ファイルのパスです。処理内容はここで決まります。

```powershell
$Command = 'svn'
$Command = 'git'
$Command = 'python'
$Command = 'C:\Tools\my-command.exe'
```

### `Arguments`

コマンドへ渡す引数を配列で指定します。引数を配列に分けることで、空白を含むパスも扱いやすくなります。

```powershell
$Arguments = @('update', 'C:\Work\Project')
$Arguments = @('-C', 'C:\Work\Project', 'pull', '--ff-only')
$Arguments = @('scripts\job.py', '--once')
```

### `EnvironmentVariables`

実行するコマンドだけに渡す環境変数を指定します。スクリプト終了時には元の値へ戻します。

```powershell
$EnvironmentVariables = @{
    APP_MODE = 'production'
}
```

### `LogFile`

空欄の場合、標準出力・標準エラーは `scheduler.py` の `task_log.log` に記録されます。別ファイルにも保存したい場合だけ指定します。相対パスは `WorkingDirectory` 基準です。

```powershell
$LogFile = 'logs\command.log'
```

### `DryRun`

`$true` の場合、設定内容を表示するだけでコマンドを実行しません。初回確認後に `$false` へ変更します。

## 登録例

設定済みファイルを `tool/run_project_sync.ps1` として配置する場合:

```csv
ProcessName,Enabled,ExecutablePath,Arguments,Frequency,LastRunTime
run_project_sync,TRUE,.\tool\run_project_sync.ps1,,60,
```

このテンプレートの設定欄でコマンドと引数を管理するため、CSVの `Arguments` は空欄にできます。

## 終了コード

実行したコマンドの終了コードをそのまま返します。終了コードが0なら成功、それ以外なら失敗として `scheduler.py` のログから確認できます。コマンドを起動できない場合や設定が不正な場合は、テンプレート自身が終了コード1または2を返します。

## 運用上の注意

- `pause` を入れないでください。スケジューラから実行したときに処理が待機状態になります。
- パスワード、トークン、秘密鍵などを設定値や引数へ直接書かないでください。
- 長時間実行するコマンドは、`Frequency` を処理時間より十分長くしてください。現在の `scheduler.py` は実行開始時に `LastRunTime` を更新するため、間隔が短いと同じ処理が重複する可能性があります。
- スケジューラ自身が動いている作業コピーを、同じスケジューラから更新する用途には使用しないでください。
- `.ps1` は `scheduler.py` が PowerShell で起動するため、テンプレート内の設定だけで実行できます。
