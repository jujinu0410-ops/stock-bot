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
const prevSlot=Math.floor(now.getTime()/300000)-1;
const previous={day:'2026-11-10',slot:prevSlot,stage:'DOWN_CONFIRMED',count:1,sent:{}};
function signal(ticker='229200',pct=.0535,stage='DOWN_CONFIRMED') {
 return {
  item:{ticker,name:ticker==='229200'?'KODEX 코스닥150':'KODEX 200',sources:['ETF'],stop_price:0},
  result:{ok:true,engine_version:'three-lists-v3-m30',stage,send_allowed:true,
   session:'KRX_SESSION',live_price_source:'KIWOOM_KA10001',
   live_price:ticker==='229200'?10830:82980,live_change_pct:pct,
   reasons:[],events:[{key:stage,stage,level:1,reason:'2026-11-09 completed daily'}],
   technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-09 15:30:00',
    regime:'BEAR_CONFIRMED',stage},m30:{available:false}},
  old:JSON.parse(JSON.stringify(previous))
 };
}
function plan(ticker,pct,stage){const x=signal(ticker,pct,stage);return ctx.umPlan_M30(x.item,x.result,x.old,now);}
let count=0;
function test(name,fn){fn();count++;console.log('PASS '+name);}
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
test('live rebound suppresses held historical down only',
 ()=>{const x=signal('229200',.0344,'DOWN_CONFIRMED');
  x.item={ticker:'133690',name:'TIGER 미국나스닥100',sources:['HELD'],stop_price:0};
  assert.equal(ctx.umPlan_M30(x.item,x.result,x.old,now).events.length,0);});
test('STOP_BREACH remains urgent despite rebound',
 ()=>{const x=signal('229200',.06,'RISK');x.item={ticker:'133690',name:'TIGER 미국나스닥100',sources:['HELD'],stop_price:12000};
  x.result.events=[{key:'STOP_BREACH',stage:'RISK',level:1,reason:'실시간 손절선'}];
  const p=ctx.umPlan_M30(x.item,x.result,x.old,now);
  assert.equal(p.events[0].key,'STOP_BREACH');});
test('30m independent risk warning remains active',
 ()=>{const x=signal('229200',.06,'RISK');x.result.events=[{key:'SELL_PREP_WARNING',stage:'RISK',level:1,reason:'30m weak'}];
  assert.equal(ctx.umPlan_M30(x.item,x.result,x.old,now).events[0].key,'SELL_PREP_WARNING');});
test('unverified daily classification is not secretly rewritten',
 ()=>{const p=plan('229200',.0535,'DOWN_CONFIRMED');assert.equal(p.result.technical.regime,'BEAR_CONFIRMED');});
test('after rebound turns negative, previously withheld down confirmation is eligible',
 ()=>{const p=plan('229200',-.02,'DOWN_CONFIRMED');assert.equal(p.events[0].stateKey,'DOWN_CONFIRMED');});
console.log(JSON.stringify({tests:count,failures:0,mailSent:0,orders:0}));