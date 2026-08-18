"""
Process Lock & Reserved Window Safety Manager
Phase 8 Runtime Layer (V1.0 Operations)

- Process mutex lock to prevent concurrent executions.
- Stale lock detection and automatic cleanup (dead PID or expired TTL).
- Blackout window protection for existing 11:20 and 15:35 operational reports.
"""

import os
import ctypes
import subprocess
from datetime import datetime, time, timedelta
from typing import Optional, Tuple
from src.database.db_manager import DatabaseManager
from src.utils.logger import logger

class SchedulerLockManager:
    """Manages process locks and reserved execution windows"""

    # Reserved Windows (Start Time, End Time, Window Label)
    RESERVED_WINDOWS = [
        (time(11, 10), time(11, 30), "RESERVED_WINDOW_1120_PORTFOLIO_MONITOR"),
        (time(15, 25), time(15, 50), "RESERVED_WINDOW_1535_CLOSE_REPORT")
    ]

    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    @classmethod
    def check_reserved_window(cls, dt: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        """
        Check if the current time falls inside a reserved operational window
        Returns: (is_reserved, window_label)
        """
        if dt is None:
            dt = datetime.now()
        t = dt.time()

        for w_start, w_end, label in cls.RESERVED_WINDOWS:
            if w_start <= t <= w_end:
                return True, label
        return False, None

    def is_pid_alive(self, pid: int) -> bool:
        """Check if process with given PID is actively running (Pure Standard Library)"""
        if pid <= 0:
            return False
        if os.name == 'nt':
            try:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                SYNCHRONIZE = 0x00100000
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
                if handle == 0:
                    return False
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                STILL_ACTIVE = 259
                return exit_code.value == STILL_ACTIVE
            except Exception:
                return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    def acquire_lock(
        self,
        lock_name: str = "SHADOW_SCHEDULER_LOCK",
        task_name: str = "INTRADAY_SHADOW_SCAN",
        ttl_seconds: int = 600
    ) -> bool:
        """
        Acquire process lock. Automatically cleans up stale locks.
        """
        pid = os.getpid()
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        # 1. Check existing lock in DB
        query = "SELECT * FROM scheduler_locks WHERE lock_name = ?"
        rows = self.db.execute_query(query, (lock_name,))
        if rows:
            lock = dict(rows[0])
            lock_pid = lock["pid"]
            expires_at_str = lock["expires_at"]

            try:
                expires_at = datetime.strptime(expires_at_str, "%Y-%m-%d %H:%M:%S")
            except Exception:
                expires_at = now

            # If lock expired or holding PID is dead -> reclaim
            is_alive = self.is_pid_alive(lock_pid)
            if now > expires_at or not is_alive:
                logger.info(f"[LockManager] Stale lock found (PID={lock_pid}, Alive={is_alive}, Expired={now > expires_at}). Reclaiming...")
                self.release_lock(lock_name)
            else:
                logger.warning(f"[LockManager] Lock '{lock_name}' is currently held by active PID={lock_pid} ({lock['task_name']})")
                return False

        # 2. Acquire lock
        success = self.db.acquire_scheduler_lock(lock_name, task_name, pid, ttl_seconds)
        if success:
            logger.info(f"[LockManager] Successfully acquired lock '{lock_name}' for PID={pid} ({task_name})")
        return success

    def release_lock(self, lock_name: str = "SHADOW_SCHEDULER_LOCK") -> bool:
        """Release process lock"""
        res = self.db.release_scheduler_lock(lock_name, pid=None)
        logger.info(f"[LockManager] Released lock '{lock_name}'")
        return res

    def is_existing_job_active(self) -> bool:
        """
        Check if an existing portfolio report or daily report script is actively running
        """
        current_pid = os.getpid()
        if os.name == 'nt':
            try:
                cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match \'main.py|run_daily_report|run_stock_analysis\' -and $_.ProcessId -ne ' + str(current_pid) + ' } | Select-Object -ExpandProperty ProcessId"'
                out = subprocess.check_output(cmd, shell=True, text=True, timeout=5).strip()
                if out:
                    logger.warning(f"[LockManager] Existing portfolio job detected: PID={out}")
                    return True
            except Exception as e:
                logger.warning(f"[LockManager] Process check exception (proceeding safely): {e}")
        return False

