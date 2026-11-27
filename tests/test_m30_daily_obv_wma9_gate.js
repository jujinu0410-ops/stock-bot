'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '../apps_script/paper_game/UnifiedWatchlists30.js'), 'utf8');
const sandbox = {
  Date, Math, Number, JSON, Set, String, Array, Object, console,
  Utilities: {formatDate() { return '2026-11-27'; }},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename:'UnifiedWatchlists30.js'});

sandbox.umDirectionContext_M30 = (result) => ({
  relation:'DOWN_DOWN',
  daily_direction:result.daily_bearish===true?'DOWN':'UNCONFIRMED',
  daily_regime:result.daily_bearish===true?'BEAR_CONFIRMED':'',
  daily_basis:result.technical&&result.technical.basis==='COMPLETED_DAILY'?'COMPLETED_DAILY':'UNVERIFIED',
  daily_as_of:(result.technical&&result.technical.as_of)||'',
  m30_direction:result.m30_direction||'DOWN',
  caption:'test',
  completed_bar:''
});
sandbox.umLiveDirection_M30 = (result) => ({
  official:!!(result.technical&&result.technical.basis==='COMPLETED_DAILY'),
  daily_bearish:result.daily_bearish===true,
  m30_direction:result.m30_direction||'DOWN',
  live_direction:result.live_change_pct>0?'UP':result.live_change_pct<0?'DOWN':'FLAT'
});
sandbox.umDirectionNote_M30 = () => '';

const now = new Date('2026-11-27T10:05:00+09:00');
const slot = Math.floor(now.getTime()/300000);
const daily = (obv=100,wma9=90) => ({
  basis:'COMPLETED_DAILY',as_of:'2026-11-26 15:30:00',
  vwap9:95,obv,obv_wma9:wma9
});
const base = (options={}) => ({
  ok:true,session:'KRX_SESSION',live_price_source:'KIWOOM_KA10001',
  live_price:100,live_change_pct:0,send_allowed:true,
  stage:'WATCH',events:[],technical:daily(options.obv,options.wma9),
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
  live_change_pct:0.02,daily_bearish:true,m30_direction:'UP'
}});
plan = sandbox.umPlan_M30(marketItem,reboundDead,old('DOWN_CONFIRMED'),now);
assert.equal(plan.events.length,0,'downtrend rebound must be suppressed while daily OBV is dead');
assert.ok(plan.result.reasons.includes('DAILY_OBV9_WMA_GATE_CLOSED'));

const reboundGold = base({obv:100,wma9:90,extra:{
  stage:'DOWN_CONFIRMED',events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED'}],
  live_change_pct:0.02,daily_bearish:true,m30_direction:'UP'
}});
plan = sandbox.umPlan_M30(marketItem,reboundGold,old('DOWN_CONFIRMED'),now);
assert.ok(plan.events.some(e=>e.key==='DOWN_CONFIRMED'),'gold daily OBV may pass a confirmed rebound');

const genuineDown = base({obv:80,wma9:90,extra:{
  stage:'DOWN_CONFIRMED',events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED'}],
  live_change_pct:-0.02,daily_bearish:true,m30_direction:'DOWN'
}});
plan = sandbox.umPlan_M30(marketItem,genuineDown,old('DOWN_CONFIRMED'),now);
assert.ok(plan.events.some(e=>e.key==='DOWN_CONFIRMED'),'genuine downside alert must bypass bullish OBV gate');

const stopItem = {ticker:'005930',sources:['HELD'],stop_price:90};
const stopResult = base({obv:80,wma9:90,extra:{
  stage:'RISK',events:[{key:'STOP_BREACH',stage:'RISK'}]
}});
plan = sandbox.umPlan_M30(stopItem,stopResult,old('RISK'),now);
assert.ok(plan.events.some(e=>e.key==='STOP_BREACH'),'risk alerts must bypass bullish OBV gate');

const m30 = {
  available:true,completed_bar:'2026-11-27 10:00:00',
  source:'KIWOOM_15M_BAR_START',watermark:'new',
  vwap9:98,vwap26:95,gold:true,vwap9_slope:0.01,vwap9_dir:'RISING'
};
const earlyState = (wasStacked=false,prevAsOf='2026-11-26 15:30:00') => old('WATCH',{
  vwap30StackValid:true,vwap30Stacked:wasStacked,
  vwap30Source:'KIWOOM_15M_BAR_START',vwap30Bar:'2026-11-27 09:30:00',
  vwap30Price:97,vwap30Vwap9:96,vwap30Vwap26:94,vwap30Watermark:'old',
  vwap30DailyGateInitialized:true,vwap30DailyQualified:true,
  vwap30ObvGateInitialized:true,vwap30ObvGateValid:true,
  vwap30ObvQualified:false,vwap30ObvAsOf:prevAsOf
});
const earlyDead = base({obv:80,wma9:90,extra:{m30}});
plan = sandbox.umPlan_M30(watchItem,earlyDead,earlyState(false),now);
assert.equal(plan.events.length,0,'M30 stack alone must not emit early buy observation');
assert.ok(plan.result.reasons.includes('DAILY_OBV9_WMA_GATE_CLOSED'));

const earlyGold = base({obv:100,wma9:90,extra:{m30}});
plan = sandbox.umPlan_M30(watchItem,earlyGold,earlyState(false),now);
assert.ok(plan.events.some(e=>e.key==='VWAP30_BULL_STACK_EARLY'),
  'new M30 stack emits only when daily VWAP9 and OBV WMA9 gates both pass');

const delayedGold = base({obv:100,wma9:90,extra:{m30}});
plan = sandbox.umPlan_M30(watchItem,delayedGold,earlyState(true,'2026-11-25 15:30:00'),now);
assert.ok(plan.events.some(e=>e.key==='VWAP30_BULL_STACK_EARLY'),
  'OBV turning gold later must release a still-valid M30 stack once');

console.log('PASS: OBV WMA9 gate, buy stages, early M30 stack, delayed OBV turn, downtrend rebound, true downside and risk bypass.');
