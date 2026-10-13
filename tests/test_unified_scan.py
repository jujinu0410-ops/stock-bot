from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo
import pandas as pd
import pytest
from services import unified_scan as u
from services.unified_signal_engine import evaluate_signal

NOW=datetime(2026,10,13,12,30,tzinfo=ZoneInfo('Asia/Seoul'))

def frame():
    dates=pd.bdate_range(end='2026-10-12',periods=65)
    return pd.DataFrame({'Open':range(100,165),'High':range(102,167),
        'Low':range(98,163),'Close':range(101,166),'Volume':[1000]*65},index=dates)

def service(price=166,pct=.01):
    return SimpleNamespace(
        SignalServiceError=ValueError,
        _clean_ticker=lambda x:x,
        RetryingKiwoomSession=lambda **kw:SimpleNamespace(retry_count=0),
        _get_kiwoom_token=Mock(return_value='token'),
        _fetch_kiwoom_quote=Mock(return_value={'available':True,'current_price':price,'change_pct':pct,'source':'KIWOOM_KA10001'}),
        _fetch_kiwoom_minute_bars=Mock(return_value={'available':True,'bars':[
            {'time':f'20261013122{i}00','close':164+i/4,'high':165+i/4,'low':163+i/4,'volume':1000} for i in range(5,10)]}),
        get_strictly_sliced_45m_df=Mock(return_value=(frame().set_axis(pd.date_range(end='2026-10-13 12:00',periods=65,freq='45min')),'TEST','VALID')),
        _analyze_45m_exit_risk_df=Mock(return_value={'decision':'PASS'}))

@pytest.fixture(autouse=True)
def reset_cache():
    u._CACHE.clear()

def test_defaults_never_manufacture_technical_verification():
    r=evaluate_signal({'stage':'BUY_SETUP','live_verified':True})
    assert not r['send_allowed']

@patch.object(u,'daily_frame',side_effect=ValueError('outage'))
def test_kospi_minus_seven_survives_technical_outage_and_jev(mock_daily):
    r=u.scan({'ticker':'069500','sources':['ETF'],'market':'KOSPI'},service(94090,-.074),NOW)
    assert r['send_allowed'] and r['stage']=='RISK'
    assert any(e['key']=='MARKET_CRASH' and e['level']==7 for e in r['events'])
    assert r['jev_gate']['reason']=='RISK_BYPASS_OR_NO_BUY'

@patch.object(u,'daily_frame',side_effect=ValueError('outage'))
def test_stop_is_independent_of_candles_or_atr(mock_daily):
    r=u.scan({'ticker':'103590','sources':['HELD','YOUTUBE'],'stop_price':170},service(),NOW)
    assert r['send_allowed'] and r['events'][0]['key']=='STOP_BREACH'

@patch.object(u,'daily_frame',side_effect=lambda ticker:frame())
def test_sources_never_change_computation(mock_daily):
    results=[]
    for source in ['HELD','ETF','YOUTUBE']:
        u._CACHE.clear()
        r=u.scan({'ticker':'007660','sources':[source],'market':'KOSPI','evaluate_jev':False},service(),NOW)
        results.append({k:r[k] for k in ['stage','technical','atr14','anchor','events','send_allowed']})
    assert results[0]==results[1]==results[2]

def test_lg_buy_is_blocked_by_weak_45m_even_if_market_rises():
    reasons=u.entry_guard(390000,17000,350000,{'ma20':370000},
        {'available':True,'gold':False,'vwap9':395000},{'current':True,'bars':[]},{'change_pct':.04})
    assert '45M_NOT_GOLD_OR_PRICE_BELOW_VWAP9' in reasons

def test_inverse_buy_is_blocked_by_current_weak_minutes():
    bars=[{'close':120-i*2,'high':121-i*2,'low':119-i*2,'volume':100} for i in range(5)]
    reasons=u.entry_guard(111,10,100,{'ma20':110},{'available':True,'gold':True,'vwap9':105},
        {'current':True,'bars':bars},{'change_pct':.07})
    assert 'RECENT_1M_WEAKENING' in reasons

@patch.object(u,'daily_frame',side_effect=lambda ticker:frame())
@patch.object(u,'analyze_obv15_sell_warning')
def test_iljin_warn_seah_pass_remain_distinct(obv,daily):
    for decision in ['WARN','PASS']:
        u._CACHE.clear();obv.return_value={'decision':decision,'consecutive_no_gold_bars':19}
        r=u.scan({'ticker':'103590','sources':['HELD'],'evaluate_jev':False},service(),NOW)
        assert any(e['key']=='OBV15_WARNING' for e in r['events']) == (decision=='WARN')

def test_stale_and_future_minute_bars_rejected():
    for ts in ['20261012122900','20261013122000','20261013123500','bad']:
        assert not u.minute_is_current({'available':True,'bars':[{'time':ts}]},NOW)

@patch.object(u,'daily_frame',side_effect=lambda ticker:frame())
def test_missing_price_cannot_send_buy_or_fake_risk(daily):
    svc=service();svc._fetch_kiwoom_quote.return_value={'available':False}
    r=u.scan({'ticker':'007660','sources':['YOUTUBE'],'stop_price':999999},svc,NOW)
    assert not r['send_allowed'] and not r['events']

@patch.object(u,'daily_frame',side_effect=lambda ticker:frame())
def test_unknown_market_and_unverified_sector_are_neutral(daily):
    r=u.scan({'ticker':'007660','sources':['HELD'],'market':'KRX',
        'sector_etf_code':'123456','etf_universe':['069500'],'evaluate_jev':False},service(),NOW)
    assert r['confidence_adjustment']==0 and not r['sector_etf']

def test_http_requires_authentication_and_accepts_multiple_sources():
    from services import youtube_signal_service as s
    client=s.app.test_client()
    with patch.object(s,'_secret_ok',return_value=False):
        assert client.post('/unified-scan',json={'ticker':'069500'}).status_code==401
    with patch.object(s,'_secret_ok',return_value=True),patch.object(u,'scan',return_value={'ok':True,'engine_version':u.VERSION}):
        assert client.post('/unified-scan',json={'ticker':'069500','sources':['HELD','ETF']}).json['ok']
