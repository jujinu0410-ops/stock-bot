"""Isolated persistence and state transitions for Policy Shadow v1.

Nothing in this module writes to the operational database or calls an external
service.  The only writable target is this module's separate SQLite database.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.policy.policy_shadow_engine import (
    DATA_GAP,
    LOSS_DEFENSE_NOT_DETERMINABLE,
    LOSS_DEFENSE_ON,
    PREEXISTING_HARD_STOP_BREACH,
    calculate_hard_stop,
    calculate_433_target_quantities,
    evaluate_loss_defense,
    stage_size_status,
)


POLICY_VERSION = "POLICY_SHADOW_V1"
ROOT = Path(__file__).resolve().parents[2]
POLICY_SNAPSHOT_MAX_AGE_MINUTES = 60
POSITION_AVG_PRICE_TOLERANCE_PCT = 0.001


def resolve_policy_shadow_db_path(db_path: Optional[str | Path] = None) -> Path:
    """Resolve the one Policy DB location shared by writer and read-only reader."""
    if db_path is not None:
        return Path(db_path)
    if os.environ.get("STOCKBOT_TEST_MODE") == "1":
        return Path(tempfile.gettempdir()) / f"stockbot_policy_shadow_{os.getpid()}.db"
    if os.environ.get("STOCKBOT_STATE_DIR"):
        return Path(os.environ["STOCKBOT_STATE_DIR"]) / "data" / "policy_shadow.db"
    return ROOT / "data" / "policy_shadow.db"


class PolicyShadowStore:
    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = resolve_policy_shadow_db_path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _connection(self):
        conn = self._conn()
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS policy_shadow_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS policy_shadow_cycles (
                cycle_row_id INTEGER PRIMARY KEY AUTOINCREMENT, policy_version TEXT NOT NULL, stock_code TEXT NOT NULL,
                position_cycle_id TEXT NOT NULL UNIQUE, position_kind TEXT NOT NULL, cycle_status TEXT NOT NULL,
                shadow_started_at TEXT NOT NULL, shadow_start_price REAL, initial_quantity INTEGER,
                hard_stop_already_breached_at_shadow_start INTEGER NOT NULL DEFAULT 0,
                f0 REAL, t0 REAL, stage INTEGER, stage1_date TEXT, stage1_price REAL, stage1_low REAL, stage1_atr REAL,
                t2 REAL, stage2_date TEXT, stage2_price REAL, stage3_date TEXT, stage3_price REAL,
                add_locked INTEGER NOT NULL DEFAULT 0, last_stage_date TEXT, last_stage_quantity INTEGER,
                last_completed_45m_at_stage TEXT, defense_state TEXT NOT NULL DEFAULT 'OFF', defense_started_at TEXT,
                defense_atr REAL, defense_low REAL, rebound_reached_at TEXT, defense_peak REAL, defense_trail REAL,
                hard_stop REAL, last_reason TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS policy_shadow_snapshots (
                snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT, policy_version TEXT NOT NULL, asof_timestamp TEXT NOT NULL,
                source_identifier TEXT NOT NULL, stock_code TEXT NOT NULL, position_cycle_id TEXT, position_kind TEXT,
                market_price REAL, quantity INTEGER, weighted_avg_price REAL, loss_pct REAL, atr14 REAL, f_score REAL,
                t_score REAL, t_change_3d REAL, daily_state TEXT, is_45m_bearish_2plus INTEGER,
                is_45m_breakdown INTEGER, is_45m_bearish_gate INTEGER, completed_45m_timestamp TEXT,
                concentration_state TEXT, data_validity INTEGER, suspension_state TEXT, v4_effective_stop REAL,
                stage INTEGER, stage2_gate TEXT, stage3_gate TEXT, add_locked INTEGER, loss_defense_trigger TEXT,
                defense_state TEXT, defense_atr REAL, defense_low REAL, rebound_trigger REAL, rebound_reached INTEGER,
                defense_peak REAL, defense_trail REAL, hard_stop_candidate REAL, hard_stop REAL,
                shadow_effective_exit REAL, shadow_action TEXT, shadow_reason TEXT, actual_order_impact INTEGER NOT NULL DEFAULT 0,
                UNIQUE(source_identifier, stock_code)
            );
            CREATE INDEX IF NOT EXISTS idx_policy_shadow_snapshot_stock_time ON policy_shadow_snapshots(stock_code, asof_timestamp);
            CREATE INDEX IF NOT EXISTS idx_policy_shadow_snapshot_cycle_time ON policy_shadow_snapshots(position_cycle_id, asof_timestamp);
            """)
            # Policy Shadow owns this DB.  These additive fields intentionally
            # never touch operational tables or V4 persistence.
            cycle_columns = {
                "cycle_target_qty": "INTEGER",
                "stage1_target_qty": "INTEGER",
                "stage2_target_qty": "INTEGER",
                "stage3_target_qty": "INTEGER",
                "stage_size_status": "TEXT",
                "target_qty_status": "TEXT",
                "f_baseline_status": "TEXT",
                "t_baseline_status": "TEXT",
                "baseline_repair_source": "TEXT",
            }
            snapshot_columns = {
                "is_etf": "INTEGER",
                "risk_target_qty": "INTEGER",
                "cycle_target_qty": "INTEGER",
                "stage1_target_qty": "INTEGER",
                "stage2_target_qty": "INTEGER",
                "stage3_target_qty": "INTEGER",
                "stage_size_status": "TEXT",
                "target_qty_status": "TEXT",
                "f_baseline_status": "TEXT",
                "t_baseline_status": "TEXT",
            }
            for table, columns in (("policy_shadow_cycles", cycle_columns), ("policy_shadow_snapshots", snapshot_columns)):
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                for name, sql_type in columns.items():
                    if name not in existing:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")

    def meta_json(self, key: str) -> Any:
        with self._connection() as conn:
            row = conn.execute("SELECT value FROM policy_shadow_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_meta_json(self, key: str, value: Any) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO policy_shadow_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def latest_cycle(self, stock_code: str) -> Optional[dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM policy_shadow_cycles WHERE stock_code=? ORDER BY cycle_row_id DESC LIMIT 1", (stock_code,)).fetchone()
        return dict(row) if row else None

    def upsert_cycle(self, cycle: dict[str, Any]) -> None:
        fields = list(cycle)
        values = [cycle[f] for f in fields]
        assignments = ",".join(f"{f}=excluded.{f}" for f in fields if f != "position_cycle_id")
        with self._connection() as conn:
            conn.execute(f"INSERT INTO policy_shadow_cycles ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)}) ON CONFLICT(position_cycle_id) DO UPDATE SET {assignments}", values)

    def append_snapshot(self, snapshot: dict[str, Any]) -> bool:
        fields = list(snapshot)
        with self._connection() as conn:
            cur = conn.execute(f"INSERT OR IGNORE INTO policy_shadow_snapshots ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [snapshot[f] for f in fields])
        return cur.rowcount == 1

    def recent_t_by_day(self, stock_code: str, current: dict[str, Any]) -> list[tuple[str, float]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT asof_timestamp,t_score FROM policy_shadow_snapshots WHERE stock_code=? AND t_score IS NOT NULL ORDER BY asof_timestamp DESC", (stock_code,)).fetchall()
        rows = [(current["asof_timestamp"], current.get("t_score"))] + [(r["asof_timestamp"], r["t_score"]) for r in rows]
        by_day: dict[str, float] = {}
        for timestamp, score in rows:
            if score is not None:
                by_day.setdefault(str(timestamp)[:10], float(score))
        return sorted(by_day.items())[-3:]

    def counts(self) -> tuple[int, int]:
        with self._connection() as conn:
            return tuple(conn.execute("SELECT (SELECT count(*) FROM policy_shadow_cycles),(SELECT count(*) FROM policy_shadow_snapshots)").fetchone())

    def repair_new_cycle_baseline(
        self,
        stock_code: str,
        *,
        f0: Optional[float],
        t0: Optional[float],
        source: str,
        repaired_at: str,
    ) -> Optional[dict[str, Any]]:
        """Repair one open NEW_433 baseline only from an identified original source.

        The caller must provide contemporaneous values.  This method refuses to
        fabricate a baseline and does not calculate or read any operational
        state itself.
        """
        if f0 is None or t0 is None or not source:
            return None
        cycle = self.latest_cycle(str(stock_code).zfill(6))
        if not cycle or cycle["position_kind"] != "NEW_433_CYCLE":
            return None
        cycle.update(
            f0=float(f0), t0=float(t0),
            f_baseline_status="BASELINE_REPAIRED_FROM_ORIGINAL_FIRST_OBSERVATION",
            t_baseline_status="BASELINE_REPAIRED_FROM_ORIGINAL_FIRST_OBSERVATION",
            baseline_repair_source=source,
            last_reason="BASELINE_REPAIRED_FROM_ORIGINAL_FIRST_OBSERVATION",
            updated_at=repaired_at,
        )
        self.upsert_cycle(cycle)
        return self.latest_cycle(str(stock_code).zfill(6))


class PolicyShadowReader:
    """Read-only reporting adapter for the isolated Policy Shadow database.

    This class deliberately never constructs ``PolicyShadowStore``: doing so
    would initialise a database.  A missing, locked, or malformed Policy DB is
    represented as an unavailable display payload and must not affect V4.
    """

    def __init__(self, db_path: Optional[str | Path] = None):
        self.db_path = resolve_policy_shadow_db_path(db_path)

    @staticmethod
    def _timestamp(value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace(" KST", ""))

    def _readonly_connection(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise FileNotFoundError(self.db_path)
        uri = f"file:{self.db_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def build_report_payload(
        self,
        report_timestamp: datetime | str,
        current_holdings: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """Return validated display rows without mutating V4 holding objects."""
        result: dict[str, Any] = {"status": "OK", "rows": [], "transition_rows": []}
        try:
            report_time = self._timestamp(report_timestamp)
            holdings = {
                str(item.get("stock_code", "")).zfill(6): dict(item)
                for item in (current_holdings or [])
                if int(item.get("quantity") or 0) > 0
            }
            conn = self._readonly_connection()
            try:
                snapshots = conn.execute(
                    """
                    SELECT s.*, c.cycle_status, c.position_kind AS cycle_position_kind,
                           c.hard_stop_already_breached_at_shadow_start,
                           c.f0, c.t0, c.t2, c.cycle_target_qty,
                           c.stage1_target_qty, c.stage2_target_qty, c.stage3_target_qty,
                           c.stage_size_status, c.target_qty_status,
                           c.f_baseline_status, c.t_baseline_status,
                           c.baseline_repair_source
                    FROM policy_shadow_snapshots s
                    LEFT JOIN policy_shadow_cycles c ON c.position_cycle_id = s.position_cycle_id
                    WHERE s.snapshot_id IN (
                        SELECT MAX(snapshot_id) FROM policy_shadow_snapshots
                        WHERE asof_timestamp <= ? GROUP BY stock_code
                    )
                    """,
                    (report_time.strftime("%Y-%m-%d %H:%M:%S"),),
                ).fetchall()
            finally:
                conn.close()
            by_code = {str(row["stock_code"]).zfill(6): dict(row) for row in snapshots}

            for code, holding in holdings.items():
                snap = by_code.get(code)
                row: dict[str, Any] = {
                    "stock_code": code,
                    "stock_name": holding.get("stock_name", ""),
                    "report_timestamp": report_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "quantity_current": int(holding.get("quantity") or 0),
                    "avg_current": float(holding.get("avg_buy_price") or 0),
                    "freshness_status": "STALE_POLICY_SNAPSHOT",
                    "position_match_status": "NOT_EVALUATED",
                    "display_allowed": False,
                    "actual_order_impact": 0,
                    "is_suspended": holding.get("trade_mode") == "SUSPENDED_HOLD" or code == "234920",
                }
                if not snap:
                    row["reason"] = "Policy snapshot unavailable for current position"
                    result["rows"].append(row)
                    continue

                row.update(snap)
                is_etf_item = bool(snap.get("is_etf", False)) or holding.get("is_etf", False) or code in ['371460', '484730', '490590', '161510', '088500']
                if is_etf_item:
                    row["is_etf"] = 1
                    row["f_score"] = None
                    row["f0"] = None
                    row["f_baseline_status"] = "NOT_APPLICABLE"
                snapshot_time = self._timestamp(snap["asof_timestamp"])
                age_minutes = (report_time - snapshot_time).total_seconds() / 60.0
                row["snapshot_age_minutes"] = round(age_minutes, 1)
                row["quantity_policy"] = int(snap.get("quantity") or 0)
                row["avg_policy"] = float(snap.get("weighted_avg_price") or 0)
                same_day = snapshot_time.date() == report_time.date()
                if not same_day or age_minutes < 0 or age_minutes > POLICY_SNAPSHOT_MAX_AGE_MINUTES:
                    row.update(freshness_status="STALE_POLICY_SNAPSHOT", position_match_status="NOT_EVALUATED",
                               reason="Policy snapshot is not same-day and within 60 minutes")
                elif row["quantity_policy"] != row["quantity_current"]:
                    row.update(freshness_status="STALE_POSITION_STATE", position_match_status="STALE_POSITION_STATE",
                               reason="Policy quantity differs from current V4 position")
                else:
                    tolerance = max(1.0, abs(row["avg_current"]) * POSITION_AVG_PRICE_TOLERANCE_PCT)
                    row["avg_tolerance"] = tolerance
                    if abs(row["avg_policy"] - row["avg_current"]) > tolerance:
                        row.update(freshness_status="STALE_POSITION_STATE", position_match_status="STALE_POSITION_STATE",
                                   reason="Policy average price differs from current V4 position")
                    else:
                        row.update(freshness_status="FRESH", position_match_status="MATCH", display_allowed=True,
                                   reason="Fresh policy snapshot matches current V4 position")
                row["actual_order_impact"] = 0
                result["rows"].append(row)

            for code, snap in by_code.items():
                if code not in holdings and snap.get("cycle_status") == "OPEN":
                    trans_row = dict(snap)
                    is_etf_trans = bool(snap.get("is_etf", False)) or code in ['371460', '484730', '490590', '161510', '088500']
                    if is_etf_trans:
                        trans_row["is_etf"] = 1
                        trans_row["f_score"] = None
                        trans_row["f0"] = None
                        trans_row["f_baseline_status"] = "NOT_APPLICABLE"
                    result["transition_rows"].append({
                        **trans_row,
                        "stock_code": code,
                        "transition_status": "POSITION_TRANSITION_PENDING_SHADOW",
                        "actual_order_impact": 0,
                    })
        except Exception as exc:
            return {"status": "UNAVAILABLE", "rows": [], "transition_rows": [], "reason": str(exc)}
        return result


class PolicyShadowService:
    """Stateful policy calculator; its sole side effect is the Policy DB."""
    def __init__(self, store: PolicyShadowStore):
        self.store = store

    @staticmethod
    def _cycle_id(code: str, timestamp: str) -> str:
        return f"PS_{code}_{timestamp.replace('-', '').replace(':', '').replace(' ', '')}"

    def observe(self, raw: dict[str, Any], bootstrap_codes: list[str]) -> dict[str, Any]:
        code, now = str(raw["stock_code"]).zfill(6), raw["asof_timestamp"]
        is_etf = bool(raw.get("is_etf", False)) or code in ['371460', '484730', '490590', '161510', '088500']
        if is_etf:
            raw = dict(raw, stock_code=code, is_etf=1, f_score=None)
        else:
            raw = dict(raw, stock_code=code)
        bootstrap = self.store.meta_json("bootstrap_codes")
        if bootstrap is None:
            bootstrap = sorted({str(c).zfill(6) for c in bootstrap_codes})
            self.store.set_meta_json("bootstrap_codes", bootstrap)
        cycle = self.store.latest_cycle(code)
        qty = int(raw.get("quantity") or 0)
        if cycle and cycle["cycle_status"] == "CLOSED" and qty > 0:
            cycle = None
        if cycle is None and qty <= 0:
            return {"status": "NO_POSITION"}
        if cycle is None:
            kind = "LEGACY_POSITION" if code in bootstrap else "NEW_433_CYCLE"
            avg, price, atr = raw.get("weighted_avg_price"), raw.get("market_price"), raw.get("atr14")
            hard = calculate_hard_stop(avg)
            breach = bool(kind == "LEGACY_POSITION" and hard and price is not None and price <= hard.hard_stop)
            target_qty = raw.get("risk_target_qty") if kind == "NEW_433_CYCLE" else None
            targets = calculate_433_target_quantities(target_qty)
            f0 = raw.get("f_score") if kind == "NEW_433_CYCLE" and not is_etf else None
            t0 = raw.get("t_score") if kind == "NEW_433_CYCLE" else None
            f_status = "NOT_APPLICABLE" if is_etf else ("OK" if f0 is not None else DATA_GAP)
            t_status = "OK" if t0 is not None else DATA_GAP
            target_status = "OK" if targets[0] is not None else "TARGET_QTY_DATA_GAP"
            cycle = {
                "policy_version": POLICY_VERSION, "stock_code": code, "position_cycle_id": self._cycle_id(code, now),
                "position_kind": kind, "cycle_status": "OPEN", "shadow_started_at": now, "shadow_start_price": price,
                "initial_quantity": qty, "hard_stop_already_breached_at_shadow_start": int(breach),
                "f0": f0, "t0": t0,
                "stage": 1 if kind == "NEW_433_CYCLE" else None,
                "stage1_date": now[:10] if kind == "NEW_433_CYCLE" else None,
                "stage1_price": (avg or price) if kind == "NEW_433_CYCLE" else None,
                "stage1_low": price if kind == "NEW_433_CYCLE" else None, "stage1_atr": atr if kind == "NEW_433_CYCLE" else None,
                "t2": None, "stage2_date": None, "stage2_price": None, "stage3_date": None, "stage3_price": None,
                "add_locked": 0, "last_stage_date": now[:10] if kind == "NEW_433_CYCLE" else None,
                "last_stage_quantity": qty, "last_completed_45m_at_stage": raw.get("completed_45m_timestamp"),
                "defense_state": "OFF", "defense_started_at": None, "defense_atr": None, "defense_low": None,
                "rebound_reached_at": None, "defense_peak": None, "defense_trail": None,
                "hard_stop": hard.hard_stop if hard else None,
                "cycle_target_qty": int(target_qty) if targets[0] is not None else None,
                "stage1_target_qty": targets[0], "stage2_target_qty": targets[1], "stage3_target_qty": targets[2],
                "stage_size_status": stage_size_status(qty, targets[0], 1) if kind == "NEW_433_CYCLE" else "NOT_APPLICABLE",
                "target_qty_status": target_status if kind == "NEW_433_CYCLE" else "NOT_APPLICABLE",
                "f_baseline_status": "NOT_APPLICABLE" if is_etf else (f_status if kind == "NEW_433_CYCLE" else "NOT_APPLICABLE"),
                "t_baseline_status": t_status if kind == "NEW_433_CYCLE" else "NOT_APPLICABLE",
                "baseline_repair_source": None,
                "last_reason": PREEXISTING_HARD_STOP_BREACH if breach else ("BOOTSTRAP_LEGACY" if kind == "LEGACY_POSITION" else "OBSERVED_CYCLE_START"), "updated_at": now,
            }
        if qty <= 0:
            cycle.update(cycle_status="CLOSED", last_reason="POSITION_CLOSED", updated_at=now)
            self.store.upsert_cycle(cycle)
            return self._snapshot(cycle, raw, None, "CLOSED", "POSITION_CLOSED")

        t_days = self.store.recent_t_by_day(code, raw)
        t_change = float(raw["t_score"]) - t_days[0][1] if len(t_days) >= 3 and raw.get("t_score") is not None else None
        bearish = bool(raw.get("is_45m_bearish_2plus") or raw.get("is_45m_breakdown"))
        loss_defense = evaluate_loss_defense(loss_pct=raw.get("loss_pct"), t_change_3d=t_change, is_45m_bearish=bearish if t_change is not None else None)
        price, atr = raw.get("market_price"), raw.get("atr14")
        if cycle["defense_state"] == "OFF" and loss_defense == LOSS_DEFENSE_ON and atr and price is not None:
            cycle.update(add_locked=1, defense_state="WAIT_REBOUND", defense_started_at=now, defense_atr=atr, defense_low=price, last_reason="LOSS_DEFENSE_ON")
        elif cycle["defense_state"] == "WAIT_REBOUND" and price is not None:
            low = min(float(cycle["defense_low"]), float(price)); cycle["defense_low"] = low
            if price >= low + 0.6 * float(cycle["defense_atr"]):
                cycle.update(defense_state="TRAIL_ACTIVE", rebound_reached_at=now, defense_peak=price, defense_trail=price - .4 * float(cycle["defense_atr"]), last_reason="REBOUND_REACHED")
        elif cycle["defense_state"] == "TRAIL_ACTIVE" and price is not None:
            peak = max(float(cycle["defense_peak"]), float(price)); candidate = peak - .4 * float(cycle["defense_atr"])
            cycle["defense_peak"] = peak; cycle["defense_trail"] = max(float(cycle["defense_trail"]), candidate)
            if price <= cycle["defense_trail"]: cycle["last_reason"] = "LOSS_DEFENSE_EXIT"

        hard = calculate_hard_stop(raw.get("weighted_avg_price"), cycle.get("hard_stop")); cycle["hard_stop"] = hard.hard_stop if hard else cycle.get("hard_stop")
        safety = raw.get("data_validity") == 1 and raw.get("suspension_state") != "SUSPENDED" and raw.get("concentration_state") != "BLOCKED"
        stage2 = stage3 = "NOT_APPLICABLE"
        if cycle["position_kind"] == "NEW_433_CYCLE":
            same_day = cycle.get("last_stage_date") == now[:10]; new_bar = raw.get("completed_45m_timestamp") != cycle.get("last_completed_45m_at_stage")
            if cycle.get("stage") == 1:
                cycle["stage_size_status"] = stage_size_status(qty, cycle.get("stage1_target_qty"), 1)
                if cycle.get("f0") is None or cycle.get("t0") is None:
                    stage2 = "BASELINE_DATA_GAP"
                elif cycle.get("cycle_target_qty") is None:
                    stage2 = "TARGET_QTY_DATA_GAP"
                else:
                    ready = safety and not same_day and new_bar and cycle["add_locked"] == 0 and loss_defense != LOSS_DEFENSE_ON and raw.get("f_score") is not None and raw["f_score"] >= cycle["f0"] and raw.get("t_score") is not None and raw["t_score"] >= cycle["t0"] + 5 and price is not None and cycle.get("stage1_low") is not None and cycle.get("stage1_atr") is not None and price >= cycle["stage1_low"] + .6 * cycle["stage1_atr"]
                    stage2 = "STAGE2_READY" if ready else "NOT_READY"
            elif cycle.get("stage") == 2:
                neutral = raw.get("daily_state") in ("NEUTRAL", "BULLISH") and not bearish
                if cycle.get("f0") is None or cycle.get("t0") is None or cycle.get("t2") is None:
                    stage3 = "BASELINE_DATA_GAP"
                elif cycle.get("cycle_target_qty") is None:
                    stage3 = "TARGET_QTY_DATA_GAP"
                else:
                    ready = safety and not same_day and new_bar and cycle["add_locked"] == 0 and loss_defense != LOSS_DEFENSE_ON and raw.get("f_score") is not None and raw["f_score"] >= cycle["f0"] and raw.get("t_score") is not None and raw["t_score"] >= cycle["t2"] + 5 and neutral and price is not None and raw.get("weighted_avg_price") is not None and price >= raw["weighted_avg_price"]
                    stage3 = "STAGE3_READY" if ready else "NOT_READY"
            if qty > int(cycle.get("last_stage_quantity") or qty):
                if cycle.get("stage") == 1 and stage2 == "STAGE2_READY": cycle.update(stage=2, t2=raw.get("t_score"), stage2_date=now[:10], stage2_price=price, last_stage_date=now[:10], last_stage_quantity=qty, last_completed_45m_at_stage=raw.get("completed_45m_timestamp"), last_reason="STAGE2_OBSERVED")
                elif cycle.get("stage") == 2 and stage3 == "STAGE3_READY": cycle.update(stage=3, stage3_date=now[:10], stage3_price=price, last_stage_date=now[:10], last_stage_quantity=qty, last_completed_45m_at_stage=raw.get("completed_45m_timestamp"), last_reason="STAGE3_OBSERVED")
                else: cycle["last_reason"] = "OUT_OF_POLICY_ADD_OBSERVED"
        action = cycle.get("last_reason", "OBSERVE")
        if cycle.get("hard_stop") and price is not None and price <= cycle["hard_stop"] and not cycle["hard_stop_already_breached_at_shadow_start"]: action = "HARD_STOP_EXIT"
        cycle["updated_at"] = now; self.store.upsert_cycle(cycle)
        return self._snapshot(cycle, raw, t_change, action, cycle.get("last_reason"), stage2, stage3, loss_defense)

    def _snapshot(self, cycle: dict[str, Any], raw: dict[str, Any], t_change: Optional[float], action: str, reason: str, stage2: str = "NOT_APPLICABLE", stage3: str = "NOT_APPLICABLE", loss_defense: str = LOSS_DEFENSE_NOT_DETERMINABLE) -> dict[str, Any]:
        price, hard, v4 = raw.get("market_price"), cycle.get("hard_stop"), raw.get("v4_effective_stop") or 0
        trail = cycle.get("defense_trail") if cycle.get("defense_state") == "TRAIL_ACTIVE" else None
        exit_line = max(x for x in (v4, hard or 0, trail or 0))
        is_etf = bool(raw.get("is_etf", False)) or str(raw.get("stock_code", "")).zfill(6) in ['371460', '484730', '490590', '161510', '088500']
        f_score_val = None if is_etf else raw.get("f_score")
        f_baseline_val = "NOT_APPLICABLE" if is_etf else cycle.get("f_baseline_status")
        snap = {**raw, "is_etf": int(is_etf), "f_score": f_score_val, "policy_version": POLICY_VERSION, "position_cycle_id": cycle["position_cycle_id"], "position_kind": cycle["position_kind"], "t_change_3d": t_change, "stage": cycle.get("stage"), "stage2_gate": stage2, "stage3_gate": stage3, "add_locked": cycle.get("add_locked"), "loss_defense_trigger": loss_defense, "defense_state": cycle.get("defense_state"), "defense_atr": cycle.get("defense_atr"), "defense_low": cycle.get("defense_low"), "rebound_trigger": (cycle.get("defense_low") + .6 * cycle.get("defense_atr")) if cycle.get("defense_low") is not None and cycle.get("defense_atr") is not None else None, "rebound_reached": int(cycle.get("rebound_reached_at") is not None), "defense_peak": cycle.get("defense_peak"), "defense_trail": trail, "hard_stop_candidate": calculate_hard_stop(raw.get("weighted_avg_price")).candidate if calculate_hard_stop(raw.get("weighted_avg_price")) else None, "hard_stop": hard, "shadow_effective_exit": exit_line, "shadow_action": action, "shadow_reason": reason, "cycle_target_qty": cycle.get("cycle_target_qty"), "stage1_target_qty": cycle.get("stage1_target_qty"), "stage2_target_qty": cycle.get("stage2_target_qty"), "stage3_target_qty": cycle.get("stage3_target_qty"), "stage_size_status": cycle.get("stage_size_status"), "target_qty_status": cycle.get("target_qty_status"), "f_baseline_status": f_baseline_val, "t_baseline_status": cycle.get("t_baseline_status"), "actual_order_impact": 0}
        self.store.append_snapshot(snap)
        return snap
