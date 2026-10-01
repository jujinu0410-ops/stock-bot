from datetime import datetime, timezone

from src.analysis.market_catalyst_context import assess_catalyst, plain_text


def test_plain_text_strips_html():
    assert plain_text("<b>호재</b> &amp; 확인") == "호재 & 확인"


def test_material_dart_plus_recent_news_is_high():
    now = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)
    disclosures = [{"report_nm": "단일판매ㆍ공급계약체결"}]
    news = [{
        "title": "수주 확대",
        "published_at": "2026-10-01T03:30:00+00:00",
    }]
    result = assess_catalyst(disclosures, news, now=now)
    assert result["confidence"] == "HIGH"
    assert result["label"] == "영향 가능성 높음"


def test_old_news_is_not_direct_catalyst():
    now = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)
    news = [{
        "title": "전일 기사",
        "published_at": "2026-09-30T00:00:00+00:00",
    }]
    result = assess_catalyst([], news, now=now)
    assert result["confidence"] == "NONE"


def test_recent_news_without_disclosure_is_medium():
    now = datetime(2026, 10, 1, 5, 0, tzinfo=timezone.utc)
    news = [{
        "title": "관련 기사",
        "published_at": "2026-10-01T02:00:00+00:00",
    }]
    result = assess_catalyst([], news, now=now)
    assert result["confidence"] == "MEDIUM"
    assert result["news_6h_count"] == 1


def test_no_evidence_is_none():
    result = assess_catalyst([], [])
    assert result["label"] == "확인된 촉매 없음"
    assert result["confidence"] == "NONE"
