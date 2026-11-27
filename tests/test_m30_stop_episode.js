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
  Utilities: {formatDate(d,zone,pattern) {
    const kst=new Date(d.getTime()+9*3600000).toISOString();
    if(pattern==='yyyy-MM-dd')return kst.slice(0,10);
    if(pattern==='HH:mm')return kst.slice(11,16);
    if(pattern==='MM-dd HH:mm')return kst.slice(5,16).replace('T',' ');
    return kst.slice(0,19).replace('T',' ');
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
assert.doesNotMatch(noAtr,/본전 회복까지/);
const withoutAvg = sandbox.umSignalBrief_M30({
  ...d2,item:{...item,average_price:null}
},new Date(2026,10,27,10,15,0));
assert.doesNotMatch(withoutAvg,/본전 회복까지/);

const at = new Date(2026,10,27,10,20,0);
const increased = {...item,quantity:155,average_price:195000};
const more = sandbox.umPlan_M30(increased,down,d2.state,at);
assert.match(sandbox.umSignalBrief_M30(more,at),/본전 회복까지 \+7\.4% \/ 0\.82 ATR/);
assert.equal(sandbox.umPlan_M30(increased,stop,more.state,at).events.length,0);
const up = {...down,stage:'BUY_SETUP',events:[{key:'BUY_SETUP',stage:'BUY_SETUP'}],
  jev_gate:{status:'OK',decision:'SEND'}};
const upAt=new Date(at.getTime()+300000);
const u1 = sandbox.umPlan_M30(item,up,more.state,upAt);
const u2 = sandbox.umPlan_M30(item,up,u1.state,new Date(at.getTime()+600000));
assert.ok(u2.events.some(e=>e.key==='BUY_SETUP'));
assert.match(sandbox.umSignalBrief_M30(u2,at),/본전 회복까지/);

const sameDayAcknowledged = sandbox.umCommit_M30(first,at,'old-message','old-event');
const reopened = {...item,holding_episode:'ep-'+'a'.repeat(32)};
assert.equal('stop_notified' in JSON.parse(sandbox.umRequest_M30(reopened,props,sameDayAcknowledged).payload),false);
const repurchase = sandbox.umPlan_M30(reopened,stop,sameDayAcknowledged,at);
assert.equal(repurchase.events.length,1);
assert.notEqual(sandbox.umEventId_M30(repurchase,at),sandbox.umEventId_M30(first,at));
const watch = {...item,sources:['YOUTUBE']};
assert.equal(sandbox.umPlan_M30(watch,stop,null,at).events.length,0);
assert.doesNotMatch(sandbox.umSignalBrief_M30({...d2,item:watch},at),/본전 회복까지/);

const recoveryItem={...item,average_price:100,stop_price:90};
const tick=(d,t)=>new Date(`2026-11-${d}T${t}:00+09:00`);
const view=(price,at,base={...stop,stage:'WATCH',events:[],send_allowed:false})=>({
  ...base,live_price:price,breakeven_observation:{price,as_of:at.toISOString()}
});
const recoveredEvent=p=>p.events.find(e=>e.key==='BREAKEVEN_RECOVERED');
const firstAt=tick(26,'14:50'),openAt=tick(27,'09:05');
const seed=sandbox.umPlan_M30(recoveryItem,view(85,firstAt,stop),null,firstAt);
assert.equal(recoveredEvent(seed),undefined,'a planned stop alone cannot alert');
assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(100,openAt),null,openAt)),undefined);
const seedState=sandbox.umCommit_M30(seed,firstAt,'first-stop','first-id');
const loaded=JSON.parse(JSON.stringify(seedState));
const atAverage=sandbox.umPlan_M30(recoveryItem,view(100,openAt),loaded,openAt);
assert.equal(recoveredEvent(atAverage).average,100);
assert.match(sandbox.umMessage_M30([atAverage],openAt).subject,/본전 회복/);
assert.match(sandbox.umSignalBrief_M30(atAverage,openAt),/평단 100원 · 확인가 100원/);
const sentRecovery=sandbox.umCommit_M30(atAverage,openAt,'recovery-receipt','recovery-id');
const later=tick(27,'09:10');
assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(110,later),sentRecovery,later)),undefined);
const nextTradingDay=new Date('2026-11-30T09:05:00+09:00');
assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(110,nextTradingDay),sentRecovery,nextTradingDay)),undefined);
assert.equal(sentRecovery.breakeven.receipt.messageId,'recovery-receipt');
const lowerAverage={...recoveryItem,average_price:80};
const averageReset=sandbox.umPlan_M30(lowerAverage,view(85,openAt),loaded,openAt);
assert.equal(recoveredEvent(averageReset),undefined,'changed average discards the old threshold crossing');
const belowNew=sandbox.umPlan_M30(lowerAverage,view(79,later),averageReset.state,later);
const later2=tick(27,'09:15');
assert.equal(recoveredEvent(sandbox.umPlan_M30(lowerAverage,view(80,later2),belowNew.state,later2)).average,80);
for(const invalid of [null,true,'',' ',0,-1,Infinity,NaN,'invalid','0x64']){
  assert.equal(recoveredEvent(sandbox.umPlan_M30({...recoveryItem,average_price:invalid},view(100,openAt),loaded,openAt)),undefined);
  assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(invalid,openAt),loaded,openAt)),undefined);
}
assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(100,tick(27,'09:01')),loaded,openAt)),undefined);
assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(100,later),loaded,openAt)),undefined);
assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(100,firstAt),loaded,openAt)),undefined);
assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(100,tick(27,'08:59')),loaded,tick(27,'09:01'))),undefined);
const repriced={...recoveryItem,holding_episode:'ep-'+'c'.repeat(32)};
const newSeed=sandbox.umPlan_M30(repriced,view(85,later,stop),sentRecovery,later);
assert.ok(newSeed.events.some(e=>e.key==='STOP_BREACH'));
const newHolding=sandbox.umCommit_M30(newSeed,later,'new-stop','new-stop-event');
assert.ok(recoveredEvent(sandbox.umPlan_M30(repriced,view(100,later2),newHolding,later2)));

