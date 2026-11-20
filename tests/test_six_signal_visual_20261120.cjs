'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const file = path.join(__dirname,'../apps_script/paper_game/UnifiedWatchlists30.js');
const Utilities = {formatDate(date,zone,pattern) {
  const s = new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',
    year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',
    minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(date);
  return pattern==='yyyy-MM-dd' ? s.slice(0,10) :
    pattern==='MM-dd HH:mm' ? s.slice(5,16) : s;
}};
const c=vm.createContext({Utilities,Date,Math,JSON,Number,String,Array,Set,Object,isFinite,console});
vm.runInContext(fs.readFileSync(file,'utf8'),c,{filename:file});
const now = new Date('2026-11-20T09:33:00+09:00');
const visual = (p) => c.umSixSignalVisual_M30(p,now);
const make=(stage,regime,pct,events,opts={})=>({
  item:{ticker:opts.ticker||'328130',name:opts.name||'루닛',
        sources:opts.sources||['YOUTUBE'],stop_price:opts.stop_price||0},
  result:{
    ok:true,session:'KRX_SESSION',live_price_source:'KIWOOM_KA10001',
    live_price:opts.price||16320,live_change_pct:pct,stage,send_allowed:true,
    events:events.map(key=>({key,stage:key==='SELL_PREP_WARNING'||key==='STOP_BREACH'||key==='MARKET_CRASH'?'RISK':stage,level:1})),
    reasons:[],technical:{basis:'COMPLETED_DAILY',
      as_of:'2026-11-19 15:30:00',regime,stage,vwap9:15000,prev_high:16000},
    m30:{available:true,gold:false,vwap9_dir:'FALLING',vwap9:16000,vwap26:16500,
      completed_bar:'2026-11-20 09:30:00',source:'KIWOOM_15M_BAR_START',
      watermark:'2026-11-20 09:33:00'}
  },
  events:events.map(key=>({key,stage:key==='SELL_PREP_WARNING'||key==='STOP_BREACH'||key==='MARKET_CRASH'?'RISK':stage,level:1,stateKey:key}))
});
let count=0;
function test(name,fn){fn();count++;console.log('PASS',name);}
test('Lunit: completed daily up + live decline = blue V, not ▲',()=>{
  const p=make('BUY_CONFIRMED','BULL_CONFIRMED',-.0222,['BUY_CONFIRMED']);
  const v=visual(p),mail=c.umMessage_M30([p],now);
  assert.equal(v.mark,'V');assert.equal(v.color,'#1565c0');
  assert.equal(v.caption,'일봉 상승 중 눌림목');
  assert.match(mail.subject,/V 루닛 일봉 상승 중 눌림목/);
  assert.match(mail.body,/일봉 상승 중 눌림목/);
  assert.match(mail.body,/매수확인/);
  assert.match(mail.htmlBody,/<span style="color:#1565c0">V<\/span>/);
  assert.equal(JSON.parse(mail.body.split('UNIFIED_EVENTS_JSON=')[1])[0].id,c.umEventId_M30(p,now));
  assert.match(JSON.parse(mail.body.split('UNIFIED_EVENTS_JSON=')[1])[0].stage,/매수확인/);
});
test('Inverse: completed daily down + live rise despite SELL_PREP warning = red ^ with warning intact',()=>{
  const p=make('RISK','BEAR_CONFIRMED',.0397,['SELL_PREP_WARNING'],{
    ticker:'114800',name:'KODEX 인버스',sources:['ETF'],price:1361});
  const v=visual(p),mail=c.umMessage_M30([p],now);
  assert.equal(v.mark,'^');assert.equal(v.color,'#c62828');
  assert.equal(v.caption,'일봉 하락 중 상승 시도');
  assert.match(mail.subject,/\^ .*일봉 하락 중 상승 시도.*위험경고/);
  assert.match(mail.body,/일봉 하락 중 상승 시도/);
  assert.match(mail.body,/단기 매도 경고/);
  assert.match(mail.htmlBody,/<span style="color:#c62828">\^<\/span>/);
  assert.equal(JSON.parse(mail.body.split('UNIFIED_EVENTS_JSON=')[1])[0].id,c.umEventId_M30(p,now));
});
test('Live inverse case: EARLY_IMPROVEMENT is not misrepresented as completed daily downtrend',()=>{
  const p=make('RISK','EARLY_IMPROVEMENT',.0397,['SELL_PREP_WARNING'],{
    ticker:'114800',name:'KODEX 인버스',sources:['ETF'],price:1361});
  const v=visual(p),mail=c.umMessage_M30([p],now);
  assert.equal(v.mark,'^');assert.equal(v.color,'#c62828');
  assert.equal(v.caption,'일봉 추세 미확정 · 당일 상승 시도');
  assert.match(mail.subject,/일봉 추세 미확정 · 당일 상승 시도/);
  assert.match(mail.body,/단기 매도 경고/);
  assert(!mail.subject.includes('일봉 하락 중 상승 시도'));
});
test('Today HPSP -6.86%: daily down and live down + M30 observation must be blue ▽',()=>{
  const p=make('WATCH','BOUNCE_IN_DOWNREGIME',-.0686,['VWAP30_BULL_STACK_EARLY'],{
    ticker:'403870',name:'HPSP',price:39400});
  const v=visual(p),m=c.umMessage_M30([p],now);
  assert.equal(v.mark,'▽');assert.equal(v.color,'#1565c0');
  assert.match(m.subject,/▽ HPSP 일봉·당일 하락 중 30분봉 정배열 관심/);
  assert.match(m.htmlBody,/<span style="color:#1565c0">▽<\/span>/);
  assert.match(m.body,/30분봉 정배열 새로 포착/);
  assert.equal(JSON.parse(m.body.split('UNIFIED_EVENTS_JSON=')[1])[0].stage,'30분봉 정배열 관심');
  assert.equal(p.events[0].key,'VWAP30_BULL_STACK_EARLY');
});
test('Today KC Tech and Korean Kolmar: negative live / down or uncertain daily = blue ▽',()=>{
  for(const regime of ['BOUNCE_IN_DOWNREGIME','EARLY_WEAKENING','NEUTRAL']){
    const p=make('WATCH',regime,-.0048,['VWAP30_BULL_STACK_EARLY'],{ticker:'281820',name:'케이씨텍',price:83200});
    const v=visual(p);
    assert.equal(v.mark,'▽',regime);
    assert.match(v.caption,/당일 하락/);
    assert.equal(v.color,'#1565c0');
  }
});
test('Today Hyundai Glovis +1.47%: daily up and live up keeps red △',()=>{
  const p=make('WATCH','BULL_CONFIRMED',.0147,['VWAP30_BULL_STACK_EARLY'],{
    ticker:'086280',name:'현대글로비스',price:206500});
  const v=visual(p),m=c.umMessage_M30([p],now);
  assert.equal(v.mark,'△');assert.equal(v.color,'#c62828');
  assert.equal(v.caption,'');
  assert.match(m.subject,/△ 현대글로비스/);
});
test('Day down but live rising 30m stack remains red ^ and holds source event ID',()=>{
  const p=make('WATCH','BOUNCE_IN_DOWNREGIME',.0033,['VWAP30_BULL_STACK_EARLY'],{
    ticker:'003550',name:'LG',price:90600});
  const v=visual(p),m=c.umMessage_M30([p],now);
  assert.equal(v.mark,'^');assert.equal(v.caption,'일봉 하락 중 상승 시도');
  assert.match(m.body,/일봉 하락 중 상승 시도/);
  assert.equal(JSON.parse(m.body.split('UNIFIED_EVENTS_JSON=')[1])[0].id,c.umEventId_M30(p,now));
});
test('STOP_BREACH takes precedence over upside on previous close',()=>{
  const p=make('RISK','BEAR_CONFIRMED',.0397,['STOP_BREACH'],{
    ticker:'114800',name:'KODEX 인버스',sources:['ETF','HELD'],price:1361,stop_price:1400});
  assert.equal(visual(p).caption,'');
  assert.equal(visual(p).mark,'▼');
  const m=c.umMessage_M30([p],now);
  assert.match(m.subject,/▼/);
  assert.match(m.body,/손절선/);
});
test('Market crash takes precedence over mixed-direction icon',()=>{
  const p=make('RISK','BEAR_CONFIRMED',.02,['MARKET_CRASH'],{ticker:'069500',name:'KODEX 200',sources:['ETF']});
  assert.equal(visual(p).caption,'');
  assert.equal(visual(p).mark,'▼');
});
test('Unknown daily source and quote freshness do not create V or ^',()=>{
  for(const change of [
    p=>p.result.technical.basis='INCOMPLETE_DAILY_NOT_CONFIRMED',
    p=>p.result.technical.as_of='2026-11-20 15:30:00',
    p=>p.result.live_price_source='STALE_QUOTE',
    p=>p.result.session='OUTSIDE',
    p=>{delete p.result.live_change_pct;},
    p=>p.result.technical.regime='EARLY_IMPROVEMENT'
  ]){
    const p=make('BUY_CONFIRMED','BULL_CONFIRMED',-.02,['BUY_CONFIRMED']);
    change(p);assert.equal(visual(p).caption,'');
  }
});
test('Aligned and flat signals keep original four triangles',()=>{
  for(const [stage,regime,pct,mark] of [
    ['BUY_SETUP','BULL_CONFIRMED',.02,'△'],
    ['BUY_CONFIRMED','BULL_CONFIRMED',.02,'▲'],
    ['DOWN_EARLY','BEAR_CONFIRMED',-.02,'▽'],
    ['DOWN_CONFIRMED','BEAR_CONFIRMED',-.02,'▼'],
    ['BUY_CONFIRMED','BULL_CONFIRMED',0,'▲'],
    ['DOWN_CONFIRMED','BEAR_CONFIRMED',0,'▼']
  ]){
    const p=make(stage,regime,pct,[stage]);
    assert.equal(visual(p).mark,mark,stage+' '+pct);
    assert.equal(visual(p).caption,'');
  }
});
test('Mixed-trend state changes display only, original alert and machine payload unchanged',()=>{
  const p=make('BUY_CONFIRMED','BULL_CONFIRMED',-.02,['BUY_CONFIRMED']);
  const originalStage=p.result.stage;
  const originalKeys=p.events.map(e=>e.key).join('+');
  const stateBefore=JSON.stringify(p);
  const msg=c.umMessage_M30([p],now);
  assert.equal(p.result.stage,originalStage);
  assert.equal(p.events.map(e=>e.key).join('+'),originalKeys);
  assert.equal(JSON.stringify(p),stateBefore);
  assert.equal(JSON.parse(msg.body.split('UNIFIED_EVENTS_JSON=')[1])[0].ticker,p.item.ticker);
});
test('Confirmed rebound in daily downtrend uses ^ not legacy orange seventh icon',()=>{
  const p=make('DOWN_CONFIRMED','BEAR_CONFIRMED',.04,['DOWN_CONFIRMED']);
  p.result.m30.vwap9_dir='RISING';
  assert.equal(c.umIsReboundDisplay_M30(p,now),true);
  assert.equal(visual(p).mark,'^');
  assert.match(c.umMessage_M30([p],now).subject,/\^ .*일봉 하락 중 상승 시도/);
});
console.log('PASS_COUNT',count,'MAIL_SENT',0,'ORDERS',0);
