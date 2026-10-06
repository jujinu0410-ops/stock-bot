"""
Daily V8 One-Stock Briefing — data models.
"""
from typing import Optional, List
from dataclasses import dataclass, field


@dataclass
class YouTubeCandidate:
    stock_code: str
    stock_name: str
    mention_count: int
    independent_channel_count: Optional[int]   # None = UNAVAILABLE
    channel_count_source: str                  # "DB_JOINED" | "UNAVAILABLE"
    video_count: int
    direct_or_related: str                     # "DIRECT" | "RELATED"
    reason: str
    theme: str
    report_date: str
    reason_fingerprint: str = ""


@dataclass
class TechnicalResult:
    status: str          # STRONG | NORMAL | WEAK | BROKEN | NO_DATA
    raw_t_score: float   # 0–100 from TechnicalAnalysis.evaluate_signals()
    selector_component: float  # 0–25 mapped for selector use
    note: str = ""


@dataclass
class Intraday45mResult:
    status: str     # GOOD | NEUTRAL | BAD | NO_DATA
    note: str = ""


@dataclass
class SelectorResult:
    stock_code: str
    stock_name: str
    report_date: str
    video_count: int
    independent_channel_count: Optional[int]
    channel_count_source: str
    novelty_score: float
    yt_evidence_score: float
    technical: TechnicalResult
    intraday: Intraday45mResult
    selector_score: float
    decision: str       # CANDIDATE | REJECT_* | NO_DATA_BOTH | NO_SELECTION
    reason_fingerprint: str = ""


@dataclass
class DailyV8BriefingHistory:
    report_date: str
    stock_code: str
    stock_name: str
    youtube_mention_count: int = 0
    youtube_channel_count: Optional[int] = None
    reason_fingerprint: str = ""
    novelty_score: float = 0.0
    technical_score: float = 0.0
    intraday_45m_state: str = ""
    selector_score: float = 0.0
    selected: int = 0
    briefing_status: str = ""
    created_at: str = ""
