"""
scripts/run_tests_isolated.py

전체 테스트를 STOCKBOT_TEST_MODE=1 환경에서 실행하면서
운영 DB·JSON의 사전/사후 SHA-256을 검증하는 래퍼 스크립트.

사용법:
  python scripts/run_tests_isolated.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OP_DB = BASE_DIR / "data" / "stock_system.db"
OP_JSON = BASE_DIR / "config" / "portfolio_holdings.json"
OP_LOG = BASE_DIR / "logs" / "stock_system.log"
TEST_SHIM_DIR = BASE_DIR / "tests" / "test_runtime_shims"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def file_snapshot(path: Path) -> dict:
    """운영 파일의 존재 여부, 크기, SHA-256을 한 번에 기록합니다."""
    if not path.exists():
        return {"exists": False, "size": None, "sha256": None}
    return {
        "exists": True,
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def snapshot_unchanged(before: dict, after: dict) -> bool:
    """존재 여부와 내용 해시가 모두 같은 경우에만 불변으로 판정합니다."""
    return before["exists"] == after["exists"] and before["sha256"] == after["sha256"]


def print_snapshot(label: str, snapshot: dict) -> None:
    print(
        f"{label}: exists={snapshot['exists']}, "
        f"size={snapshot['size']}, sha256={snapshot['sha256']}"
    )


def main():
    print("=" * 60)
    print("[run_tests_isolated] 전체 테스트 격리 실행 래퍼")
    print("=" * 60)

    # 1. 사전 SHA-256 기록
    pre_db = file_snapshot(OP_DB)
    pre_json = file_snapshot(OP_JSON)
    pre_log = file_snapshot(OP_LOG)

    print_snapshot("[PRE]  DB", pre_db)
    print_snapshot("[PRE]  JSON", pre_json)
    print_snapshot("[PRE]  LOG", pre_log)
    print()

    # 2. 테스트 실행 (STOCKBOT_TEST_MODE=1)
    env = os.environ.copy()
    env["STOCKBOT_TEST_MODE"] = "1"
    env["KIWOOM_USE_MOCK"] = "True"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # The bundled document/test runtime intentionally excludes optional live
    # API SDKs.  In test mode only, sitecustomize supplies inert import shims
    # so imports cannot create network/API side effects.
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(TEST_SHIM_DIR), env.get("PYTHONPATH", "")]))
    python_exe = sys.executable

    print("[RUN] python -m unittest discover -s tests -p 'test_*.py'")
    print("-" * 60)

    with tempfile.NamedTemporaryFile(prefix="stockbot_full_regression_", suffix=".log", delete=False, mode="w", encoding="utf-8") as log_file:
        log_path = Path(log_file.name)
        result = subprocess.run(
            [python_exe, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
            env=env,
            cwd=str(BASE_DIR),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    exit_code = result.returncode
    print(f"[LOG] stdout/stderr: {log_path}")
    print(log_path.read_text(encoding="utf-8", errors="replace")[-6000:].encode(sys.stdout.encoding or "utf-8", errors="replace").decode(sys.stdout.encoding or "utf-8"))

    print("-" * 60)
    print(f"[TEST] Exit code: {exit_code}")
    print()

    # 3. 사후 SHA-256 계산
    post_db = file_snapshot(OP_DB)
    post_json = file_snapshot(OP_JSON)
    post_log = file_snapshot(OP_LOG)

    print("=" * 60)
    print("[POST] 운영파일 불변성 검증")
    print("=" * 60)

    db_ok = snapshot_unchanged(pre_db, post_db)
    json_ok = snapshot_unchanged(pre_json, post_json)
    log_ok = snapshot_unchanged(pre_log, post_log)

    print(f"DB   : {'OK (UNCHANGED)' if db_ok else 'CHANGED!'}")
    print_snapshot("  PRE", pre_db)
    print_snapshot("  POST", post_db)
    print(f"JSON : {'OK (UNCHANGED)' if json_ok else 'CHANGED!'}")
    print_snapshot("  PRE", pre_json)
    print_snapshot("  POST", post_json)
    print(f"LOG  : {'OK (UNCHANGED)' if log_ok else 'CHANGED!'}")
    print_snapshot("  PRE", pre_log)
    print_snapshot("  POST", post_log)
    print()

    if not db_ok:
        print("[FAIL] 운영 DB가 전체 테스트 중 변경되었습니다!")
        sys.exit(1)
    if not json_ok:
        print("[FAIL] 운영 JSON이 전체 테스트 중 변경되었습니다!")
        sys.exit(1)
    if not log_ok:
        print("[FAIL] 운영 로그가 전체 테스트 중 변경되었습니다!")
        sys.exit(1)

    if exit_code != 0:
        print("[FAIL] 테스트 실패 (운영 파일은 보존됨)")
        sys.exit(exit_code)

    print("[SUCCESS] 전체 테스트 통과 및 운영 DB/JSON/로그 불변성 확인 완료")
    sys.exit(0)


if __name__ == "__main__":
    main()
