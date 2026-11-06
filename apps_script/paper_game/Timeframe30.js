/** Permanent full-day cutover. All trading here is virtual; no order API. */
const M30 = Object.freeze({START:'2026-11-05',VERSION:'KRX_M30_V1',COST:.0032,BUDGET:1000000});
function m30Install(){
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))throw Error('M30_INSTALL_LOCK_BUSY');
  try{
    PropertiesService.getScriptProperties().setProperties({E30_ENABLED:'ON',E30_START_DATE:M30.START,E30_END_DATE:PG30.END,E45_END_DATE:'2026-11-04'});
    const ss=m30Book_(),sh=ss.getSheetByName('TIMEFRAME_VERSION')||ss.insertSheet('TIMEFRAME_VERSION');
    const rows=[['항목','확정값'],['표준',M30.VERSION],['적용 시작 거래일',M30.START],['정규장 슬롯 수',13],['첫 봉','09:00~09:30'],['마지막 봉','15:00~15:30'],
      ['종가 단일가','15:20~15:30 시장가 제출·검증 종가 가상체결 가정'],['마지막 봉 품질','전체 OHLCV 미검증 시 PARTIAL/NO_DATA·꼬리 분석 제외'],
      ['과거 기록','기존 A~D/E45 탭 보존·신규 M30/E30 탭에서 성과 분리'],['A~D 자본','신규 버전별 1,000만원 가상자본·이전 버전 성과와 합산 금지'],
      ['PATTERN-B 결정','15:20 이전 유효 관측이 있는 4 ETF의 무조건 매수 기준군·마지막 봉 모양 사용 금지'],['왕복 비용',M30.COST],
      ['D 시드 상태',PropertiesService.getScriptProperties().getProperty('M30_SEED_READY')===M30.START?'READY_42_ROWS':'WAIT_2026-11-04_COMPLETED_HISTORY'],['설치 확인 시각',new Date().toISOString()]];
    sh.getRange(1,1,rows.length,2).setValues(rows);sh.setFrozenRows(1);sh.setColumnWidth(1,180);sh.setColumnWidth(2,720);sh.getRange(1,1,rows.length,2).setWrap(true);
    sh.getRange('B12').setNumberFormat('0.00%');
    const score=ss.getSheetByName('SCOREBOARD_M30');score.getRange('B2:C1000').setNumberFormat('#,##0');score.getRange('D2:E1000').setNumberFormat('0.00%');score.getRange('H2:I1000').setNumberFormat('0.00%');score.getRange('A1:K5').setWrap(true);score.setColumnWidth(11,350);
    Object.keys(E30.TABS).forEach(name=>ss.getSheetByName(name).setFrozenRows(1));
    pgRecompute_M30(new Date());
    PropertiesService.getScriptProperties().setProperty('M30_INSTALL_VERIFIED',JSON.stringify({version:M30.VERSION,start:M30.START,installed_at:new Date().toISOString()}));
    console.log(JSON.stringify({status:'M30_INSTALLED',start:M30.START,legacy_preserved:true,real_orders:0,mail:0}));
    return {status:'M30_INSTALLED',start:M30.START};
  }finally{lock.releaseLock();}
}
function m30Active_(now){return e30Day_(now||new Date())>=M30.START;}
function m30Book_(){
  const ss=SpreadsheetApp.openById(PG30.ID);
  const tabs=['MARKET_DATA','TRADES','NAV_DAILY','SCOREBOARD','REVIEW','D_REGIME','D_SEED'];
  tabs.forEach(name=>{
    const target=name+'_M30';if(ss.getSheetByName(target))return;
    const source=ss.getSheetByName(name);if(!source)throw Error('M30_SOURCE_TAB_'+name);
    const sh=name==='REVIEW'?source.copyTo(ss).setName(target):ss.insertSheet(target),width=source.getLastColumn();
    if(name==='REVIEW')sh.getRange('B2:F9').clearContent();
    const header=source.getRange(1,1,1,width).getValues().map(r=>r.map(x=>String(x).replace(/45분|45m/g,'30분')));
    sh.getRange(1,1,1,width).setValues(header);sh.setFrozenRows(1);
  });
  e30Book_();return ss;
}
function m30PrepareCutover_(){
  const now=new Date(),props=PropertiesService.getScriptProperties();
  if(props.getProperty('M30_SEED_READY')===M30.START)return {status:'READY'};
  if(now.getTime()<e30At_('2026-11-04','16:10'))return {status:'NOT_DUE'};
  const prepDay=e30Day_(now);let attempts={day:prepDay,count:0,last:0};try{const a=JSON.parse(props.getProperty('M30_PREPARE_ATTEMPTS')||'{}');if(a.day===prepDay)attempts=a;}catch(e){}
  if(attempts.count>=3||now.getTime()-attempts.last<15*60000)return {status:'PREPARE_BACKOFF'};
  props.setProperty('M30_PREPARE_ATTEMPTS',JSON.stringify({day:prepDay,count:attempts.count+1,last:now.getTime()}));
  const ss=m30Book_(),data=m30FetchSession_('2026-11-04',props),seed=[];
  for(const [code] of PG30.PAIRS){
    const item=data.find(x=>x.ticker===code),rows=item&&item.seed||[];
    if(rows.length!==21||rows.some(r=>r[4]!=='VALID_30M_COMPLETE')||rows[20][0].slice(0,10)!=='2026-11-04')throw Error('M30_SEED_INCOMPLETE_'+code);
    seed.push.apply(seed,rows);
  }
  const account=pgAccount_M30('D');if(!pgDLoadSeed_M30(account,seed))throw Error('M30_SEED_VALIDATION_FAILED');
  const sh=ss.getSheetByName('D_SEED_M30');sh.getRange(2,1,seed.length,2).setNumberFormat('@');sh.getRange(2,1,seed.length,6).setValues(seed);
  props.setProperty('M30_SEED_READY',M30.START);
  const version=ss.getSheetByName('TIMEFRAME_VERSION');if(version)version.getRange('B13').setValue('READY_42_ROWS');
  return {status:'READY',rows:seed.length,version:M30.VERSION};
}
function m30SafePrepare_(){
  try{const lock=LockService.getScriptLock();if(!lock.tryLock(500))return {status:'LOCKED'};
    try{return m30PrepareCutover_();}finally{lock.releaseLock();}
  }catch(e){console.error('M30_PREPARE_ISOLATED '+String(e));return {status:'ERROR',reason:String(e)};}
}
function m30ValidBar_(b){
  return !!b&&b.completed===true&&b.quality==='VALID'&&
    ['open','high','low','close'].every(k=>e30Positive_(b[k]))&&
    Number.isFinite(b.volume)&&b.volume>0&&b.high>=Math.max(b.open,b.close,b.low)&&b.low<=Math.min(b.open,b.close,b.high)&&
    e30Time_(b.end)-e30Time_(b.start)===1800000;
}
function m30Classify_(b){
  if(!m30ValidBar_(b))return {quality:b&&b.quality||'NO_DATA',type:'',upper:null,lower:null};
  const range=b.high-b.low,dir=b.close>b.open?'양봉':b.close<b.open?'음봉':'도지';
  const upper=range?(b.high-Math.max(b.open,b.close))/range:0,lower=range?(Math.min(b.open,b.close)-b.low)/range:0;
  const type=dir==='도지'?'도지':dir==='양봉'?(lower>=.35&&lower>=1.5*upper?'강양봉':upper>=.35&&upper>=1.5*lower?'준음봉':'양봉'):
    (upper>=.35&&upper>=1.5*lower?'강음봉':lower>=.35&&lower>=1.5*upper?'준양봉':'음봉');
  return {quality:'VALID',direction:dir,type:type,upper:upper,lower:lower};
}
function m30FirstEntry_(obs,day,code){
  const list=obs.filter(e=>e.day===day&&e.data.code===code).sort((a,b)=>e30Time_(a.at)-e30Time_(b.at));
  const signal=list.find(e=>{
    const p=e.data.paper||{},b=p.first30||{};
    return p.source==='KIWOOM_15M_BAR_START'&&p.schema===2&&p.bar_interval_minutes===30&&m30ValidBar_(b)&&b.end===day+'T09:30:00+09:00'&&
      e30Time_(b.end)<=e30Time_(p.computed_at)&&e30Time_(p.computed_at)<=e30Time_(e.at)&&e30Time_(e.at)<=e30At_(day,'10:00');
  });
  if(!signal)return null;
  const fill=list.find(e=>e30QuoteValid_(e,signal));
  return fill?{price:fill.data.price,at:fill.data.paper.quote_received_at,signal_at:signal.at,bar:signal.data.paper.first30}:null;
}
function m30AuctionPlans_(events,now){
  const day=e30Day_(now),ms=now.getTime();
  if(ms<e30At_(day,'15:20')||ms>=e30At_(day,'15:30'))return [];
  const result=[],obs=events.filter(e=>e.kind==='OBS');
  PG30.CODES.forEach(code=>{
    const id='PATTERN_ORDER|'+day+'|'+code;if(events.some(e=>e.id===id))return;
    const pre=obs.filter(e=>e.day===day&&e.data.code===code&&e30Time_(e.at)<e30At_(day,'15:20')&&e30Time_(e.at)>=e30At_(day,'15:10'))
      .sort((a,b)=>e30Time_(b.at)-e30Time_(a.at))[0];
    if(!pre)return;
    const p=pre.data.paper||{},m=p.m30||{},end=e30Time_(String(m.completed_bar||'').replace(' ','T')+'+09:00');
    const quote=e30Time_(p.quote_received_at),computed=e30Time_(p.computed_at),received=e30Time_(pre.at);
    if(p.source!=='KIWOOM_15M_BAR_START'||p.schema!==2||p.bar_interval_minutes!==30||!pre.data.ok||pre.data.source!=='KIWOOM_KA10001'||!e30Positive_(pre.data.price)||
       ![quote,computed,received].every(Number.isFinite)||quote<end||quote>computed||computed>received||received-quote>120000||e30Day_(p.quote_received_at)!==day||
       m.available!==true||!Number.isFinite(end)||end!==e30At_(day,'15:00')||
       !m30ValidBar_(p.latest30)||e30Time_(p.latest30.end)!==end)return;
    const entry=m30FirstEntry_(obs,day,code),bull=p.latest30.close>p.latest30.open;
    const hold=!!entry&&pre.data.price/entry.price-1-M30.COST>0&&bull;
    result.push(e30Event_(day,'PATTERN_ORDER',id,{code:code,decision_at:pre.at,decision_price:pre.data.price,
      last_completed_end:p.latest30.end,last_completed_bull:bull,entry:entry,
      A:entry?'SELL_CLOSE':'NO_ENTRY',B:'BUY_CLOSE_ALL_VALID_PRE1520',C:entry?(hold?'HOLD_NEXT_OPEN':'SELL_CLOSE'):'NO_ENTRY',
      cost:M30.COST,version:M30.VERSION,paper_only:true,model:'VIRTUAL_MARKET_AUCTION_NO_FILL_GUARANTEE'},now));
  });return result;
}
function m30DailyRecord_(events,day,code){
  const obs=events.filter(e=>e.kind==='OBS'),entry=m30FirstEntry_(obs,day,code);
  const orders=events.filter(e=>e.day===day&&e.kind==='PATTERN_ORDER'&&e.data.code===code&&
    e30Time_(e.at)>=e30At_(day,'15:20')&&e30Time_(e.at)<e30At_(day,'15:30')&&
    e30Time_(e.data.decision_at)<e30At_(day,'15:20'));
  const order=orders.sort((a,b)=>e30Time_(a.at)-e30Time_(b.at))[0];
  const sessions=events.filter(e=>e.kind==='SESSION_DATA'&&e.day===day&&e.data.ticker===code).sort((a,b)=>e30Time_(b.at)-e30Time_(a.at));
  const s=sessions[0]&&sessions[0].data||{},d=s.daily||{},last=s.last30||{};
  const close=d.quality==='CONFIRMED_DAILY_CLOSE'&&d.source==='KIWOOM_KA10081_KRX_DAILY'&&d.market_time===day+'T15:30:00+09:00'&&
    e30Time_(d.observed_at)>=e30At_(day,'16:10')&&sessions[0]&&e30Time_(d.observed_at)<=e30Time_(sessions[0].at)&&e30Positive_(d.close)?d.close:null;
  const next=s.next_daily||{},nextOpen=next.quality==='OPEN_CONFIRMED'&&next.day===s.next_trading_day&&
    next.source==='KIWOOM_KA10081_KRX_DAILY'&&sessions[0]&&e30Time_(next.observed_at)>=e30At_(next.day,'09:01')&&e30Time_(next.observed_at)<=e30Time_(sessions[0].at)&&e30Positive_(next.open)?next.open:null;
  const first=entry?entry.bar:(s.bars||[]).find(b=>b.end===day+'T09:30:00+09:00');
  const f=m30Classify_(first),l=m30Classify_(last),net=(a,b)=>e30Positive_(a)&&e30Positive_(b)?b/a-1-M30.COST:null;
  const quarantined=m30QuarantinedDay_(day);
  const A=!quarantined&&order&&entry&&order.data.A==='SELL_CLOSE'?net(entry.price,close):null;
  const B=!quarantined&&order&&order.data.B==='BUY_CLOSE_ALL_VALID_PRE1520'?net(close,nextOpen):null;
  const C=!quarantined&&order&&entry?net(entry.price,order.data.C==='HOLD_NEXT_OPEN'?nextOpen:close):null;
  return {id:day+'|'+code,day:day,code:code,version:M30.VERSION,first:first||null,last:last,
    first_pattern:quarantined?{quality:'INVALID_SOURCE_WATERMARK',type:'',upper:f.upper,lower:f.lower}:f,last_pattern:l,entry:entry,order:order||null,close:close,next_day:s.next_trading_day||'',next_open:nextOpen,
    A:A,B:B,C:C,gap:e30Positive_(close)&&e30Positive_(nextOpen)?nextOpen/close-1:null,
    status:quarantined?'DATA_INTEGRITY_HOLD':!entry?'NO_OBSERVED_ENTRY':!order?'AUCTION_ORDER_MISSING':!close?'CLOSE_PENDING':!nextOpen?'NEXT_OPEN_PENDING':'COMPLETE'};
}
function m30Stats_(records){
  const output=[],types=['ALL','양봉','강양봉','준음봉','음봉','강음봉','준양봉','도지'];
  for(const strategy of ['A','B','C'])for(const code of ['ALL',...PG30.CODES])for(const type of types){
    const rows=records.filter(r=>(code==='ALL'||r.code===code)&&(type==='ALL'||r.first_pattern.type===type)&&Number.isFinite(r[strategy]));
    const values=rows.map(r=>r[strategy]),n=values.length;
    output.push(['PATTERN-'+strategy,code,'FIRST30',type,n,n?values.filter(v=>v>0).length/n:'',n?values.reduce((a,b)=>a+b,0)/n:'',
      n?values.reduce((a,b)=>a+b*M30.BUDGET,0):'',n?Math.min(0,...values):'','OBSERVED_ENTRY_AUCTION_ASSUMPTION_COST_0.32%']);
  }
  // These are post-close observations only, never a 15:20 decision input.
  for(const type of types){
    const values=records.filter(r=>(type==='ALL'||r.last_pattern.type===type)&&m30ValidBar_(r.last)&&Number.isFinite(r.gap)).map(r=>r.gap),n=values.length;
    output.push(['POSTHOC_LAST30_GAP','ALL','LAST30_POSTHOC',type,n,n?values.filter(v=>v>0).length/n:'',n?values.reduce((a,b)=>a+b,0)/n:'','',n?Math.min(0,...values):'','NOT_A_TRADING_STRATEGY']);
  }
  return output;
}
function m30ProjectPatterns_(ss,events){
  const days=Array.from(new Set(events.filter(e=>e.day>=M30.START).map(e=>e.day))).sort(),records=[];
  days.forEach(day=>PG30.CODES.forEach(c=>records.push(m30DailyRecord_(events,day,c))));
  const sh=ss.getSheetByName('CANDLE_PATTERN_DAILY'),stats=ss.getSheetByName('CANDLE_PATTERN_STATS');
  if(!sh||!stats)throw Error('M30_PATTERN_TABS_MISSING');
  // One-time exact snapshot of historical formulas and values. Never relabel
  // those M45 observations as M30, including their unfinished rows.
  if(!ss.getSheetByName('CANDLE_PATTERN_DAILY_M45_ARCHIVE')){
    const oldDaily=sh.getDataRange().getValues(),oldStats=stats.getDataRange().getValues();
    const archiveDaily=sh.copyTo(ss).setName('CANDLE_PATTERN_DAILY_M45_ARCHIVE'),archiveStats=stats.copyTo(ss).setName('CANDLE_PATTERN_STATS_M45_ARCHIVE');
    // Freeze evaluated M45 observations. Leaving copied array formulas linked
    // to the live DAILY tab would reinterpret archived statistics as M30.
    archiveDaily.getDataRange().clearContent();archiveStats.getDataRange().clearContent();
    archiveDaily.getRange(1,1,oldDaily.length,oldDaily[0].length).setValues(oldDaily);
    archiveStats.getRange(1,1,oldStats.length,oldStats[0].length).setValues(oldStats);
  }
  const header=['거래일','ETF코드','버전','첫30 시가','첫30 고가','첫30 저가','첫30 종가','첫30 유형','윗꼬리비율','아랫꼬리비율','실제관찰 진입가','진입수신시각','15:20전 판단시각','C 익일보유','15:30 검증종가','다음거래일','검증 익일시가','A 순수익률','B 순수익률','C 순수익률','사후 마지막30 유형','마지막봉 품질','사후 시가갭','자료상태','가상체결 가정','원자료JSON'];
  const rows=records.map(r=>[r.day,r.code,r.version,...['open','high','low','close'].map(k=>r.first&&r.first[k]||''),r.first_pattern.type,r.first_pattern.upper,r.first_pattern.lower,
    r.entry?r.entry.price:'',r.entry?r.entry.at:'',r.order?r.order.data.decision_at:'',r.order?r.order.data.C:'',r.close,r.next_day,r.next_open,r.A,r.B,r.C,
    r.last_pattern.type,r.last.quality||'NO_DATA',r.gap,r.status,'VIRTUAL_ONLY_ROUNDTRIP_0.32%',JSON.stringify(r)]);
  sh.getDataRange().clearContent();sh.getRange(1,1,1,26).setValues([header]);
  if(rows.length){sh.getRange(2,1,rows.length,2).setNumberFormat('@');sh.getRange(2,1,rows.length,26).setValues(rows.map(r=>r.map(v=>v==null?'':v)));}
  const summaries=m30Stats_(records);if(stats.getMaxRows()<summaries.length+1)stats.insertRowsAfter(stats.getMaxRows(),summaries.length+1-stats.getMaxRows());
  stats.getDataRange().clearContent();stats.getRange(1,1,1,10).setValues([['독립전략','ETF','분류기준','유형','표본수','승률','평균순수익률','누적손익(고정100만원)','최대단일손실률','체결/비용 가정']]);
  stats.getRange(2,1,summaries.length,10).setValues(summaries);
  stats.getRange(2,6,summaries.length,2).setNumberFormat('0.00%');stats.getRange(2,9,summaries.length,1).setNumberFormat('0.00%');stats.getRange(2,8,summaries.length,1).setNumberFormat('#,##0');
  return {records:records.length,stats:summaries.length};
}
function m30FetchSession_(day,props){
  const url=String(props.getProperty('SIGNAL_API_URL')||'').replace(/\/$/,''),token=props.getProperty('SIGNAL_API_TOKEN');
  if(!/^https:\/\//.test(url)||!token)throw Error('M30_API_NOT_CONFIGURED');
  const response=UrlFetchApp.fetch(url+'/paper-m30-session',{method:'post',contentType:'application/json',headers:{'X-StockBot-Token':token},
    payload:JSON.stringify({day:day,tickers:PG30.CODES}),muteHttpExceptions:true});
  if(response.getResponseCode()!==200)throw Error('M30_SESSION_HTTP_'+response.getResponseCode());
  const result=JSON.parse(response.getContentText());if(result.ok!==true||result.paper_only!==true||!Array.isArray(result.results))throw Error('M30_SESSION_CONTRACT');
  return result.results;
}
function m30Maintenance_(now,verified){
  if(!m30Active_(now))return {status:'BEFORE_CUTOVER'};
  if(e30Day_(now)>PG30.END)return {status:'EXPERIMENT_ENDED'};
  if(hmKrxSessionStatus_(now).isTradingDay!==true)return {status:'NO_TRADING_DAY'};
  if(m30QuarantinedDay_(e30Day_(now)))return {status:'DATA_INTEGRITY_HOLD'};
  const ss=m30Book_(),events=e30Read_(ss),props=PropertiesService.getScriptProperties(),day=e30Day_(now),ms=now.getTime(),fresh=m30AuctionPlans_(events,now);
  if(props.getProperty('M30_SEED_READY')!==M30.START&&(ms<e30At_(day,'09:25')||ms>=e30At_(day,'16:10'))){try{m30PrepareCutover_();}catch(e){console.error('M30_SEED_RETRY_ISOLATED '+String(e));}}
  const afterClose=ms>=e30At_(day,'16:10'),afterOpen=ms>=e30At_(day,'09:05')&&ms<e30At_(day,'09:30');
  if(afterClose||afterOpen){
    const candidateDays=(afterClose?[day]:Array.from(new Set(events.filter(e=>e.day<day&&e.day>=M30.START).map(e=>e.day))).sort().slice(-5)).filter(target=>PG30.CODES.some(code=>{
      const known=events.filter(e=>e.kind==='SESSION_DATA'&&e.day===target&&e.data.ticker===code).sort((a,b)=>e30Time_(b.at)-e30Time_(a.at))[0];
      if(!known)return true;const d=known.data;
      return afterClose?!(d.daily&&d.daily.quality==='CONFIRMED_DAILY_CLOSE'):
        (!d.next_trading_day||d.next_trading_day<=day)&&!(d.next_daily&&d.next_daily.quality==='OPEN_CONFIRMED');
    }));
    let attempts={};try{attempts=JSON.parse(props.getProperty('M30_SESSION_ATTEMPTS')||'{}');}catch(e){}
    Object.keys(attempts).forEach(k=>{if(ms-e30At_(k.slice(0,10),'00:00')>=7*86400000)delete attempts[k];});
    candidateDays.forEach(target=>{
      const key=day+'_'+target;const attempt=attempts[key]||{count:0,last:0};
      if(attempt.count>=3||ms-attempt.last<15*60000)return;
      attempts[key]={count:attempt.count+1,last:ms};props.setProperty('M30_SESSION_ATTEMPTS',JSON.stringify(attempts));
      const data=m30FetchSession_(target,props),received=new Date();
      data.filter(r=>r.schema===2&&r.bar_interval_minutes===30).forEach(r=>fresh.push(e30Event_(target,'SESSION_DATA','SESSION_DATA|'+target+'|'+r.ticker+'|'+received.toISOString(),r,received)));
    });
  }
  e30Append_(ss,fresh);
  const early=ms>=e30At_(day,'09:30')&&ms<=e30At_(day,'10:05');
  if(fresh.length||early||props.getProperty('M30_PATTERN_PROJECTED_DAY')!==day){
    const result=m30ProjectPatterns_(ss,e30Read_(ss));props.setProperty('M30_PATTERN_PROJECTED_DAY',day);return result;
  }
  return {status:'NO_PATTERN_CHANGE'};
}
function m30SafeMaintenance_(now,verified){
  try{const lock=LockService.getScriptLock();if(!lock.tryLock(500))return {status:'LOCKED'};
    try{return m30Maintenance_(now,verified);}finally{lock.releaseLock();}
  }catch(e){console.error('M30_PATTERN_ISOLATED '+String(e));return {status:'ERROR',reason:String(e)};}
}

function m30QuarantinedDay_(day){
  let days=[];try{days=JSON.parse(PropertiesService.getScriptProperties().getProperty('M30_QUARANTINED_DAYS')||'["2026-11-05"]');}catch(e){throw Error('M30_INVALID_QUARANTINE_CONFIG');}
  return days.indexOf(day)>=0;
}
function m30QuarantineFirstDay(){
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))throw Error('M30_QUARANTINE_LOCK');
  try{
    const p=PropertiesService.getScriptProperties();let days=JSON.parse(p.getProperty('M30_QUARANTINED_DAYS')||'["2026-11-05"]');
    if(days.indexOf('2026-11-05')<0)days.push('2026-11-05');p.setProperty('M30_QUARANTINED_DAYS',JSON.stringify(days));
    const now=new Date(),ss=e30Book_();e30Append_(ss,[e30Event_('2026-11-05','DATA_CORRECTION','SOURCE_WATERMARK_CORRECTION|2026-11-05',{scope:'E30_AND_CANDLE_PATTERNS',reason:'Yahoo first30 finalized later than wall clock; pre0953 observations and simulated fills invalid; raw ledger preserved; no retrofill',paper_only:true},now)]);
    const result=e30Repair_(ss,now,e30Config_());
    console.log(JSON.stringify({status:'FIRST_DAY_QUARANTINED',day:'2026-11-05',trades_computed:result.trades.length,raw_preserved:true,real_orders:0,mail:0}));
  }finally{lock.releaseLock();}
}
