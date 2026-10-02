const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const direct=path.join(__dirname,'apps_script/held_monitor_5m_v3.gs');
const src=fs.readFileSync(fs.existsSync(direct)?direct:path.join(__dirname,'../apps_script/held_monitor_5m_v3.gs'),'utf8');
let sent=[];
const ctx={console,Date,Utilities:{formatDate:()=> '2026-10-06'},MailApp:{sendEmail:m=>sent.push(m)}};
vm.createContext(ctx);vm.runInContext(src,ctx);
const now=new Date('2026-10-06T10:00:00+09:00');
const ev=key=>({key,title:key,message:key,level:'WATCH'});
const cur=regime=>({ticker:'TEST',name:'ETF',dataQuality:'VALID',regime,stopBroken:false,atrBand:'NONE',price:100});
const prev=stage=>({gateState:{version:3,date:'2026-10-06',stage,stageCount:1,sent:{}}});
for(const [regime,key,stage] of [['EARLY_IMPROVEMENT','SIGNAL_EARLY_UP','EARLY_UP'],['BULL_CONFIRMED','SIGNAL_CONF_UP','CONF_UP'],['EARLY_WEAKENING','SIGNAL_EARLY_DOWN','EARLY_DOWN'],['BEAR_CONFIRMED','SIGNAL_CONF_DOWN','CONF_DOWN']]){
  const c=cur(regime),p=prev(stage),g=ctx.hmGateEvents_(p,c,[],now);
  assert(g.mailEvents.some(e=>e.key===key));
  const marked=ctx.hmMarkGateSent_(g.gateState,c,g.mailEvents,now);
  assert.equal(ctx.hmGateEvents_({gateState:marked},c,[],new Date(now.getTime()+300000)).mailEvents.length,0);
}
const neutral=cur('NEUTRAL');
assert.equal(ctx.hmGateEvents_(prev('NONE'),neutral,[ev('ATR_UP_1_0'),ev('VWAP_GOLD_CROSS'),ev('INFO')],now).mailEvents.length,0);
const recover=prev('NONE');recover.gateState.stopRecoverCount=1;
assert.equal(ctx.hmGateEvents_(recover,neutral,[],now).mailEvents.length,0);
for(const key of ['STOP_BREACH','STOP_BREACH_INITIAL','ATR_DOWN_1_0']) assert(ctx.hmGateEvents_(prev('NONE'),neutral,[ev(key)],now).mailEvents.some(e=>e.key===key));
const stopP=prev('NONE');stopP.gateState.lastStopAlertAt=new Date(now.getTime()-10*60000).toISOString();
assert.equal(ctx.hmGateEvents_(stopP,neutral,[ev('STOP_BREACH')],now).mailEvents.length,0);
const pair=ctx.hmGateEvents_(prev('NONE'),neutral,[ev('VWAP26_TURN_UP'),ev('OBV9_TURN_UP')],now);assert(pair.mailEvents.some(e=>e.key==='BASE_PAIR_UP'));
ctx.hmPreviewMailPolicy();assert.equal(sent.length,0);
ctx.hmSendBatchAlertMail_('test@example.com',[{cur:neutral,events:[ev('INFO')]}],now);assert.equal(sent.length,0);
ctx.hmSendBatchAlertMail_('test@example.com',[{cur:neutral,events:[ev('INFO')]},{cur:cur('EARLY_IMPROVEMENT'),events:[ev('SIGNAL_EARLY_UP'),ev('INFO')]}],now);
assert.equal(sent.length,1);assert(sent[0].subject.includes('△'));assert(!sent[0].body.includes('INFO'));
console.log('PASS: observation suppression, buy signals, risk retention, dedup, stop cooldown, batch filtering, no-mail preview');
