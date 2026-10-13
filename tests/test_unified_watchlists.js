const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('apps_script/unified_watchlists.gs','utf8');
const ctx={console,Date,Utilities:{formatDate:(d,tz,f)=>f==='yyyy-MM-dd'?'2026-10-13':'12:00'}};
vm.createContext(ctx);vm.runInContext(source,ctx);
const now=new Date('2026-10-13T03:00:00Z');
const item={ticker:'490590',sources:['ETF','HELD','YOUTUBE'],name:'중복 ETF',refs:[],stop_price:90};
const buy={stage:'BUY_SETUP',send_allowed:true,live_price:100,events:[{key:'BUY_SETUP',stage:'BUY_SETUP',level:1,reason:'verified'}]};
const merged=ctx.umMerge_([{ticker:'490590',source:'ETF',row:2,name:'ETF',market:'UNKNOWN',is_etf:true},
 {ticker:'490590',source:'HELD',row:4,quantity:3,stop_price:90},{ticker:'490590',source:'YOUTUBE',row:5,market:'KOSPI'}]);
assert.equal(merged.length,1);assert.equal(merged[0].sources.length,3);assert.equal(merged[0].stop_price,90);
let p=ctx.umPlan_(item,buy,null,now);assert.equal(p.events.length,0);
p=ctx.umPlan_(item,buy,p.state,now);assert.equal(p.state.count,1);assert.equal(p.events.length,0);
p=ctx.umPlan_(item,buy,p.state,new Date(now.getTime()+300000));assert.equal(p.events.length,1);
// Failed email does not advance sent keys: same event can be retried.
assert.equal(Object.keys(p.state.sent).length,0);
assert.equal(ctx.umPlan_(item,buy,p.state,new Date(now.getTime()+600000)).events.length,1);
ctx.umCommit_(p,now,'message','event');
assert.equal(ctx.umPlan_(item,buy,p.state,new Date(now.getTime()+600000)).events.length,0);
const risk=level=>({stage:'RISK',send_allowed:true,live_price:80,events:[{key:'MARKET_CRASH',stage:'RISK',level,reason:'crash'}]});
p=ctx.umPlan_(item,risk(3),null,now);assert.equal(p.events.length,1);ctx.umCommit_(p,now,'id','e');
let stronger=ctx.umPlan_(item,risk(7),p.state,new Date(now.getTime()+300000));assert.equal(stronger.events.length,1);ctx.umCommit_(stronger,now,'id','e');
assert.equal(ctx.umPlan_(item,risk(5),stronger.state,new Date(now.getTime()+600000)).events.length,0);
assert.equal(ctx.umPlan_(item,{...buy,send_allowed:false},null,now).events.length,0);
const stop={stage:'RISK',send_allowed:true,live_price:80,events:[{key:'STOP_BREACH',stage:'RISK',level:1,reason:'stop'}]};
p=ctx.umPlan_(item,stop,null,now);assert.equal(p.events.length,1);ctx.umCommit_(p,now,'id','e');
let recovered=ctx.umPlan_(item,{...buy,send_allowed:false},p.state,new Date(now.getTime()+300000));
assert.equal(ctx.umPlan_(item,stop,recovered.state,new Date(now.getTime()+600000)).events.length,0);
assert.equal(ctx.umPlan_(item,stop,recovered.state,new Date(now.getTime()+1800000)).events.length,1);
// The exact same event ID is carried to the existing D0/D3/D5 parser.
const msg=ctx.umMessage_([stronger],now),review={console,Logger:{log:()=>{}},Utilities:ctx.Utilities};
vm.createContext(review);vm.runInContext(fs.readFileSync('apps_script/signal_review_unified.gs','utf8'),review);
const events=review.srExtractSignals_(msg.subject,msg.body,{});
assert.equal(events.length,1);assert.equal(events[0].source,'ETF+HELD+YOUTUBE');
assert.equal(events[0].code,'490590');assert.equal(events[0].price,80);assert(events[0].eventId.startsWith('U2_'));
assert.equal(review.srExtractSignals_('[주식 통합신호]','UNIFIED_EVENTS_JSON=broken',{}).length,0);
ctx.SpreadsheetApp={openById:()=>{throw new Error('ORIGINAL_SHEET_MUST_NOT_BE_TOUCHED')}};
assert.equal(ctx.umDisplay_(item,buy).recordedBy,'umRecord_');
console.log('PASS union, distinct-slot confirmation, failure retry, cross-list dedup, crash escalation, stop recross, exact replay identity, original formulas preserved');
