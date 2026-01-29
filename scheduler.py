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
from concurrent.futures import ThreadPoolExecutor

# --- 設定定数 ---
BASE_DIR = pathlib.Path(__file__).parent.absolute()
CSV_PATH = BASE_DIR / "process_schedule.csv"
LOG_PATH = BASE_DIR / "task_log.log"
LOCK_PORT = 62000
CHECK_INTERVAL = 300 # miniute
APP_NAME = "PyTaskScheduler"  # スタートアップ登録名
RETRY_COOUT = 5

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

class SingleInstanceLock:
    """多重起動防止クラス"""
    def __init__(self, port=LOCK_PORT):
        self.port = port
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._locked = False

    def __enter__(self):
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
        if self._locked:
            self.socket.close()

class StartupManager:
    """
    Windowsのスタートアップ登録（バッチファイル作成）を管理するクラス
    仮想環境(venv)のactivate.batを自動探索するロジックを含む
    """
    def __init__(self, app_name=APP_NAME, script_path=None):
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
        現在の実行環境やプロジェクト構成から activate.bat を探索する
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
        """スタートアップにバッチファイルを作成"""
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
            content.append(f'start "" "{python_exe}" "{self.script_path}"')

            with open(self.bat_path, "w", encoding="utf-8") as f:
                f.write("\n".join(content))
            
            logger.info(f"Startup script created at: {self.bat_path}")
            print(f"Success: Registered to Windows Startup.\nPath: {self.bat_path}")

        except Exception as e:
            logger.error(f"Failed to create startup script: {e}")
            print(f"Error: {e}")

    def uninstall(self):
        """スタートアップからバッチファイルを削除"""
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

class TaskValidatorBase:
    """タスクデータの検証・判定を行う基底クラス"""
    REQUIRED_HEADERS = {'Enabled', 'ProcessName', 'ExecutablePath', 'Frequency'}

    def get_absolute_path(self, exec_path):
        f_path = pathlib.Path(exec_path)
        if not f_path.is_absolute():
            f_path = BASE_DIR / f_path
        return f_path

    def validate_csv_structure(self, fieldnames):
        if not self.REQUIRED_HEADERS.issubset(fieldnames):
            missing = self.REQUIRED_HEADERS - set(fieldnames)
            return False, f"Missing headers: {missing}"
        return True, ""

    def validate_row_data(self, row):
        name = row.get('ProcessName', 'Unknown')
        if not row.get('ExecutablePath'):
            return False, f"[{name}] Missing ExecutablePath."
        try:
            int(row.get('Frequency', 0))
        except ValueError:
            return False, f"[{name}] Frequency is not a valid number."
        return True, ""

    def check_file_existence(self, exec_path):
        f_path = self.get_absolute_path(exec_path)
        if not f_path.exists():
            return False, f"File not found: {f_path}"
        return True, str(f_path)

    def should_run_task(self, row, current_time):
        enabled = row.get('Enabled', '').lower() in ('true', '1', 'yes')
        if not enabled:
            return False, "Disabled"

        freq_min = int(row['Frequency'])
        last_run_str = row.get('LastRunTime', '')

        if not last_run_str:
            return True, "First Run"

        try:
            last_run = datetime.datetime.strptime(last_run_str, "%Y-%m-%d %H:%M:%S")
            next_run = last_run + datetime.timedelta(minutes=freq_min)
            if current_time >= next_run:
                return True, "Scheduled"
            else:
                return False, f"Next run: {next_run}"
        except ValueError:
            return True, "Invalid Date Reset"

