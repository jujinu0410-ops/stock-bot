import gc,importlib,io,json,os,pathlib,sqlite3,sys,tempfile,types,unittest
from datetime import datetime
from unittest.mock import Mock,patch
import pandas as pd

B=pathlib.Path(__file__).resolve().parent
ROOT=pathlib.Path(os.environ.get('FIX_RUNTIME',str(B/'runtime')))
sys.path.insert(0,str(ROOT));sys.path.insert(1,str(B/'service-runtime'))
os.environ['STOCKBOT_TEST_MODE']='1'
os.environ['KIWOOM_USE_MOCK']='True'
os.environ['STOCKBOT_STATE_DIR']=tempfile.mkdtemp(prefix='signal-fix-test-')
from src.runtime.krx_calendar import KRXCalendar
from src.analysis.add_advisory_engine import AddAdvisory30mEngine
from src.analysis.bollinger_atr_strategy import prepare_completed_30m_data,resolve_cutoff
from src.analysis.sell_warning_30m_engine import SellWarning30mEngine
from src.runtime.runtime_scheduler import RuntimeScheduler
from src.database.db_manager import DatabaseManager
from src.notifications.mobile_renderer_v2 import build_today_action_summary_html,build_30m_add_advisory_table_section_html
import main

def raw():
    stamps=[]
    for day in ['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06']:
        stamps.extend(pd.date_range(day+' 09:00',periods=26,freq='15min'))
    n=len(stamps)
    return pd.DataFrame({'Open':[100+i*.1 for i in range(n)],'High':[102+i*.1 for i in range(n)],'Low':[98+i*.1 for i in range(n)],'Close':[101+i*.1 for i in range(n)],'Volume':[1000.]*n},index=stamps)

class Cutoff(unittest.TestCase):
    def test_close_resolver(self):
        for t in ['15:00','15:30','15:35','16:00']:
            with self.subTest(t=t):self.assertEqual(KRXCalendar.get_completed_30m_bar(datetime.fromisoformat('2026-11-06 '+t))[2],'15:00')
    def test_bb_close_cutoff(self):self.assertEqual(resolve_cutoff(datetime(2026,11,6,15,35),'1535').hour*100+resolve_cutoff(datetime(2026,11,6,15,35),'1535').minute,1500)
    def test_valid_close(self):
        cutoff=resolve_cutoff(datetime(2026,11,6,15,35),'1535')
        frame,q=prepare_completed_30m_data(raw(),'TEST_15M_BAR_START',cutoff,allow_sparse_completed=True)
        self.assertEqual(q,'VALID');self.assertEqual(str(frame.index[-1]),'2026-11-06 15:00:00')
    def test_missing_close(self):
        r=raw().drop(pd.Timestamp('2026-11-06 14:45'))
        f,q=prepare_completed_30m_data(r,'TEST_15M_BAR_START',datetime(2026,11,6,15),allow_sparse_completed=True)
        self.assertIsNone(f);self.assertEqual(q,'CUTOFF_MISMATCH')
    def test_incomplete_source(self):
        r=raw();r.attrs['source_watermark']='2026-11-06 14:59:59'
        f,q=prepare_completed_30m_data(r,'KIWOOM_15M_BAR_START',datetime(2026,11,6,15),allow_sparse_completed=True)
        self.assertIsNone(f);self.assertEqual(q,'SOURCE_NOT_COMPLETE')
    def test_old_source(self):
        f,q=prepare_completed_30m_data(raw().loc[:'2026-11-05'],'TEST_15M_BAR_START',datetime(2026,11,6,15),allow_sparse_completed=True)
        self.assertIsNone(f)
    def test_holidays(self):
        for d in ['2026-11-07','2026-12-25']:
            self.assertIsNone(KRXCalendar.get_completed_30m_bar(datetime.fromisoformat(d+' 15:35')))
    def test_add_actual_cutoff(self):
        analyzer=Mock();analyzer.fetch_canonical_15m_data.return_value=(raw(),'TEST_15M_BAR_START','NONE')
        slot=KRXCalendar.get_completed_30m_bar(datetime(2026,11,6,15,35))
        result=AddAdvisory30mEngine(analyzer).evaluate_stock_advisory('000990','2026-11-06',f'2026-11-06 {slot[2]}:00')
        self.assertTrue(result['data_quality'].startswith('VALID'))

