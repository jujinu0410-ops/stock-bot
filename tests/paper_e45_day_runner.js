/** Cross-language integration: actual Flask wire -> Apps Script batch adapter,
 * paper candidate/storage/recompute + independent E45 projections. No network.
 */
const {assert,fs,vm,path,source,baseline,Sheet,context,plain,dt,forecast}=require('./helpers/paper_game_harness');
const fixture=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),day=fixture.day;
const h=context(),old=context(true),legacyStates={},states={};
for(const [ctx,dir] of [[h,source],[old,baseline]]){
  vm.runInContext(fs.readFileSync(path.join(dir,'UnifiedWatchlists.js'),'utf8'),ctx.c);
  for(const name of ['MARKET_DATA','TRADES','NAV_DAILY','D_REGIME','SCOREBOARD','REVIEW'])ctx.sheets[name]=new Sheet([['header']]);
  ctx.sheets.D_SEED=new Sheet([['header'],...fixture.seed]);
}
h.props.E45_ENABLED='ON';h.props.E45_START_DATE=day;h.props.SIGNAL_API_URL='https://offline.invalid';h.props.SIGNAL_API_TOKEN='fixture';
const ss=h.c.e45Book_();h.c.e45Append_(ss,[forecast('UP','08:12','09:02',day)]);
let apiRequests=0;
function collect(round){
  const at=new Date(round.at),reqs=round.batch.results.map(packet=>h.c.umRequest_({ticker:packet.ticker,sources:['ETF']},h.c.PropertiesService.getScriptProperties()));
  h.c.UrlFetchApp.fetchAll=requests=>{
    apiRequests+=requests.length;
    assert.equal(requests.length,1);assert.ok(requests[0].url.endsWith('/unified-scan-batch'));
    assert.deepEqual(JSON.parse(requests[0].payload).items.map(i=>i.ticker),round.batch.results.map(p=>p.ticker));
    return [{getResponseCode:()=>200,getContentText:()=>JSON.stringify(round.batch)}];
  };
  const packets=h.c.umP0BatchFetch_(reqs),samples=[],oracle=[],events=[];
  assert.equal(packets.blocked,false);
  round.batch.results.forEach((packet,i)=>{
    const item={ticker:packet.ticker,sources:['ETF']},wire=JSON.parse(packets.results[i].response.getContentText()),unchanged=JSON.stringify(wire);
    const rawPlan=old.c.umPlan_(item,wire,legacyStates[item.ticker],at);
    const sample=h.c.pgCandidate_(item,wire,at,at,[]);if(sample)samples.push(sample);
    const after=h.c.umPlan_(item,wire,states[item.ticker],at);
    assert.equal(JSON.stringify(wire),unchanged);assert.deepEqual(plain(after),plain(rawPlan));
    states[item.ticker]=h.c.umCommit_(after,at,'fixture-mail','fixture-event');legacyStates[item.ticker]=old.c.umCommit_(rawPlan,at,'fixture-mail','fixture-event');
    const direct=old.c.pgCandidate_(item,round.full[item.ticker],at);if(direct)oracle.push(direct);
    const event=h.c.e45Observation_(item,wire,round.at,at,{enabled:true,start:day,end:'2026-11-30'});if(event)events.push(event);
  });
  h.c.pgSaveSamples_(samples,at);old.c.pgSaveSamples_(oracle,at);h.c.e45Append_(ss,events);h.c.e45Repair_(ss,at,{enabled:true,start:day,end:'2026-11-30'});
  assert.deepEqual(plain(h.sheets.MARKET_DATA.rows),plain(old.sheets.MARKET_DATA.rows));
}
fixture.rounds.forEach((round,i)=>{
  collect(round);
  const n=h.sheets.E45_TRADES.getLastRow()-1;
  if(i<2)assert.equal(n,0,'no first-collection fill');
  if(i===2){assert.equal(n,4,'all four enter on the next collection');collect(round);}
  if(i>2)assert.equal(n,4,'no extra entries after 10:00 or on later signals');
});
// The response was re-delivered at the same collection above, without rewind.
assert.equal(h.sheets.MARKET_DATA.getLastRow()-1,32);
assert.equal(h.sheets.E45_TRADES.getLastRow()-1,4);
h.c.e45Repair_(ss,dt(day,'15:35'),{enabled:true,start:day,end:'2026-11-30'});
assert.ok(h.sheets.E45_STATUS.rows.slice(1).every(r=>r[3]==='CLOSE_PENDING'));
h.sheets.NAV_DAILY.fail='fixture A-D partial recompute';
assert.throws(()=>h.c.pgRecompute_(dt(day,'16:12')),/A-D partial recompute/);
for(const ctx of [h,old])ctx.c.pgRecompute_(dt(day,'16:12'));
h.c.pgRecompute_(dt(day,'16:12')); // Rebuild, not another execution of trades.
for(const name of ['TRADES','NAV_DAILY','D_REGIME','SCOREBOARD','REVIEW'])assert.deepEqual(plain(h.sheets[name].rows),plain(old.sheets[name].rows));
const ad=plain(h.c.pgCompute_(h.sheets.MARKET_DATA.rows.slice(1),fixture.seed));
for(const name of ['A','B','C','D']){assert.ok(ad.accounts[name].trades.length>0,name+' has a fixed-fixture trade flow');assert.equal(ad.accounts[name].daily.length,1);}
const adBefore=JSON.stringify(['TRADES','NAV_DAILY','D_REGIME','SCOREBOARD','REVIEW'].map(n=>h.sheets[n].rows));
const closes=fixture.closes.map(c=>({id:'CLOSE|'+day+'|'+c.ticker,day,kind:'CLOSE',at:dt(day,'16:13').toISOString(),data:c}));
h.c.e45Append_(ss,closes);h.sheets.E45_NAV.fail='fixture partial failure';
assert.throws(()=>h.c.e45Repair_(ss,dt(day,'16:20'),{enabled:true,start:day,end:'2026-11-30'}),/partial failure/);
h.c.e45Append_(ss,closes);const e45=plain(h.c.e45Repair_(ss,dt(day,'16:20'),{enabled:true,start:day,end:'2026-11-30'}));
assert.equal(e45.trades.length,8);assert.equal(new Set(e45.trades.map(r=>r[0])).size,8);
assert.ok(e45.status.every(r=>r[3]==='CLOSED'));assert.ok(e45.nav.every(r=>Math.abs(r[4]-9990432)<1e-7));
assert.ok(e45.trades.filter(r=>r[4]==='BUY').every(r=>r[6]===10000&&r[5]===299&&r[11]===fixture.rounds[2].at));
assert.equal(JSON.stringify(['TRADES','NAV_DAILY','D_REGIME','SCOREBOARD','REVIEW'].map(n=>h.sheets[n].rows)),adBefore);
assert.equal(h.calls.fetch,0,'no entry market lookup outside the existing batch transport');
assert.equal(apiRequests,fixture.rounds.length+1,'only replayed existing batch requests, no added quote calls');
process.stdout.write(JSON.stringify({observations:32,ad_trade_counts:Object.fromEntries(['A','B','C','D'].map(s=>[s,ad.accounts[s].trades.length])),
  e45_trade_count:8,e45_nav:e45.nav.map(r=>r[4]),batch_requests:apiRequests,additional_entry_market_requests:0,legacy_alerts_equal:true}));
