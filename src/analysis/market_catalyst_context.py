# -*- coding: utf-8 -*-
"""Pure helpers for conservative disclosure/news catalyst assessment.

The output deliberately says "influence possible" rather than asserting causation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import unescape
import re
from typing import Any, Dict, Iterable, List


_TAG_RE = re.compile(r"<[^>]+>")


def plain_text(value: Any) -> str:
    text = unescape(str(value or ""))
    text = _TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _published_at(item: Dict[str, Any]) -> datetime | None:
    raw = str(item.get("published_at") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def assess_catalyst(
    disclosures: Iterable[Dict[str, Any]],
    news_items: Iterable[Dict[str, Any]],
    *,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Classify evidence around a price/technical alert without claiming causality."""
    disclosures = list(disclosures or [])
    news_items = list(news_items or [])
    now = now or datetime.now().astimezone()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)

    material_words = (
        "단일판매", "공급계약", "유상증자", "무상증자", "전환사채",
        "신주인수권부사채", "영업실적", "잠정실적", "사업보고서",
        "분기보고서", "반기보고서", "자기주식", "합병", "분할",
    )
    material_dart = [
        d for d in disclosures
        if any(word in str(d.get("report_nm") or "") for word in material_words)
    ]

    recent_6h: List[Dict[str, Any]] = []
    recent_24h: List[Dict[str, Any]] = []
    for item in news_items:
        dt = _published_at(item)
        if not dt:
            recent_24h.append(item)
            continue
        age_h = max(0.0, (now_utc - dt.astimezone(timezone.utc)).total_seconds() / 3600.0)
        if age_h <= 6:
            recent_6h.append(item)
        if age_h <= 24:
            recent_24h.append(item)

    if material_dart and recent_6h:
        label = "영향 가능성 높음"
        confidence = "HIGH"
        reason = "당일 중요 DART 공시와 최근 6시간 내 관련 뉴스가 함께 확인됩니다."
    elif material_dart:
        label = "공시 영향 가능성"
        confidence = "HIGH"
        reason = "당일 중요 DART 공시가 확인됩니다. 가격 반응과 시간대를 함께 확인해야 합니다."
    elif recent_6h:
        label = "뉴스 영향 가능성"
        confidence = "MEDIUM"
        reason = "최근 6시간 내 관련 뉴스가 확인되지만 인과관계는 확정할 수 없습니다."
    elif recent_24h:
        label = "관련 뉴스 있음"
        confidence = "LOW"
        reason = "24시간 내 관련 뉴스가 있으나 현재 움직임의 직접 촉매인지는 불확실합니다."
    elif disclosures:
        label = "일반 공시 있음"
        confidence = "LOW"
        reason = "당일 공시는 있으나 중요 촉매로 분류되지는 않았습니다."
    else:
        label = "확인된 촉매 없음"
        confidence = "NONE"
        reason = "당일 주요 DART 공시와 최근 관련 뉴스에서 뚜렷한 촉매를 확인하지 못했습니다."

    return {
        "label": label,
        "confidence": confidence,
        "reason": reason,
        "material_disclosure_count": len(material_dart),
        "disclosure_count": len(disclosures),
        "news_6h_count": len(recent_6h),
        "news_24h_count": len(recent_24h),
    }
