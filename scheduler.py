"""
Python Task Scheduler - 定期タスク自動実行ツール

CSVファイルに定義されたタスクを定期的に監視・実行するスケジューラです。
多重起動防止、並列実行、リトライ機構などの堅牢性を備えています。

主な機能:
    - CSVベースのタスク定義管理
    - ThreadPoolExecutorによる並列実行
    - ソケット通信による多重起動防止
    - Windowsスタートアップへの自動登録
    - 複数の日付フォーマット対応
    - CSV書き込みリトライ機構

使用方法:
    # 通常起動（常駐）
    python scheduler.py
    
    # 1回だけ実行
    python scheduler.py --once
    
    # スタートアップに登録
    python scheduler.py --install
    
    # スタートアップから削除
    python scheduler.py --uninstall

設定:
    - CSV_PATH: タスク定義ファイル (process_schedule.csv)
    - LOG_PATH: ログファイル (task_log.log)
    - CHECK_INTERVAL: 監視間隔（秒）
    - LOCK_PORT: 多重起動防止用ポート番号

対応スクリプト形式:
    - .py: Python
    - .ps1: PowerShell
    - .bat/.cmd: Batch
    - .exe: 実行ファイル

Author: mm25356
Version: 2026/01/23
Environment: Python 3.11.9+
"""
import csv
import sys
import os
import time
import socket
import logging
import subprocess
import argparse
import datetime
import pathlib
import shlex
from dataclasses import dataclass, asdict
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

# --- 設定定数 ---
BASE_DIR = pathlib.Path(__file__).parent.absolute()
CSV_PATH = BASE_DIR / "process_schedule.csv"
LOG_PATH = BASE_DIR / "task_log.log"
LOCK_PORT = 62001
CHECK_INTERVAL = 300 # miniute
APP_NAME = "PyTaskScheduler"  # スタートアップ登録名
RETRY_COUNT = 5

# --- ロギング設定 ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


