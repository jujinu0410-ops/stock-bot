'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '../apps_script/paper_game/UnifiedWatchlists30.js'), 'utf8');
const sandbox = {
  Date, Math, Number, JSON, Set, String, Array, Object, console,
  Utilities: {formatDate(date, zone, pattern) {
    const stamp = new Date(date.getTime()+9*3600000).toISOString();
    if(pattern==='HH:mm')return stamp.slice(11,16);
    return pattern==='yyyy-MM-dd'?stamp.slice(0,10):stamp.slice(0,19).replace('T',' ');
  }},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename:'UnifiedWatchlists30.js'});

if(process.argv.includes('--check-wire')){
  const {now,cases}=JSON.parse(fs.readFileSync(0,'utf8'));
  for(const r of cases){
    const actual=sandbox.umDailyObvWma9Gate_M30(r,new Date(now));
    assert.equal(actual.valid,r.daily_obv_wma9_gate.valid);
    assert.equal(actual.qualified,r.daily_obv_wma9_gate.qualified);
  }
  console.log(`PASS: ${cases.length} Python wire decisions match Apps Script`);
  process.exit(0);
}

const now = new Date('2026-11-27T10:05:00+09:00');
const slot = Math.floor(now.getTime()/300000);
const daily = (as_of='2026-11-26 15:30:00') => ({
  basis:'COMPLETED_DAILY',as_of,vwap9:95,regime:'BEAR_CONFIRMED'
});
const wire = (session_date='2026-11-27',as_of='2026-11-26 15:30:00',obv=100,wma9=90) => ({
  session_date,as_of,expected_as_of:as_of,obv,wma9,valid:true,qualified:obv>wma9
});
const base = (options={}) => ({
  ok:true,session:'KRX_SESSION',live_price_source:'KIWOOM_KA10001',
  live_price:100,live_change_pct:0,send_allowed:true,
  stage:'WATCH',events:[],technical:daily(),
  daily_obv_wma9_gate:wire(undefined,undefined,options.obv,options.wma9),
  m30:{},reasons:[],...(options.extra||{})
});
const old = (stage='WATCH',extra={}) => ({
  day:'2026-11-27',sent:{},count:2,stage,slot:slot-1,...extra
});
const watchItem = {ticker:'005930',sources:['YOUTUBE']};
const marketItem = {ticker:'069500',sources:['ETF']};

let gate = sandbox.umDailyObvWma9Gate_M30(base({obv:100,wma9:90}),now);
assert.equal(gate.valid,true);
assert.equal(gate.qualified,true);
gate = sandbox.umDailyObvWma9Gate_M30(base({obv:90,wma9:90}),now);
assert.equal(gate.qualified,false,'equal OBV and WMA9 is not gold');
gate = sandbox.umDailyObvWma9Gate_M30(base({obv:100,wma9:null}),now);
assert.equal(gate.valid,false,'missing WMA9 must fail closed');
gate = sandbox.umDailyObvWma9Gate_M30(base({extra:{technical:{...daily(),basis:'INCOMPLETE_DAILY_NOT_CONFIRMED'}}}),now);
assert.equal(gate.valid,false,'provisional daily data must fail closed');
for(const bad of [null,false,true,'',' ','90','broken',NaN,Infinity,-Infinity,[],{}]){
  assert.equal(sandbox.umDailyObvWma9Gate_M30(base({wma9:bad}),now).valid,false);
  assert.equal(sandbox.umDailyObvWma9Gate_M30(base({obv:bad}),now).valid,false);
}
for(const override of [{session_date:'2026-11-26'},{as_of:'2026-11-25 15:30:00'},
  {expected_as_of:'2026-11-25 15:30:00'},{valid:false},{qualified:false},
  {expected_as_of:'2026-11-27 15:30:00'}]){
  const r=base({extra:{daily_obv_wma9_gate:{...wire(),...override}}});
  assert.equal(sandbox.umDailyObvWma9Gate_M30(r,now).valid,false);
}

const m30 = {
  available:true,completed_bar:'2026-11-27 10:00:00',
  source:'KIWOOM_15M_BAR_START',watermark:'new',
  vwap9:98,vwap26:95,gold:true,vwap9_slope:0.01,vwap9_dir:'RISING'
};

