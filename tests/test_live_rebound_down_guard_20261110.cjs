'use strict';
const fs=require('fs'),vm=require('vm'),path=require('path'),assert=require('assert');
const script=path.join(__dirname,'../apps_script/paper_game/UnifiedWatchlists30.js');
function formatDate(d,z,p){
 const opts={timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'};
 const s=new Intl.DateTimeFormat('sv-SE',opts).format(d);
 return p==='yyyy-MM-dd'?s.slice(0,10):p==='MM-dd HH:mm'?s.slice(5,16):s;
}
const ctx=vm.createContext({Utilities:{formatDate},console,Date,Math,JSON,Number,String,Set,Object,Array,isFinite});
vm.runInContext(fs.readFileSync(script,'utf8'),ctx);
const now=new Date('2026-11-10T09:07:00+09:00');
const now0935=new Date('2026-11-10T09:35:00+09:00');
const prevSlot=Math.floor(now.getTime()/300000)-1;
const prevSlot0935=Math.floor(now0935.getTime()/300000)-1;
const previous={day:'2026-11-10',slot:prevSlot,stage:'DOWN_CONFIRMED',count:1,sent:{}};
function signal(ticker='229200',pct=.0535,stage='DOWN_CONFIRMED',extra={}) {
 const names={'229200':'KODEX 코스닥150','069500':'KODEX 200','133690':'TIGER 미국나스닥100'};
 const prices={'229200':10830,'069500':82980,'133690':166470};
 const sources=ticker==='133690'?['ETF','HELD']:['ETF'];
 return {
  item:Object.assign({ticker,name:names[ticker]||'테스트종목',sources,stop_price:0},extra.item||{}),
  result:Object.assign({ok:true,engine_version:'three-lists-v3-m30',stage,send_allowed:true,
   session:'KRX_SESSION',live_price_source:'KIWOOM_KA10001',
   live_price:prices[ticker]||50000,live_change_pct:pct,
   reasons:[],events:[{key:stage,stage,level:1,reason:'2026-11-09 completed daily'}],
   technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-09 15:30:00',
    regime:stage==='DOWN_EARLY'?'EARLY_WEAKENING':'BEAR_CONFIRMED',stage},
   m30:{available:false}},extra.result||{}),
  old:JSON.parse(JSON.stringify(extra.old||previous))
 };
}
function m30Bar(dir='RISING',bar='2026-11-10 09:30:00',gold=true){
 return {available:true,gold,vwap9:10500,vwap26:10200,vwap9_dir:dir,
  completed_bar:bar,source:'KIWOOM_15M_BAR_START',watermark:'2026-11-10 09:35:00'};
}
function plan(ticker,pct,stage,extra,at=now){
 const x=signal(ticker,pct,stage,extra);
 return ctx.umPlan_M30(x.item,x.result,x.old,at);
}
let count=0;
function test(name,fn){fn();count++;console.log('PASS '+name);}

// 1. Original 10 guard tests at 09:07 (unverified 30m bar)
test('11/10 09:07 KODEX KOSDAQ150 +5.35% does not emit fresh bearish confirmation',
 ()=>{const p=plan('229200',.0535,'DOWN_CONFIRMED');
  assert.equal(p.events.length,0);assert.equal(p.state.lastResult.stage,'DOWN_CONFIRMED');
  assert.equal(p.state.count,2);});
test('11/10 09:07 KODEX200 +5.72% does not emit fresh bearish confirmation',
 ()=>assert.equal(plan('069500',.0572,'DOWN_CONFIRMED').events.length,0));
test('positive KOSDAQ ETF does not emit early previous-day down',
 ()=>assert.equal(plan('229200',.0535,'DOWN_EARLY').events.length,0));
test('negative KOSDAQ ETF retains genuine bearish daily alert',
 ()=>assert.equal(plan('229200',-.05,'DOWN_CONFIRMED').events[0].key,'DOWN_CONFIRMED'));
test('neutral KOSDAQ ETF retains formal down alert',
 ()=>assert.equal(plan('229200',0,'DOWN_CONFIRMED').events[0].key,'DOWN_CONFIRMED'));
test('live rebound suppresses held historical down only when 30m unverified',
 ()=>{const x=signal('133690',.0344,'DOWN_CONFIRMED');
  assert.equal(ctx.umPlan_M30(x.item,x.result,x.old,now).events.length,0);});
