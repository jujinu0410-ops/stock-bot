"""
scripts/migrate_v4_anchor_baseline.py

V4-PILOT-C 기존 보유종목 앵커 기준값 일회성 마이그레이션 도구.

사용법:
  python scripts/migrate_v4_anchor_baseline.py             # dry-run (기본값)
  python scripts/migrate_v4_anchor_baseline.py --dry-run  # dry-run 명시
  python scripts/migrate_v4_anchor_baseline.py --apply    # 실제 적용 (별도 승인 필요)

경고:
  --apply는 사용자가 별도로 명시적 승인한 경우에만 사용하세요.
"""
import argparse
import hashlib
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "stock_system.db"

MIGRATION_BASELINE = [
    {"stock_code": "000490", "stock_name": "대동",
     "anchor_price_p0": 7810.0, "anchor_atr_a0": 674.8,
     "previous_confirmed_stop": 6790.0, "expected_qty_min": 1},
    {"stock_code": "004960", "stock_name": "한신공영",
     "anchor_price_p0": 11050.0, "anchor_atr_a0": 612.8,
     "previous_confirmed_stop": 10130.0, "expected_qty_min": 1},
    {"stock_code": "055490", "stock_name": "테이팩스",
     "anchor_price_p0": 14150.0, "anchor_atr_a0": 1159.6,
     "previous_confirmed_stop": 12410.0, "expected_qty_min": 1},
    {"stock_code": "140670", "stock_name": "알에스오토메이션",
     "anchor_price_p0": 10100.0, "anchor_atr_a0": 1023.6,
     "previous_confirmed_stop": 8560.0, "expected_qty_min": 1},
    {"stock_code": "161510", "stock_name": "PLUS 고배당주",
     "anchor_price_p0": 25335.0, "anchor_atr_a0": 731.2,
     "previous_confirmed_stop": 24235.0, "expected_qty_min": 1},
    {"stock_code": "206650", "stock_name": "유바이오로직스",
     "anchor_price_p0": 9150.0, "anchor_atr_a0": 622.6,
     "previous_confirmed_stop": 8210.0, "expected_qty_min": 1},
    {"stock_code": "241520", "stock_name": "DSC인베스트먼트",
     "anchor_price_p0": 8200.0, "anchor_atr_a0": 937.6,
     "previous_confirmed_stop": 6790.0, "expected_qty_min": 1},
    {"stock_code": "348340", "stock_name": "뉴로메카",
     "anchor_price_p0": 21300.0, "anchor_atr_a0": 4466.3,
     "previous_confirmed_stop": 0.0,
     "expected_qty_min": 1},
    {"stock_code": "490590", "stock_name": "RISE 미국AI밸류체인데일리고정커버드콜",
     "anchor_price_p0": 14635.0, "anchor_atr_a0": 403.4,
     "previous_confirmed_stop": 14025.0, "expected_qty_min": 1},
    # 자이글: SUSPENDED_HOLD - 앵커링 완전 제외
    {"stock_code": "234920", "stock_name": "자이글",
     "skip_anchor": True, "expected_qty_min": 1},
]