class TaskRunner:
    """実行ロジッククラス"""
    @staticmethod
    def execute(row, full_path_str):
        name = row['ProcessName']
        args = row.get('Arguments', '')
        
        f_path = pathlib.Path(full_path_str)
        suffix = f_path.suffix.lower()
        cmd = []

        if suffix == '.ps1':
            cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", full_path_str]
        elif suffix == '.py':
            cmd = [sys.executable, full_path_str]
        elif suffix in ['.bat', '.cmd']:
            cmd = ["cmd.exe", "/c", full_path_str]
        else:
            cmd = [full_path_str]

        if args:
            cmd.extend(shlex.split(args))

        logger.info(f"[{name}] Starting execution...")
        try:
            # subprocess.run は同期実行だが、ThreadPoolExecutor内で呼ばれるためメインループをブロックしない
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            
            output_msg = ""
            if result.stdout:
                output_msg += f"\n[STDOUT]\n{result.stdout.strip()}"
            if result.stderr:
                output_msg += f"\n[STDERR]\n{result.stderr.strip()}"

            if result.returncode == 0:
                logger.info(f"[{name}] Completed successfully.{output_msg}")
                return True
            else:
                logger.warning(f"[{name}] Failed (Code: {result.returncode}).{output_msg}")
                return False
        except Exception as e:
            logger.error(f"[{name}] Exception: {e}")
            return False

class Scheduler(TaskValidatorBase):
    """メインスケジューラクラス"""
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=5)
        self.last_run_cache = {}  # {ProcessName: LastRunTime_str}

    def process_tasks(self):
        if not CSV_PATH.exists():
            logger.error(f"CSV file not found: {CSV_PATH}")
            return

        updated = False
        now = datetime.datetime.now()
        new_rows = []

        try:
            with open(CSV_PATH, mode='r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                
                is_valid_csv, msg = self.validate_csv_structure(fieldnames)
                if not is_valid_csv:
                    logger.error(msg)
                    return

                if 'LastRunTime' not in fieldnames:
                    fieldnames.append('LastRunTime')
                
                rows = list(reader)

            for row in rows:
                p_name = row.get('ProcessName')
                
                # In-memory cache fallback: If CSV write failed previously, use cached time
                if p_name in self.last_run_cache:
                    cached_time = self.last_run_cache[p_name]
                    # Only use cache if it looks valid and potentially newer (simple string replacement here)
                    if cached_time: 
                        row['LastRunTime'] = cached_time

                is_valid_row, msg = self.validate_row_data(row)
                if not is_valid_row:
                    logger.warning(msg)
                    new_rows.append(row)
                    continue

                # Check Enabled status first to avoid checking file existence for disabled tasks
                enabled = row.get('Enabled', '').lower() in ('true', '1', 'yes')
                if not enabled:
                    logger.debug(f"[{row['ProcessName']}] Disabled. Skipping.")
                    new_rows.append(row)
                    continue

                is_file_exist, path_or_msg = self.check_file_existence(row['ExecutablePath'])
                if not is_file_exist:
                    logger.error(f"[{row['ProcessName']}] {path_or_msg}")
                    new_rows.append(row)
                    continue
                
                full_path = path_or_msg
                should_run, reason = self.should_run_task(row, now)
                
                if should_run:
                    logger.info(f"[{row['ProcessName']}] Triggered ({reason})")
                    self.executor.submit(TaskRunner.execute, row, full_path)
                    
                    new_last_run = now.strftime("%Y-%m-%d %H:%M:%S")
                    row['LastRunTime'] = new_last_run
                    self.last_run_cache[p_name] = new_last_run  # Update cache
                    updated = True
                
                new_rows.append(row)

            if updated:
                self._update_csv(fieldnames, new_rows)

        except Exception as e:
            logger.error(f"Scheduler processing error: {e}")

    def _update_csv(self, fieldnames, rows):
        temp_path = CSV_PATH.with_suffix('.tmp')
        try:
            with open(temp_path, mode='w', encoding='utf-8-sig', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            
            # Retry logic for file locking issues (WinError 5)
            for attempt in range(RETRY_COOUT):
                try:
                    os.replace(temp_path, CSV_PATH)
                    logger.info("CSV Schedule updated.")
                    break
                except OSError as e:
                    # WinError 5 (Access Denied) or 32 (Sharing Violation) など
                    if attempt < RETRY_COOUT - 1:
                        wait_time = 1 + (attempt * 0.5)
                        logger.warning(f"CSV update failed (Attempt {attempt+1}/{RETRY_COOUT}). Retrying in {wait_time}s. Error: {e}")
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
        logger.info(f"Scheduler started. Interval: {CHECK_INTERVAL}s")
        while True:
            self.process_tasks()
            time.sleep(CHECK_INTERVAL)

def main():
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