// In-memory stand-ins exercise the real draft journal, commit, and state reload.
let pendingRows=[], stateRows=[], sendAttempts=0;
const properties = new Map();
const testProps = {
  getProperty:k=>properties.get(k),
  setProperty:(k,v)=>properties.set(k,v),
  deleteProperty:k=>properties.delete(k),
};
const control = {getRange:(row)=>row==='E1'?{setValue:()=>{}}:{
  setValues:v=>{pendingRows=v;}, getValues:()=>pendingRows,
  clearContent:()=>{pendingRows=[];},
}};
const sheet = {getLastRow:()=>stateRows.length+1,getRange:()=>({
  setValues:v=>{stateRows=v;}, getValues:()=>stateRows,
})};
let logWriteCrash=false, receipt=null;
const log = {getLastRow:()=>1,getRange:()=>({setValues:()=>{
  if(logWriteCrash)throw Error('audit write failed');
}})};
const book = {getSheetByName:name=>name==='UNIFIED_CONTROL'?control:name==='ALERT_LOG'?log:sheet};
sandbox.SpreadsheetApp={openById:()=>book};
sandbox.hmAlertRecipient_=()=> 'test@example.invalid';
sandbox.umMessage_M30=()=>({subject:'test',body:'test',htmlBody:'test'});
const draft = {getId:()=> 'draft',send:()=>{
  if(++sendAttempts===1)throw Error('mail provider failed');
  receipt={getId:()=> 'gmail-receipt',getPlainBody:()=>JSON.parse(testProps.getProperty('UM2_PENDING')).ids[0]};
  return receipt;
}};
sandbox.GmailApp={createDraft:()=>draft,getDraft:()=>draft,search:()=>receipt?[{getMessages:()=>[receipt]}]:[]};
// Use the real property and sheet names while keeping all services in memory.
book.getSheetByName=name=>name===vm.runInContext('UM30.CONTROL',sandbox)?control:name==='ALERT_LOG'?log:sheet;
assert.throws(()=>sandbox.umDispatch_M30([repurchase],testProps,at,null,null),/mail provider failed/);
assert.equal(stateRows.length,0);
assert.ok(testProps.getProperty('UM2_PENDING'));
const expired=JSON.parse(testProps.getProperty('UM2_PENDING'));
expired.created=Date.now()-120001;
testProps.setProperty('UM2_PENDING',JSON.stringify(expired));
logWriteCrash=true;
assert.throws(()=>sandbox.umResumeDelivery_M30(testProps),/audit write failed/);
const persisted=sandbox.umLoadState_M30(testProps,item.ticker,sandbox.umOpenStateStore_M30(book,testProps,[]));
assert.equal(persisted.sent.STOP_BREACH.messageId,'gmail-receipt');
assert.equal(sandbox.umPlan_M30(reopened,stop,persisted,at).events.length,0);
logWriteCrash=false;
sandbox.umResumeDelivery_M30(testProps);
assert.equal(sendAttempts,2,'recover successful send through Sent without resending');
assert.equal(testProps.getProperty('UM2_PENDING'),undefined);
const inactiveStore=sandbox.umOpenStateStore_M30(book,testProps,[{ticker:'005930'}]);
inactiveStore.dirty=true;
sandbox.umFlushStateStore_M30(inactiveStore,at);
assert.equal(stateRows.length,1,'keep successful stop state even when temporarily inactive');
const damaged='{"day":"2026-11-27","sent":{"STOP_BREACH":{"messageId":"verified"}}';
stateRows.push(['005930','2026-11-27',at.toISOString(),damaged]);
const damagedStore=sandbox.umOpenStateStore_M30(book,testProps,[{ticker:item.ticker}]);
const quarantined=sandbox.umLoadState_M30(testProps,'005930',damagedStore);
assert.equal(quarantined.quarantined,true);
sandbox.umPutState_M30(damagedStore,'005930',quarantined,at);
damagedStore.dirty=true;
sandbox.umFlushStateStore_M30(damagedStore,at);
assert.equal(stateRows.find(row=>row[0]==='005930')[3],damaged);

