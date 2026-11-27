/* Run: node tests/test_m30_bollinger_assist.js */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '../apps_script/paper_game/UnifiedWatchlists30.js'), 'utf8');
const sandbox = {
  Utilities:{formatDate:()=> '2026-11-27'},
  console,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
sandbox.umDailyVwap9Gate_M30=()=>({qualified:true,valid:true});
sandbox.umSignalNews_M30=()=>[];
sandbox.umDirectionContext_M30=()=>({});
sandbox.umDirectionNote_M30=()=>'';
sandbox.umLiveDirection_M30=()=>({ctx:{},official:false,daily_date:''});

const item={ticker:'005930',name:'삼성전자',sources:['HELD']};
const now=new Date('2026-11-27T05:10:00Z');
const state={day:'2026-11-27',slot:Math.floor(now.getTime()/300000)-1,
  count:1,stage:'DOWN_EARLY',sent:{}};
const result={ok:true,stage:'DOWN_EARLY',live_price:100000,send_allowed:true,events:[
  {key:'DOWN_EARLY',stage:'DOWN_EARLY'}]};
const bands={stage:'TRAILING_WATCH',completed_bar:'2026-11-27T14:00',percent_b:53.6,
  bandwidth_pct:2.42,compression_ceiling_pct:2.15,volume_ratio:1.3};

const original=sandbox.umPlan_M30(item,{...result},state,now);
const advisory=sandbox.umPlan_M30(item,{...result,bollinger_assist:bands},state,now);
assert.equal(advisory.events.length,original.events.length);
assert.equal(sandbox.umEventId_M30(advisory,now),sandbox.umEventId_M30(original,now));
const before=sandbox.umMessage_M30([original],now);
const after=sandbox.umMessage_M30([advisory],now);
assert.equal(before.subject,after.subject);
assert.match(after.subject,/▽/);
assert.match(after.body,/트레일링 매수 관찰.*Band %b 53\.6.*Bandwidth 2\.42%.*1\.30배/);
assert.match(after.htmlBody,/보조신호.*트레일링 매수 관찰/);
assert.equal(after.body.split('UNIFIED_EVENTS_JSON=')[1],before.body.split('UNIFIED_EVENTS_JSON=')[1]);
assert.equal(sandbox.umPlan_M30(item,{...result,events:[],bollinger_assist:bands},state,now).events.length,0);
assert.match(sandbox.umBollingerDetail_M30({bollinger_assist:{...bands,stage:'COMPRESSION'}}),/변동성 압축/);
assert.match(sandbox.umBollingerDetail_M30({bollinger_assist:{...bands,stage:'BUY_PREPARATION'}}),/매수 준비/);

// Planning alone cannot mark the advisory sent. A successful receipt can.
const retry=sandbox.umPlan_M30(item,{...result,bollinger_assist:bands},state,now);
assert.equal(retry.events[0].bollingerAssist,true);
const sent=sandbox.umCommit_M30(advisory,now,'receipt','event');
const next=new Date(now.getTime()+300000);
const another=sandbox.umPlan_M30(item,{...result,stage:'DOWN_CONFIRMED',
  events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED'}],bollinger_assist:bands},
  {...sent,stage:'DOWN_CONFIRMED'},next);
assert.equal(another.events.length,1);
assert.equal(another.events[0].bollingerAssist,undefined);

const props=enabled=>({getProperty:key=>({
  SIGNAL_API_URL:'https://example.invalid',SIGNAL_API_TOKEN:'test',
  E30_BOLLINGER_ASSIST_ENABLED:enabled,
})[key]});
assert.equal(JSON.parse(sandbox.umRequest_M30(item,props('false')).payload).bollinger_assist,undefined);
assert.equal(JSON.parse(sandbox.umRequest_M30(item,props('true')).payload).bollinger_assist,true);
console.log('PASS: opt-in, audit labels, original mail and event IDs, no extra mail, receipt deduplication.');