test('STOP_BREACH remains urgent despite rebound',
 ()=>{const x=signal('133690',.06,'RISK',{item:{stop_price:170000},
   result:{events:[{key:'STOP_BREACH',stage:'RISK',level:1,reason:'실시간 손절선'}]}} );
  const p=ctx.umPlan_M30(x.item,x.result,x.old,now);
  assert.equal(p.events[0].key,'STOP_BREACH');
  const brief=ctx.umSignalBrief_M30(p,now);
  assert.match(brief,/^실시간 손절선/);
  assert.match(brief,/당일 반등과 별개로 위험경고 유지/);});
test('30m independent risk warning remains active',
 ()=>{const x=signal('229200',.06,'RISK',{result:{events:[{key:'SELL_PREP_WARNING',stage:'RISK',level:1,reason:'30m weak'}]}} );
  assert.equal(ctx.umPlan_M30(x.item,x.result,x.old,now).events[0].key,'SELL_PREP_WARNING');});
test('unverified daily classification is not secretly rewritten',
 ()=>{const p=plan('229200',.0535,'DOWN_CONFIRMED');assert.equal(p.result.technical.regime,'BEAR_CONFIRMED');});
test('after rebound turns negative, previously withheld down confirmation is eligible',
 ()=>{const p=plan('229200',-.02,'DOWN_CONFIRMED');assert.equal(p.events[0].stateKey,'DOWN_CONFIRMED');});

