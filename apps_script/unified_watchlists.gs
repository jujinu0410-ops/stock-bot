/** Three lists, one technical API, one persistent state, one Gmail dispatcher.
 * Existing hmTick5m owns scheduling; all old signal entry points delegate here.
 * UNIFIED_CONTROL B2 is PREVIEW or ACTIVE. Candidate ingestion stays independent.
 * No portfolio values, existing formulas or order APIs are changed.
 */
const UM = Object.freeze({
  VERSION:'three-lists-v2', TZ:'Asia/Seoul', CONTROL:'UNIFIED_CONTROL',
  BOOK:'15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw',
  YT:'10R11CnOs0Gi3Ffag8aWOw8sNZw0E_ZFlM5_qqSyO2xw',
  STATE_PREFIX:'UM2_', MAX_RUNTIME:260000, BATCH:2,
  LABELS:{WATCH:'· 관찰',BUY_SETUP:'△ 매수조짐',BUY_CONFIRMED:'▲ 매수확인',
    DOWN_EARLY:'▽ 하락조짐',DOWN_CONFIRMED:'▼ 하락확인',RISK:'⚠ 위험경고'}
});
function umMode_() {
  const sh=SpreadsheetApp.openById(UM.BOOK).getSheetByName(UM.CONTROL);
  return sh ? String(sh.getRange('B2').getValue()) : 'OFF';
}
function umEnabled_() { return umMode_()==='ACTIVE'; }
function umUseCoordinator_() { return ['ACTIVE','PREVIEW'].indexOf(umMode_())>=0; }
function umDay_(now) { return Utilities.formatDate(now,UM.TZ,'yyyy-MM-dd'); }
function umNumber_(x) { const n=Number(x);return x!==''&&x!=null&&isFinite(n)&&n>0?n:null; }
function umCode_(x) {
  const s=String(x||'').replace(/^(KRX:|KOSDAQ:|A)/,'');
  return /^\d{1,6}$/.test(s)?s.padStart(6,'0'):'';
}
function umOn_(x) { return x===true||/^(Y|TRUE|1)$/i.test(String(x)); }
function umRows_(book,name,width) {
  const sh=book.getSheetByName(name);if(!sh)throw new Error('MISSING_LIST '+name);
  return sh.getLastRow()>1?sh.getRange(2,1,Math.min(sh.getLastRow()-1,1000),width).getValues():[];
}
function umMerge_(rows) {
  const merged={};
  rows.forEach(function(row) {
    const code=umCode_(row.ticker);if(!code)return;
    const old=merged[code]||{ticker:code,sources:[],market:'UNKNOWN',refs:[]};
    ['name','sector_etf_code'].forEach(k=>{if(row[k]&&!old[k])old[k]=row[k];});
    if(row.market==='KOSPI'||row.market==='KOSDAQ')old.market=row.market;
    if(row.source==='HELD') {
      old.stop_price=row.stop_price;old.atr14=row.atr14;
      old.quantity=row.quantity;old.average_price=row.average_price;
    }
    old.suspended=old.suspended||row.suspended===true;
    old.inverse=old.inverse||row.inverse===true;
    if(row.is_etf)old.is_etf=true;
    if(old.sources.indexOf(row.source)<0)old.sources.push(row.source);
    old.refs.push({source:row.source,row:row.row});
    merged[code]=old;
  });
  return Object.keys(merged).map(k=>{
    merged[k].sources.sort();return merged[k];
  }).sort((a,b)=>{
    const rank=x=>x.ticker==='069500'?0:x.sources.indexOf('HELD')>=0?1:2;
    return rank(a)-rank(b)||a.ticker.localeCompare(b.ticker);
  });
}
function umUniverse_(now) {
  const book=SpreadsheetApp.openById(UM.BOOK), yt=SpreadsheetApp.openById(UM.YT), rows=[];
  umRows_(book,'MONITOR_CONFIG',20).forEach(function(r,i){
    if(!umOn_(r[9])||!umNumber_(r[3])||String(r[7])==='CLOSED')return;
    rows.push({source:'HELD',ticker:r[0],name:String(r[2]),market:String(r[1]),
      quantity:r[3],average_price:r[4],stop_price:umNumber_(r[6]),atr14:umNumber_(r[16]),
      suspended:/SUSPENDED/.test(String(r[7])+' '+String(r[14])),row:i+2});
  });
  const etfs=[];
  umRows_(book,'ETF_WATCHLIST',35).forEach(function(r,i){
    if(!umOn_(r[0]))return;const code=umCode_(r[3]);if(!code)return;etfs.push(code);
    const sector=String(r[2]);
    rows.push({source:'ETF',ticker:code,name:String(r[4]),is_etf:true,
      market:sector.indexOf('코스닥')>=0?'KOSDAQ':String(r[1])==='국내'?'KOSPI':'UNKNOWN',
      inverse:/인버스/.test(String(r[4])),row:i+2});
  });
  umRows_(yt,'CANDIDATES',26).forEach(function(r,i){
    const expiry=r[6] instanceof Date?umDay_(r[6]):String(r[6]).slice(0,10);
    if(!umOn_(r[0])||(expiry&&/^\d{4}-\d{2}-\d{2}$/.test(expiry)&&expiry<umDay_(now)))return;
    rows.push({source:'YOUTUBE',ticker:r[3],name:String(r[1]),market:String(r[2]),row:i+2});
  });
  // Explicit, reviewable mapping only. No fuzzy company-name/sector guesses.
  const mapSh=book.getSheetByName(UM.CONTROL), mapping={};
  if(mapSh&&mapSh.getLastRow()>8)mapSh.getRange(9,1,mapSh.getLastRow()-8,3).getDisplayValues().forEach(r=>{
    const code=umCode_(r[0]),sector=umCode_(r[2]);
    if(code)mapping[code]={market:r[1],sector_etf_code:etfs.indexOf(sector)>=0?sector:''};
  });
  const result=umMerge_(rows);
  result.forEach(r=>{
    if(mapping[r.ticker])Object.assign(r,mapping[r.ticker]);
    if(r.sector_etf_code===r.ticker)delete r.sector_etf_code;
    r.etf_universe=etfs;
  });
  return result;
}
function umCursorStart_(props,mode,slot) {
  // PREVIEW and ACTIVE may run in the same 5-minute slot; never let the
  // small preview cursor skip the priority head of a 201-symbol active scan.
  return props.getProperty('UM2_CURSOR_SLOT')===mode+':'+slot ?
    Number(props.getProperty('UM2_CURSOR')||0) : 0;
}
function umPlan_(item,result,old,now) {
  const day=umDay_(now),slot=Math.floor(now.getTime()/300000);
  const state=old&&old.day===day?JSON.parse(JSON.stringify(old)):{day:day,sent:{},count:0};
  const stage=String(result.stage||'WATCH');
  if(state.slot!==slot) {
    state.count=state.stage===stage&&state.slot===slot-1?Math.min(2,(state.count||0)+1):1;
    state.stage=stage;state.slot=slot;
  }
  state.lastResult={stage:stage,price:result.live_price||null,at:now.toISOString(),
    sources:item.sources,reasons:(result.reasons||[]).slice(-5)};
  if(item.stop_price&&result.live_price>item.stop_price)delete state.sent.STOP_BREACH;
  const candidates=[];
  if(result.send_allowed===true)(result.events||[]).forEach(function(e){
    // One shared calculation, source-aware notifications only:
    // HELD -> both directions / stop / OBV risk; other watchlists -> BUY only.
    const isHeld=(item.sources||[]).indexOf('HELD')>=0;
    if(!isHeld && !(
      (e.key==='BUY_SETUP' && e.stage==='BUY_SETUP') ||
      (e.key==='BUY_CONFIRMED' && e.stage==='BUY_CONFIRMED')
    ))return;
    const key=e.key+(e.key==='MARKET_CRASH'?':'+e.level:'');
    if(state.sent[key])return;
    if(e.key==='STOP_BREACH'&&state.lastStopAt&&now.getTime()-Date.parse(state.lastStopAt)<30*60000)return;
    const urgent=e.stage==='RISK'||e.key==='ATR_DOWN_1_0';
    if(!urgent&&state.count<2)return;
    // An earlier crash level must never be resent after escalation.
    if(e.key==='MARKET_CRASH'&&Number(state.crashLevel||0)>=Number(e.level))return;
    candidates.push(Object.assign({},e,{stateKey:key}));
  });
  return {item:item,result:result,state:state,events:candidates};
}
function umCommit_(plan,now,messageId,eventId) {
  plan.events.forEach(function(e){
    plan.state.sent[e.stateKey]={at:now.toISOString(),messageId:messageId,eventId:eventId};
    if(e.key==='MARKET_CRASH')plan.state.crashLevel=e.level;
    if(e.key==='STOP_BREACH')plan.state.lastStopAt=now.toISOString();
  });
  return plan.state;
}
function umLoadState_(props,ticker) {
  const raw=props.getProperty(UM.STATE_PREFIX+ticker);
  if(!raw)return null;
  try{return JSON.parse(raw);}catch(e){throw new Error('CORRUPT_UNIFIED_STATE '+ticker);}
}
function umLegacySeed_(book,props,now) {
  const day=umDay_(now),map={};
  const mark=(code,key)=>{if(!code||!key)return;const x=map[code]||(map[code]={day:day,sent:{},count:0});x.sent[key]={legacy:true};};
  const aliases={SIGNAL_CONF_UP:'BUY_CONFIRMED',SIGNAL_EARLY_UP:'BUY_SETUP',BASE_PAIR_UP:'BUY_SETUP',
    SIGNAL_CONF_DOWN:'DOWN_CONFIRMED',SIGNAL_EARLY_DOWN:'DOWN_EARLY',BASE_PAIR_DOWN:'DOWN_EARLY',
    STOP_BREACH_INITIAL:'STOP_BREACH',STOP_BREACH:'STOP_BREACH',OBV15_WARNING:'OBV15_WARNING',
    SELL_PREP_WARNING:'SELL_PREP_WARNING',ATR_DOWN_1_0:'ATR_DOWN_1_0'};
  umRows_(book,'ALERT_LOG',11).forEach(r=>{
    if(r[0] instanceof Date&&umDay_(r[0])===day)String(r[3]).split('|').forEach(k=>mark(umCode_(r[1]),aliases[k]));
  });
  const etf=JSON.parse(props.getProperty('ETF_WATCH_SIGNAL_STATE_V2')||'{}');
  if(etf.day===day)Object.keys(etf.items||{}).forEach(code=>Object.keys(etf.items[code].sent||{}).forEach(k=>{
    if(etf.items[code].sent[k])mark(code,{'▲':'BUY_CONFIRMED','△':'BUY_SETUP','▼':'DOWN_CONFIRMED','▽':'DOWN_EARLY'}[k]);
  }));
  const risk=JSON.parse(props.getProperty('ETF_MARKET_RISK_KOSPI200_V1')||'{}');
  if(risk.day===day&&Number(risk.sentLevel)>0){
    mark('069500','MARKET_CRASH:'+risk.sentLevel);map['069500'].crashLevel=Number(risk.sentLevel);
  }
  return map;
}
function umDisplay_(item,result) {
  // Immutable source sheets: ETF_WATCHLIST!K and REGIME!Q:R contain formulas,
  // while CANDIDATES!W:Y stores historical analysis. NEVER overwrite them.
  // umRecord_ writes all current unified verdicts into UNIFIED_DECISIONS.
  return {ticker:item.ticker,stage:result.stage,recordedBy:'umRecord_'};
}
function umEventId_(plan,now) {
  return ['U2',umDay_(now),plan.result.session||'KRX_SESSION',plan.item.ticker,
    plan.events.map(e=>e.stateKey).sort().join('+')].join('_');
}
function umEscape_(s) { return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function umMessage_(plans,now) {
  const lines=['주식 통합신호 · '+Utilities.formatDate(now,UM.TZ,'MM-dd HH:mm'),
    '같은 종목은 한 번만 판단합니다. 출처는 보유·ETF·유튜브 태그로 표시합니다.',''];
  const metadata=[];
  plans.forEach(function(p){
    const r=p.result,item=p.item,label=UM.LABELS[r.stage]||UM.LABELS.WATCH;
    const id=umEventId_(p,now),adjust=Number(r.confidence_adjustment||0);
    lines.push(label+' '+item.name+' ('+item.ticker+')',
      '현재가 '+Number(r.live_price).toLocaleString('ko-KR')+'원 · '+(100*Number(r.live_change_pct||0)).toFixed(2)+'%',
      '출처: '+item.sources.join(' + '),
      '근거: '+p.events.map(e=>e.reason).join(' / '),
      '시장·섹터 보정: '+(adjust>0?'+1 적극':adjust<0?'-1 보수':'0 중립')+' · 기술단계 '+label,
      '손절선: '+(item.stop_price||'미설정')+' · ATR14: '+(r.atr14?Math.round(r.atr14):'미확인'),
      '검증: '+String(r.live_price_source||'미확인')+' · 45분봉 '+String(r.m45&&r.m45.completed_bar||'미확인'),
      '이벤트 ID: '+id,'');
    metadata.push({id:id,source:item.sources.join('+'),ticker:item.ticker,name:item.name,
      stage:label,price:r.live_price,session:r.session||'KRX_SESSION'});
  });
  // Visible machine-readable footer makes replay exact even if Gmail flattens HTML.
  lines.push('UNIFIED_EVENTS_JSON='+JSON.stringify(metadata));
  const body=lines.join('\n');
  return {subject:'[주식 통합신호] '+plans.length+'종목 · '+Utilities.formatDate(now,UM.TZ,'MM-dd HH:mm'),
    body:body,htmlBody:'<div style="font-family:Arial,sans-serif;line-height:1.65;white-space:pre-wrap">'+umEscape_(body)+'</div>'};
}
function umRecoverDraft_(pending) {
  // Provider acceptance and local-state write cannot be one transaction. Reconcile
  // by deterministic event IDs before retrying a persisted draft after a crash.
  const needle=pending.ids[0];
  const threads=GmailApp.search('in:sent "'+needle+'" newer_than:2d',0,10);
  for(const th of threads)for(const m of th.getMessages()){
    if(pending.ids.every(id=>m.getPlainBody().indexOf(id)>=0))return m;
  }
  if(pending.phase==='SENDING'&&Date.now()-pending.created<120000)throw new Error('SEND_RECONCILIATION_PENDING');
  const draft=GmailApp.getDraft(pending.draftId);
  if(!draft)throw new Error('SEND_STATUS_UNCERTAIN');
  return draft.send();
}
function umDispatch_(plans,props,now) {
  if(!plans.length)return {sent:0};
  const recipient=hmAlertRecipient_(),msg=umMessage_(plans,now),ids=plans.map(p=>umEventId_(p,now));
  const pendingKey='UM2_PENDING';let pending=JSON.parse(props.getProperty(pendingKey)||'null'),sent;
  if(pending)throw new Error('PENDING_DELIVERY_MUST_RECOVER_FIRST');
  const draft=GmailApp.createDraft(recipient,msg.subject,msg.body,{htmlBody:msg.htmlBody,name:'주식 통합감시'});
  pending={ids:ids,draftId:draft.getId(),created:Date.now(),phase:'SENDING',
    plans:plans.map(p=>({item:p.item,result:p.result,state:p.state,events:p.events})),at:now.toISOString()};
  // Full data stays in a sheet so a single ScriptProperty never exceeds 9 KB.
  const sh=SpreadsheetApp.openById(UM.BOOK).getSheetByName(UM.CONTROL);
  const pendingRows=pending.plans.map(p=>[JSON.stringify(p)]);
  sh.getRange('E1').setValue('PENDING_DELIVERY_JSON');
  sh.getRange(2,5,pendingRows.length,1).setValues(pendingRows);
  delete pending.plans;pending.count=pendingRows.length;
  props.setProperty(pendingKey,JSON.stringify(pending));
  sent=draft.send();
  umFinishDelivery_(plans,sent.getId(),props,now);
  props.deleteProperty(pendingKey);
  sh.getRange(2,5,pendingRows.length,1).clearContent();
  return {sent:plans.length,messageId:sent.getId()};
}
function umFinishDelivery_(plans,messageId,props,now) {
  // A successful Gmail send is persisted before optional/audit operations.
  const values={};
  plans.forEach(p=>{values[UM.STATE_PREFIX+p.item.ticker]=JSON.stringify(umCommit_(p,now,messageId,umEventId_(p,now)));});
  props.setProperties(values);
  const ss=SpreadsheetApp.openById(UM.BOOK);
  const log=ss.getSheetByName('ALERT_LOG'),existing=log.getLastRow()>1?
    log.getRange(Math.max(2,log.getLastRow()-500),11,Math.min(500,log.getLastRow()-1),1).getDisplayValues().flat().join('\n'):'';
  const rows=[];
  plans.forEach(p=>{
    const id=umEventId_(p,now);if(existing.indexOf(id)>=0)return;
    rows.push([now,p.item.ticker,p.item.name,p.events.map(e=>e.key).join('|'),p.result.stage,
      p.result.live_price,p.result.live_change_pct||0,p.item.stop_price||'','',now,
      id+' | '+p.item.sources.join('+')+' | '+p.events.map(e=>e.reason).join(' / ')+' | GmailId='+messageId]);
  });
  if(rows.length)log.getRange(log.getLastRow()+1,1,rows.length,11).setValues(rows);
}
function umResumeDelivery_(props) {
  const raw=props.getProperty('UM2_PENDING');if(!raw)return;
  const pending=JSON.parse(raw),sh=SpreadsheetApp.openById(UM.BOOK).getSheetByName(UM.CONTROL);
  const plans=sh.getRange(2,5,pending.count,1).getValues().map(r=>JSON.parse(r[0]));
  const message=umRecoverDraft_(pending);
  umFinishDelivery_(plans,message.getId(),props,new Date(pending.at));
  props.deleteProperty('UM2_PENDING');sh.getRange(2,5,pending.count,1).clearContent();
}
function umRequest_(item,props) {
  const url=String(props.getProperty('SIGNAL_API_URL')||'').replace(/\/$/,''),token=props.getProperty('SIGNAL_API_TOKEN');
  if(!url||!token)throw new Error('UNIFIED_API_NOT_CONFIGURED');
  const data=Object.assign({},item);delete data.refs;delete data.quantity;delete data.average_price;
  return {url:url+'/unified-scan',method:'post',contentType:'application/json',
    headers:{'X-StockBot-Token':token},payload:JSON.stringify(data),muteHttpExceptions:true};
}
function umRecord_(rows) {
  if(!rows.length)return;
  const ss=SpreadsheetApp.openById(UM.BOOK),name='UNIFIED_DECISIONS';
  const sh=ss.getSheetByName(name)||ss.insertSheet(name);
  if(!sh.getLastRow()){
    sh.appendRow(['Timestamp','Ticker','Sources','Stage','SendAllowed','Price','Reasons','Engine','EventKeys']);
    sh.setFrozenRows(1);sh.hideSheet();
  }
  sh.getRange(sh.getLastRow()+1,1,rows.length,9).setValues(rows);
  if(sh.getLastRow()>3001)sh.deleteRows(2,sh.getLastRow()-3001);
}
function umNotifyFailure_(err,props,now) {
  const last=Number(props.getProperty('UM2_LAST_ERROR_MAIL')||0);
  if(now.getTime()-last<3600000)return;
  MailApp.sendEmail({to:hmAlertRecipient_(),subject:'[주식 통합감시 장애] 점검 필요',
    body:'통합감시 실행 또는 발송이 완료되지 않았습니다. 미발송 경보는 다음 실행에서 재시도합니다.\n'+String(err).slice(0,500)});
  props.setProperty('UM2_LAST_ERROR_MAIL',String(now.getTime()));
}
function umTick5m() {
  const now=new Date(),start=now.getTime(),mode=umMode_();
  if(['ACTIVE','PREVIEW'].indexOf(mode)<0)return {status:'OFF'};
  if(!hmIsMarketWindow_(now))return {status:'OUTSIDE_SESSION'};
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'LOCKED'};
  const props=PropertiesService.getScriptProperties(),slot=String(Math.floor(start/300000));
  try {
    if(mode==='ACTIVE')umResumeDelivery_(props);
    if(props.getProperty('UM2_COMPLETED_'+mode)===slot)return {status:'SLOT_DONE'};
    const book=SpreadsheetApp.openById(UM.BOOK),all=umUniverse_(now),control=book.getSheetByName(UM.CONTROL);
    const universe=mode==='PREVIEW'?all.filter(x=>['069500','114800','373220','103590','001430','490590','007660'].indexOf(x.ticker)>=0):all;
    const legacy=mode==='ACTIVE'?umLegacySeed_(book,props,now):{};
    let evaluated=0,sent=0,errors=0;const audit=[],allPlans=[];
    // Resume a long tick from its cursor; priority risks always head a new slot.
    let cursor=Number(props.getProperty('UM2_CURSOR_SLOT')===mode+':'+slot?props.getProperty('UM2_CURSOR')||0:0);
    for(;cursor<universe.length;cursor+=UM.BATCH) {
      if(Date.now()-start>UM.MAX_RUNTIME)break;
      const batch=universe.slice(cursor,cursor+UM.BATCH),reqs=batch.map(x=>umRequest_(x,props)),responses=UrlFetchApp.fetchAll(reqs),plans=[];
      // Retry transport failures within this 5-minute pass; no state is committed
      // for unavailable tickers and the next pass can retry them again.
      for(let attempt=0;attempt<2;attempt++){
        const retryIndices=[];
        responses.forEach((r,i)=>{if(r.getResponseCode()===429||r.getResponseCode()>=500)retryIndices.push(i);});
        if(!retryIndices.length)break;
        Utilities.sleep(attempt===0?1500:4200);
        const retried=UrlFetchApp.fetchAll(retryIndices.map(i=>reqs[i]));
        retryIndices.forEach((i,j)=>{responses[i]=retried[j];});
      }
      batch.forEach(function(item,i){
        let r;try{r=JSON.parse(responses[i].getContentText());}catch(e){r={};}
        if(responses[i].getResponseCode()!==200||r.ok!==true||r.engine_version!==UM.VERSION){
          errors++;audit.push([now,item.ticker,item.sources.join('+'),'ERROR',false,'','API_'+responses[i].getResponseCode(),UM.VERSION,'']);return;
        }
        evaluated++;
        audit.push([now,item.ticker,item.sources.join('+'),r.stage,r.send_allowed,r.live_price||'',(r.reasons||[]).join('|'),r.engine_version,(r.events||[]).map(e=>e.key).join('|')]);
        if(mode==='ACTIVE'){
          const p=umPlan_(item,r,umLoadState_(props,item.ticker)||legacy[item.ticker],now);
          if(p.events.length)plans.push(p);
          else props.setProperty(UM.STATE_PREFIX+item.ticker,JSON.stringify(p.state));
          umDisplay_(item,r);
        }
      });
      // Collect the pass's alerts before mailing. One email per 4 symbols
      // would flood the inbox during market-wide sell-offs.
      if(plans.length)allPlans.push.apply(allPlans,plans);
      props.setProperties({UM2_CURSOR_SLOT:mode+':'+slot,UM2_CURSOR:String(cursor+UM.BATCH)});
    }
    if(mode==='ACTIVE'&&allPlans.length){
      // Keep Gmail's draft/receipt reconciliation and per-ticker event ids.
      // Bounded digest also stays well below Apps Script 9-KB property limits.
      for(let i=0;i<allPlans.length;i+=20)
        sent+=umDispatch_(allPlans.slice(i,i+20),props,now).sent;
    }
    umRecord_(audit);
    const done=cursor>=universe.length;
    // A slot containing HTTP errors is NOT a successful full-universe check.
    if(done&&!errors)props.setProperty('UM2_COMPLETED_'+mode,slot);
    if(errors){props.deleteProperty('UM2_CURSOR_SLOT');props.deleteProperty('UM2_CURSOR');}
    control.getRange('B3:B7').setValues([[now],[mode+(done&&!errors?'_COMPLETE':'_PARTIAL')],
      [JSON.stringify({universe:universe.length,evaluated:evaluated,sent:sent,errors:errors,cursor:cursor})],[UM.VERSION],[errors?'API_ERRORS':'OK']]);
    if(errors&&mode==='ACTIVE')umNotifyFailure_('API 확인 실패 '+errors+'개 종목',props,now);
    if(done&&!errors&&mode==='ACTIVE'){
      // Keep original trigger installations through cutover verification.
      // Their entrypoints delegate to umTick5m in ACTIVE and existing slot/lock
      // gates prevent duplicate sends. Deletion requires a separate verified
      // maintenance step, never the first successful active run.
      console.log('UNIFIED_ACTIVE_OK original triggers preserved for rollback');
    }
    return {status:done&&!errors?'OK':'PARTIAL',mode:mode,universe:universe.length,evaluated:evaluated,sent:sent,errors:errors};
  }catch(err){
    if(mode==='ACTIVE')try{umNotifyFailure_(err,props,now);}catch(notifyErr){console.error(String(notifyErr));}
    throw err;
  }finally{lock.releaseLock();}
}