const buyDead = base({obv:80,wma9:90,extra:{
  stage:'BUY_SETUP',events:[{key:'BUY_SETUP',stage:'BUY_SETUP'}]
}});
let plan = sandbox.umPlan_M30(watchItem,buyDead,old('BUY_SETUP'),now);
assert.equal(plan.events.length,0,'dead daily OBV must suppress BUY_SETUP');
assert.ok(plan.result.reasons.includes('DAILY_OBV9_WMA_GATE_CLOSED'));

const buyGold = base({obv:100,wma9:90,extra:{
  stage:'BUY_CONFIRMED',events:[{key:'BUY_CONFIRMED',stage:'BUY_CONFIRMED'}]
}});
plan = sandbox.umPlan_M30(watchItem,buyGold,old('BUY_CONFIRMED'),now);
assert.ok(plan.events.some(e=>e.key==='BUY_CONFIRMED'),'gold daily OBV may pass BUY_CONFIRMED');

const reboundDead = base({obv:80,wma9:90,extra:{
  stage:'DOWN_CONFIRMED',events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED'}],
  live_change_pct:0.02,m30
}});
plan = sandbox.umPlan_M30(marketItem,reboundDead,old('DOWN_CONFIRMED'),now);
assert.equal(plan.events.length,0,'downtrend rebound must be suppressed while daily OBV is dead');
assert.ok(plan.result.reasons.includes('DAILY_OBV9_WMA_GATE_CLOSED'));

const reboundGold = base({obv:100,wma9:90,extra:{
  stage:'DOWN_CONFIRMED',events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED'}],
  live_change_pct:0.02,m30
}});
plan = sandbox.umPlan_M30(marketItem,reboundGold,old('DOWN_CONFIRMED'),now);
assert.ok(plan.events.some(e=>e.key==='DOWN_CONFIRMED'),'gold daily OBV may pass a confirmed rebound');

const genuineDown = base({obv:80,wma9:90,extra:{
  stage:'DOWN_CONFIRMED',events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED'}],
  live_change_pct:-0.02,m30:{...m30,vwap9_dir:'FALLING'}
}});
plan = sandbox.umPlan_M30(marketItem,genuineDown,old('DOWN_CONFIRMED'),now);
assert.ok(plan.events.some(e=>e.key==='DOWN_CONFIRMED'),'genuine downside alert must bypass bullish OBV gate');

const stopItem = {ticker:'005930',sources:['HELD'],stop_price:90};
const riskEvents=[{key:'STOP_BREACH',stage:'RISK'},{key:'ATR_DOWN_1_0',stage:'DOWN_EARLY'},
  {key:'SELL_PREP_WARNING',stage:'RISK'},{key:'OBV15_WARNING',stage:'RISK'}];
const stopResult = base({obv:80,wma9:90,extra:{stage:'RISK',events:riskEvents}});
plan = sandbox.umPlan_M30(stopItem,stopResult,old('RISK'),now);
assert.deepEqual(Array.from(plan.events,e=>e.key),riskEvents.map(e=>e.key),'risks bypass the gold gate');

const earlyState = (wasStacked=false) => old('WATCH',{
  vwap30StackValid:true,vwap30Stacked:wasStacked,
  vwap30Source:'KIWOOM_15M_BAR_START',vwap30Bar:'2026-11-27 09:30:00',
  vwap30Price:97,vwap30Vwap9:96,vwap30Vwap26:94,vwap30Watermark:'old',
  vwap30DailyGateInitialized:true,vwap30DailyQualified:true,
  vwap30ObvGateInitialized:true,vwap30ObvGateValid:true,
  vwap30ObvQualified:false,vwap30ObvAsOf:'2026-11-26 15:30:00'
});
const earlyDead = base({obv:80,wma9:90,extra:{m30}});
plan = sandbox.umPlan_M30(watchItem,earlyDead,earlyState(false),now);
assert.equal(plan.events.length,0,'M30 stack alone must not emit early buy observation');
assert.ok(plan.result.reasons.includes('DAILY_OBV9_WMA_GATE_CLOSED'));

