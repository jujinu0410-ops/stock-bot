"""
Daily V8 One-Stock Briefing — Selector (hardened).

Usage
-----
  # Use today's KST data (exits with NOT_READY if no row exists)
  python -m daily_v8.selector

  # Explicit date, dry-run (no DB writes):
  python -m daily_v8.selector --date 2026-09-07 --dry-run

  # Use latest-available historical date (clearly labelled):
  python -m daily_v8.selector --latest-available --dry-run

  # Write history to DB:
  python -m daily_v8.selector --date 2026-09-07 --persist
"""

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from daily_v8.models import (
    DailyV8BriefingHistory,
    Intraday45mResult,
    SelectorResult,
    TechnicalResult,
    YouTubeCandidate,
)

# ── Constants ────────────────────────────────────────────────────────────────
DB_PATH = "economic_youtube_intelligence/data/economic_intel.db"
KST = timezone(timedelta(hours=9))

# Selector score weights (must sum to 100)
W_NOVELTY    = 30
W_YT_EVIDENCE = 25
W_TECHNICAL  = 25
W_INTRADAY   = 20
SELECTION_THRESHOLD = 60

# Novelty table: (recent_10_report_date_mentions) → score
NOVELTY_TABLE = {0: 30.0, 1: 22.0, 2: 14.0}
NOVELTY_MANY  = 5.0          # ≥ 3 mentions
NOVELTY_RECENT_BRIEF_PENALTY = -999  # hard block

NEW_REASON_BONUS = 10.0       # different fingerprint from last seen

# Technical component mapping (t_score 0–100 → selector 0–25)
TECH_THRESHOLDS = [(80, "STRONG", 25), (50, "NORMAL", 17), (30, "WEAK", 7)]


