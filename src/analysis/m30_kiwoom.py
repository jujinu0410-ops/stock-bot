"""Read-only KRX 15m OHLCV; never access positions, orders or mail."""
import threading,time,re,math,os
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests
KST=ZoneInfo('Asia/Seoul')
_LOCK=threading.RLock()
_CLIENT=None
_TOKEN_AT=0.
_LAST_CALL=0.
_CACHE={}
_HTTP=requests.Session()

def parse_chart(payload,observed_at):
    if str(payload.get('return_code','0'))!='0':raise ValueError('KIWOOM_CHART_BUSINESS_ERROR')
    rows=payload.get('stk_min_pole_chart_qry')
    if not isinstance(rows,list) or not rows:raise ValueError('KIWOOM_CHART_NO_DATA')
    records=[];seen=set()
    for row in rows:
        stamp=str(row.get('cntr_tm') or '')
        if not re.fullmatch(r'\d{14}',stamp) or stamp in seen:raise ValueError('KIWOOM_INVALID_TIMESTAMP')
        seen.add(stamp);at=pd.Timestamp(datetime.strptime(stamp,'%Y%m%d%H%M%S'),tz=KST)
        if at>pd.Timestamp(observed_at):raise ValueError('KIWOOM_FUTURE_TIMESTAMP')
        values=[]
        for field in ['open_pric','high_pric','low_pric','cur_prc','trde_qty']:
            raw=row.get(field)
            if raw is None or str(raw).strip()=='':raise ValueError('KIWOOM_MISSING_OHLCV')
            n=float(str(raw).replace(',',''));n=abs(n) if field!='trde_qty' else n
            if not math.isfinite(n) or n<0 or (field!='trde_qty' and n==0):raise ValueError('KIWOOM_INVALID_OHLCV')
            values.append(n)
        records.append([at,*values])
    frame=pd.DataFrame(records,columns=['At','Open','High','Low','Close','Volume']).set_index('At').sort_index()
    frame.attrs.update(source_watermark=frame.index[-1].isoformat(),raw_ohlcv_unmodified=True,observed_at=observed_at.isoformat())
    return frame

def fetch_chart(session,token,ticker,now):
    with _LOCK:return _fetch_chart_locked(session,token,ticker,now)

def _fetch_chart_locked(session,token,ticker,now):
    global _LAST_CALL
    # Shared per-process throttle; bounded 429 retry. Same read-only chart API as 1m.
    attempts=1 if hasattr(session,'retry_count') else 3
    for attempt in range(attempts):
        delay=.6-(time.monotonic()-_LAST_CALL)
        if delay>0:time.sleep(delay)
        _LAST_CALL=time.monotonic()
        r=session.post('https://api.kiwoom.com/api/dostk/chart',
            headers={'Content-Type':'application/json;charset=UTF-8','api-id':'ka10080','authorization':'Bearer '+token},
            json={'stk_cd':ticker,'tic_scope':'15','upd_stkpc_tp':'1','base_dt':now.strftime('%Y%m%d')},timeout=10)
        if r.status_code!=429:break
        if attempt<attempts-1:time.sleep(min(2**attempt,4))
    r.raise_for_status()
    return parse_chart(r.json(),datetime.now(KST))

def fetch_canonical(ticker):
    global _CLIENT,_TOKEN_AT
    try:
        if not re.fullmatch(r'\d{6}',str(ticker)):raise ValueError('INVALID_TICKER')
        now=datetime.now(KST);key=(ticker,now.date().isoformat(),now.hour*2+now.minute//30)
        with _LOCK:
            if key in _CACHE:return _CACHE[key].copy(),'KIWOOM_15M_BAR_START','NONE'
            from src.api.kiwoom_api import KiwoomAPIClient
            if _CLIENT is None:_CLIENT=KiwoomAPIClient(use_mock=False)
            if not _CLIENT.is_valid_key():raise ValueError('KIWOOM_CREDENTIALS_UNAVAILABLE')
            if not _CLIENT.access_token or time.monotonic()-_TOKEN_AT>1200:
                if not _CLIENT.get_access_token():raise ValueError('KIWOOM_TOKEN_UNAVAILABLE')
                _TOKEN_AT=time.monotonic()
            frame=fetch_chart(_HTTP,_CLIENT.access_token,ticker,now)
            # A new completed-slot key forces a fresh chart. Never cache failure.
            _CACHE[key]=frame
            for old in list(_CACHE):
                if old!=key and old[1: ]!=key[1: ]:_CACHE.pop(old,None)
            return frame.copy(),'KIWOOM_15M_BAR_START','NONE'
    except Exception as exc:
        return None,'KIWOOM_15M_BAR_START','KIWOOM_CHART_UNAVAILABLE:'+type(exc).__name__
