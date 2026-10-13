const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const ctx={console,Date,Utilities:{formatDate:()=> '2026-10-13'}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('apps_script/unified_watchlists.gs','utf8'),ctx);
const now=new Date('2026-10-13T05:00:00Z'),next=new Date(now.getTime()+300000);
const risk=(key='STOP_BREACH')=>({stage:'RISK',send_allowed:true,live_price:100,
  events:[{key,stage:'RISK',level:1,reason:'risk'}]});
const buy=(stage='BUY_SETUP')=>({stage,send_allowed:true,live_price:100,
  events:[{key:stage,stage,level:1,reason:'up'}]});
const item=(sources,ticker)=>({ticker,sources,name:ticker,refs:[],stop_price:110});
for(const source of ['ETF','YOUTUBE']){
  for(const key of ['MARKET_CRASH','STOP_BREACH','SELL_PREP_WARNING','OBV15_WARNING']){
    const p=ctx.umPlan_(item([source],'069500'),risk(key),null,now);
    assert.equal(p.events.length,0,source+':'+key);
  }
  for(const stage of ['BUY_SETUP','BUY_CONFIRMED']){
    let p=ctx.umPlan_(item([source],'069500'),buy(stage),null,now);
    assert.equal(p.events.length,0);
    p=ctx.umPlan_(item([source],'069500'),buy(stage),p.state,next);
    assert.equal(p.events.length,1,source+':'+stage);
  }
}
for(const sources of [['HELD'],['HELD','YOUTUBE'],['ETF','HELD']]){
  for(const key of ['STOP_BREACH','SELL_PREP_WARNING','OBV15_WARNING']){
    const p=ctx.umPlan_(item(sources,'001430'),risk(key),null,now);
    assert.equal(p.events.length,1,sources+':'+key);
  }
}
const mix=ctx.umPlan_(item(['ETF'],'069500'),{stage:'RISK',live_price:90,send_allowed:true,
 events:[{key:'SELL_PREP_WARNING',stage:'RISK',level:1},{key:'BUY_SETUP',stage:'BUY_SETUP',level:1}]},null,now);
assert.equal(mix.events.length,0);
console.log('PASS: held both directions, interested ETF/YouTube BUY-only, indexes no drop mail, mixed lists HELD priority');