# ── DB helpers ───────────────────────────────────────────────────────────────
def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_history_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_v8_briefing_history (
            report_date             TEXT NOT NULL,
            stock_code              TEXT NOT NULL,
            stock_name              TEXT,
            youtube_mention_count   INTEGER DEFAULT 0,
            youtube_channel_count   INTEGER,
            reason_fingerprint      TEXT,
            novelty_score           REAL,
            technical_score         REAL,
            intraday_45m_state      TEXT,
            selector_score          REAL,
            selected                INTEGER DEFAULT 0,
            briefing_status         TEXT,
            created_at              TEXT,
            PRIMARY KEY(report_date, stock_code)
        )
    """)
    conn.commit()


# ── Date helpers ─────────────────────────────────────────────────────────────
def kst_today() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def get_recent_report_dates(n: int = 10, before_date: Optional[str] = None) -> List[str]:
    """Return up to n most-recent report_date values from daily_intelligence."""
    conn = _conn()
    rows = conn.execute(
        "SELECT report_date FROM daily_intelligence WHERE (? IS NULL OR report_date < ?) ORDER BY report_date DESC LIMIT ?",
        (before_date, before_date, n),
    ).fetchall()
    return [r["report_date"] for r in rows]


def get_latest_available_date() -> Optional[str]:
    dates = get_recent_report_dates(1)
    return dates[0] if dates else None


# ── Reason fingerprint ───────────────────────────────────────────────────────
def compute_fingerprint(text: str) -> str:
    normalized = re.sub(r"[^\w\s]", "", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ── Channel / video count from DB join ───────────────────────────────────────
def resolve_channel_counts(video_ids: List[str]) -> Tuple[int, Optional[int], str]:
    """
    Returns (video_count, independent_channel_count, channel_count_source).
    If videos table has no rows → channel_count_source = UNAVAILABLE.
    """
    if not video_ids:
        return 0, None, "UNAVAILABLE"

    conn = _conn()
    placeholders = ",".join("?" * len(video_ids))
    rows = conn.execute(
        f"SELECT video_id, channel_id FROM videos WHERE video_id IN ({placeholders})",
        video_ids,
    ).fetchall()

    if not rows:
        # videos exist in claims but not in our DB — can't resolve
        return len(video_ids), None, "UNAVAILABLE"

    found_channels = set(r["channel_id"] for r in rows)
    return len(video_ids), len(found_channels), "DB_JOINED"


# ── YouTube candidate loading ─────────────────────────────────────────────────
def load_youtube_candidates(report_date: str) -> List[YouTubeCandidate]:
    conn = _conn()
    row = conn.execute(
        "SELECT top_mentioned_stocks FROM daily_intelligence WHERE report_date = ?",
        (report_date,),
    ).fetchone()
    if not row:
        return []

    top_stocks = json.loads(row["top_mentioned_stocks"])
    candidates: List[YouTubeCandidate] = []

    for item in top_stocks:
        stock_code = item.get("stock_code") or ""
        stock_name = item.get("stock_name") or ""

        if not stock_code:
            # skip unresolvable
            print(f"  [SKIP] 종목코드 없음: {stock_name}")
            continue

        evidence_ids = item.get("evidence_claim_ids", [])
        # Extract unique video_ids from claim IDs like "ApdCb-luAxI_C01"
        video_ids = list(dict.fromkeys(
            eid.split("_")[0] for eid in evidence_ids if "_" in eid
        ))

        vid_count, ch_count, ch_src = resolve_channel_counts(video_ids)
        if vid_count == 0 and evidence_ids:
            vid_count = len(evidence_ids)  # fallback: at least claim count

        reason_text = item.get("reason") or item.get("mention_context") or ""
        fingerprint = compute_fingerprint(reason_text)

        candidates.append(YouTubeCandidate(
            stock_code=stock_code,
            stock_name=stock_name,
            mention_count=len(evidence_ids),
            independent_channel_count=ch_count,
            channel_count_source=ch_src,
            video_count=vid_count,
            direct_or_related=item.get("mention_type", "DIRECT"),
            reason=reason_text,
            theme=item.get("theme", ""),
            report_date=report_date,
            reason_fingerprint=fingerprint,
        ))

    return candidates


# ── Technical condition ──────────────────────────────────────────────────────
def get_technical_condition(stock_code: str) -> TechnicalResult:
    """
    BROKEN: data is sufficient but indicators clearly show breakdown.
    NO_DATA: network error, ticker not found, insufficient data, any exception.
    Never conflates network failures with genuine indicator signals.
    """
    try:
        import sys as _sys
        from daily_v8.v8_runner import resolve_v8_engine_path
        v8_path = str(resolve_v8_engine_path())
        if v8_path not in _sys.path:
            _sys.path.insert(0, v8_path)
        from src.analysis.technical_analysis import TechnicalAnalysis
        import yfinance as yf

        df = None
        for suffix in (".KS", ".KQ"):
            ticker = yf.Ticker(stock_code + suffix)
            raw = ticker.history(period="3mo")
            if len(raw) >= 5:
                df = raw
                break

        if df is None or len(df) < 5:
            return TechnicalResult("NO_DATA", 0.0, 0.0, "insufficient bars from provider")

        df = df.rename(columns={
            "Open": "open_price", "High": "high_price",
            "Low": "low_price", "Close": "close_price", "Volume": "volume",
        })
        df["stk_date"] = df.index.strftime("%Y%m%d")

        analyzer = TechnicalAnalysis(df)
        res = analyzer.evaluate_signals()
        t_score = float(res.get("t_score", 0.0))

        for threshold, label, component in TECH_THRESHOLDS:
            if t_score >= threshold:
                return TechnicalResult(label, t_score, float(component))

        # t_score < 30 → genuine indicator breakdown (data was present)
        return TechnicalResult("BROKEN", t_score, 0.0, f"t_score={t_score:.1f} < 30 with full data")

    except Exception as exc:
        return TechnicalResult("NO_DATA", 0.0, 0.0, f"exception: {type(exc).__name__}: {exc}")


# ── 45-minute bar condition ───────────────────────────────────────────────────
def _get_completed_45m_bars(stock_code: str, now_kst: datetime) -> Optional[pd.DataFrame]:
    """
    Fetch 15m data, resample to 45m, then drop any bar whose END timestamp
    is in the future relative to now_kst.  Returns None on failure.
    """
    try:
        import sys as _sys
        from daily_v8.v8_runner import resolve_v8_engine_path
        v8_path = str(resolve_v8_engine_path())
        if v8_path not in _sys.path:
            _sys.path.insert(0, v8_path)
        from src.analysis.intraday_analysis import Intraday45mAnalyzer

        analyzer = Intraday45mAnalyzer()
        res = analyzer._fetch_from_yfinance(stock_code)
        if res is None:
            res = analyzer._fetch_from_naver_api(stock_code)
        if res is None:
            return None

        df_15m, _ = res
        if df_15m.empty or len(df_15m) < 3:
            return None

        df_45m = df_15m.resample("45min").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna()

        if df_45m.empty:
            return None

        # Each bar's END = bar_start + 45 min
        # Only keep bars whose end <= now_kst (completed bars)
        bar_end = df_45m.index + pd.Timedelta(minutes=45)

        # Normalise timezone: yfinance index may be tz-aware
        if bar_end.tz is None:
            now_cmp = now_kst.replace(tzinfo=None)
        else:
            now_cmp = now_kst

        completed = df_45m[bar_end <= now_cmp]
        return completed if not completed.empty else None

    except Exception:
        return None


def get_45m_condition(stock_code: str, now_kst: Optional[datetime] = None) -> Intraday45mResult:
    """
    Evaluate 45m bars using only *completed* bars.
    BAD only on clear bearish evidence; NO_DATA when data unavailable/insufficient.
    """
    if now_kst is None:
        now_kst = datetime.now(KST)

    completed = _get_completed_45m_bars(stock_code, now_kst)

    if completed is None or len(completed) < 2:
        bars = 0 if completed is None else len(completed)
        return Intraday45mResult("NO_DATA", f"completed bars={bars} < 2")

    last  = completed.iloc[-1]
    prev  = completed.iloc[-2]

    # ── Factor A: Close direction
    close_diff_pct = (last["Close"] - prev["Close"]) / (prev["Close"] + 1e-9) * 100

    # ── Factor B: Low breakdown (last low < prev low by ≥1%)
    low_breakdown = last["Low"] < prev["Low"] * 0.99

    # ── Factor C: Volume collapse (last vol < 30% of prev vol)
    vol_collapse = (prev["Volume"] > 0) and (last["Volume"] < prev["Volume"] * 0.30)

    bearish_count = sum([
        close_diff_pct <= -1.5,   # ≥1.5% drop
        low_breakdown,
        vol_collapse,
    ])
    bullish_count = sum([
        close_diff_pct >= 0.5,    # ≥0.5% rise
        last["Low"] >= prev["Low"],
        (prev["Volume"] > 0) and (last["Volume"] > prev["Volume"]),
    ])

    if bearish_count >= 2:
        return Intraday45mResult("BAD", f"bearish_factors={bearish_count} close_diff={close_diff_pct:.2f}%")
    elif bullish_count >= 2:
        return Intraday45mResult("GOOD", f"bullish_factors={bullish_count} close_diff={close_diff_pct:.2f}%")
    else:
        return Intraday45mResult("NEUTRAL", f"close_diff={close_diff_pct:.2f}%")


# ── Novelty ──────────────────────────────────────────────────────────────────
def _get_recent_n_report_dates(n: int = 10) -> List[str]:
    return get_recent_report_dates(n)


def get_mention_history_count(stock_code: str, recent_report_dates: List[str]) -> int:
    """Count how many of the recent report dates the stock appeared in Economic intelligence."""
    if not recent_report_dates:
        return 0
    conn = _conn()
    rows = conn.execute(
        "SELECT top_mentioned_stocks FROM daily_intelligence WHERE report_date IN ({})".format(
            ",".join("?" * len(recent_report_dates))
        ),
        recent_report_dates,
    ).fetchall()

    count = 0
    for row in rows:
        try:
            stocks = json.loads(row["top_mentioned_stocks"])
            for s in stocks:
                if s.get("stock_code") == stock_code:
                    count += 1
                    break
        except Exception:
            pass
    return count


def get_last_fingerprint(stock_code: str, before_date: Optional[str] = None) -> Optional[str]:
    """Return the most recent reason_fingerprint recorded in briefing history for this stock."""
    conn = _conn()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_v8_briefing_history'").fetchone():
        return None
    row = conn.execute(
        "SELECT reason_fingerprint FROM daily_v8_briefing_history "
        "WHERE stock_code = ? AND (? IS NULL OR report_date < ?) ORDER BY report_date DESC LIMIT 1",
        (stock_code, before_date, before_date),
    ).fetchone()
    return row["reason_fingerprint"] if row else None


def was_recently_selected(stock_code: str, recent_report_dates: List[str]) -> bool:
    """Return True if this stock was selected (selected=1) in any of the 5 most-recent dates."""
    if not recent_report_dates:
        return False
    recent5 = recent_report_dates[:5]
    conn = _conn()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_v8_briefing_history'").fetchone():
        return False
    row = conn.execute(
        "SELECT 1 FROM daily_v8_briefing_history "
        "WHERE stock_code = ? AND report_date IN ({}) AND selected = 1 LIMIT 1".format(
            ",".join("?" * len(recent5))
        ),
        [stock_code] + recent5,
    ).fetchone()
    return row is not None


def calculate_novelty(
    candidate: YouTubeCandidate,
    recent_10_dates: List[str],
) -> Tuple[float, str]:
    """
    Returns (novelty_score, note).
    Source: Economic daily_intelligence (not briefing history).
    """
    # Hard block: selected in recent 5 report dates
    past_10 = [d for d in recent_10_dates if d < candidate.report_date]
    if was_recently_selected(candidate.stock_code, past_10):
        return NOVELTY_RECENT_BRIEF_PENALTY, "REJECT: selected in recent 5 report dates"

    # Count appearances in recent 10 report dates (excluding today)
    mention_count = get_mention_history_count(candidate.stock_code, past_10)

    base_score = NOVELTY_TABLE.get(mention_count, NOVELTY_MANY)
    note = f"Economic mentions in last 10 report dates (excl. today) = {mention_count}"

    # New-reason bonus: only if we have a prior fingerprint and it differs
    last_fp = get_last_fingerprint(candidate.stock_code, before_date=candidate.report_date)
    reason_bonus = 0.0
    if last_fp and last_fp != candidate.reason_fingerprint:
        reason_bonus = NEW_REASON_BONUS
        note += f" | NEW_REASON_BONUS +{reason_bonus}"

    return base_score + reason_bonus, note


# ── Evidence score ────────────────────────────────────────────────────────────
def calculate_evidence_score(candidate: YouTubeCandidate) -> float:
    score = 0.0
    ch = candidate.independent_channel_count
    # Channel score (use video count as fallback if channel unavailable)
    if ch is None:
        # UNAVAILABLE — use video count as loose proxy but cap lower
        if candidate.video_count >= 3:
            score += 8
        elif candidate.video_count == 2:
            score += 5
        else:
            score += 3
    else:
        if ch >= 3:
            score += 15
        elif ch == 2:
            score += 10
        else:
            score += 5

    if candidate.direct_or_related == "DIRECT":
        score += 5
    if candidate.video_count > 1:
        score += 5

    return min(score, W_YT_EVIDENCE)  # cap at weight


# ── History persistence ───────────────────────────────────────────────────────
def upsert_history(result: SelectorResult, selected: bool) -> None:
    conn = _conn()
    ensure_history_table(conn)
    conn.execute("""
        INSERT INTO daily_v8_briefing_history
            (report_date, stock_code, stock_name, youtube_mention_count,
             youtube_channel_count, reason_fingerprint, novelty_score,
             technical_score, intraday_45m_state, selector_score,
             selected, briefing_status, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(report_date, stock_code) DO UPDATE SET
            youtube_mention_count = excluded.youtube_mention_count,
            youtube_channel_count = excluded.youtube_channel_count,
            reason_fingerprint    = excluded.reason_fingerprint,
            novelty_score         = excluded.novelty_score,
            technical_score       = excluded.technical_score,
            intraday_45m_state    = excluded.intraday_45m_state,
            selector_score        = excluded.selector_score,
            selected              = excluded.selected,
            briefing_status       = excluded.briefing_status,
            created_at            = excluded.created_at
    """, (
        result.report_date,
        result.stock_code,
        result.stock_name,
        result.video_count,
        result.independent_channel_count,
        result.reason_fingerprint,
        result.novelty_score,
        result.technical.raw_t_score,
        result.intraday.status,
        result.selector_score,
        1 if selected else 0,
        "SELECTED" if selected else "NOT_SELECTED",
        datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
    ))
    conn.commit()


# ── Main selector ─────────────────────────────────────────────────────────────
def get_same_day_selection(report_date: str) -> Optional[str]:
    """A persisted pending selection pins retries; never silently switch stocks."""
    conn = _conn()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='daily_v8_briefing_history'").fetchone():
        return None
    rows = conn.execute(
        "SELECT stock_code FROM daily_v8_briefing_history WHERE report_date=? AND selected=1",
        (report_date,),
    ).fetchall()
    if len(rows) > 1:
        raise ValueError("CONFLICTING_SAME_DAY_SELECTIONS")
    return rows[0]["stock_code"] if rows else None


def run_selector(
    report_date: str,
    kst_today_str: str,
    using_historical: bool,
    dry_run: bool,
    now_kst: Optional[datetime] = None,
) -> Optional[SelectorResult]:

    if now_kst is None:
        now_kst = datetime.now(KST)

    print("=" * 60)
    print("DAILY_V8_SELECTOR - DRY RUN" if dry_run else "DAILY_V8_SELECTOR")
    print(f"KST_TODAY={kst_today_str}")
    print(f"DATA_DATE={report_date}")
    print(f"USING_HISTORICAL_DATA={'YES' if using_historical else 'NO'}")
    print("=" * 60)

    candidates = load_youtube_candidates(report_date)
    print(f"\nTODAY_CANDIDATE_COUNT={len(candidates)}")

    if not candidates:
        print("\nFINAL_SELECTION=NO_SELECTION (no candidates)")
        return None

    pinned = get_same_day_selection(report_date)
    if pinned:
        candidates = [c for c in candidates if c.stock_code == pinned]
        if not candidates:
            print("FINAL_SELECTION=NO_SELECTION (pending stock absent; no replacement)")
            return None
    recent_10 = get_recent_report_dates(10, before_date=report_date)
    results: List[SelectorResult] = []

    for c in candidates:
        novelty_score, novelty_note = calculate_novelty(c, recent_10)
        ev_score = calculate_evidence_score(c)
        tech = get_technical_condition(c.stock_code)
        intra = get_45m_condition(c.stock_code, now_kst)

        # ── Reject logic ─────────────────────────────────────────────────
        decision = "CANDIDATE"

        if novelty_score == NOVELTY_RECENT_BRIEF_PENALTY:
            decision = "REJECT_RECENT_BRIEFING"
        elif tech.status == "BROKEN":
            decision = "REJECT_BROKEN_TECH"
        elif intra.status == "BAD":
            decision = "REJECT_BAD_45M"
        elif tech.status == "NO_DATA":
            decision = "REJECT_TECH_NO_DATA"
        elif intra.status == "NO_DATA":
            decision = "REJECT_45M_NO_DATA"

        # ── Score ─────────────────────────────────────────────────────────
        if decision.startswith("REJECT"):
            total_score = -999.0
        else:
            total_score = (
                novelty_score
                + ev_score
                + tech.selector_component
                + (W_INTRADAY if intra.status == "GOOD"
                   else W_INTRADAY // 2 if intra.status == "NEUTRAL"
                   else 0)
            )
            if decision == "NO_DATA_BOTH":
                total_score = -1.0  # eligible but below threshold

        r = SelectorResult(
            stock_code=c.stock_code,
            stock_name=c.stock_name,
            report_date=c.report_date,
            video_count=c.video_count,
            independent_channel_count=c.independent_channel_count,
            channel_count_source=c.channel_count_source,
            novelty_score=novelty_score,
            yt_evidence_score=ev_score,
            technical=tech,
            intraday=intra,
            selector_score=total_score,
            decision=decision,
            reason_fingerprint=c.reason_fingerprint,
        )
        results.append(r)

        # ── Print per-candidate ───────────────────────────────────────────
        ch_str = (str(c.independent_channel_count)
                  if c.independent_channel_count is not None else "UNAVAIL")
        print(f"\n  stock: {c.stock_name} ({c.stock_code})")
        print(f"    video_count: {c.video_count}")
        print(f"    independent_channel_count: {ch_str} [{c.channel_count_source}]")
        print(f"    novelty: {novelty_score:.1f}  ({novelty_note})")
        print(f"    yt_evidence: {ev_score:.1f}")
        print(f"    technical_status: {tech.status}  raw_t_score={tech.raw_t_score:.1f}"
              f"  selector_component={tech.selector_component:.1f}  [{tech.note}]")
        print(f"    45m_status: {intra.status}  [{intra.note}]")
        print(f"    selector_score: {total_score:.1f}")
        print(f"    decision: {decision}")

    # ── Select best ──────────────────────────────────────────────────────
    eligible = [r for r in results if r.selector_score >= SELECTION_THRESHOLD]
    if not eligible:
        print(f"\nFINAL_SELECTION=NO_SELECTION")
        winner = None
    else:
        winner = max(
            eligible,
            key=lambda r: (
                r.selector_score,
                r.novelty_score,
                r.independent_channel_count or 0,
                r.technical.raw_t_score,
            ),
        )
        print(f"\nFINAL_SELECTION={winner.stock_name} ({winner.stock_code})")
        print(f"FINAL_SCORE={winner.selector_score:.1f}")

    # ── Persist ───────────────────────────────────────────────────────────
    if not dry_run and pinned and winner is None:
        print("[HISTORY] Pending selection retained; no replacement on failed retry")
    elif not dry_run:
        for r in results:
            upsert_history(r, selected=(winner is not None and r.stock_code == winner.stock_code))
        print("\n[HISTORY] Persisted all candidates to daily_v8_briefing_history")
    else:
        print("\n[HISTORY] DRY-RUN: no DB writes")

    return winner


# ── CLI entry point ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Daily V8 stock selector")
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("--date", help="Report date YYYY-MM-DD")
    date_group.add_argument(
        "--latest-available", action="store_true",
        help="Use most recent available date in DB (historical, clearly labelled)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="Do not write to history DB (default: True)",
    )
    parser.add_argument(
        "--persist", action="store_true",
        help="Write results to history DB (overrides --dry-run)",
    )
    args = parser.parse_args()

    dry_run = not args.persist
    kst_now = datetime.now(KST)
    kst_today_str = kst_now.strftime("%Y-%m-%d")
    using_historical = False

    if args.latest_available:
        report_date = get_latest_available_date()
        if not report_date:
            print("TODAY_DATA_STATUS=NOT_READY (no data in DB)")
            sys.exit(1)
        using_historical = report_date != kst_today_str
    elif args.date:
        report_date = args.date
        using_historical = report_date != kst_today_str
    else:
        report_date = kst_today_str
        # Check if today's row exists
        conn = _conn()
        row = conn.execute(
            "SELECT 1 FROM daily_intelligence WHERE report_date = ?", (report_date,)
        ).fetchone()
        if not row:
            print(f"TODAY_DATA_STATUS=NOT_READY  (no row for {report_date})")
            sys.exit(0)

    run_selector(
        report_date=report_date,
        kst_today_str=kst_today_str,
        using_historical=using_historical,
        dry_run=dry_run,
        now_kst=kst_now,
    )


if __name__ == "__main__":
    main()
