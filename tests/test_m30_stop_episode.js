/* Run: node tests/test_m30_stop_episode.js
 * Offline tests of the real Apps Script dispatcher, with only Google services
 * stubbed. Never sends mail or touches Sheets/production state.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '../apps_script/paper_game/UnifiedWatchlists30.js'), 'utf8');
const sandbox = {
  Date, Math, Number, JSON, Set, String, Array, Object, console,
  Utilities: {formatDate(d) {
    return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+
      '-'+String(d.getDate()).padStart(2,'0');
  }},
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, {filename:'UnifiedWatchlists30.js'});
sandbox.umDirectionContext_M30 = () => ({
  relation:'DOWN_DOWN',daily_direction:'DOWN',m30_direction:'DOWN',caption:'하락 추세',completed_bar:''
});
sandbox.umDailyVwap9Gate_M30 = () => ({qualified:true,valid:true});
sandbox.umLiveDirection_M30 = () => ({
  ctx:{},official:false,daily_date:'',daily_bearish:false,live_direction:'DOWN'
});
sandbox.umDirectionNote_M30 = () => '';

const item = {
  ticker:'000250',name:'삼천당제약',sources:['HELD'],stop_price:189900,
  average_price:191287,atr14:16341
};
const props = {getProperty(k) {
  return k === 'SIGNAL_API_URL' ? 'https://example.invalid' : 'test-token';
}};
const yesterday = {
  day:'2026-11-26',sent:{STOP_BREACH:{at:'2026-11-26T00:02:00.000Z'}},
  lastStopAt:'2026-11-26T00:02:00.000Z',stopBreachSeq:1
};
assert.equal(sandbox.umStopNotified_M30(yesterday),true);
assert.equal(JSON.parse(sandbox.umRequest_M30(item,props,yesterday).payload).stop_notified,true);
assert.equal('stop_notified' in JSON.parse(sandbox.umRequest_M30(item,props,null).payload),false);
assert.equal('stop_notified' in JSON.parse(sandbox.umRequest_M30(
  {...item,sources:['YOUTUBE']},props,yesterday).payload),false);

const stop = {
  ok:true,session:'KRX_SESSION',stage:'RISK',live_price:181600,send_allowed:true,
  events:[{key:'STOP_BREACH',stage:'RISK',reason:'손절선 침범',level:1}],
  technical:{},m30:{},reasons:[]
};
const first = sandbox.umPlan_M30(item,stop,null,new Date(2026,10,27,9,0,0));
assert.equal(first.events.length,1,'first stop must still warn once');
const plan = sandbox.umPlan_M30(item,stop,yesterday,new Date(2026,10,27,10,0,0));
assert.equal(plan.state.stopNotified,true,'carry marker across dates');
assert.equal(plan.events.length,0,'never repeat first stop warning on new day');
const recovered = {...plan.state,stopRecovered:true};
const again = sandbox.umPlan_M30(item,stop,recovered,new Date(2026,10,27,10,5,0));
assert.equal(again.events.length,0,'do not treat another fall as a new stop event');

const down = {
  ok:true,session:'KRX_SESSION',stage:'DOWN_EARLY',live_price:181600,
  atr14:16341,send_allowed:true,
  events:[{key:'DOWN_EARLY',stage:'DOWN_EARLY',reason:'일봉 하락',level:1}],
  technical:{},m30:{},reasons:[]
};
const d1 = sandbox.umPlan_M30(item,down,again.state,new Date(2026,10,27,10,10,0));
const d2 = sandbox.umPlan_M30(item,down,d1.state,new Date(2026,10,27,10,15,0));
assert.equal(d2.events.some(e => e.key === 'DOWN_EARLY'),true);
const body = sandbox.umSignalBrief_M30(d2,new Date(2026,10,27,10,15,0));
assert.match(body,/본전 회복까지 \+5\.3%/);
assert.match(body,/0\.59 ATR/);
assert.doesNotMatch(body,/손절선 189,900원 도달/);

const noAtr = sandbox.umSignalBrief_M30({
  ...d2, result:{...down,atr14:null}
},new Date(2026,10,27,10,15,0));
assert.match(noAtr,/ATR 자료 없음/);
const withoutAvg = sandbox.umSignalBrief_M30({
  ...d2,item:{...item,average_price:null}
},new Date(2026,10,27,10,15,0));
assert.doesNotMatch(withoutAvg,/본전 회복까지/);

console.log('PASS: 12 independent assertions, first stop, next-day idempotence, recovery, directional alerts, breakeven percent/ATR, fallback.');
