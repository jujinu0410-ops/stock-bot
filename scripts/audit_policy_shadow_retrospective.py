"""Read-only, offline Policy Shadow retrospective for the latest three snapshots."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.policy.policy_shadow_engine import (
    APPROXIMATE_DAILY_RANGE,
    DATA_GAP,
    DailyRange,
    calculate_daily_rebound_threshold,
    calculate_t_change,
    current_only_hard_stop_status,
    evaluate_loss_defense,
)


WINDOW_DATES = ("2026-08-20", "2026-08-21", "2026-08-24")


def open_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _latest_normal_snapshots(conn: sqlite3.Connection) -> dict[str, dict[str, sqlite3.Row]]:
    conn.row_factory = sqlite3.Row
    placeholders = ", ".join("?" for _ in WINDOW_DATES)
    rows = conn.execute(
        f"""
        SELECT stock_code, stock_name, trading_date, scan_timestamp, t_score,
               technical_state, technical_action, intraday_data_quality, atr14,
               market_price
          FROM scan_journal
         WHERE trading_date IN ({placeholders})
           AND t_score IS NOT NULL
           AND intraday_data_quality = 'VALID'
         ORDER BY stock_code, trading_date, scan_timestamp
        """,
        WINDOW_DATES,
    ).fetchall()
    snapshots: dict[str, dict[str, sqlite3.Row]] = defaultdict(dict)
    for row in rows:
        snapshots[row["stock_code"]][row["trading_date"]] = row
    return snapshots


def _daily_ranges(conn: sqlite3.Connection, stock_code: str) -> list[DailyRange]:
    placeholders = ", ".join("?" for _ in WINDOW_DATES)
    rows = conn.execute(
        f"""
        SELECT stk_date, high_price, low_price
          FROM kiwoom_daily
         WHERE stock_code = ? AND stk_date IN ({placeholders})
         ORDER BY stk_date
        """,
        (stock_code, *(day.replace("-", "") for day in WINDOW_DATES)),
    ).fetchall()
    return [DailyRange(row[0], float(row[1]), float(row[2])) for row in rows if row[1] is not None and row[2] is not None]


def _current_average_price(conn: sqlite3.Connection, stock_code: str) -> Optional[float]:
    row = conn.execute(
        "SELECT avg_buy_price FROM portfolio_positions WHERE stock_code = ?",
        (stock_code,),
    ).fetchone()
    return None if row is None else row[0]


def audit(db_path: Path) -> int:
    with open_read_only(db_path) as conn:
        snapshots = _latest_normal_snapshots(conn)
        held_codes = [row[0] for row in conn.execute("SELECT stock_code FROM portfolio_positions ORDER BY stock_code")]
        print("Policy Shadow Phase 1 — Offline Retrospective (read-only)")
        print("Position class: LEGACY_POSITION; NEW_433_CYCLE stages are not reconstructed.")
        print("| 종목 | T 3D 변화 | T Gate | 손실 Gate | 45m Gate | LOSS_DEFENSE | 0.6/0.4 | Hard Stop | 계산등급 | DATA_GAP 이유 |")
        print("|---|---:|---|---|---|---|---|---|---|---|")

        for stock_code in held_codes:
            per_date = snapshots.get(stock_code, {})
            if not per_date:
                print(
                    f"| {stock_code} | DATA_GAP | DATA_GAP | DATA_GAP | DATA_GAP | NOT_DETERMINABLE | "
                    "DATA_GAP | RETRO=DATA_GAP | DATA_GAP | normal T snapshot absent |"
                )
                continue
            start = per_date.get(WINDOW_DATES[0])
            latest = per_date.get(WINDOW_DATES[-1])
            name = (latest or start or next(iter(per_date.values())))["stock_name"]
            t_change = calculate_t_change(start["t_score"] if start else None, latest["t_score"] if latest else None)
            t_display = "DATA_GAP" if t_change is None else f"{t_change:+.1f}"
            # Loss percentage is not persisted per scan.  Show each threshold
            # mechanically, but do not select a loss band or trigger a result.
            if t_change is None:
                t_gate = DATA_GAP
            else:
                t_gate = f"MID={'PASS' if t_change <= -8 else 'FAIL'}; DEEP={'PASS' if t_change <= -5 else 'FAIL'}"
            loss_gate = DATA_GAP
            bearish_gate = DATA_GAP
            loss_defense = evaluate_loss_defense(loss_pct=None, t_change_3d=t_change, is_45m_bearish=None)

            atr = latest["atr14"] if latest else None
            daily = _daily_ranges(conn, stock_code)
            if len(daily) == len(WINDOW_DATES) and atr is not None:
                defense = calculate_daily_rebound_threshold(daily, float(atr))
                range_display = (
                    f"{APPROXIMATE_DAILY_RANGE}: rebound threshold {defense.rebound_threshold:.1f}; "
                    "rebound occurrence DATA_GAP; trail DATA_GAP"
                    if defense
                    else DATA_GAP
                )
            else:
                range_display = DATA_GAP

            # This is explicitly a latest-snapshot diagnostic, never a
            # substitute for the historical average price required above.
            avg_price = _current_average_price(conn, stock_code)
            current_hard_stop, current_status = current_only_hard_stop_status(
                current_price=latest["market_price"] if latest else None,
                current_weighted_avg_price=avg_price,
            )
            if current_hard_stop is None:
                hard_display = "RETRO=DATA_GAP; CURRENT_ONLY=DATA_GAP"
            else:
                hard_display = f"RETRO=DATA_GAP; CURRENT_ONLY={current_status} @ {current_hard_stop.hard_stop:.1f}"

            gaps = ["historical loss_pct", "persisted is_45m_bearish", "historical avg_buy_price"]
            if len(daily) != len(WINDOW_DATES):
                gaps.append("daily OHLC window incomplete")
            if start is None or latest is None:
                gaps.append("normal T snapshot window incomplete")
            grade = DATA_GAP
            print(
                "| "
                + " | ".join(
                    [
                        f"{name} ({stock_code})",
                        t_display,
                        t_gate,
                        loss_gate,
                        bearish_gate,
                        loss_defense,
                        range_display,
                        hard_display,
                        grade,
                        ", ".join(gaps),
                    ]
                )
                + " |"
            )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "stock_system.db")
    args = parser.parse_args()
    return audit(args.db)


if __name__ == "__main__":
    raise SystemExit(main())