stateRows=[];receipt=null;sendAttempts=0;
const recoveryRetry=sandbox.umPlan_M30(recoveryItem,view(101,openAt),loaded,openAt);
assert.throws(()=>sandbox.umDispatch_M30([recoveryRetry],testProps,openAt,null,null),/mail provider failed/);
assert.equal(stateRows.length,0,'failed send has no recovery receipt');
const recoveryDraft=JSON.parse(testProps.getProperty('UM2_PENDING'));
recoveryDraft.created=Date.now()-120001;
testProps.setProperty('UM2_PENDING',JSON.stringify(recoveryDraft));
logWriteCrash=true;
assert.throws(()=>sandbox.umResumeDelivery_M30(testProps),/audit write failed/);
const recoveredState=sandbox.umLoadState_M30(testProps,item.ticker,sandbox.umOpenStateStore_M30(book,testProps,[]));
assert.equal(recoveredState.breakeven.receipt.messageId,'gmail-receipt');
assert.equal(recoveredEvent(sandbox.umPlan_M30(recoveryItem,view(105,later),recoveredState,later)),undefined);
logWriteCrash=false;
sandbox.umResumeDelivery_M30(testProps);
assert.equal(sendAttempts,2,'the successful recovery receipt prevents duplicate retry');

console.log('PASS: first stop, trading-day rollover, recross, up/down, new average, repurchase, watchlist, failed mail and durable recovery.');
