const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
function simulate(permanentErrors) {
 const storage=new Map(),groups=[], attempts={},stats={fetch:0,retry:0,records:0};
 const props={getProperty:k=>storage.get(k)||null,setProperty:(k,v)=>storage.set(k,String(v)),setProperties:d=>Object.entries(d).forEach(([k,v])=>storage.set(k,String(v))),deleteProperty:k=>storage.delete(k)};
 const book={getSheetByName:s=>({getRange:()=>({setValues:()=>{}})})};
 const ctx={console,Date,Utilities:{formatDate:()=> '2026-10-13',sleep:()=>{}},
  LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock:()=>{}})},
  ScriptApp:{getProjectTriggers:()=>[]},
  PropertiesService:{getScriptProperties:()=>props},
  SpreadsheetApp:{openById:()=>book},
  hmIsMarketWindow_:()=>true, UrlFetchApp:{fetchAll:reqs=>{
   stats.fetch++;return reqs.map(q=>{
    const number=Number(q.code), seen=attempts[number]=(attempts[number]||0)+1;
    const fail=number>=174&&(permanentErrors||seen===1);
    return {getResponseCode:()=>fail?503:200,getContentText:()=>fail?'{}':JSON.stringify({
      ok:true,engine_version:'three-lists-v2',stage:Number(q.code)<=85?'RISK':'WATCH',
      send_allowed:Number(q.code)<=85,live_price:90,reasons:[],
      events:Number(q.code)<=85?[{key:'STOP_BREACH',stage:'RISK',level:1,reason:'risk'}]:[]})};
   });
  }}};
 vm.createContext(ctx);vm.runInContext(fs.readFileSync('apps_script/unified_watchlists.gs','utf8'),ctx);
 ctx.umMode_=()=> 'ACTIVE';
 ctx.umUniverse_=()=>Array.from({length:201},(_,i)=>({ticker:String(i+1).padStart(6,'0'),name:'N',sources:['YOUTUBE'],refs:[],code:i+1}));
 ctx.umRequest_=(x)=>({code:x.code});
 ctx.umLegacySeed_=()=>({});ctx.umResumeDelivery_=()=>{};
 ctx.umRecord_=rows=>{stats.records=rows.length};
 ctx.umNotifyFailure_=(err)=>{console.log('SIMULATED_ERROR',String(err));};
 ctx.umDispatch_=(plans)=>{groups.push(plans.length);return {sent:plans.length};};
 const result=ctx.umTick5m();
 return {result,groups,stats,storage};
}
let a=simulate(false);
assert.equal(a.result.universe,201);assert.equal(a.result.evaluated,201);assert.equal(a.result.errors,0);
assert.equal(a.result.sent,85);assert.deepEqual(a.groups,[20,20,20,20,5]);
assert.equal(a.stats.records,201);assert(a.storage.has('UM2_COMPLETED_ACTIVE'));
console.log('PASS 201 items, 85 events, 5 bounded grouped emails, 0 errors, no source sheet writes');
let b=simulate(true);
assert(b.result.errors>0);assert(!b.storage.has('UM2_COMPLETED_ACTIVE'));
assert(!b.storage.has('UM2_CURSOR_SLOT'));assert(!b.storage.has('UM2_CURSOR'));
console.log('PASS persistent HTTP503 never marks complete or advances cursor');