const earlyGold = base({obv:100,wma9:90,extra:{m30}});
plan = sandbox.umPlan_M30(watchItem,earlyGold,earlyState(false),now);
assert.ok(plan.events.some(e=>e.key==='VWAP30_BULL_STACK_EARLY'),
  'new M30 stack emits only when daily VWAP9 and OBV WMA9 gates both pass');

const priorDay=new Date('2026-11-26T15:05:00+09:00');
const prior=sandbox.umPlan_M30(watchItem,base({extra:{
  technical:daily('2026-11-25 15:30:00'),
  daily_obv_wma9_gate:wire('2026-11-26','2026-11-25 15:30:00',80,90),
  m30:{...m30,completed_bar:'2026-11-26 15:00:00'},
}}),null,priorDay).state;
prior.sent.BUY_SETUP={messageId:'yesterday'};
assert.equal(prior.vwap30Stacked,true);
assert.equal(prior.vwap30ObvQualified,false);
const beforeFirstBar=new Date('2026-11-27T09:10:00+09:00');
const pre=sandbox.umPlan_M30(watchItem,base({extra:{m30:{...m30,completed_bar:'2026-11-26 15:00:00'}}}),prior,beforeFirstBar);
assert.equal(pre.events.length,0);
assert.equal(pre.state.vwap30Stacked,true,'retain prior session evidence until a current bar can complete');
assert.equal(pre.state.count,1);
assert.equal(pre.state.sent.BUY_SETUP,undefined);
const delayedGold=()=>base({extra:{m30}});
plan=sandbox.umPlan_M30(watchItem,delayedGold(),pre.state,now);
assert.deepEqual(Array.from(plan.events,e=>e.key),['VWAP30_BULL_STACK_EARLY']);
const committed=sandbox.umCommit_M30(plan,now,'receipt','event');
const later=new Date('2026-11-27T10:10:00+09:00');
assert.equal(sandbox.umPlan_M30(watchItem,delayedGold(),committed,later).events.length,0);
const stale=sandbox.umPlan_M30(watchItem,base({extra:{m30:{...m30,completed_bar:'2026-11-27 09:30:00'}}}),pre.state,now);
assert.equal(stale.state.vwap30StackValid,false);
assert.equal(sandbox.umPlan_M30(watchItem,delayedGold(),stale.state,later).events.length,0);
const broken=sandbox.umPlan_M30(watchItem,base({extra:{m30:{...m30,gold:false}}}),pre.state,now);
assert.equal(broken.state.vwap30Stacked,false);
assert.equal(broken.state.vwap30ObvTransitionAsOf,undefined);
assert.equal(sandbox.umPlan_M30(watchItem,delayedGold(),{...prior,day:'2026-11-25'},now).events.length,0);
const friday=sandbox.umPlan_M30(watchItem,earlyDead,null,now).state;
const monday=new Date('2026-11-30T10:05:00+09:00');
const afterWeekend=base({extra:{technical:daily('2026-11-27 15:30:00'),
  daily_obv_wma9_gate:wire('2026-11-30','2026-11-27 15:30:00'),
  m30:{...m30,completed_bar:'2026-11-30 10:00:00'}}});
assert.equal(sandbox.umPlan_M30(watchItem,afterWeekend,friday,monday).events[0].key,'VWAP30_BULL_STACK_EARLY');
const both=sandbox.umPlan_M30({...stopItem,average_price:100},base({extra:{m30,
  breakeven_observation:{as_of:now.toISOString(),price:100}}}),{...pre.state,stopNotified:true,
  breakeven:{average:100,belowAt:priorDay.getTime()}},now);
assert.deepEqual(Array.from(both.events,e=>e.key),['VWAP30_BULL_STACK_EARLY','BREAKEVEN_RECOVERED']);
assert.equal(sandbox.umDisplayStage_M30(both),'BUY_SETUP');

console.log('PASS: OBV WMA9 gate, buy stages, early M30 stack, delayed OBV turn, downtrend rebound, true downside and risk bypass.');