class Safety(unittest.TestCase):
    def holding(self,price=161765,prev=162610,stop=162660):
        return {'stock_code':'133690','stock_name':'TIGER 미국나스닥100','current_price':price,'previous_confirmed_stop':prev,'confirmed_stop_price':stop,'is_stop_breached':price<=stop,'data_validity_flag':1,'trade_mode':'NORMAL'}
    def test_audit_nasdaq(self):
        h=build_today_action_summary_html([self.holding()],{})
        self.assertIn('손절선 침범',h);self.assertNotIn('<b>손절 상향</b>',h)
    def test_actual_raise_retained(self):self.assertIn('<b>손절 상향</b>',build_today_action_summary_html([self.holding(price=163000)],{}))
    def test_no_raise(self):self.assertNotIn('<b>손절 상향</b>',build_today_action_summary_html([self.holding(price=163000,prev=162660)],{}))
    def test_unproven_raise(self):
        h={'stock_code':'000990','smartphone_action':'상향 수정','stock_name':'DB하이텍'}
        self.assertNotIn('<b>손절 상향</b>',build_today_action_summary_html([h],{}))
    def test_boundary_price(self):
        from src.analysis.advisory_safety import stop_breached
        for price,want in [(162659,True),(162660,True),(162661,False)]:self.assertEqual(stop_breached(self.holding(price=price)),want)
    def test_audit_samchun_render(self):
        h={'stock_code':'000250','stock_name':'삼천당제약','current_price':176800,'confirmed_stop_price':189900}
        with patch('src.notifications.mobile_renderer_v2.get_latest_add_advisory_entry',return_value={'add_advisory_state':'ADD_WATCH','data_quality':'VALID'}):
            result=build_30m_add_advisory_table_section_html([h])
        self.assertIn('BLOCKED',result);self.assertIn('손절선 침범 우선',result)
    def test_engine_stop_blocks_strong(self):
        analyzer=Mock();analyzer.fetch_canonical_15m_data.return_value=(raw(),'TEST_15M_BAR_START','NONE')
        engine=AddAdvisory30mEngine(analyzer)
        before=engine.evaluate_stock_advisory('133690','2026-11-06','2026-11-06 15:00:00')
        self.assertIn(before['add_advisory_state'],('ADD_STRONG','ADD_WATCH'))
        after=engine.evaluate_stock_advisory('133690','2026-11-06','2026-11-06 15:00:00',holding_context=self.holding())
        self.assertEqual(after['add_advisory_state'],'ADD_BLOCKED');self.assertEqual(after['alert_candidate'],0)

class Sidecar(unittest.TestCase):
    def test_real_bar_pipeline_warning_and_normal(self):
        frame=raw()
        for bearish in (True,False):
            r=frame.copy()
            n=len(r);base=pd.Series([10000+(-1 if bearish else 1)*i*10 for i in range(n)],index=r.index)
            r['Open']=base+2;r['High']=base+10;r['Low']=base-10;r['Close']=base+(-8 if bearish else 8)
            analyzer=Mock();analyzer.fetch_canonical_15m_data.return_value=(r,'TEST_15M_BAR_START','NONE')
            row=SellWarning30mEngine(analyzer).evaluate_stock_warning('000990','2026-11-06','2026-11-06 15:00:00')
            self.assertTrue(row['data_quality'].startswith('VALID'))
            self.assertEqual(row['sell_warning_state'],'SELL_WARNING' if bearish else 'NORMAL')
    def test_continuity(self):
        cases=[('2026-11-06 14:30:00','2026-11-06 15:00:00',True),('2026-11-06 15:00:00','2026-11-09 09:30:00',True),('2026-10-08 15:00:00','2026-10-12 09:30:00',True),('2026-11-05 15:00:00','2026-11-09 09:30:00',False),('2026-11-06 15:00:00','2026-11-07 09:30:00',False),('2026-11-06 15:00:00','2026-11-06 15:00:00',False),('2026-11-06 15:00:00','2026-11-06 15:30:00',False),('2026-11-06 10:00:00','2026-11-06 11:00:00',False)]
        for a,b,want in cases:
            with self.subTest(a=a,b=b):self.assertEqual(RuntimeScheduler._is_immediately_preceding_30m_bar(a,b),want)
    def test_real_axes_and_db_confirm_duplicate(self):
        add={'vwap9':90.,'vwap26':100.,'obv':-100.,'obv9':0.,'obv_gap':-100.,'obv_gap_delta':-10.,'obv_gap_state':'CONTRACTING','chaikin_value':-100.,'chaikin_delta':-10.,'chaikin_state':'CHAIKIN_FALLING'}
        trend={'close_30m':85.,'cloud_bottom_30m':95.,'adx_14_30m':25.,'plus_di_30m':10.,'minus_di_30m':30.}
        engine=SellWarning30mEngine(Mock())
        with tempfile.TemporaryDirectory() as tmp:
            db=DatabaseManager(str(pathlib.Path(tmp)/'test.db'));scheduler=object.__new__(RuntimeScheduler);scheduler.db=db;scheduler._sell_warning_engine=Mock()
            for date,ts in [('2026-11-06','2026-11-06 15:00:00'),('2026-11-09','2026-11-09 09:30:00')]:
                row=engine.evaluate_canonical_axes('000990',date,ts,add,trend,completed_bar_count=78)
                self.assertEqual(row['sell_warning_state'],'SELL_WARNING')
                scheduler._sell_warning_engine.evaluate_stock_warning.return_value=row
                scheduler._evaluate_sell_warning_sidecar('000990',{},date,ts)
                scheduler._evaluate_sell_warning_sidecar('000990',{},date,ts)
            result=db.get_latest_sell_warning_for_stock('000990')
            self.assertEqual(result['sell_confirmed'],1)
            self.assertEqual(db.execute_query('select count(*) from sell_warning_30m')[0][0],2)
            gc.collect()
    def test_report_path_persists(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=DatabaseManager(str(pathlib.Path(tmp)/'test.db'));e=Mock()
            e.evaluate_stock_warning.return_value=SellWarning30mEngine._base_result('000990','2026-11-06','2026-11-06 15:00:00')
            main._ensure_report_sell_warnings(db,[{'stock_code':'000990'}],datetime(2026,11,6,15,35),e)
            self.assertEqual(db.execute_query('select count(*) from sell_warning_30m')[0][0],1)
            main._ensure_report_sell_warnings(db,[{'stock_code':'000990'}],datetime(2026,11,6,15,35),e)
            self.assertEqual(e.evaluate_stock_warning.call_count,1)
            gc.collect()

if __name__=='__main__':unittest.main(verbosity=2)