SIXTEEN_FIELDS = [
    "position_cycle_id", "anchor_price_p0", "anchor_atr_a0", "anchor_created_at",
    "reanchor_flag", "highest_close", "highest_intraday", "previous_confirmed_stop",
    "confirmed_stop_price", "ratchet_stop", "profit_activation_status",
    "previous_profit_trail", "profit_trail", "effective_exit_line",
    "profit_activation_raw", "profit_activation_effective",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def backup_db(src: Path):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = Path(tempfile.gettempdir()) / f"stock_system_mig_backup_{timestamp}.db"
    shutil.copy2(src, dst)
    orig_hash = sha256_file(src)
    bkp_hash = sha256_file(dst)
    assert orig_hash == bkp_hash, f"백업 해시 불일치!"
    return dst, orig_hash, bkp_hash


def run_dry_run(db_path: Path = DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    results = []
    for spec in MIGRATION_BASELINE:
        code = spec["stock_code"]
        name_spec = spec["stock_name"]
        skip = spec.get("skip_anchor", False)
        row = cur.execute("SELECT * FROM portfolio_positions WHERE stock_code = ?", (code,)).fetchone()
        info = cur.execute("SELECT stock_name FROM stock_info WHERE stock_code = ?", (code,)).fetchone()
        res = {"code": code, "name_spec": name_spec, "skip": skip, "checks": [], "can_apply": True, "warnings": []}
        if row is None:
            res["can_apply"] = False
            res["checks"].append("FAIL: portfolio_positions 행 없음")
            results.append(res)
            continue
        qty = row["quantity"]
        db_name = info["stock_name"] if info else "(없음)"
        p0_now = float(row["anchor_price_p0"] or 0)
        a0_now = float(row["anchor_atr_a0"] or 0)
        res["qty"] = qty
        res["db_name"] = db_name
        res["p0_now"] = p0_now
        res["a0_now"] = a0_now
        if db_name != name_spec:
            res["warnings"].append(f"종목명 불일치: DB='{db_name}' vs 기준='{name_spec}'")
        min_qty = spec.get("expected_qty_min", 1)
        if qty < min_qty:
            res["can_apply"] = False
            res["checks"].append(f"FAIL: 수량 미충족 qty={qty} < min={min_qty}")
        if skip:
            res["checks"].append("SKIP: SUSPENDED_HOLD - 앵커링 제외, HOLD 상태 보존")
            res["can_apply"] = False
            results.append(res)
            continue
        if p0_now > 0 and a0_now > 0:
            res["can_apply"] = False
            res["checks"].append(f"SKIP: 이미 P0={p0_now}/A0={a0_now} 설정됨 - 덮어쓰기 금지")
            results.append(res)
            continue
        p0 = spec["anchor_price_p0"]
        a0 = spec["anchor_atr_a0"]
        prev_stop = spec.get("previous_confirmed_stop", 0.0)
        init_stop = round(max(0.0, p0 - 1.5 * a0))
        activation = round(p0 + 3.0 * a0)
        ratchet_stop = float(max(prev_stop, init_stop))
        res["p0_new"] = p0
        res["a0_new"] = a0
        res["prev_stop"] = prev_stop
        res["init_stop"] = init_stop
        res["activation"] = activation
        res["ratchet_stop"] = ratchet_stop
        res["checks"].append(f"OK: P0={p0} A0={a0}")
        res["checks"].append(f"   익절 활성가 = P0 + 3*A0 = {activation}")
        res["checks"].append(f"   초기 손절 = P0 - 1.5*A0 = {init_stop}")
        res["checks"].append(f"   래칫 손절 = max(전일={prev_stop}, 초기={init_stop}) = {ratchet_stop}")
        trade_mode_override = spec.get("trade_mode_override")
        if trade_mode_override:
            res["checks"].append(f"   trade_mode_override = {trade_mode_override}")
            note = spec.get("user_override_note", "")
            if note:
                res["checks"].append(f"   USER_OVERRIDE 메모: {note}")
        results.append(res)
    conn.close()
    return results


def print_dry_run_results(results, db_hash):
    print()
    print("=" * 70)
    print("V4-PILOT-C 앵커 기준값 마이그레이션 DRY-RUN 결과")
    print(f"대상 DB: {DB_PATH}")
    print(f"DB SHA-256: {db_hash}")
    print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    apply_count = skip_count = fail_count = 0
    for r in results:
        code = r["code"]
        name = r["name_spec"]
        can_apply = r.get("can_apply", False)
        skip = r.get("skip", False)
        print(f"\n[{code}] {name}")
        if r.get("db_name"):
            match = "OK" if r["db_name"] == name else "WARNING"
            print(f"  DB 종목명: {r['db_name']} [{match}]")
        if r.get("qty") is not None:
            print(f"  현재 수량: {r['qty']}주")
        if r.get("p0_now") is not None:
            print(f"  현재 P0: {r['p0_now']} / A0: {r['a0_now']}")
        if r.get("p0_new") is not None:
            print(f"  적용 P0: {r['p0_new']} / A0: {r['a0_new']}")
        for chk in r.get("checks", []):
            print(f"  {chk}")
        for warn in r.get("warnings", []):
            print(f"  !! {warn}")
        if skip:
            skip_count += 1
            print(f"  -> SKIPPED (앵커링 제외 종목)")
        elif not can_apply:
            fail_count += 1
            print(f"  -> BLOCKED (적용 불가)")
        else:
            apply_count += 1
            print(f"  -> 적용 예정 (--apply 시 실행됨)")
    print()
    print("=" * 70)
    print(f"요약: 적용 예정 {apply_count}개 | SKIP {skip_count}개 | 차단 {fail_count}개")
    print("=" * 70)
    print()
    print("!! 이 결과는 dry-run입니다. 실제 DB 변경은 없었습니다.")
    print("   실제 적용은 사용자의 별도 서면 승인 후 --apply 옵션으로만 실행하세요.")
    print()


def run_apply(db_path: Path = DB_PATH):
    print("\n!! --apply 모드: 실제 운영 DB를 변경합니다.")
    print("먼저 운영 DB를 자동 백업합니다...")
    bkp_path, orig_hash, bkp_hash = backup_db(db_path)
    print(f"백업 완료: {bkp_path}")
    print(f"원본 SHA-256: {orig_hash}")
    print(f"백업 SHA-256: {bkp_hash}")
    results = run_dry_run(db_path)
    to_apply = [r for r in results if r.get("can_apply") and not r.get("skip")]
    if not to_apply:
        print("적용 대상 종목이 없습니다. DB 변경 없음.")
        return
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            cur = conn.cursor()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for r in to_apply:
                code = r["code"]
                p0 = r["p0_new"]
                a0 = r["a0_new"]
                prev_stop = r["prev_stop"]
                ratchet_stop = r["ratchet_stop"]
                init_stop = r["init_stop"]
                activation = r["activation"]
                cycle_id = f"{code}_{datetime.now().strftime('%Y%m%d')}_V4"
                row = cur.execute(
                    "SELECT quantity, anchor_price_p0, anchor_atr_a0 FROM portfolio_positions WHERE stock_code = ?",
                    (code,)
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"{code}: portfolio_positions 행 없음 - 전체 롤백")
                if float(row[1] or 0) > 0 or float(row[2] or 0) > 0:
                    raise RuntimeError(f"{code}: P0/A0가 이미 설정됨 ({row[1]}/{row[2]}) - 전체 롤백")
                cur.execute("""
                    UPDATE portfolio_positions SET
                        anchor_price_p0 = ?, anchor_atr_a0 = ?, anchor_created_at = ?,
                        position_cycle_id = ?, initial_stop = ?, ratchet_stop = ?,
                        confirmed_stop_price = ?, previous_confirmed_stop = ?,
                        profit_activation_raw = ?, profit_activation_effective = ?,
                        profit_activation_status = 'INACTIVE',
                        profit_trail = 0.0, previous_profit_trail = 0.0,
                        effective_exit_line = ?, reanchor_flag = 0, updated_at = ?
                    WHERE stock_code = ? AND (anchor_price_p0 IS NULL OR anchor_price_p0 <= 0)
                """, (
                    p0, a0, now_str, cycle_id, init_stop, ratchet_stop,
                    ratchet_stop, prev_stop, activation, activation,
                    ratchet_stop, now_str, code
                ))
                affected = cur.execute("SELECT changes()").fetchone()[0]
                if affected == 0:
                    raise RuntimeError(f"{code}: UPDATE 적용 실패 (0행 변경) - 전체 롤백")
                print(f"  [{code}] 적용: P0={p0} A0={a0} 손절={ratchet_stop} 익절활성가={activation}")
        print("\nOK: 단일 트랜잭션 커밋 완료.")
        conn2 = sqlite3.connect(db_path)
        conn2.row_factory = sqlite3.Row
        cur2 = conn2.cursor()
        print("\n적용 후 16개 감시필드 검증:")
        for r in to_apply:
            code = r["code"]
            row = cur2.execute(
                f"SELECT {', '.join(SIXTEEN_FIELDS)} FROM portfolio_positions WHERE stock_code = ?",
                (code,)
            ).fetchone()
            if row is None:
                print(f"  [{code}] FAIL: 행 없음")
                continue
            p0_check = float(row["anchor_price_p0"] or 0)
            a0_check = float(row["anchor_atr_a0"] or 0)
            ok = p0_check > 0 and a0_check > 0
            status = "OK" if ok else "FAIL"
            print(f"  [{code}] {status}: P0={p0_check} A0={a0_check} cycle={row['position_cycle_id']}")
        conn2.close()
    except Exception as e:
        conn.rollback()
        print(f"\nERROR: 오류 발생 - 전체 롤백: {e}")
        print(f"백업 보존됨: {bkp_path}")
        sys.exit(1)
    finally:
        conn.close()
    final_hash = sha256_file(db_path)
    print(f"\n최종 DB SHA-256: {final_hash}")
    print(f"백업 DB 경로: {bkp_path} (원본: {orig_hash})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="V4-PILOT-C 앵커 기준값 마이그레이션")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--apply", action="store_true", default=False)
    args = parser.parse_args()
    if args.apply and not args.dry_run:
        run_apply()
    else:
        db_hash = sha256_file(DB_PATH) if DB_PATH.exists() else "DB_NOT_FOUND"
        results = run_dry_run()
        print_dry_run_results(results, db_hash)