@dataclass
class Task:
    """
    タスク情報を保持するデータクラス
    
    CSVファイルから読み込んだタスク定義をカプセル化し、
    実行判定、ファイル検証、実行などのビジネスロジックを提供します。
    
    Attributes:
        ProcessName (str): タスク識別名
        Enabled (str): 有効フラグ ("true"/"false", "TRUE"/"FALSE")
        ExecutablePath (str): 実行ファイルのパス（相対 or 絶対）
        Arguments (str): コマンドライン引数
        Frequency (str): 実行間隔（分単位、文字列）
        LastRunTime (str): 最終実行時刻（ISO形式文字列）
    """
    ProcessName: str
    Enabled: str
    ExecutablePath: str
    Arguments: str = ""
    Frequency: str = "0"
    LastRunTime: str = ""
    
    @classmethod
    def from_csv_row(cls, row: dict) -> 'Task':
        """
        CSVの行（dict）からTaskインスタンスを生成
        
        Args:
            row (dict): csv.DictReaderから取得した行データ
        
        Returns:
            Task: Taskインスタンス
        """
        return cls(
            ProcessName=row.get('ProcessName', 'Unknown'),
            Enabled=row.get('Enabled', 'false'),
            ExecutablePath=row.get('ExecutablePath', ''),
            Arguments=row.get('Arguments', ''),
            Frequency=row.get('Frequency', '0'),
            LastRunTime=row.get('LastRunTime', '')
        )
    
    def to_csv_row(self) -> dict:
        """
        TaskインスタンスをCSV行（dict）に変換
        
        Returns:
            dict: CSVに書き込める形式の辞書
        """
        return asdict(self)
    
    def is_enabled(self) -> bool:
        """
        タスクが有効かどうか判定
        
        Returns:
            bool: 有効な場合True
        """
        return self.Enabled.lower() in ('true', '1', 'yes')
    
    def get_frequency_minutes(self) -> int:
        """
        実行頻度を分単位の整数で取得
        
        Returns:
            int: 実行間隔（分）
        
        Raises:
            ValueError: Frequencyが数値でない場合
        """
        return int(self.Frequency)
    
    def get_absolute_path(self) -> pathlib.Path:
        """
        実行ファイルの絶対パスを取得
        
        相対パスの場合はプロジェクトルート（BASE_DIR）からの
        絶対パスに変換します。
        
        Returns:
            Path: 絶対パスのPathオブジェクト
        """
        f_path = pathlib.Path(self.ExecutablePath)
        if not f_path.is_absolute():
            f_path = BASE_DIR / f_path
        return f_path
    
    def file_exists(self) -> bool:
        """
        実行ファイルが存在するか確認
        
        Returns:
            bool: ファイルが存在する場合True
        """
        return self.get_absolute_path().exists()
    
    def validate(self) -> tuple[bool, str]:
        """
        タスクデータの整合性を検証
        
        ExecutablePathの存在確認とFrequencyの数値型チェックを行います。
        
        Returns:
            tuple: (is_valid: bool, message: str)
                - is_valid: 検証が成功した場合True
                - message: エラーメッセージ（成功時は空文字列）
        """
        if not self.ExecutablePath:
            return False, f"[{self.ProcessName}] Missing ExecutablePath."
        
        try:
            self.get_frequency_minutes()
        except ValueError:
            return False, f"[{self.ProcessName}] Frequency is not a valid number."
        
        if not self.file_exists():
            return False, f"[{self.ProcessName}] File not found: {self.get_absolute_path()}"
        
        return True, ""
    
    def should_run(self, current_time: datetime.datetime) -> tuple[bool, str]:
        """
        タスクを実行すべきか判定
        
        Enabledフラグ、実行頻度、最終実行時刻から、
        現在のタイミングでタスクを実行すべきか判定します。
        
        Args:
            current_time (datetime): 現在時刻
        
        Returns:
            tuple: (should_run: bool, reason: str)
                - should_run: 実行すべき場合True
                - reason: 判定理由（"First Run", "Scheduled", "Disabled", etc.）
        """
        if not self.is_enabled():
            return False, "Disabled"
        
        freq_min = self.get_frequency_minutes()
        
        if not self.LastRunTime:
            return True, "First Run"
        
        try:
            last_run = self._parse_last_run_time(self.LastRunTime)
            if last_run is None:
                return True, "Invalid Date Reset"
            
            next_run = last_run + datetime.timedelta(minutes=freq_min)
            if current_time >= next_run:
                return True, "Scheduled"
            else:
                return False, f"Next run: {next_run}"
        except Exception as e:
            logger.warning(f"[{self.ProcessName}] Date parsing error: {e}")
            return True, "Invalid Date Reset"
    
    def _parse_last_run_time(self, time_str: str) -> Optional[datetime.datetime]:
        """
        複数の日付フォーマットに対応してパース
        
        対応フォーマット:
        - YYYY-MM-DD HH:MM:SS (ISO形式)
        - YYYY/M/D HH:MM (スラッシュ区切り、秒なし)
        - YYYY-MM-DD HH:MM (ハイフン区切り、秒なし)
        
        Args:
            time_str (str): 日付文字列
        
        Returns:
            datetime.datetime or None: パース成功時はdatetimeオブジェクト、失敗時はNone
        """
        formats = [
            "%Y-%m-%d %H:%M:%S",  # 2026-06-22 08:57:09
            "%Y/%m/%d %H:%M",     # 2026/1/29 11:47
            "%Y-%m-%d %H:%M",     # 2026-06-22 08:57
            "%Y/%m/%d %H:%M:%S",  # 2026/1/29 11:47:00
        ]
        
        for fmt in formats:
            try:
                return datetime.datetime.strptime(time_str.strip(), fmt)
            except ValueError:
                continue
        
        logger.warning(f"[{self.ProcessName}] Unsupported date format: '{time_str}'")
        return None
    
    def execute(self) -> bool:
        """
        タスクを実行し、結果をログに記録
        
        ファイルの拡張子に応じて適切な実行方法を選択します:
        - .py: Pythonインタープリタで実行
        - .ps1: PowerShellで実行
        - .bat/.cmd: コマンドプロンプトで実行
        - その他: 直接実行
        
        Returns:
            bool: 実行成功時True、失敗時False
        
        Note:
            この関数はThreadPoolExecutor内で呼ばれるため、
            同期実行でもメインループはブロックされません。
        """
        full_path = self.get_absolute_path()
        suffix = full_path.suffix.lower()
        cmd = []

        if suffix == '.ps1':
            cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(full_path)]
        elif suffix == '.py':
            cmd = [sys.executable, str(full_path)]
        elif suffix in ['.bat', '.cmd']:
            cmd = ["cmd.exe", "/c", str(full_path)]
        else:
            cmd = [str(full_path)]

        if self.Arguments:
            cmd.extend(shlex.split(self.Arguments))

        logger.info(f"[{self.ProcessName}] Starting execution...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            
            output_msg = ""
            if result.stdout:
                output_msg += f"\n[STDOUT]\n{result.stdout.strip()}"
            if result.stderr:
                output_msg += f"\n[STDERR]\n{result.stderr.strip()}"

            if result.returncode == 0:
                logger.info(f"[{self.ProcessName}] Completed successfully.{output_msg}")
                return True
            else:
                logger.warning(f"[{self.ProcessName}] Failed (Code: {result.returncode}).{output_msg}")
                return False
        except Exception as e:
            logger.error(f"[{self.ProcessName}] Exception: {e}")
            return False
    
    def update_last_run_time(self, run_time: datetime.datetime) -> None:
        """
        最終実行時刻を更新
        
        Args:
            run_time (datetime): 実行時刻
        """
        self.LastRunTime = run_time.strftime("%Y-%m-%d %H:%M:%S")


class SingleInstanceLock:
    """
    多重起動防止クラス
    
    ソケット通信を利用して、スクリプトの同時実行を防止します。
    指定されたポートをバインドすることでロックを取得し、
    既に起動中の場合は新しいプロセスを終了させます。
    
    Attributes:
        port (int): ロック用のポート番号
        socket (socket.socket): TCPソケットオブジェクト
        _locked (bool): ロック取得状態
    
    Usage:
        with SingleInstanceLock():
            # 多重起動が防止された状態で実行
            main_process()
    """
    def __init__(self, port=LOCK_PORT):
        """
        Args:
            port (int): ロック用のポート番号（デフォルト: LOCK_PORT）
        """
        self.port = port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._locked = False

    def __enter__(self):
        """
        コンテキストマネージャのエントリポイント
        
        指定されたポートへのバインドを試み、ロックを取得します。
        バインド失敗時（既に起動中）はプロセスを終了します。
        
        Returns:
            SingleInstanceLock: 自身のインスタンス
        
        Raises:
            SystemExit: 既に起動中の場合
        """
        try:
            self.socket.bind(("127.0.0.1", self.port))
            self._locked = True
            return self
        except OSError:
            # 既に起動している場合はログを出さずに静かに終了（スタートアップ起動時の競合などを考慮）
            # 明示的に確認したい場合はログレベルを変更してください
            print("ALREADY RUNNING: Could not acquire lock. Exiting.")
            sys.exit(0)

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        コンテキストマネージャの終了処理
        
        ロックが取得されている場合、ソケットをクローズします。
        
        Args:
            exc_type: 例外の型
            exc_val: 例外の値
            exc_tb: トレースバック
        """
        if self._locked:
            self.socket.close()

class StartupManager:
    """
    Windowsスタートアップ登録管理クラス
    
    スクリプトをWindowsのスタートアップに登録・削除するための
    バッチファイルを作成・管理します。仮想環境(venv)のactivate.batを
    自動探索し、適切な起動スクリプトを生成します。
    
    Attributes:
        app_name (str): アプリケーション名
        script_path (Path): スクリプトの絶対パス
        project_root (Path): プロジェクトルートディレクトリ
        startup_folder (Path): Windowsスタートアップフォルダのパス
        bat_path (Path): 生成されるバッチファイルのパス
    
    Note:
        Windows以外のプラットフォームでは機能しません。
    """
    def __init__(self, app_name=APP_NAME, script_path=None):
        """
        Args:
            app_name (str): アプリケーション名（デフォルト: APP_NAME）
            script_path (Path, optional): スクリプトパス（デフォルト: 現在のファイル）
        """
        self.app_name = app_name
        self.script_path = script_path if script_path else pathlib.Path(__file__).absolute()
        self.project_root = self.script_path.parent
        
        if sys.platform == "win32":
            self.startup_folder = pathlib.Path(os.environ['APPDATA']) / 'Microsoft' / 'Windows' / 'Start Menu' / 'Programs' / 'Startup'
            self.bat_path = self.startup_folder / f"{self.app_name}.bat"
        else:
            self.startup_folder = None
            self.bat_path = None

    def _find_activate_bat(self):
        """
        仮想環境のactivate.batを探索
        
        現在の実行環境やプロジェクト構成から activate.bat の位置を
        複数の候補パスから探索します。
        
        探索候補:
            - 現在のPython実行ファイルと同じディレクトリ
            - 現在のPython実行ファイルの親/Scripts
            - プロジェクトルート/venv/Scripts
            - プロジェクトルート/.venv/Scripts
            - プロジェクトルート/env/Scripts
        
        Returns:
            Path or None: activate.batのパス（見つからない場合はNone）
        """
        current_python_dir = pathlib.Path(sys.executable).parent
        
        # 探索候補リスト
        candidates = [
            current_python_dir / "activate.bat",                 # Scriptsフォルダ直下
            current_python_dir / "Scripts" / "activate.bat",     # python.exeの親/Scripts
            self.project_root / "venv" / "Scripts" / "activate.bat",  # 一般的なvenv
            self.project_root / ".venv" / "Scripts" / "activate.bat", # 一般的な.venv
            self.project_root / "env" / "Scripts" / "activate.bat"    # 一般的なenv
        ]

        for path in candidates:
            if path.exists():
                return path
        return None

    def install(self):
        """
        スクリプトをWindowsスタートアップに登録
        
        仮想環境のactivate.batを自動検出し、適切な起動バッチファイルを
        スタートアップフォルダに作成します。pythonw.exeを優先して使用し、
        ウィンドウを表示せずにバックグラウンド実行します。
        
        Raises:
            Exception: バッチファイル作成に失敗した場合
        
        Note:
            Windows以外のプラットフォームではエラーログを出力して終了します。
        """
        if sys.platform != "win32":
            logger.error("Startup registration is only supported on Windows.")
            return

        try:
            activate_path = self._find_activate_bat()
            
            # pythonw.exe を使用してウィンドウを表示せずに実行する
            # sys.executableが python.exe の場合、pythonw.exe に置換を試みる
            python_exe = sys.executable
            if "python.exe" in python_exe.lower():
                pythonw_candidate = python_exe.lower().replace("python.exe", "pythonw.exe")
                if os.path.exists(pythonw_candidate):
                    python_exe = pythonw_candidate

            # バッチファイルの内容作成
            content = ["@echo off"]
            content.append(f'cd /d "{self.project_root}"')
            
            if activate_path:
                content.append(f'if exist "{activate_path}" call "{activate_path}"')
            else:
                logger.warning("activate.bat not found. Using global python environment.")

            # start "" "path_to_pythonw" "path_to_script"
            content.append(f'start "" "{python_exe}"  "{self.script_path}"')

            with open(self.bat_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content))
            
            logger.info(f"Startup script created at: {self.bat_path}")
            print(f"Success: Registered to Windows Startup.\nPath: {self.bat_path}")

        except Exception as e:
            logger.error(f"Failed to create startup script: {e}")
            print(f"Error: {e}")

    def uninstall(self):
        """
        スクリプトをWindowsスタートアップから削除
        
        スタートアップフォルダに作成されたバッチファイルを削除します。
        
        Note:
            バッチファイルが存在しない場合は情報メッセージを表示します。
        """
        if self.bat_path and self.bat_path.exists():
            try:
                os.remove(self.bat_path)
                logger.info(f"Startup script removed: {self.bat_path}")
                print("Success: Removed from Windows Startup.")
            except Exception as e:
                logger.error(f"Failed to remove startup script: {e}")
                print(f"Error: {e}")
        else:
            print("Info: Startup script does not exist.")


class CSVValidator:
    """
    CSVファイル構造の検証を行うクラス
    
    タスクCSVファイルが必要なヘッダー列を持っているか検証します。
    
    Attributes:
        REQUIRED_HEADERS (set): CSV必須ヘッダー列の集合
    """
    REQUIRED_HEADERS = {'Enabled', 'ProcessName', 'ExecutablePath', 'Frequency'}

    @staticmethod
    def validate_csv_structure(fieldnames: list) -> tuple[bool, str]:
        """
        CSVファイルの構造を検証
        
        必須ヘッダー（Enabled, ProcessName, ExecutablePath, Frequency）が
        すべて存在するかチェックします。
        
        Args:
            fieldnames (list): CSVのヘッダー列リスト
        
        Returns:
            tuple: (is_valid: bool, message: str)
                - is_valid: 検証が成功した場合True
                - message: エラーメッセージ（成功時は空文字列）
        """
        if not CSVValidator.REQUIRED_HEADERS.issubset(fieldnames):
            missing = CSVValidator.REQUIRED_HEADERS - set(fieldnames)
            return False, f"Missing headers: {missing}"
        return True, ""


class Scheduler:
    """
    メインスケジューラクラス
    
    CSVファイルを定期的に監視し、登録されたタスクを適切なタイミングで
    実行します。ThreadPoolExecutorを使用した並列実行に対応し、
    実行結果をCSVファイルに書き戻します。
    
    Attributes:
        executor (ThreadPoolExecutor): タスク実行用のスレッドプール
        last_run_cache (dict): CSV書き込み失敗時のフォールバック用キャッシュ
    """
    def __init__(self):
        """
        スケジューラを初期化
        
        最大5ワーカーのスレッドプールと、実行時刻キャッシュを準備します。
        """
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.last_run_cache = {}  # {ProcessName: LastRunTime_str}

    def process_tasks(self):
        """
        タスク処理のメインロジック
        
        CSVファイルを読み込み、各タスクの検証・実行判定を行い、
        実行すべきタスクをスレッドプールに投入します。
        実行後はLastRunTimeを更新してCSVに書き戻します。
        
        処理フロー:
            1. CSVファイル読み込み
            2. CSV構造検証
            3. Taskオブジェクトに変換
            4. キャッシュからLastRunTime復元（必要な場合）
            5. タスク検証（Enabled、ファイル存在等）
            6. 実行タイミング判定
            7. タスク実行（非同期）
            8. LastRunTime更新
            9. CSV書き戻し
        
        Note:
            CSV書き込みに失敗した場合、last_run_cacheに状態を保持します。
        """
        if not CSV_PATH.exists():
            logger.error(f"CSV file not found: {CSV_PATH}")
            return

        updated = False
        now = datetime.datetime.now()
        tasks = []

        try:
            with open(CSV_PATH, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                
                is_valid_csv, msg = CSVValidator.validate_csv_structure(fieldnames)
                if not is_valid_csv:
                    logger.error(msg)
                    return

                if 'LastRunTime' not in fieldnames:
                    fieldnames = list(fieldnames) + ['LastRunTime']
                
                rows = list(reader)

            for row in rows:
                task = Task.from_csv_row(row)
                
                # In-memory cache fallback: If CSV write failed previously, use cached time
                if task.ProcessName in self.last_run_cache:
                    cached_time = self.last_run_cache[task.ProcessName]
                    if cached_time:
                        task.LastRunTime = cached_time

                # Validation
                is_valid, msg = task.validate()
                if not is_valid:
                    logger.warning(msg)
                    tasks.append(task)
                    continue

                # Check Enabled status
                if not task.is_enabled():
                    logger.debug(f"[{task.ProcessName}] Disabled. Skipping.")
                    tasks.append(task)
                    continue

                # Check execution timing
                should_run, reason = task.should_run(now)
                
                if should_run:
                    logger.info(f"[{task.ProcessName}] Triggered ({reason})")
                    self.executor.submit(task.execute)
                    
                    task.update_last_run_time(now)
                    self.last_run_cache[task.ProcessName] = task.LastRunTime
                    updated = True
                
                tasks.append(task)

            if updated:
                self._update_csv(fieldnames, tasks)

        except Exception as e:
            logger.error(f"Scheduler processing error: {e}")

    def _update_csv(self, fieldnames: list, tasks: list):
        """
        CSVファイルを更新（リトライ機構付き）
        
        一時ファイルに書き込んでから、os.replaceで原子的に置換します。
        Windowsのファイルロック問題に対応するため、失敗時はリトライします。
        
        Args:
            fieldnames (list): CSVヘッダー列リスト
            tasks (list[Task]): Taskオブジェクトのリスト
        
        Note:
            最大RETRY_COUNT回までリトライし、失敗した場合は一時ファイルを削除します。
        """
        temp_path = CSV_PATH.with_suffix('.tmp')
        try:
            with open(temp_path, mode='w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows([task.to_csv_row() for task in tasks])
            
            # Retry logic for file locking issues (WinError 5)
            for attempt in range(RETRY_COUNT):
                try:
                    os.replace(temp_path, CSV_PATH)
                    logger.info("CSV Schedule updated.")
                    break
                except OSError as e:
                    # WinError 5 (Access Denied) or 32 (Sharing Violation) など
                    if attempt < RETRY_COUNT - 1:
                        wait_time = 1 + (attempt * 0.5)
                        logger.warning(f"CSV update failed (Attempt {attempt+1}/{RETRY_COUNT}). Retrying in {wait_time}s. Error: {e}")
                        time.sleep(wait_time)
                    else:
                        raise
        except Exception as e:
            logger.error(f"Failed to update CSV: {e}")
            # クリーンアップ
            if temp_path.exists():
                try:
                    os.remove(temp_path)
                except:
                    pass

    def run_loop(self):
        """
        スケジューラのメインループ
        
        CHECK_INTERVAL秒ごとにprocess_tasks()を呼び出し、
        タスクの実行判定と実行を継続的に行います。
        
        Note:
            このメソッドは無限ループです。終了するには外部からプロセスを停止してください。
        """
        logger.info(f"Scheduler started. Interval: {CHECK_INTERVAL}s")
        while True:
            self.process_tasks()
            time.sleep(CHECK_INTERVAL)

def main():
    """
    エントリポイント関数
    
    コマンドライン引数を解析し、以下のモードで動作します:
    - 通常モード: スケジューラを常駐起動
    - --once: タスクを1回だけ実行して終了
    - --install: Windowsスタートアップに登録
    - --uninstall: Windowsスタートアップから削除
    
    多重起動防止機構により、既に起動中の場合は新しいプロセスが自動終了します。
    """
    parser = argparse.ArgumentParser(description="Python Task Scheduler")
    parser.add_argument("--once", action="store_true", help="Run tasks once and exit (no loop)")
    parser.add_argument("--install", action="store_true", help="Register script to Windows Startup")
    parser.add_argument("--uninstall", action="store_true", help="Remove script from Windows Startup")
    args = parser.parse_args()

    # スタートアップ設定の管理
    startup_mgr = StartupManager()
    if args.install:
        startup_mgr.install()
        return
    if args.uninstall:
        startup_mgr.uninstall()
        return

    # 通常実行
    with SingleInstanceLock():
        scheduler = Scheduler()
        if args.once:
            scheduler.process_tasks()
        else:
            scheduler.run_loop()

if __name__ == "__main__":
    main()
