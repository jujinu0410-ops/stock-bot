"""Read-only, cached paper evidence. No order or mail paths exist here."""
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import math
import pandas as pd
from flask import request, jsonify
from src.runtime.krx_calendar import KRXCalendar

KST=ZoneInfo('Asia/Seoul')
CODES={'069500','114800','229200','251340'}

def confirmed_daily(code,day,now,svc):
    """Broker's KRX daily record; current daily close is final only after 16:10.
    Never use a current quote, and never turn daily high/low into final-bar OHLC.
    """
    from services.unified_scan_m30 import cached
    def load():
        session=svc['RetryingKiwoomSession'](max_attempts=3)
        token=cached('token',1200,lambda:svc['_get_kiwoom_token'](session))
        response=session.post(svc['KIWOOM_BASE_URL']+'/api/dostk/chart',
            headers={'content-type':'application/json;charset=UTF-8','authorization':'Bearer '+token,
                'api-id':'ka10081','cont-yn':'N','next-key':''},
            json={'stk_cd':code,'base_dt':day.replace('-',''),'upd_stkpc_tp':'0'},timeout=10)
        response.raise_for_status();body=response.json()
        if str(body.get('return_code','0'))!='0':raise ValueError('BROKER_DAILY_REJECTED')
        rows=body.get('stk_dt_pole_chart_qry',[])
        matches=[r for r in rows if str(r.get('dt',''))==day.replace('-','')]
        if len(matches)!=1:return {'quality':'NO_DATA','ticker':code,'day':day}
        row=matches[0]
        def n(key):
            v=abs(float(str(row.get(key,'')).replace(',','')))
            if not math.isfinite(v) or v<=0:raise ValueError('INVALID_DAILY_PRICE')
            return v
        result={'ticker':code,'day':day,'open':n('open_pric'),
            'source':'KIWOOM_KA10081_KRX_DAILY','observed_at':now.isoformat(),
            'open_market_time':day+'T09:00:00+09:00','quality':'OPEN_CONFIRMED'}
        if now>=datetime.fromisoformat(day+'T16:10:00+09:00'):
            result.update(close=n('cur_prc'),quality='CONFIRMED_DAILY_CLOSE',
                market_time=day+'T15:30:00+09:00',paper_fill=True)
        return result
    final=now>=datetime.fromisoformat(day+'T16:10:00+09:00')
    return cached(('m30_broker_daily',code,day,final),86400 if final else 300,load,
        cache_if=lambda r:r.get('quality') in {'OPEN_CONFIRMED','CONFIRMED_DAILY_CLOSE'})

def session_evidence(code,day,now,svc):
    from services.unified_scan_m30 import cached, previous_day
    target=date.fromisoformat(day)
    # No current incomplete interval may enter the history. Final interval is
    # always separate until exchange auction OHLCV provenance is established.
    slot=KRXCalendar.get_completed_30m_bar(now) if target==now.date() else None
    end=min(slot[2],'15:00') if slot else '15:00'
    history_day=target if target<now.date() or slot else previous_day(now)
    cutoff=datetime.fromisoformat(history_day.isoformat()+'T'+end+':00+09:00')
    frame,source,quality=cached(('30m',code,cutoff.isoformat()),900,
        lambda:svc['get_strictly_sliced_30m_df'](code,cutoff,allow_sparse_completed=True),
        cache_if=lambda r:r[0] is not None and r[2]=='VALID')
    bars=[];seed=[]
    if frame is not None and quality=='VALID':
        for stamp,row in frame.iterrows():
            ts=pd.Timestamp(stamp)
            if ts.tzinfo is None:ts=ts.tz_localize(KST)
            else:ts=ts.tz_convert(KST)
            bar={'start':(ts-pd.Timedelta(minutes=30)).isoformat(),'end':ts.isoformat(),
                **{k.lower():float(row[k]) for k in ['Open','High','Low','Close','Volume']},
                'completed':True,'quality':'VALID' if row['Volume']>0 else 'NO_VOLUME','source':source}
            if ts.date()==target:bars.append(bar)
            if ts.date().isoformat()<'2026-11-05' and ts<=now:
                seed.append([ts.strftime('%Y-%m-%d %H:%M:%S'),code,float(row.Close),source,'VALID_30M_COMPLETE','ER20_WARMUP_ONLY_NO_TRADES'])
    daily=confirmed_daily(code,day,now,svc) if target<=now.date() else {'quality':'NO_DATA'}
    last={'start':day+'T15:00:00+09:00','end':day+'T15:30:00+09:00',
        'completed':False,'quality':'NOT_DUE' if now<datetime.fromisoformat(day+'T15:30:00+09:00') else 'NO_DATA',
        'open':None,'high':None,'low':None,'close':None,'volume':None,
        'source':'FINAL_AUCTION_OHLCV_NOT_VERIFIED','tail_eligible':False}
    if daily.get('quality')=='CONFIRMED_DAILY_CLOSE':
        last.update(close=daily['close'],quality='PARTIAL',close_source=daily['source'])
    next_day=target+timedelta(days=1)
    while not KRXCalendar.is_krx_trading_day(next_day):next_day+=timedelta(days=1)
    next_daily={}
    if now>=datetime.fromisoformat(next_day.isoformat()+'T09:01:00+09:00'):
        next_daily=confirmed_daily(code,next_day.isoformat(),now,svc)
        if next_daily.get('quality')=='CONFIRMED_DAILY_CLOSE':
            next_daily={**next_daily,'quality':'OPEN_CONFIRMED'}
    return {'ticker':code,'day':day,'schema':2,'bar_interval_minutes':30,'contract_version':'KRX_M30_V1',
        'computed_at':now.isoformat(),'source':source,'history_quality':quality,
        'bars':bars,'last30':last,'daily':daily,'seed':seed[-21:],
        'next_trading_day':next_day.isoformat(),'next_daily':next_daily}

def install_routes(app,secret_ok,svc):
    @app.post('/paper-m30-session')
    def paper_m30_session():
        if not secret_ok():return jsonify({'ok':False,'error':'UNAUTHORIZED'}),401
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or set(body)-{'day','tickers'}:return jsonify({'ok':False,'error':'INVALID_REQUEST'}),400
        codes=body.get('tickers');day=body.get('day')
        try:
            if date.fromisoformat(day).isoformat()!=day:raise ValueError()
        except (TypeError,ValueError):return jsonify({'ok':False,'error':'INVALID_DAY'}),400
        now=datetime.now(KST)
        if not isinstance(codes,list) or not 1<=len(codes)<=4 or any(c not in CODES for c in codes) or len(set(codes))!=len(codes):return jsonify({'ok':False,'error':'INVALID_TICKERS'}),400
        if day>now.date().isoformat() or not KRXCalendar.is_krx_trading_day(day):return jsonify({'ok':False,'error':'NOT_AVAILABLE_TRADING_DAY'}),400
        results=[]
        for code in codes:
            try:results.append(session_evidence(code,day,now,svc))
            except Exception as e:results.append({'ticker':code,'day':day,'quality':'NO_DATA','reason':type(e).__name__})
        return jsonify({'ok':True,'paper_only':True,'order_api':False,'results':results})
