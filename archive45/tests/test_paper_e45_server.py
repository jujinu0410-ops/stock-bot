"""Fixed data only; runner blocks outbound HTTP and uses temporary state."""
import importlib.util
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest import TestCase
from unittest.mock import patch
from contextlib import ExitStack
import os
import json
import subprocess
import tempfile
import pandas as pd
from services import unified_scan as us
from services import youtube_signal_service as svc

KST=ZoneInfo('Asia/Seoul')
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('legacy_scan',ROOT/'tests/fixtures/paper_game_baseline/unified_scan.py')
legacy=importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


def bars():
    stamps=[]
    for day in pd.bdate_range('2026-10-26','2026-11-03'):
        stamps.extend(pd.date_range(day+pd.Timedelta(hours=9,minutes=45),periods=8,freq='45min'))
    stamps.append(pd.Timestamp('2026-11-04 09:45'))
    n=len(stamps)
    return pd.DataFrame({'Open':[10000.]*n,'High':[10200.]*n,'Low':[9900.]*n,'Close':[10100.]*n,'Volume':[1000.]*n},index=stamps)


class PaperMetadataTests(TestCase):
    def setUp(self):
        self.now=datetime(2026,11,4,9,48,tzinfo=KST)
        us._CACHE.clear();legacy._CACHE.clear()

    def scan_both(self,quality='VALID',code='069500',price=10000.,stop=None,frame_override=None):
        frame=(frame_override if frame_override is not None else bars()) if quality=='VALID' else None
        daily=pd.DataFrame({'Open':[10000.]*40,'High':[10200.]*40,'Low':[9900.]*40,'Close':[10100.]*40,'Volume':[1000.]*40},index=pd.bdate_range(end='2026-11-04',periods=40))
        class Session:
            retry_count=0
            def __init__(self,**kwargs):pass
        def quote(*args):return {'available':price is not None,'current_price':price,'change_pct':-.04,'source':'KIWOOM_KA10001'}
        payload={'ticker':code,'sources':['HELD'] if stop else ['ETF'],'market':'KOSPI','evaluate_jev':False}
        if stop:payload['stop_price']=stop
        results=[]
        with ExitStack() as stack:
            stack.enter_context(patch.object(svc,'RetryingKiwoomSession',Session))
            stack.enter_context(patch.object(svc,'_get_kiwoom_token',return_value='fake-token'))
            stack.enter_context(patch.object(svc,'_fetch_kiwoom_quote',side_effect=quote))
            stack.enter_context(patch.object(svc,'_fetch_kiwoom_minute_bars',return_value={'available':False,'bars':[]}))
            fetch=stack.enter_context(patch.object(svc,'get_strictly_sliced_45m_df',return_value=(frame,'TEST_15M_BAR_START',quality)))
            for module in (legacy,us):
                stack.enter_context(patch.object(module,'daily_frame',return_value=daily))
                stack.enter_context(patch.object(module,'analyze_obv15_sell_warning',return_value={'decision':'HOLD'}))
                stack.enter_context(patch.object(module.KRXCalendar,'is_krx_trading_day',return_value=True))
            stack.enter_context(patch.object(us,'paper_clock',return_value=self.now))
            for module in (legacy,us):results.append(module.scan(payload,svc,now=self.now))
        return results,fetch.call_count

    def test_additive_only_legacy_verdict_equivalence_and_no_extra_fetches(self):
        for code in ['069500','114800','229200','251340','005930']:
            for quality in ['VALID','CUTOFF_MISMATCH','INSUFFICIENT_COMPLETED_45M_BARS']:
                us._CACHE.clear();legacy._CACHE.clear()
                (old,new),count=self.scan_both(quality,code)
                stripped={k:v for k,v in new.items() if k!='paper_data'}
                self.assertEqual(old,stripped)
                self.assertEqual(count,2,'one existing history read in each old/new scan')
                self.assertEqual('paper_data' in new,code in us.PAPER_CODES)
                if code in us.PAPER_CODES:self.assertEqual(new['paper_data']['quality'],quality)

    def test_stop_and_missing_price_preserve_all_verdict_fields(self):
        for price in [None,9000.]:
            (old,new),_=self.scan_both('CUTOFF_MISMATCH','069500',price,10000.)
            self.assertEqual(old,{k:v for k,v in new.items() if k!='paper_data'})
            if price:self.assertEqual(new['stage'],'RISK')

    def test_first_bar_ohlc_completion_and_receipt_not_scan_start(self):
        (old,new),_=self.scan_both()
        p=new['paper_data'];b=p['first45']
        self.assertTrue(b['completed']);self.assertEqual(b['quality'],'VALID')
        self.assertEqual(b['start'],'2026-11-04T09:00:00+09:00');self.assertEqual(b['end'],'2026-11-04T09:45:00+09:00')
        self.assertEqual(b['open'],10000.);self.assertEqual(b['close'],10100.)
        self.assertEqual(p['quote_received_at'],self.now.isoformat())
        before=self.now.replace(hour=9,minute=44)
        self.assertFalse(us.paper_metadata(bars(),'TEST','VALID',before,None)['first45']['completed'])
        missing=bars().iloc[:-1]
        self.assertEqual(us.paper_metadata(missing,'TEST','VALID',self.now,None)['first45']['quality'],'FIRST45_MISSING')
        zero=bars();zero.loc[zero.index[-1],'Volume']=0
        first=us.paper_metadata(zero,'TEST','VALID',self.now,None)['first45']
        self.assertTrue(first['completed']);self.assertEqual(first['quality'],'NO_VOLUME')

    def test_batch_legacy_m45_omission_reproduced_and_additive_evidence_survives(self):
        (_,result),_=self.scan_both()
        with patch.dict(os.environ,{'YOUTUBE_SIGNAL_API_TOKEN':'test'}),patch.object(us,'scan',return_value=result):
            response=svc.app.test_client().post('/unified-scan-batch',json={'items':[{'ticker':'069500'}]},headers={'X-StockBot-Token':'test'})
        self.assertEqual(response.status_code,200)
        wire=response.json['results'][0]['result']
        self.assertEqual(set(wire['m45']),{'completed_bar'})
        self.assertNotIn('available',wire['m45'])
        self.assertTrue(wire['paper_data']['m45']['available'])
        self.assertTrue(wire['paper_data']['first45']['completed'])

    def test_batch_preserves_every_legacy_wire_field_and_call_count_for_four_etfs_and_other_signals(self):
        for code in ['069500','114800','229200','251340','058470','091160']:
            for quality in ['VALID','PARTIAL_BAR_CONTAMINATION','CUTOFF_MISMATCH']:
                us._CACHE.clear();legacy._CACHE.clear()
                (_,result),calls=self.scan_both(quality,code)
                with patch.dict(os.environ,{'YOUTUBE_SIGNAL_API_TOKEN':'test'}),patch.object(us,'scan',return_value=result) as scan:
                    response=svc.app.test_client().post('/unified-scan-batch',json={'items':[{'ticker':code}]},headers={'X-StockBot-Token':'test'})
                self.assertEqual(calls,2);self.assertEqual(scan.call_count,1)
                wire=response.json['results'][0]['result']
                keys=('ok','engine_version','stage','send_allowed','events','reasons','live_price','live_change_pct','live_price_source','atr14','session','confidence_adjustment')
                expected={k:result[k] for k in keys if k in result};expected['m45']={'completed_bar':result['m45'].get('completed_bar')}
                self.assertEqual({k:v for k,v in wire.items() if k!='paper_data'},expected)
                if quality!='VALID' and code in us.PAPER_CODES:
                    self.assertEqual(wire['paper_data']['quality'],quality)
                    self.assertFalse(wire['paper_data']['m45'].get('available',False))

    def test_full_day_actual_flask_batch_to_apps_script_ad_and_e45(self):
        codes=['069500','114800','229200','251340'];rounds=[]
        for hhmm in ['09:42','09:48','09:53','10:33','11:18','12:03','12:48','13:33','14:18','15:03','15:28']:
            hour,minute=map(int,hhmm.split(':'));self.now=self.now.replace(hour=hour,minute=minute)
            full={}
            for code in codes:
                frame=bars().iloc[:-1].copy()
                # Historical trend is synthetic, never downloaded or backfilled.
                up=code in ['069500','229200']
                history=[9000+i*1000/(len(frame)-1) if up else 11000-i*1000/(len(frame)-1) for i in range(len(frame))]
                for column,offset in [('Open',0),('Close',0),('High',100),('Low',-100)]:frame[column]=[v+offset for v in history]
                for stamp in pd.date_range('2026-11-04 09:45','2026-11-04 15:00',freq='45min'):
                    if stamp.time()<=self.now.time():frame.loc[stamp]=[10000.,10200.,9900.,10100.,1000.]
                price=10000. if hhmm<'10:00' else (10300. if up else 9700.)
                us._CACHE.clear();legacy._CACHE.clear()
                (_,full[code]),_=self.scan_both(code=code,price=price,frame_override=frame)
            with patch.dict(os.environ,{'YOUTUBE_SIGNAL_API_TOKEN':'test'}),patch.object(us,'scan',side_effect=lambda payload,service:full[payload['ticker']]):
                response=svc.app.test_client().post('/unified-scan-batch',json={'items':[{'ticker':code,'sources':['ETF']} for code in codes]},headers={'X-StockBot-Token':'test'})
            self.assertEqual(response.status_code,200)
            rounds.append({'at':self.now.isoformat(),'batch':response.json,'full':full})
        seed_times=[pd.Timestamp('2026-10-27 15:00')]
        for day in ['2026-10-28','2026-10-29','2026-10-30']:
            seed_times.extend(pd.date_range(day+' 09:45',day+' 15:00',freq='45min'))
        seed=[[str(t),code,10000.,'YFINANCE_15M (SYNTHETIC_FIXTURE)','VALID_45M_COMPLETE','ER20_WARMUP_ONLY_NO_TRADES'] for code in ['069500','229200'] for t in seed_times[:21]]
        now=self.now.replace(hour=16,minute=12)
        with patch.dict(os.environ,{'YOUTUBE_SIGNAL_API_TOKEN':'test','PAPER_E45_CLOSE_ENABLED':'ON'}),patch.object(us,'paper_clock',return_value=now),patch.object(us,'daily_frame',return_value=pd.DataFrame({'Close':[10000.],'Volume':[1000.]},index=[pd.Timestamp('2026-11-04')])):
            close=svc.app.test_client().post('/paper-e45-close',json={'day':'2026-11-04','tickers':codes[:2]},headers={'X-StockBot-Token':'test'})
        self.assertEqual(close.status_code,200)
        fixture={'day':'2026-11-04','rounds':rounds,'seed':seed,'closes':close.json['closes']}
        with tempfile.TemporaryDirectory(prefix='e45-day-fixture-') as directory:
            path=Path(directory)/'day.json';path.write_text(json.dumps(fixture),encoding='utf-8')
            result=subprocess.run(['node','tests/paper_e45_day_runner.js',str(path)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        summary=json.loads(result.stdout);self.assertEqual(summary['observations'],32);self.assertEqual(summary['e45_trade_count'],8)
        self.assertEqual(summary['additional_entry_market_requests'],0);self.assertTrue(summary['legacy_alerts_equal'])
        print('FULL_DAY_FIXED_FIXTURE '+json.dumps(summary))


class PaperCloseTests(TestCase):
    def setUp(self):
        us._CACHE.clear();self.after=datetime(2026,11,4,16,12,tzinfo=KST)

    def test_pending_before_finality_and_invalid_targets_do_not_fetch(self):
        with patch.object(us,'daily_frame',side_effect=AssertionError('must not fetch')):
            self.assertEqual(us.paper_close('069500','2026-11-04',self.after.replace(hour=15))['quality'],'CLOSE_PENDING')
            self.assertEqual(us.paper_close('005930','2026-11-04',self.after)['quality'],'INVALID_TARGET')
            self.assertEqual(us.paper_close('069500','2026-11-05',self.after)['quality'],'INVALID_TARGET')

    def test_target_date_no_quote_substitution_success_cached_failures_recover(self):
        frame=pd.DataFrame({'Close':[10000.],'Volume':[100.]},index=[pd.Timestamp('2026-11-03')])
        with patch.object(us,'daily_frame',return_value=frame) as fetch,patch.object(us,'paper_clock',return_value=self.after):
            self.assertEqual(us.paper_close('069500','2026-11-04',self.after)['quality'],'CLOSE_PENDING')
            frame.index=[pd.Timestamp('2026-11-04')]
            a=us.paper_close('069500','2026-11-04',self.after)
            b=us.paper_close('069500','2026-11-04',self.after)
            self.assertEqual(a,b);self.assertEqual(fetch.call_count,2)
            self.assertEqual(a['market_time'],'2026-11-04T15:30:00+09:00');self.assertTrue(a['paper_fill'])

    def test_endpoint_off_auth_whitelist_and_partial_failure(self):
        client=svc.app.test_client();headers={'X-StockBot-Token':'test'}
        with patch.dict(os.environ,{'YOUTUBE_SIGNAL_API_TOKEN':'test','PAPER_E45_CLOSE_ENABLED':'OFF'}),patch.object(us,'paper_close') as close:
            self.assertEqual(client.post('/paper-e45-close',json={},headers=headers).status_code,403);close.assert_not_called()
        with patch.dict(os.environ,{'YOUTUBE_SIGNAL_API_TOKEN':'test','PAPER_E45_CLOSE_ENABLED':'ON'}),patch.object(us,'paper_close',side_effect=RuntimeError('temporary')) as close:
            self.assertEqual(client.post('/paper-e45-close',json={'day':'2026-11-04','tickers':['069500']}).status_code,401)
            for codes in [['005930'],['069500']*2,[],['069500','114800','005930'],[{}],[None]]:
                self.assertEqual(client.post('/paper-e45-close',json={'day':'2026-11-04','tickers':codes},headers=headers).status_code,400)
            self.assertEqual(client.post('/paper-e45-close',json={'day':'bad','tickers':['069500']},headers=headers).status_code,400)
            close.assert_not_called()
            r=client.post('/paper-e45-close',json={'day':'2026-11-04','tickers':['069500','114800']},headers=headers)
            self.assertEqual(close.call_count,2);self.assertEqual([c['quality'] for c in r.json['closes']],['CLOSE_PENDING']*2)
