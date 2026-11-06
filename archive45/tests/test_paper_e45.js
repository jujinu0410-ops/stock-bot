const {test,assert,fs,vm,path,crypto,root,source,baseline,Sheet,context,plain,dt,day,cfg,obs,forecast,closing,inputs,compute}=require('./helpers/paper_game_harness');
function compactPaperResult(code='069500',bar='09:45',at='09:48'){
  return {ok:true,engine_version:'three-lists-v2',stage:'WATCH',send_allowed:false,events:[],reasons:[],
    live_price:10000,live_price_source:'KIWOOM_KA10001',m45:{completed_bar:day+' '+bar+':00'},
    paper_data:{schema:1,quality:'VALID',row_count:40,quote_received_at:dt(day,at).toISOString(),computed_at:dt(day,at).toISOString(),
      m45:{available:true,gold:true,vwap9:9900,vwap26:9800,completed_bar:day+' '+bar+':00'}}};
}
test('compact batch bridge restores A-D observations only, with unchanged strategy output and no mutation',()=>{
  const h=context(),old=context(true);h.sheets.MARKET_DATA=new Sheet([['header']]);h.sheets.ERROR_LOG=new Sheet([['header']]);h.props.PG_DIAGNOSTICS_ENABLED='ON';
  const rows=[],oracle=[],diags=[];
  for(const ticker of ['069500','114800','229200','251340']){
    const r=compactPaperResult(ticker),before=JSON.stringify(r),time=dt(day,'09:48');
    const sample=plain(h.c.pgCandidate_({ticker},r,time,time,diags));assert.ok(sample);
    const direct={...r,m45:r.paper_data.m45};oracle.push(plain(old.c.pgCandidate_({ticker},direct,time)));rows.push(sample);
    assert.equal(JSON.stringify(r),before);assert.deepEqual(Object.keys(r.m45),['completed_bar']);
  }
  h.c.pgSaveSamples_(rows,dt(day,'09:48'));h.c.pgSaveSamples_(rows,dt(day,'09:53'));h.c.pgSaveDiagnostics_(diags);
  assert.equal(h.sheets.MARKET_DATA.getLastRow(),5);assert.deepEqual(rows,oracle);
  assert.deepEqual(plain(h.c.pgCompute_(rows,[])),plain(old.c.pgCompute_(oracle,[])));
  assert.ok(diags.every(r=>r[4]==='INFO_VALID_OBSERVATION'&&JSON.parse(r[3]).m45_delivery==='VALIDATED_PAPER_DATA'));
  assert.equal(h.calls.fetch,0);
});
test('paper bridge fails closed on failed, missing, contaminated, incomplete or future evidence',()=>{
  const mutations=[r=>delete r.paper_data,r=>r.paper_data.schema=2,r=>r.paper_data.quality='PARTIAL_BAR_CONTAMINATION',
    r=>r.paper_data.quality='CUTOFF_MISMATCH',r=>r.paper_data.row_count=27,r=>r.paper_data.row_count='40',
    r=>r.paper_data.m45.available=false,r=>delete r.paper_data.m45.available,r=>delete r.paper_data.m45.vwap9,
    r=>r.paper_data.m45.vwap9=Infinity,r=>r.paper_data.m45.vwap26=0,r=>r.paper_data.m45.gold=false,
    r=>r.paper_data.m45.completed_bar=day+' 10:30:00',r=>r.paper_data.quote_received_at=dt(day,'09:44').toISOString(),
    r=>r.paper_data.computed_at=dt(day,'09:49').toISOString(),r=>r.paper_data.quote_received_at='invalid',
    r=>{r.m45.completed_bar=day+' 09:30:00';r.paper_data.m45.completed_bar=r.m45.completed_bar;},
    r=>r.m45={completed_bar:day+' 09:45:00',available:false}];
  for(const mutate of mutations){const h=context(),r=compactPaperResult(),diags=[];mutate(r);
    assert.equal(h.c.pgCandidate_({ticker:'069500'},r,dt(day,'09:48'),dt(day,'09:48'),diags),null);
    assert.equal(diags[0][4],'DATA_MISSING');assert.notEqual(JSON.parse(diags[0][3]).m45_delivery,'VALIDATED_PAPER_DATA');
  }
  const h=context(),r=compactPaperResult();assert.equal(h.c.pgCandidate_({ticker:'069500'},r,dt(day,'09:48'),dt('2026-11-05','09:48'),[]),null);
});
test('restored neutral conditions stay valid observations; lost paper field is recorded per ETF',()=>{
  const h=context(),r=compactPaperResult(),diags=[];r.paper_data.m45={...r.paper_data.m45,vwap9:11000,vwap26:12000,gold:false};
  const sample=h.c.pgCandidate_({ticker:'069500'},r,dt(day,'09:48'),dt(day,'09:48'),diags);
  assert.ok(sample);assert.equal(diags[0][4],'INFO_VALID_OBSERVATION');
  // C does not buy when neither leg is bullish, but a complete group is data.
  const raw=['069500','114800','229200','251340'].map(ticker=>h.c.pgCandidate_({ticker},r,dt(day,'09:48'),dt(day,'09:48'),[]));
  const c=h.c.pgCompute_(raw,[]).accounts.C;assert.equal(c.trades.length,0);assert.equal(c.daily.length,1);
  for(const ticker of ['069500','114800','229200','251340']){const bad=compactPaperResult();delete bad.paper_data.m45.vwap26;
    const logs=[];assert.equal(h.c.pgCandidate_({ticker},bad,dt(day,'09:48'),dt(day,'09:48'),logs),null);
    assert.equal(logs[0][2],ticker);assert.ok(JSON.parse(logs[0][3]).reasons.includes('BATCH_M45_FIELDS_OMITTED'));
  }
});
test('real umPlan_/umCommit_ HELD and ETF alerts unchanged by paper repair; shared enrichment would change an alert',()=>{
  const old=context(true),h=context();
  vm.runInContext(fs.readFileSync(path.join(baseline,'UnifiedWatchlists.js'),'utf8'),old.c);
  vm.runInContext(fs.readFileSync(path.join(source,'UnifiedWatchlists.js'),'utf8'),h.c);
  const at=dt(day,'09:48'),prior={day,sent:{},count:1,stage:'WATCH',slot:Math.floor(at.getTime()/300000)-1,vwap45StackValid:true,vwap45Stacked:false};
  const scenarios=[
    {item:{ticker:'069500',sources:['ETF']},stage:'WATCH',events:[]},
    {item:{ticker:'058470',sources:['HELD'],stop_price:11000},stage:'RISK',events:[{key:'STOP_BREACH',stage:'RISK',level:1}]},
    {item:{ticker:'069500',sources:['ETF']},stage:'RISK',events:[{key:'MARKET_CRASH',stage:'RISK',level:3}]},
    {item:{ticker:'091160',sources:['ETF']},stage:'BUY_CONFIRMED',events:[{key:'BUY_CONFIRMED',stage:'BUY_CONFIRMED',level:1}]}
  ];
  for(const s of scenarios){
    const r={...compactPaperResult(),stage:s.stage,events:s.events,send_allowed:s.events.length>0,jev_gate:{status:'OK',decision:'SEND'}};
    const original=JSON.stringify(r),a=old.c.umPlan_(s.item,r,prior,at);
    h.c.pgCandidate_(s.item,r,at,at,[]);
    const b=h.c.umPlan_(s.item,r,prior,at);
    assert.equal(JSON.stringify(r),original);assert.deepEqual(plain(b),plain(a));
    assert.deepEqual(plain(h.c.umCommit_(b,at,'fixture-message','fixture-event')),plain(old.c.umCommit_(a,at,'fixture-message','fixture-event')));
  }
  const item=scenarios[0].item,r=compactPaperResult(),compact=h.c.umPlan_(item,r,prior,at);
  const enriched=h.c.umPlan_(item,{...r,m45:r.paper_data.m45},prior,at);
  assert.equal(compact.events.length,0);assert.equal(enriched.events[0].key,'VWAP45_BULL_STACK_EARLY','test is sensitive to accidental shared m45 enrichment');
});
test('T/R/G/H direction matrix and exact independent cash oracle',()=>{
  for(const bar of [10100,9900])for(const f of ['UP','DOWN']){
    const h=context(),events=inputs(f,bar).concat(closing('069500'),closing('114800')),r=compute(h,events);
    const buys=r.trades.filter(t=>t[4]==='BUY'),expected={
      'E45-T':bar>10000?'069500':'114800','E45-R':bar>10000?'114800':'069500','E45-G':f==='UP'?'069500':'114800'};
    if((bar>10000)===(f==='UP'))expected['E45-H']=f==='UP'?'069500':'114800';
    assert.deepEqual(Object.fromEntries(buys.map(t=>[t[2],t[3]])),expected);
    buys.forEach(t=>{assert.equal(t[5],299);assert.ok(Math.abs(t[7]-10010)<1e-8);assert.ok(Math.abs(t[8]-1795.794)<1e-8);});
    r.nav.forEach(t=>assert.ok(Math.abs(t[4]-(expected[t[2]]?9990432:10000000))<1e-7));
    assert.equal(r.trades.length,buys.length*2);assert.ok(r.trades.filter(t=>t[4]==='SELL').every(t=>t[12]==='ASSUMED_1530_CLOSE_NOT_EXCHANGE_FILL'));
  }
  const data=inputs('DOWN');data.filter(e=>e.kind==='OBS'&&e.data.code==='114800').forEach(e=>e.data.price=1500);
  const mixed=compute(context(),data).trades.filter(t=>t[4]==='BUY'),inverse=mixed.filter(t=>t[3]==='114800');
  assert.deepEqual(inverse.map(t=>t[2]),['E45-R','E45-G']);assert.ok(inverse.every(t=>t[6]===1500&&Math.abs(t[7]-1501.5)<1e-8));
  assert.deepEqual(inverse[0].slice(5,10),inverse[1].slice(5,10),'same ETF snapshot means identical quantity, fill and fee');
  assert.equal(mixed.find(t=>t[2]==='E45-T')[6],10000,'different ETF must use its own quote');
});
test('flat first candle: only G; missing/late/conflicting forecasts do not stop T/R',()=>{
  const h=context();let r=compute(h,inputs('UP',10000));assert.deepEqual(r.trades.map(t=>t[2]),['E45-G']);
  for(const received of ['09:00','09:01']){
    const e=inputs();e[0]=forecast('UP',received);r=compute(h,e);assert.deepEqual(r.trades.map(t=>t[2]),['E45-T','E45-R']);
  }
  r=compute(h,inputs().filter(e=>e.kind!=='FORECAST'));assert.equal(r.status.find(x=>x[2]==='E45-G')[3],'FORECAST_MISSING');
  const late=inputs();late[0]=forecast('UP','08:00','09:54');assert.equal(compute(h,late).trades.length,2);
  assert.equal(h.c.e45ParseForecast_('오늘 시가 대비 종가 전망: 상승\n오늘 시가 대비 종가 전망: 하락',day),'');
});
test('same round, pre-signal, stale, wrong date, missing fields, future, >10:00 never fill',()=>{
  const h=context();assert.equal(compute(h,inputs().slice(0,3)).trades.length,0);
  const changes=[e=>e.data.round='r1',e=>e.data.paper.quote_received_at=dt(day,'09:47').toISOString(),
    e=>e.at=dt(day,'09:59').toISOString(),e=>e.data.paper.quote_received_at=dt('2026-11-03','09:53').toISOString(),
    e=>delete e.data.paper.quote_received_at,e=>e.data.price=0,e=>e.at=dt(day,'10:01').toISOString(),e=>e.data.source='GOOGLEFINANCE'];
  for(const change of changes){const data=inputs();change(data[4]);assert.equal(compute(h,data).trades.length,0);}
  const future=inputs();future[1].data.paper.computed_at=dt(day,'09:49').toISOString();assert.equal(compute(h,future).trades.length,0);
  assert.equal(compute(h,inputs(),dt(day,'09:52')).trades.length,0);
});
test('first45 invalid / partial / later candle / missing / flat status',()=>{
  for(const mutate of [e=>e.data.paper.first45.completed=false,e=>e.data.paper.first45.end=dt(day,'10:30').toISOString(),e=>e.data.paper.first45.quality='PARTIAL',e=>e.data.paper.first45.high=1]){
    const data=inputs();data.filter(e=>e.kind==='OBS'&&e.data.code==='069500').forEach(mutate);assert.equal(compute(context(),data).trades.length,0);
  }
});
test('missing/unconfirmed/wrong-day/early closes stay CLOSE_PENDING; late close blocks intervening new entry',()=>{
  const h=context();let r=compute(h,inputs());assert.equal(r.status[0][3],'CLOSE_PENDING');assert.equal(r.nav[0][4],'');
  for(const mutate of [c=>c.data.quality='LIVE',c=>c.data.day='2026-11-03',c=>c.data.observed_at=dt(day,'15:29').toISOString(),c=>c.data.market_time=dt(day,'15:00').toISOString()]){
    const c=closing('069500');mutate(c);assert.equal(compute(h,inputs().concat(c)).trades.filter(t=>t[4]==='SELL').length,0);
  }
  const next='2026-11-05',events=inputs().concat(inputs('UP',10100,next),closing('069500',10000,day,next),closing('114800',10000,day,next));
  r=compute(h,events,dt(next,'16:20'));assert.equal(r.trades.filter(t=>t[1]===next).length,0);assert.equal(r.status.find(t=>t[1]===next)[3],'PRIOR_CLOSE_PENDING');
});
test('event/projection retries and partial failure repair produce one immutable trade ID',()=>{
  const h=context();h.props.E45_ENABLED='ON';h.props.E45_START_DATE=day;
  const ss=h.c.e45Book_(),events=inputs().concat(closing('069500'),closing('114800'));
  h.c.e45Append_(ss,events);h.sheets.E45_NAV.fail='INJECTED_WRITE_FAILURE';
  assert.throws(()=>h.c.e45Repair_(ss,dt(day,'16:20'),cfg),/INJECTED/);
  h.c.e45Append_(ss,events);h.c.e45Repair_(ss,dt(day,'16:20'),cfg);h.c.e45Repair_(ss,dt(day,'16:20'),cfg);
  assert.equal(h.sheets.E45_INPUTS.getLastRow(),events.length+1);assert.equal(h.sheets.E45_TRADES.getLastRow(),9);
  assert.equal(new Set(h.sheets.E45_TRADES.rows.slice(1).map(r=>r[0])).size,8);
  h.sheets.E45_TRADES.rows[1][5]=999;assert.throws(()=>h.c.e45Repair_(ss,dt(day,'16:20'),cfg),/TRADE_CONFLICT/);
});
test('flags OFF are side-effect-free and explicit start prevents historical backfill',()=>{
  const h=context();assert.equal(h.c.e45SafeTick_(dt(day,'09:02')).status,'OFF');assert.equal(Object.keys(h.sheets).length,0);assert.equal(h.calls.fetch+h.calls.mail+h.calls.locks,0);
  h.props.E45_ENABLED='ON';assert.equal(h.c.e45Config_().enabled,false);
  const earlier=inputs('UP',10100,'2026-11-03');assert.equal(compute(h,earlier).trades.length,0);
});
test('lock contention skips without I/O; forecast is frozen once; closed-session day is not a data error',()=>{
  const h=context();h.props.E45_ENABLED='ON';h.props.E45_START_DATE=day;
  h.c.LockService.getScriptLock=()=>({tryLock:()=>false,releaseLock(){throw Error('must not release unowned lock');}});
  assert.equal(h.c.e45SafeTick_(dt(day,'09:02'),true).status,'LOCKED');assert.equal(Object.keys(h.sheets).length,0);
  let lookups=0;h.c.e45FetchForecast_=(d,cfg,now)=>{lookups++;return forecast('UP','08:00','09:02',d);};
  h.c.e45Tick_(dt(day,'09:02'),true);h.c.e45Tick_(dt(day,'09:07'),true);
  assert.equal(lookups,1);const stored=plain(h.c.e45Read_(h.book));assert.equal(stored.filter(e=>e.kind==='FORECAST').length,1);
  assert.equal(stored.find(e=>e.kind==='FORECAST').data.direction,'UP');
  const off=context();off.props.E45_ENABLED='ON';off.props.E45_START_DATE=day;off.c.e45Tick_(dt(day,'16:12'),false);
  assert.equal(off.sheets.E45_STATUS.getLastRow(),1);
});
test('candidate diagnostics persist INFO separately from EXEC_ERROR without changing any ledger',()=>{
  const h=context();h.props.PG_DIAGNOSTICS_ENABLED='ON';h.sheets.ERROR_LOG=new Sheet([['at','phase','code','message','status']]);
  const wait=h.c.pgDiagnostic_({ticker:'069500'},{},dt(day,'09:42'),dt(day,'09:42'),null);
  const error=h.c.pgExecutionDiagnostic_('PG_SAVE','069500','injected');h.c.pgSaveDiagnostics_([wait,null,error]);
  assert.deepEqual(h.sheets.ERROR_LOG.rows.slice(1).map(r=>r[4]),['INFO_WAIT_FIRST45','EXEC_ERROR']);assert.equal(h.sheets.TRADES,undefined);
});
test('legacy candidate outputs and A-D computation / full ledger projections identical',()=>{
  const old=context(true),h=context(),raw=[];
  for(const d of ['2026-11-02','2026-11-03'])for(const time of ['09:45','10:30','11:15'])for(const code of ['069500','114800','229200','251340']){
    const now=dt(d,time),price=10000+(time==='11:15'?500:time==='10:30'?-500:0);
    const r={m45:{available:true,vwap9:9900,vwap26:9800,gold:code==='069500',completed_bar:d+' '+time+':00'},live_price:price,live_price_source:'KIWOOM_KA10001',stage:'WATCH'};
    assert.deepEqual(plain(h.c.pgCandidate_({ticker:code},r,now,now,[])),plain(old.c.pgCandidate_({ticker:code},r,now)));
    raw.push(plain(old.c.pgCandidate_({ticker:code},r,now)));
  }
  assert.deepEqual(plain(h.c.pgCompute_(raw,[])),plain(old.c.pgCompute_(raw,[])));
  const headers={MARKET_DATA:[['header'],...raw],D_SEED:[['header']],TRADES:[['header']],NAV_DAILY:[['header']],D_REGIME:[['header']],SCOREBOARD:[['header']],REVIEW:[['header']]};
  for(const ctx of [old,h])for(const [name,rows]of Object.entries(headers))ctx.sheets[name]=new Sheet(rows);
  old.c.pgRecompute_(dt(day,'16:12'));h.c.pgRecompute_(dt(day,'16:12'));
  for(const name of ['TRADES','NAV_DAILY','D_REGIME','SCOREBOARD','REVIEW'])assert.deepEqual(h.sheets[name].rows,old.sheets[name].rows);
  assert.equal(fs.readFileSync(path.join(source,'PaperGameD.js'),'utf8'),fs.readFileSync(path.join(baseline,'PaperGameD.js'),'utf8'));
  const current=fs.readFileSync(path.join(source,'PaperGame.js'),'utf8').replace(/\r\n/g,'\n'),original=fs.readFileSync(path.join(baseline,'PaperGame.js'),'utf8').replace(/\r\n/g,'\n');
  assert.equal(current.slice(current.indexOf('function pgSaveSamples_')),original.slice(original.indexOf('function pgSaveSamples_')),'all sample persistence, trading/accounting/recompute code stays byte-identical after newline normalization');
  assert.equal(current.slice(current.indexOf('function pgCandidateLegacy_'),current.indexOf('function pgCandidate_')).trim().replace('function pgCandidateLegacy_','function pgCandidate_'),original.slice(original.indexOf('function pgCandidate_'),original.indexOf('function pgSaveSamples_')).trim(),'original candidate predicate unchanged');
});
test('Nov 2/3 batch omission reproduces NO_DATA; diagnostics distinguish neutral and wait',()=>{
  const h=context();for(const d of ['2026-11-02','2026-11-03']){
    const rows=[],diags=[];for(const ticker of ['069500','114800','229200','251340']){
      const r={m45:{completed_bar:d+' 09:45:00'},live_price:10000,live_price_source:'KIWOOM_KA10001',stage:'WATCH'};
      const sample=h.c.pgCandidate_({ticker},r,dt(d,'09:48'),dt(d,'09:48'),diags);if(sample)rows.push(sample);
    }
    assert.equal(rows.length,0);assert.equal(h.c.pgCompute_(rows,[]).daily.length,0);
    assert.ok(diags.every(r=>r[4]==='DATA_MISSING'&&JSON.parse(r[3]).reasons.includes('BATCH_M45_FIELDS_OMITTED')));
  }
  const r={m45:{available:true,vwap9:11000,vwap26:12000,completed_bar:day+' 09:45:00'},live_price:10000,live_price_source:'KIWOOM_KA10001',stage:'WATCH'},diags=[];
  h.c.pgCandidate_({ticker:'069500'},r,dt(day,'09:48'),dt(day,'09:48'),diags);assert.equal(diags[0][4],'INFO_VALID_OBSERVATION');
  assert.equal(h.c.pgDiagnostic_({ticker:'069500'},{},dt(day,'09:42'),dt(day,'09:42'),null)[4],'INFO_WAIT_FIRST45');
});
test('existing DAILY close adapter ignores live overlay; fallback whitelist and bounded calls',()=>{
  const h=context();h.props.E45_ENABLED='ON';h.props.E45_START_DATE=day;
  const serial=Date.parse(day+'T00:00:00Z')/86400000+25569;
  h.sheets.ETF_DATA=new Sheet([['069500','KODEX 200'],['Date','Open','High','Low','Close','Volume'],[serial,100,110,90,105,1000],['114800','KODEX inverse'],[serial,100,110,90,95,1000]]);
  const closes=plain(h.c.e45ExistingCloses_(day,dt(day,'16:12')));assert.deepEqual(closes.map(c=>c.close),[105,95]);
  const ss=h.c.e45Book_();h.c.e45Append_(ss,inputs());assert.equal(h.c.e45CloseInputs_(ss,dt(day,'16:12'),cfg).length,2);assert.equal(h.calls.fetch,0);
  delete h.sheets.ETF_DATA;h.props.SIGNAL_API_URL='https://test.invalid';h.props.SIGNAL_API_TOKEN='TEST';
  let requests=[];h.c.UrlFetchApp.fetch=(url,opt)=>{requests.push([url,JSON.parse(opt.payload)]);return {getResponseCode:()=>200,getContentText:()=>JSON.stringify({ok:true,paper_only:true,closes:[]})};};
  for(const time of ['16:12','16:13','16:27','16:42','16:57'])h.c.e45CloseInputs_(ss,dt(day,time),cfg);
  assert.equal(requests.length,3);assert.ok(requests.every(r=>r[1].tickers.length<=2&&r[1].tickers.every(c=>['069500','114800'].includes(c))));
  h.sheets.ETF_DATA=new Sheet([['069500','KODEX 200'],[serial-1,100,110,90,105,1000],['091160','Sector ETF'],[serial,100,110,90,999,1000]]);
  assert.equal(h.c.e45ExistingCloses_(day,dt(day,'16:12')).length,0,'never misattribute another ETF block to a missing target date');
});
test('close response observed after HTTP start is accepted using actual completion time',()=>{
  const h=context();h.props.E45_ENABLED='ON';h.props.E45_START_DATE=day;h.props.SIGNAL_API_URL='https://test.invalid';h.props.SIGNAL_API_TOKEN='TEST';
  const start=dt(day,'16:12').getTime();h.c.Date=class extends Date{static now(){return start+3000;}};
  const ss=h.c.e45Book_();h.c.e45Append_(ss,inputs());
  h.c.UrlFetchApp.fetch=()=>({getResponseCode:()=>200,getContentText:()=>JSON.stringify({ok:true,paper_only:true,closes:['069500','114800'].map(code=>({...closing(code).data,observed_at:new Date(start+1000).toISOString()}))})});
  const events=plain(h.c.e45CloseInputs_(ss,new Date(start),cfg));assert.equal(events.length,2);assert.ok(events.every(e=>Date.parse(e.at)===start+3000));
});
test('forecast adapter uses Gmail internalDate, freezes ID/hash, excludes post-open mail',()=>{
  const h=context(),messages=[{id:'pre',received:'08:12',text:'오늘 시가 대비 종가 전망: 하락 (신뢰도: 보통)'},{id:'post',received:'09:01',text:'오늘 시가 대비 종가 전망: 상승'}];
  h.c.GmailApp.search=()=>[{getMessages:()=>messages.map(m=>({getSubject:()=> '[모바일 대시보드] 2026년 11월 4일 일일 모닝 브리핑',getFrom:()=> 'jujinu0410@gmail.com',getId:()=>m.id,getDate:()=>dt(day,'07:20'),getPlainBody:()=>m.text}))}];
  h.c.UrlFetchApp.fetch=url=>{const m=messages.find(m=>url.includes('/'+m.id+'?'));return {getResponseCode:()=>200,getContentText:()=>JSON.stringify({internalDate:dt(day,m.received).getTime()})};};
  const f=plain(h.c.e45FetchForecast_(day,{sender:'jujinu0410@gmail.com'},dt(day,'09:02')));assert.equal(f.data.source_id,'pre');assert.equal(f.data.direction,'DOWN');assert.equal(f.data.received_at,dt(day,'08:12').toISOString());assert.equal(f.data.sha256.length,64);
});
test('actual unified coordinator regression: same legacy returns, writes, state and API request count even if E45 capture fails',()=>{
  function run(legacy,failPaper=false){
    const h=context(legacy),at=dt(day,'09:48');
    vm.runInContext(fs.readFileSync(path.join(legacy?baseline:source,'UnifiedWatchlists.js'),'utf8'),h.c);
    const calls=[],control=new Sheet([['header']]);h.book.getSheetByName=n=>n==='UNIFIED_CONTROL'?control:h.sheets[n]||null;
    h.sheets.MARKET_DATA=new Sheet([['header']]);
    Object.assign(h.c,{hmIsMarketWindow_:()=>true,umMode_:()=> 'ACTIVE',umResumeDelivery_:()=>{},umLegacySeed_:()=>({}),umLoadState_:()=>null,
      umUniverse_:()=>['069500','114800','229200','251340'].map(ticker=>({ticker,sources:['ETF']})),
      umP0Schedule_:(universe)=>({work:universe.map(item=>({item,kind:'PRIORITY',offset:0})),start:0,retry:new Set(),group:'A',priorityCount:4,groupCounts:{A:0,B:0,C:0}}),
      umP0Checkpoint_:()=>{},umPlan_:(item)=>({events:[],state:{ticker:item.ticker}}),umSaveEtfDiagnostics_:()=>({status:'OK'}),
      umRecord_:rows=>calls.push(['audit',plain(rows)]),umRequest_:item=>{calls.push(['request',item.ticker]);return {ticker:item.ticker};},
      umP0BatchFetch_:reqs=>({blocked:false,results:reqs.map(req=>({response:{getResponseCode:()=>200,getContentText:()=>JSON.stringify({ok:true,engine_version:'three-lists-v2',stage:'WATCH',send_allowed:false,events:[],reasons:[],m45:{available:true,vwap9:100,vwap26:101,gold:false,completed_bar:day+' 09:45:00'},live_price:100,live_price_source:'KIWOOM_KA10001'})}}))}),
      umNotifyFailure_:()=>calls.push(['notify'])});
    if(failPaper)h.c.e45Observation_=()=>{throw Error('ISOLATED_TEST');};
    const result=plain(h.c.umTick5m(at));return {result,calls,control:plain(control.rows),market:plain(h.sheets.MARKET_DATA.rows),props:h.props};
  }
  const old=run(true),newer=run(false),failing=run(false,true);
  // Slot id is wall-clock based in the legacy coordinator; its value is immaterial.
  delete old.props.UM2_COMPLETED_ACTIVE;delete newer.props.UM2_COMPLETED_ACTIVE;delete failing.props.UM2_COMPLETED_ACTIVE;
  assert.deepEqual(newer,old);assert.deepEqual(failing,old);
});