// 2. Incident formatting verification: 2026-11-10 09:07 three actual cases (229200 +5.35%, 069500 +5.72%, 133690 +3.44%)
test('11/10 09:07 incident cases format as rebound in daily downtrend, never 하락확인 or 매수확인',
 ()=>{
  for(const [code,pct,pctText] of [['229200',.0535,'+5.35%'],['069500',.0572,'+5.72%'],['133690',.0344,'+3.44%']]){
   const x=signal(code,pct,'DOWN_CONFIRMED');
   const syntheticPlan={item:x.item,result:x.result,state:x.old,events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED',stateKey:'DOWN_CONFIRMED'}]};
   const msg=ctx.umMessage_M30([syntheticPlan],now);
   assert.match(msg.subject,/일봉 하락 추세 속 반등 진행/);
   assert(!msg.subject.includes('하락확인'));
   assert(!msg.subject.includes('매수확인'));
   assert(!msg.subject.includes('▼'));
   assert.match(msg.body,new RegExp('일봉 하락 추세 속 반등 진행 · 전일\\(2026-11-09\\) 완성 일봉 하락 추세 · 당일 \\'+pctText+' 반등 · 30분봉 방향 미확인 \\(추세 전환 단정 유보 · 매수확인 아님\\)'));
  }
 });

// 3. 30m-confirmed rebound (전일 하락 추세 + 당일 상승 + 30분봉 상승 확인 → 일봉 약세 속 30분봉 반등 확인)
test('30m-confirmed rebound passes once per day for Market ETF and Held, with accurate subject and body',
 ()=>{
  for(const [code,pct] of [['229200',.0535],['069500',.0572],['133690',.0344]]){
   const old0935={day:'2026-11-10',slot:prevSlot0935,stage:'DOWN_CONFIRMED',count:1,sent:{}};
   const p=plan(code,pct,'DOWN_CONFIRMED',{old:old0935,result:{m30:m30Bar('RISING')}},now0935);
   assert.equal(p.events.length,1);
   assert.equal(p.events[0].key,'DOWN_CONFIRMED');
   const msg=ctx.umMessage_M30([p],now0935);
   assert.match(msg.subject,/↗/);
   assert.match(msg.subject,/일봉 약세 속 30분봉 반등 확인/);
   assert(!msg.subject.includes('하락확인'));
   assert(!msg.subject.includes('매수확인'));
   assert.match(msg.body,/일봉 약세 속 30분봉 반등 확인 · 전일\(2026-11-09\) 완성 일봉 하락 추세/);
   assert.match(msg.body,/30분봉 09:30 상승 확인 \(매수확인 아님\)/);
   // Deduplication: once committed, next slot must not re-emit
   const committed=ctx.umCommit_M30(p,now0935,'msg-1','evt-1');
   const nextNow=new Date('2026-11-10T09:40:00+09:00');
   const p2=plan(code,pct,'DOWN_CONFIRMED',{old:committed,result:{m30:m30Bar('RISING')}},nextNow);
   assert.equal(p2.events.length,0);
  }
 });

// 4. Prevent alert flood: ordinary YOUTUBE / non-market ETF never emit DOWN_CONFIRMED rebound, and STOP_BREACH held stocks do not double-send
test('ordinary watchlists and already-warned RISK holdings never flood rebound alerts',
 ()=>{
  const old0935={day:'2026-11-10',slot:prevSlot0935,stage:'DOWN_CONFIRMED',count:1,sent:{}};
  const yt=plan('005930',.04,'DOWN_CONFIRMED',{item:{ticker:'005930',name:'삼성전자',sources:['YOUTUBE']},old:old0935,result:{m30:m30Bar('RISING')}},now0935);
  assert.equal(yt.events.length,0);
  const sentStop=Object.assign({},old0935,{sent:{STOP_BREACH:{at:'2026-11-10T00:02:00Z'}}});
  const heldAfterStop=plan('133690',.0344,'DOWN_CONFIRMED',{old:sentStop,result:{m30:m30Bar('RISING')}},now0935);
  assert.equal(heldAfterStop.events.length,0);
 });

// 5. Downtrend continuation (전일 하락 추세 + 당일 하락 + 30분봉 하락 → 하락 추세 지속 · 단기 약세)
test('daily downtrend + intraday fall + 30m falling formats as 하락 추세 지속 · 단기 약세',
 ()=>{
  const old0935={day:'2026-11-10',slot:prevSlot0935,stage:'DOWN_CONFIRMED',count:1,sent:{}};
  const p=plan('229200',-.025,'DOWN_CONFIRMED',{old:old0935,result:{m30:m30Bar('FALLING')}},now0935);
  assert.equal(p.events.length,1);
  const msg=ctx.umMessage_M30([p],now0935);
  assert.match(msg.subject,/▼ 코스닥 하락확인/);
  assert.match(msg.body,/하락 확인 · 전일\(2026-11-09\) 완성 일봉 기술지표 · 30분봉 09:30 참조 · 하락 추세 지속 · 단기 약세/);
 });

// 6. Rise, sideways, trend reversal vs rebound, and missing data scenarios
test('uptrend, sideways, and unverified data scenarios distinguish timeframe provenance',
 ()=>{
  // Uptrend (전일 상승 추세 + 당일 상승 + 30분봉 상승)
  const upSignal=signal('133690',.021,'BUY_CONFIRMED',{
   old:{day:'2026-11-10',slot:prevSlot0935,stage:'BUY_CONFIRMED',count:1,sent:{}},
   result:{stage:'BUY_CONFIRMED',jev_gate:{status:'OK',decision:'SEND'},
    events:[{key:'BUY_CONFIRMED',stage:'BUY_CONFIRMED',level:1,reason:'BULL_CONFIRMED'}],
    technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-09 15:30:00',regime:'BULL_CONFIRMED',stage:'BUY_CONFIRMED'},
    m30:m30Bar('RISING')}
  });
  const pUp=ctx.umPlan_M30(upSignal.item,upSignal.result,upSignal.old,now0935);
  const briefUp=ctx.umSignalBrief_M30(pUp,now0935);
  assert.match(briefUp,/전일\(2026-11-09\) 완성 일봉 매수확인/);
  assert.equal(ctx.umLiveDirection_M30(upSignal.result,now0935).summary,'일봉 상승 추세 유지 · 30분봉 단기 강세');

  // Sideways (전일 하락 추세 + 당일 상승 + 30분봉 중립)
  const flatM30=signal('133690',.015,'DOWN_CONFIRMED',{result:{m30:m30Bar('FLAT')}});
  const flatPlan={item:flatM30.item,result:flatM30.result,state:flatM30.old,events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED',stateKey:'DOWN_CONFIRMED'}]};
  const briefFlat=ctx.umSignalBrief_M30(flatPlan,now0935);
  assert.match(briefFlat,/일봉 하락 추세 속 반등 진행/);
  assert.match(briefFlat,/30분봉 09:30 중립 \(추세 전환 단정 유보 · 매수확인 아님\)/);

  // Insufficient / unverified daily data
  const noDaily=signal('133690',.03,'BUY_SETUP',{result:{stage:'BUY_SETUP',technical:{basis:'INCOMPLETE_DAILY_NOT_CONFIRMED'},m30:{available:false}}});
  const briefNoDaily=ctx.umSignalBrief_M30({item:noDaily.item,result:noDaily.result,events:[{key:'BUY_SETUP',stage:'BUY_SETUP'}]},now);
  assert(!briefNoDaily.includes('완성 일봉'));
  assert.match(briefNoDaily,/상위 추세 확인 필요/);
 });

console.log(JSON.stringify({tests:count,failures:0,mailSent:0,orders:0}));
