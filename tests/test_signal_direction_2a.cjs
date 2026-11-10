const fs=require('fs'),path=require('path'),vm=require('vm'),assert=require('assert');
const B=__dirname;
const stamp=d=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(d);
const Utilities={formatDate(d,z,p){const t=stamp(d);return p==='yyyy-MM-dd'?t.slice(0,10):p==='MM-dd HH:mm'?t.slice(5,16):t;}};
function context(folder){const local=path.join(B,folder,'UnifiedWatchlists30.js');const file=fs.existsSync(local)?local:folder==='apps'?path.join(B,'../apps_script/paper_game/UnifiedWatchlists30.js'):path.join(B,'fixtures/signal_direction_2a/UnifiedWatchlists30.before.js');const c=vm.createContext({Utilities,console,Date,Math,JSON,Number,String,Set,Object,Array,isFinite});vm.runInContext(fs.readFileSync(file,'utf8'),c);return c;}
// Compare against the exact production Apps Script clone captured before this gate change.
const updated=context('apps'),before=context('apps-before'),now=new Date('2026-11-06T13:35:00+09:00');
const clone=x=>JSON.parse(JSON.stringify(x));
function result(regime='BEAR_CONFIRMED',dir='RISING',extra={}) {return Object.assign({ok:true,engine_version:'three-lists-v3-m30',stage:'BUY_SETUP',live_price:110,live_change_pct:-.03,atr14:5,send_allowed:false,events:[],reasons:[],session:'KRX_SESSION',technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-05 15:30:00',vwap9:100,prev_high:105,regime,stage:'BUY_SETUP',provisional:{available:true,basis:'INCOMPLETE_DAILY_NOT_CONFIRMED',regime:'BULL_CONFIRMED'}},live_price_source:'KIWOOM_KA10001',m30:{available:true,gold:true,vwap9:105,vwap26:100,vwap9_dir:dir,completed_bar:'2026-11-06 13:30:00',source:'KIWOOM_15M_BAR_START',watermark:'2026-11-06 13:35:00'}},extra);}
function call(name,...args){return clone(updated[name](...args));}
function plan(r,events=[]){return {item:{ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:90},result:r,events};}
let tests=0,failures=0,trials=0;const outcomes=[];
function test(name,fn){tests++;try{fn();outcomes.push({name,status:'PASS'});}catch(e){failures++;outcomes.push({name,status:'FAIL',error:String(e.stack)});console.error(name,e);}}
test('1 official up + short up',()=>assert.equal(call('umDirectionContext_M30',result('BULL_CONFIRMED'),now).relation,'UP_UP'));
test('2 official up + short down',()=>assert.match(updated.umSignalBrief_M30(plan(result('BULL_CONFIRMED','FALLING',{stage:'DOWN_EARLY'})),now),/일봉 상승 추세 속 30분봉 단기 약화/));
test('3 official down + short up',()=>assert.match(updated.umSignalBrief_M30(plan(result()),now),/일봉 약세 속 30분봉 반등 포착/));
test('4 official down + short down',()=>assert.equal(call('umDirectionContext_M30',result('BEAR_CONFIRMED','FALLING'),now).relation,'DOWN_DOWN'));
test('5 neutral and missing upper evidence',()=>{for(const regime of ['NEUTRAL','EARLY_IMPROVEMENT','EARLY_WEAKENING',''])assert.equal(call('umDirectionContext_M30',result(regime),now).daily_direction,'UNCONFIRMED');const r=result();delete r.technical;assert.match(updated.umSignalBrief_M30(plan(r),now),/상위 추세 확인 필요/);});
test('6 opposite provisional never changes official',()=>{const r=result('BEAR_CONFIRMED');r.technical.provisional.regime='BULL_CONFIRMED';assert.equal(call('umDirectionContext_M30',r,now).relation,'DOWN_UP');});
test('7 stop breach + short rebound keeps risk first',()=>{const p=plan(result('BEAR_CONFIRMED','RISING',{stage:'RISK',live_price:85}),[{key:'STOP_BREACH',stage:'RISK'}]);const s=updated.umSignalBrief_M30(p,now);assert.match(s,/^실시간 손절선/);assert.match(s,/반등과 별개로 위험경고 유지/);assert(!s.includes('반등 포착'));});
test('8 rising quote does not remove bearish warning',()=>{const r=result('BULL_CONFIRMED','FALLING',{stage:'DOWN_CONFIRMED',live_change_pct:.05});assert.equal(call('umDirectionContext_M30',r,now).relation,'UP_DOWN');assert.match(updated.umSignalBrief_M30(plan(r),now),/하락 확인/);});
test('9 mail footer and IDs retain machine meaning',()=>{const p=plan(result(),[{key:'VWAP30_BULL_STACK_EARLY',stage:'BUY_SETUP',stateKey:'VWAP30_BULL_STACK_EARLY'}]);const a=before.umMessage_M30([clone(p)],now),b=updated.umMessage_M30([clone(p)],now);assert.equal(a.subject,b.subject);assert.equal(a.body.split('UNIFIED_EVENTS_JSON=')[1],b.body.split('UNIFIED_EVENTS_JSON=')[1]);assert.match(b.htmlBody,/일봉 약세 속 30분봉 반등 포착/);});
test('10 no 45m fallback',()=>{for(const bar of ['2026-11-06 09:45:00','2026-11-06 15:30:00']){const r=result();r.m30.completed_bar=bar;assert.equal(call('umDirectionContext_M30',r,now).m30_direction,'UNKNOWN');}const r=result();r.m45=r.m30;delete r.m30;assert.equal(call('umDirectionContext_M30',r,now).m30_direction,'UNKNOWN');});
test('quote sign cannot replace formal regime',()=>{const a=result(),b=result();b.live_change_pct=.08;assert.deepEqual(call('umDirectionContext_M30',a,now),call('umDirectionContext_M30',b,now));});
test('missing 5m data adds no filter',()=>{const a=result(),b=result();b.m5={available:false,direction:'FALLING'};assert.deepEqual(call('umDirectionContext_M30',a,now),call('umDirectionContext_M30',b,now));});
test('aligned and neutral cases stay concise',()=>{const p=plan(result('BULL_CONFIRMED'));assert.equal(updated.umDirectionNote_M30(p,now),'');assert.equal(call('umDirectionContext_M30',result('BULL_CONFIRMED','FLAT'),now).relation,'SHORT_NEUTRAL');});
test('gold stack is not a rising slope',()=>{const p=plan(result('BULL_CONFIRMED','FALLING'),[{key:'VWAP30_BULL_STACK_EARLY',stage:'BUY_SETUP'}]);assert.match(updated.umSignalBrief_M30(p,now),/정배열은 위치 관계/);assert.equal(call('umDirectionContext_M30',p.result,now).m30_alignment,'GOLD');});
test('unknown upper and falling short direction',()=>assert.equal(call('umDirectionContext_M30',result('NEUTRAL','FALLING'),now).relation,'UNCONFIRMED_DOWN'));
test('invalid source completion never becomes a direction',()=>{for(const modify of [r=>r.m30.available=false,r=>r.m30.source='',r=>r.m30.completed_bar='2026-11-06 13:00:00',r=>r.m30.completed_bar='2026-11-06 14:00:00',r=>r.m30.completed_bar='2026-11-05 13:30:00',r=>r.m30.completed_bar='2026-11-06 13:30:01']){const r=result();modify(r);assert.equal(call('umDirectionContext_M30',r,now).m30_direction,'UNKNOWN');}});
test('daily current/future/incomplete evidence is unconfirmed',()=>{for(const modify of [r=>r.technical.basis='INCOMPLETE_DAILY_NOT_CONFIRMED',r=>r.technical.as_of='2026-11-06 15:30:00',r=>r.technical.as_of='',r=>r.technical.as_of='2026-11-09 15:30:00']){const r=result('BULL_CONFIRMED');modify(r);assert.equal(call('umDirectionContext_M30',r,now).daily_direction,'UNCONFIRMED');}});
test('15:35 respects final 15:00 bar',()=>{const at=new Date('2026-11-06T15:35:00+09:00'),r=result();r.m30.completed_bar='2026-11-06 15:00:00';assert.equal(call('umDirectionContext_M30',r,at).m30_direction,'UP');});
test('Monday accepts completed Friday formal daily',()=>{const at=new Date('2026-11-09T13:35:00+09:00'),r=result();r.technical.as_of='2026-11-06 15:30:00';r.m30.completed_bar='2026-11-09 13:30:00';assert.equal(call('umDirectionContext_M30',r,at).relation,'DOWN_UP');});
test('context helper never mutates source fields',()=>{const r=result(),saved=JSON.stringify(r);updated.umDirectionContext_M30(r,now);updated.umDirectionNote_M30(plan(r),now);assert.equal(JSON.stringify(r),saved);});
test('missing daily provenance cannot be described as completed daily',()=>{for(const stage of ['BUY_SETUP','BUY_CONFIRMED','DOWN_EARLY','DOWN_CONFIRMED']){const r=result('BULL_CONFIRMED','RISING',{stage});delete r.technical;assert(!updated.umSignalBrief_M30(plan(r),now).includes('완성 일봉'));}});
test('daily VWAP9 gate requires strict live price above completed daily VWAP9',()=>{
  const r=result('BEAR_CONFIRMED','RISING');
  assert.equal(updated.umDailyVwap9Gate_M30(r,now).qualified,true);
  r.live_price=100;assert.equal(updated.umDailyVwap9Gate_M30(r,now).qualified,false);
  r.live_price=99;assert.equal(updated.umDailyVwap9Gate_M30(r,now).qualified,false);
  r.live_price=110;r.technical.basis='INCOMPLETE_DAILY_NOT_CONFIRMED';
  assert.equal(updated.umDailyVwap9Gate_M30(r,now).qualified,false);
  r.technical.basis='COMPLETED_DAILY';r.technical.as_of='2026-11-06 15:30:00';
  assert.equal(updated.umDailyVwap9Gate_M30(r,now).qualified,false);
  r.technical.as_of='2026-11-05 15:30:00';r.live_price_source='STALE_CACHE';
  assert.equal(updated.umDailyVwap9Gate_M30(r,now).qualified,false);
});
function oldM30State(stacked=false,gateInitialized=false,gateQualified=false){
  const slot=Math.floor(now.getTime()/300000);
  return {day:'2026-11-06',stage:'WATCH',slot:slot-1,count:1,sent:{},
    vwap30StackValid:true,vwap30Stacked:stacked,vwap30Source:'KIWOOM_15M_BAR_START',
    vwap30Bar:'2026-11-06 13:00:00',vwap30Price:100,vwap30Vwap9:105,vwap30Vwap26:100,
    vwap30Watermark:'2026-11-06 13:05:00',
    ...(gateInitialized?{vwap30DailyGateInitialized:true,vwap30DailyGateValid:true,vwap30DailyQualified:gateQualified}:{})};
}
test('new 30m stack below daily VWAP9 does not emit bullish alert',()=>{
  const r=result('BEAR_CONFIRMED','RISING',{stage:'WATCH',technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-05 15:30:00',vwap9:115}});
  const p=updated.umPlan_M30({ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:90},r,oldM30State(false),now);
  assert.equal(p.events.length,0);
  assert.equal(p.state.vwap30DailyQualified,false);
});
test('new 30m stack above daily VWAP9 emits one bullish alert without previous-high breakout',()=>{
  const r=result('BEAR_CONFIRMED','RISING',{stage:'WATCH',technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-05 15:30:00',vwap9:105,prev_high:120}});
  const p=updated.umPlan_M30({ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:90},r,oldM30State(false),now);
  assert.deepEqual(p.events.map(e=>e.key),['VWAP30_BULL_STACK_EARLY']);
  assert.equal(p.result.sim_bull_confirmed.qualified,true);
  assert.equal(p.result.sim_bull_confirmed.price_gt_prev_high,false);
});
test('existing 30m stack alerts when price later crosses above daily VWAP9',()=>{
  const r=result('BEAR_CONFIRMED','RISING',{stage:'WATCH',technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-05 15:30:00',vwap9:105}});
  const p=updated.umPlan_M30({ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:90},r,oldM30State(true,true,false),now);
  assert.deepEqual(p.events.map(e=>e.key),['VWAP30_BULL_STACK_EARLY']);
});
test('BUY_SETUP and BUY_CONFIRMED are blocked below daily VWAP9',()=>{
  for(const stage of ['BUY_SETUP','BUY_CONFIRMED']){
    const r=result('BULL_CONFIRMED','RISING',{stage,send_allowed:true,jev_gate:{status:'OK',decision:'SEND'},
      events:[{key:stage,stage,level:1}],technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-05 15:30:00',vwap9:115}});
    const p=updated.umPlan_M30({ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:90},r,oldM30State(true),now);
    assert.equal(p.events.length,0,stage);
  }
});
test('BUY_SETUP and BUY_CONFIRMED pass only above daily VWAP9',()=>{
  for(const stage of ['BUY_SETUP','BUY_CONFIRMED']){
    const r=result('BULL_CONFIRMED','RISING',{stage,send_allowed:true,jev_gate:{status:'OK',decision:'SEND'},
      events:[{key:stage,stage,level:1}],technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-05 15:30:00',vwap9:105}});
    const old=oldM30State(true);old.stage=stage;
    const p=updated.umPlan_M30({ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:90},r,old,now);
    assert.deepEqual(p.events.map(e=>e.key),[stage]);
  }
});
test('daily VWAP9 bullish gate does not suppress downside or stop-risk alerts',()=>{
  const down=result('BEAR_CONFIRMED','FALLING',{stage:'DOWN_CONFIRMED',send_allowed:true,
    events:[{key:'DOWN_CONFIRMED',stage:'DOWN_CONFIRMED',level:1}],
    technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-05 15:30:00',vwap9:115}});
  const downOld=oldM30State(true);downOld.stage='DOWN_CONFIRMED';
  const p=updated.umPlan_M30({ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:90},down,downOld,now);
  assert.deepEqual(p.events.map(e=>e.key),['DOWN_CONFIRMED']);
  const risk=result('BEAR_CONFIRMED','FALLING',{stage:'RISK',live_price:85,send_allowed:true,
    events:[{key:'STOP_BREACH',stage:'RISK',level:1}],
    technical:{basis:'COMPLETED_DAILY',as_of:'2026-11-05 15:30:00',vwap9:115}});
  const q=updated.umPlan_M30({ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:90},risk,oldM30State(true),now);
  assert.deepEqual(q.events.map(e=>e.key),['STOP_BREACH']);
});
test('all stages, regimes, slopes and quote signs preserve existing decisions',()=>{
  for(const stage of ['WATCH','BUY_SETUP','BUY_CONFIRMED','DOWN_EARLY','DOWN_CONFIRMED','RISK'])for(const regime of ['BULL_CONFIRMED','BEAR_CONFIRMED','BOUNCE_IN_DOWNREGIME','PULLBACK_IN_UPREGIME','OBV_REBOUND_BASE_DOWN','EARLY_IMPROVEMENT','EARLY_WEAKENING','NEUTRAL'])for(const dir of ['RISING','FALLING','FLAT'])for(const sign of [-.05,.05])for(const second of [false,true]){
    const r=result(regime,dir,{stage,live_change_pct:sign,send_allowed:true,jev_gate:{status:'OK',decision:'SEND'},events:stage==='WATCH'?[]:[{key:stage,stage,level:1,reason:'fixed-machine-evidence'}]});
    const item={ticker:'000990',name:'DB하이텍',sources:['HELD'],stop_price:stage==='RISK'?120:90};
    const slot=Math.floor(now.getTime()/300000),old=second?{day:'2026-11-06',stage,slot:slot-1,count:1,sent:{BUY_SETUP:'existing-message-id'},vwap30StackValid:true,vwap30Stacked:false,vwap30Source:'KIWOOM_15M_BAR_START',vwap30Bar:'2026-11-06 13:00:00'}:null;
    const a=clone(before.umPlan_M30(clone(item),clone(r),clone(old),now)),b=clone(updated.umPlan_M30(clone(item),clone(r),clone(old),now));
    delete a.state.lastResult.directionContext;delete b.state.lastResult.directionContext;
    delete b.state.lastResult.dailyVwap9Gate;
    delete b.state.vwap30DailyGateInitialized;
    delete b.state.vwap30DailyGateValid;
    delete b.state.vwap30DailyQualified;
    delete a.result.sim_bull_confirmed;delete b.result.sim_bull_confirmed;
    delete a.state.simBullConfirmed;delete b.state.simBullConfirmed;
    a.result.reasons=a.result.reasons.filter(x=>!String(x).startsWith('SIM_BULL_CONFIRMED:'));
    b.result.reasons=b.result.reasons.filter(x=>!String(x).startsWith('SIM_BULL_CONFIRMED:'));
    a.events.forEach(e=>{if(e.key==='VWAP30_BULL_STACK_EARLY')e.reason='__BULLISH_GATE_REASON__';});
    b.events.forEach(e=>{if(e.key==='VWAP30_BULL_STACK_EARLY')e.reason='__BULLISH_GATE_REASON__';});
    assert.deepEqual(b,a);trials++;
  }
});
const summary={tests,failures,errors:0,skipped:0,machine_parity_cases:trials,outbound_network:0,orders:0,outcomes};
fs.mkdirSync(path.join(B,'evidence'),{recursive:true});fs.writeFileSync(path.join(B,'evidence/direction-tests.json'),JSON.stringify(summary,null,2));
console.log(JSON.stringify({...summary,outcomes:undefined}));process.exitCode=failures?1:0;
