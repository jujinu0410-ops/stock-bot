/** Three lists, one technical API, one persistent state, one Gmail dispatcher.
 * Existing hmTick5m owns scheduling; all old signal entry points delegate here.
 * UNIFIED_CONTROL B2 is PREVIEW or ACTIVE. Candidate ingestion stays independent.
 * No portfolio values, existing formulas or order APIs are changed.
 */
const UM = Object.freeze({
  VERSION:'three-lists-v2', TZ:'Asia/Seoul', CONTROL:'UNIFIED_CONTROL',
  BOOK:'15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw',
  YT:'10R11CnOs0Gi3Ffag8aWOw8sNZw0E_ZFlM5_qqSyO2xw',
  STATE_PREFIX:'UM2_', MAX_RUNTIME:260000, BATCH:12, GROUP_SIZE:6,
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
const UM_PRIORITY_INDEX_ETFS = Object.freeze(['069500','114800','251340']);
const UM_MARKET_MAIL_NAMES = Object.freeze({
  '069500':'코스피', '229200':'코스닥',
  '114800':'인버스', '251340':'코스닥 인버스'
});
function umIsMarketMail_(item) {
  return !!(item && (item.sources||[]).indexOf('ETF')>=0 &&
    UM_MARKET_MAIL_NAMES[item.ticker]);
}
function umIsPriority_(item) {
  if(!item)return false;
  if(item.sources && item.sources.indexOf('HELD')>=0)return true;
  return UM_PRIORITY_INDEX_ETFS.indexOf(item.ticker)>=0;
}
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
    const rank=x=>x.ticker==='069500'?0:umIsPriority_(x)?1:2;
    return rank(a)-rank(b)||a.ticker.localeCompare(b.ticker);
  });
}
const CANONICAL_V4_STOPS = Object.freeze({
  '058470': 60000,
  '138080': 25300,
  '203650': 2180,
  '253590': 14040,
  '295310': 57400,
  '298040': 1650000
});

function umUniverse_(now) {
  const book=SpreadsheetApp.openById(UM.BOOK), yt=SpreadsheetApp.openById(UM.YT), rows=[];
  const confSheet = book.getSheetByName('MONITOR_CONFIG');
  umRows_(book,'MONITOR_CONFIG',20).forEach(function(r,i){
    if(!umOn_(r[9])||!umNumber_(r[3])||String(r[7])==='CLOSED')return;
    const ticker = String(r[0]||'').padStart(6, '0');
    let stop = umNumber_(r[6]);
    if ((stop === null || stop === 0) && CANONICAL_V4_STOPS[ticker]) {
      stop = CANONICAL_V4_STOPS[ticker];
      try {
        if (confSheet) {
          confSheet.getRange(i + 2, 7).setValue(stop);
          confSheet.getRange(i + 2, 13).setValue('V4_CANONICAL_CONFIRMED');
        }
      } catch (e) {
        Logger.log('AUTO_FILL_STOP_PRICE_WARN ' + ticker + ': ' + e);
      }
    }
    rows.push({source:'HELD',ticker:ticker,name:String(r[2]),market:String(r[1]),
      quantity:r[3],average_price:r[4],stop_price:stop,atr14:umNumber_(r[16]),
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
  const isPriority=umIsPriority_(item);
  const isConsecutive=isPriority ?
    (state.slot===slot-1) :
    (state.slot!=null && slot>state.slot && slot-state.slot<=5);
  if(state.slot!==slot) {
    state.count=state.stage===stage&&isConsecutive?Math.min(2,(state.count||0)+1):1;
    state.stage=stage;state.slot=slot;
  }
  state.lastResult={stage:stage,price:result.live_price||null,at:now.toISOString(),
    sources:item.sources,reasons:(result.reasons||[]).slice(-5)};
  if(item.stop_price&&result.live_price>item.stop_price)delete state.sent.STOP_BREACH;
  const candidates=[];
  if(result.send_allowed===true)(result.events||[]).forEach(function(e){
    // One shared calculation, source-aware notifications only:
    // HELD -> both directions / stop / OBV risk; market ETFs -> both directions;
    // all other watchlists keep their BUY-only policy.
    const isHeld=(item.sources||[]).indexOf('HELD')>=0;
    const isMarketProxy=item.ticker==='069500'&&(item.sources||[]).indexOf('ETF')>=0;
    const isMarketMail=umIsMarketMail_(item);
    if(!isHeld && !(
      (e.key==='BUY_SETUP' && e.stage==='BUY_SETUP') ||
      (e.key==='BUY_CONFIRMED' && e.stage==='BUY_CONFIRMED') ||
      (isMarketProxy && (e.key==='MARKET_CRASH'||e.key==='MARKET_WEAK_EARLY')) ||
      (isMarketMail && (e.key==='DOWN_EARLY'||e.key==='DOWN_CONFIRMED'||
        e.key==='OBV15_WARNING'||e.key==='SELL_PREP_WARNING'))
    ))return;
    const key=e.key+(e.key==='MARKET_CRASH'?':'+e.level:'');
    if(state.sent[key])return;
    if(isMarketMail&&e.key==='DOWN_EARLY'&&
      (state.sent.MARKET_WEAK_EARLY||candidates.some(x=>x.key==='MARKET_WEAK_EARLY')))return;
    if(e.key==='STOP_BREACH'&&state.lastStopAt&&now.getTime()-Date.parse(state.lastStopAt)<30*60000)return;
    const urgent=e.stage==='RISK'||e.key==='ATR_DOWN_1_0';
    if(!urgent&&state.count<2)return;
    // An earlier crash level must never be resent after escalation.
    if(e.key==='MARKET_CRASH'&&Number(state.crashLevel||0)>=Number(e.level))return;
    candidates.push(Object.assign({},e,{stateKey:key}));
  });
  // Observe a NEW 45m bullish stack for every monitored source (HELD, ETF,
  // YOUTUBE), including WATCH/neutral names. This is an MTS inspection alert,
  // not a buy approval; engine buy rules, JEV and 2-pass ▲ remain unchanged.
  // Only completed current-day 45m bars and existing quote data are used.
  const m45=result.m45||{},jev=result.jev_gate||{},earlyKey='VWAP45_BULL_STACK_EARLY';
  const price=Number(result.live_price),v9=Number(m45.vwap9),v26=Number(m45.vwap26);
  const fresh=m45.available===true&&
    String(m45.completed_bar||'').slice(0,10)===day&&
    String(result.live_price_source||'')==='KIWOOM_KA10001'&&
    isFinite(price)&&price>0&&isFinite(v9)&&v9>0&&isFinite(v26)&&v26>0;
  const stacked=fresh&&price>v9&&v9>v26&&m45.gold===true;
  const previouslySeen=state.vwap45StackValid===true;
  const wasStacked=state.vwap45Stacked===true;
  const freshCross=previouslySeen&&!wasStacked&&stacked;
  const isBuyStage=stage==='BUY_SETUP'||stage==='BUY_CONFIRMED';
  const buyGateOK=!isBuyStage||(result.send_allowed===true&&jev.status==='OK'&&jev.decision==='SEND');
  const firstStrong=stage==='BUY_CONFIRMED'&&result.send_allowed===true&&state.count===1&&
    jev.status==='OK'&&jev.decision==='SEND'&&stacked;
  const unsafe=stage==='RISK'||(item.stop_price&&price<=item.stop_price)||
    (result.reasons||[]).some(x=>/ABOVE_OVERHEAT_LIMIT|RECENT_1M_WEAKENING|LIVE_MINUTES_UNAVAILABLE_OR_STALE/.test(String(x)));
  // Remember valid observations, including WATCH and non-signal runs, so a
  // pre-existing stack at first observation does not flood the user's inbox.
  if(fresh) {
    state.vwap45StackValid=true;
    state.vwap45Stacked=stacked;
    state.vwap45LastChecked=now.toISOString();
  }
  if((freshCross||firstStrong)&&buyGateOK&&!unsafe&&
    candidates.length===0&&
    !state.sent[earlyKey]&&!state.sent.BUY_SETUP&&!state.sent.BUY_CONFIRMED) {
    candidates.push({key:earlyKey,stage:'WATCH',level:1,
      reason:'45분봉 정배열 신규 포착 · 현재가 > VWAP9 > VWAP26 · MTS 확인용',
      stateKey:earlyKey});
  }
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
// One glance first: coloured ▲/△ (up) and ▼/▽ (down).
// Keep the existing plain-text machine footer for downstream replay and
// Gmail getPlainBody() delivery reconciliation; never show it in HTML.
function umSignalGlyph_(stage) {
  if(stage==='BUY_CONFIRMED')return {mark:'▲',color:'#c62828'};
  if(stage==='BUY_SETUP')return {mark:'△',color:'#c62828'};
  if(stage==='DOWN_EARLY')return {mark:'▽',color:'#1565c0'};
  return {mark:'▼',color:'#1565c0'};
}
function umDisplayStage_(p) {
  // The underlying engine can be BUY_CONFIRMED while the emitted event is
  // deliberately only an early △ observation awaiting the next evaluation.
  return p.events&&p.events.length===1&&p.events[0].key==='VWAP45_BULL_STACK_EARLY'?
    'BUY_SETUP':p.result.stage;
}
function umSignalBrief_(p,now) {
  const r=p.result,item=p.item,keys=(p.events||[]).map(e=>e.key);
  const stop=Number(item.stop_price||0);
  const atr=Number(r.atr14||0);
  const price=Number(r.live_price||0);
  const inverseInfo=r.jev_gate&&r.jev_gate.status==='EARLY_MARKET_INFO';
  let basis='';
  if(keys.indexOf('VWAP45_BULL_STACK_EARLY')>=0)
    basis='45분봉 정배열 새로 포착 · 현재가 > VWAP9 > VWAP26 · MTS 관심종목';
  else if(keys.indexOf('STOP_BREACH')>=0)
    basis='실시간 손절선 '+stop.toLocaleString('ko-KR')+'원 도달·이탈';
  else if(keys.indexOf('MARKET_CRASH')>=0)
    basis='시장 급락 위험 · 실시간 지수 대표 ETF';
  else if(keys.indexOf('MARKET_WEAK_EARLY')>=0)
    basis='시장 하락 조짐 · 실시간 지수 대표 ETF';
  else if(keys.indexOf('OBV15_WARNING')>=0)
    basis='45분봉 OBV 지속 약세 · 단기 매도 경고';
  else if(keys.indexOf('SELL_PREP_WARNING')>=0)
    basis='45분봉 약세 · 단기 매도 경고';
  else if(keys.indexOf('ATR_DOWN_1_0')>=0)
    basis='전일 기준 1 ATR 이상 하락 · 실시간 시세';
  else if(inverseInfo)
    basis='인버스 상승 조짐 · 기초지수 하락 확인 (매수확정 아님)';
  else if(r.stage==='BUY_CONFIRMED')
    basis='상승 확인 · 기술지표/45분봉 판정';
  else if(r.stage==='BUY_SETUP')
    basis='상승 조짐 · 기술지표/45분봉 관찰';
  else if(r.stage==='DOWN_CONFIRMED'||r.stage==='RISK')
    basis='하락 확인 · 일봉 기술지표';
  else if(r.stage==='DOWN_EARLY')
    basis='하락 조짐 · 일봉 기술지표';
  else
    basis='기술 신호';
  // Older completed bars are never presented as today's confirmation.
  const bar=String(r.m45&&r.m45.completed_bar||'');
  if(keys.indexOf('STOP_BREACH')<0 && bar.slice(0,10)===umDay_(now) &&
      !/45분봉/.test(basis))basis+=' · 45분봉 '+bar.slice(11,16)+' 참조';
  return basis;
}
function umSignalNews_(p) {
  const r=p.result||{}, context=r.market_context||{};
  const news=Array.isArray(r.news)?r.news:(Array.isArray(context.news)?context.news:[]);
  return news.filter(n=>n&&typeof n.title==='string'&&n.title.trim())
    .slice(0,2).map(n=>n.title.trim());
}
function umQuoteTime_(value) {
  if(typeof value!=='string'||
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?(?:Z|[+-]\d\d:\d\d)$/.test(value))return NaN;
  const at=Date.parse(value),zone=value.slice(-6);
  const offset=value.endsWith('Z')?0:(zone.charAt(0)==='-'?-1:1)*
    (Number(zone.slice(1,3))*60+Number(zone.slice(4,6)));
  // Reject normalized invalid dates (e.g. February 30).
  return isFinite(at)&&new Date(at+offset*60000).toISOString().slice(0,19)===value.slice(0,19)?at:NaN;
}
function umCollectMarketQuote_(round,item,r,receivedAt) {
  // Optional display-only data. Never fetch, persist or replace a signal verdict.
  // live_quote_time must be the provider's quote timestamp. The current API
  // omits it: fail closed, never substitute scan as_of / receipt / 45m bar time.
  try {
    if(!round||!item||(item.sources||[]).indexOf('ETF')<0)return;
    delete round.quotes[item.ticker];
    if(!isFinite(round.startedAt)||!isFinite(receivedAt)||receivedAt<round.startedAt)return;
    if(['069500','229200','114800','251340','091160','305720'].indexOf(item.ticker)<0)return;
    const at=umQuoteTime_(r.live_quote_time),asOf=umQuoteTime_(r.as_of);
    if(r.ok!==true||r.engine_version!==UM.VERSION||r.ticker!==item.ticker||
      r.live_price_source!=='KIWOOM_KA10001'||r.session!=='KRX_SESSION'||
      typeof r.live_price!=='number'||!isFinite(r.live_price)||r.live_price<=0||
      typeof r.live_change_pct!=='number'||!isFinite(r.live_change_pct)||
      !isFinite(at)||!isFinite(asOf)||at<round.startedAt||asOf<round.startedAt||
      at>receivedAt||asOf>receivedAt||receivedAt-round.startedAt>UM.MAX_RUNTIME)return;
    round.quotes[item.ticker]={at:at,asOf:asOf,price:r.live_price,pct:r.live_change_pct};
  } catch(e) { /* Optional context cannot interrupt technical mail delivery. */ }
}
function umMarketSituation_(p,round,now) {
  try {
    if(!round||round.startedAt!==now.getTime())return '';
    const q=round.quotes,own=q[p.item.ticker],r=p.result;
    if(!own||own.at!==umQuoteTime_(r.live_quote_time)||own.asOf!==umQuoteTime_(r.as_of)||
      own.price!==r.live_price||own.pct!==r.live_change_pct)return '';
    const same=code=>q[code]&&q[code].at===own.at;
    const pair=(a,b,an,bn,joined)=>{
      if(!same(a)||!same(b))return '';
      const x=q[a].pct,y=q[b].pct;
      if(x>0&&y>0)return joined+' 동반 상승';
      if(x<0&&y<0)return joined+' 동반 하락';
      if(x>0&&y<0)return an+' 상승·'+bn+' 하락';
      if(x<0&&y>0)return an+' 하락·'+bn+' 상승';
      if(x===y)return '';
      return x>y?an+'가 '+bn+'보다 강세':bn+'가 '+an+'보다 강세';
    };
    let text='';
    if(p.item.ticker==='114800'||p.item.ticker==='251340') {
      const kosdaq=p.item.ticker==='251340';
      text=pair(kosdaq?'229200':'069500',p.item.ticker,
        kosdaq?'코스닥 ETF':'코스피 ETF','인버스',kosdaq?'코스닥 ETF·인버스':'코스피 ETF·인버스');
    } else {
      text=pair('069500','229200','코스피 ETF','코스닥 ETF','코스피·코스닥 ETF')||
        pair('091160','305720','반도체 ETF','2차전지 ETF','반도체·2차전지 ETF');
    }
    return text.length>=15&&text.length<=25?text:'';
  } catch(e) { return ''; }
}
function umMarketSubject_(p,now,round) {
  const item=p.item,stage=umDisplayStage_(p);
  const titleStage=(p.events||[]).some(e=>e.key==='VWAP45_BULL_STACK_EARLY')?
    '45분봉 정배열 관심':({BUY_SETUP:'상승조짐',BUY_CONFIRMED:'상승확인',
      DOWN_EARLY:'하락조짐',DOWN_CONFIRMED:'하락확인',RISK:'위험경고'}[stage]||'기술신호');
  const context=umMarketSituation_(p,round,now);
  return '[주식 통합신호] '+umSignalGlyph_(stage).mark+' '+
    UM_MARKET_MAIL_NAMES[item.ticker]+' '+titleStage+
    (context?' | '+context:'')+' · '+Utilities.formatDate(now,UM.TZ,'MM-dd HH:mm');
}
function umMessage_(plans,now,round) {
  const human=[],html=[],metadata=[],marks=[];
  plans.forEach(function(p){
    const r=p.result,item=p.item,displayStage=umDisplayStage_(p);
    const label=(p.events||[]).some(e=>e.key==='VWAP45_BULL_STACK_EARLY')?
      '45분봉 정배열 관심':(UM.LABELS[displayStage]||UM.LABELS.WATCH);
    const visual=umSignalGlyph_(displayStage),id=umEventId_(p,now);
    const price=Number(r.live_price||0).toLocaleString('ko-KR')+'원';
    const change=Number(r.live_change_pct||0)*100;
    const pct=(change>0?'+':'')+change.toFixed(2)+'%';
    const brief=umSignalBrief_(p,now),news=umSignalNews_(p);
    const first=visual.mark+' '+item.name+' ('+item.ticker+')  '+price+' · 전일비 '+pct;
    human.push(first,'  '+brief);
    if(news.length)human.push('  뉴스: '+news.join(' / '));
    human.push('');
    const pctColor=change>0?'#c62828':change<0?'#1565c0':'#444444';
    const newsHtml=news.length?
      '<div style="font-size:13px;color:#555;margin-top:5px">뉴스 · '+news.map(umEscape_).join(' / ')+'</div>':'';
    html.push('<div style="padding:10px 0 12px;border-bottom:1px solid #eeeeee">'+
      '<div style="font-size:17px;font-weight:700;line-height:1.45">'+
      '<span style="color:'+visual.color+'">'+visual.mark+'</span> '+
      umEscape_(item.name)+' <span style="font-weight:400;color:#666;font-size:12px">('+umEscape_(item.ticker)+')</span> '+
      '<span style="white-space:nowrap">'+umEscape_(price)+'</span> '+
      '<span style="font-size:14px;color:'+pctColor+';white-space:nowrap">(전일비 '+umEscape_(pct)+')</span></div>'+
      '<div style="font-size:14px;color:#303030;margin-top:4px">'+umEscape_(brief)+'</div>'+
      newsHtml+'</div>');
    marks.push(visual.mark);
    metadata.push({id:id,source:item.sources.join('+'),ticker:item.ticker,name:item.name,
      stage:label,price:r.live_price,session:r.session||'KRX_SESSION'});
  });
  // Plain MIME part retains machine ids/JSON for existing parsers and retry safety.
  // The visible HTML MIME part intentionally contains only the investor's signals.
  const body=human.join('\n')+'\nUNIFIED_EVENTS_JSON='+JSON.stringify(metadata);
  const subjectHint=plans.length===1?marks[0]+' '+plans[0].item.name+' · ':Array.from(new Set(marks)).join('')+' ';
  const subject=plans.length===1&&umIsMarketMail_(plans[0].item)?
    umMarketSubject_(plans[0],now,round):
    '[주식 통합신호] '+subjectHint+plans.length+'종목 · '+Utilities.formatDate(now,UM.TZ,'MM-dd HH:mm');
  return {subject:subject,
    body:body,htmlBody:'<div style="font-family:Arial,sans-serif;line-height:1.5">'+html.join('')+'</div>'};
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
function umDispatch_(plans,props,now,round) {
  if(!plans.length)return {sent:0};
  const recipient=hmAlertRecipient_(),msg=umMessage_(plans,now,round),ids=plans.map(p=>umEventId_(p,now));
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
function umDispatchGrouped_(plans,props,now,round) {
  // Preserve the existing draft/receipt/state transaction for every message.
  const market=[],ordinary=[],seen={};
  plans.forEach(function(p){
    if(umIsMarketMail_(p.item)){
      const id=umEventId_(p,now);
      if(!seen[id]){seen[id]=true;market.push(p);}
    } else ordinary.push(p);
  });
  let sent=0;
  market.forEach(p=>{sent+=umDispatch_([p],props,now,round).sent;});
  for(let i=0;i<ordinary.length;i+=20)
    sent+=umDispatch_(ordinary.slice(i,i+20),props,now).sent;
  return sent;
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
// Lightweight proof of work for ETF-only candidates. Diagnostic summary is
// written only to spare cells in hidden UNIFIED_CONTROL, never to formula cells.
function umEtfDiagnostics_(audit) {
  const rows=(audit||[]).filter(function(r){return String(r[2]||'').split('+').indexOf('ETF')>=0;});
  const buys=rows.filter(function(r){return r[3]==='BUY_SETUP'||r[3]==='BUY_CONFIRMED';});
  const holds=buys.filter(function(r){return String(r[6]||'').indexOf('JEV_BUY_HOLD:')>=0;});
  const allowed=buys.filter(function(r){return r[4]===true;});
  const down=rows.filter(function(r){return /^DOWN_|^RISK$/.test(String(r[3]||''));});
  const watch=rows.filter(function(r){return r[3]==='WATCH';});
  return {
    evaluated:rows.length,upStage:buys.length,jevHold:holds.length,
    buyAllowed:allowed.length,downOnly:down.length,watch:watch.length
  };
}
function umSaveEtfDiagnostics_(control,audit,now,expected) {
  const d=umEtfDiagnostics_(audit),healthy=(expected===0||d.evaluated===expected);
  control.getRange('G1:H9').setValues([
    ['ETF 통합감시 진단','값'],
    ['최종 평가 시각',now],
    ['ETF 평가 완료',d.evaluated+' / '+expected],
    ['매수 단계 진입',d.upStage],
    ['JEV 매수 보류',d.jevHold],
    ['매수 허용 후보',d.buyAllowed],
    ['하락 기록 (ETF 단독 메일 제외)',d.downOnly],
    ['관찰 (메일 제외)',d.watch],
    ['진단',!healthy?'ETF_EVALUATION_MISSING':d.buyAllowed>0?'BUY_ELIGIBLE':'FILTERED_NO_BUY']
  ]);
  control.getRange('H2').setNumberFormat('yyyy-mm-dd hh:mm:ss');
  return {status:healthy?'OK':'MISSING_ETF',details:d};
}
function umNotifyFailure_(err,props,now) {
  const last=Number(props.getProperty('UM2_LAST_ERROR_MAIL')||0);
  if(now.getTime()-last<3600000)return;
  MailApp.sendEmail({to:hmAlertRecipient_(),subject:'[주식 통합감시 장애] 점검 필요',
    body:'통합감시 실행 또는 발송이 완료되지 않았습니다. 미발송 경보는 다음 실행에서 재시도합니다.\n'+String(err).slice(0,500)});
  props.setProperty('UM2_LAST_ERROR_MAIL',String(now.getTime()));
}
/** P0: isolate UrlFetchApp transport errors instead of aborting an entire watch pass. */
function umP0Quota_(err) { return /Bandwidth quota exceeded|Service invoked too many times/i.test(String(err||'')); }
function umP0Fetch_(requests) {
  const out=requests.map(()=>({response:null,error:'NOT_ATTEMPTED'}));
  let blocked=false;
  try {
    const responses=UrlFetchApp.fetchAll(requests);
    responses.forEach((response,i)=>{out[i]={response:response,error:''};});
  } catch(err) {
    if(umP0Quota_(err))return {results:out.map(()=>({response:null,error:'BANDWIDTH_QUOTA'})),blocked:true};
    // A batch-level exception does not identify the bad ticker. Isolate calls.
    for(let i=0;i<requests.length;i++) {
      try {out[i]={response:UrlFetchApp.fetch(requests[i]),error:''};}
      catch(e) {out[i]={response:null,error:String(e).slice(0,180)};if(umP0Quota_(e)){blocked=true;break;}}
    }
  }
  if(blocked)return {results:out,blocked:true};
  for(let i=0;i<out.length;i++) {
    let code=0;
    try {if(out[i].response)code=out[i].response.getResponseCode();}catch(e){out[i]={response:null,error:String(e).slice(0,180)};}
    if(code===200||code>=400&&code<500&&code!==429)continue;
    // One bounded retry for 429/5xx or transient connection errors.
    try {
      Utilities.sleep(900);
      out[i]={response:UrlFetchApp.fetch(requests[i]),error:''};
    }catch(e) {
      out[i]={response:null,error:String(e).slice(0,180)};
      if(umP0Quota_(e)){blocked=true;break;}
    }
  }
  return {results:out,blocked:blocked};
}
/** P0 bandwidth: one URL Fetch for up to six existing per-symbol scan calls.
 * Response parity is enforced per ticker; no missing or reordered ticker can
 * silently produce a buy signal. Quota breaker and retry queue remain intact.
 */
function umP0BatchFetch_(requests) {
  const groups=[],out=requests.map(()=>({response:null,error:'NOT_ATTEMPTED'}));
  for(let start=0;start<requests.length;start+=UM.GROUP_SIZE) {
    const members=requests.slice(start,start+UM.GROUP_SIZE);
    const first=members[0];
    if(!first.url.endsWith('/unified-scan'))throw new Error('P0_EXPECTED_UNIFIED_SCAN_URL');
    const req=Object.assign({},first,{
      url:first.url.slice(0,-('/unified-scan'.length))+'/unified-scan-batch',
      payload:JSON.stringify({items:members.map(r=>JSON.parse(r.payload))})
    });
    groups.push({start:start,requests:members,request:req});
  }
  if(!groups.length)return {results:out,blocked:false};
  const fetched=umP0Fetch_(groups.map(g=>g.request));
  groups.forEach(function(g,idx) {
    const packet=fetched.results[idx]||{},response=packet.response;
    let payload=null,reason=packet.error||'';
    try{
      if(response&&response.getResponseCode()===200)
        payload=JSON.parse(response.getContentText());
      else if(response)reason='BATCH_HTTP_'+response.getResponseCode();
    }catch(e){reason='BATCH_INVALID_JSON';}
    if(!payload||payload.ok!==true||payload.engine_version!==UM.VERSION||
       !Array.isArray(payload.results)||payload.results.length!==g.requests.length) {
      for(let j=0;j<g.requests.length;j++)
        out[g.start+j]={response:null,error:reason||'BATCH_PROTOCOL_INVALID'};
      return;
    }
    for(let j=0;j<g.requests.length;j++) {
      const expected=JSON.parse(g.requests[j].payload).ticker;
      const got=payload.results[j],code=Number(got&&got.status);
      if(!got||String(got.ticker)!==String(expected)||!isFinite(code)||code!==200||
         !got.result||typeof got.result!=='object') {
        out[g.start+j]={response:null,error:!got||String(got.ticker)!==String(expected)?
          'BATCH_TICKER_MISMATCH':String(got.error||'BATCH_ITEM_HTTP_'+code).slice(0,180)};
        continue;
      }
      const body=JSON.stringify(got.result);
      out[g.start+j]={response:{
        getResponseCode:function(){return 200;},
        getContentText:function(){return body;}
      },error:''};
    }
  });
  return {results:out,blocked:fetched.blocked};
}

/** Each slot: 5m priority (HELD, 069500, 114800, 251340) first,
 * then capped failed-ticker retries, then the active 15m group (A/B/C). */
function umP0Schedule_(universe,props,mode,now) {
  const key='UM2_P0_SCAN_'+mode,day=umDay_(now);
  let prev={};try{prev=JSON.parse(props.getProperty(key)||'{}');}catch(e){}
  if(prev.day!==day)prev={day:day,cursor:0,retry:[]};

  const urgent=universe.filter(x=>umIsPriority_(x));
  const normal=universe.filter(x=>!umIsPriority_(x));
  normal.sort((a,b)=>a.ticker.localeCompare(b.ticker));

  const groupA=normal.filter((_,i)=>i%3===0);
  const groupB=normal.filter((_,i)=>i%3===1);
  const groupC=normal.filter((_,i)=>i%3===2);
  const groups=[groupA,groupB,groupC];
  const groupNames=['A','B','C'];

  const slotNumber=Math.floor(now.getTime()/300000);
  const groupIndex=((slotNumber%3)+3)%3;
  const activeGroup=groups[groupIndex];
  const activeGroupName=groupNames[groupIndex];

  const known={};universe.forEach(x=>{known[x.ticker]=x;});
  const previouslyFailed=Array.isArray(prev.retry)?prev.retry.filter(code=>known[code]):[];
  previouslyFailed.sort((a,b)=>{
    const rank=c=>umIsPriority_(known[c])?0:1;
    return rank(a)-rank(b);
  });

  const seen=new Set(),work=[];
  const add=(item,kind,offset)=>{if(!item||seen.has(item.ticker))return;seen.add(item.ticker);work.push({item:item,kind:kind,offset:offset});};
  urgent.forEach(x=>add(x,'PRIORITY',-1));
  previouslyFailed.slice(0,20).forEach(code=>add(known[code],'RETRY',-1));
  activeGroup.forEach((x,idx)=>add(x,'NORMAL',idx));

  return {
    key:key,day:day,work:work,
    normalLength:normal.length,
    activeGroupLength:activeGroup.length,
    group:activeGroupName,
    groupIndex:groupIndex,
    groupCounts:{A:groupA.length,B:groupB.length,C:groupC.length},
    priorityCount:urgent.length,
    retry:new Set(previouslyFailed)
  };
}
function umP0Checkpoint_(props,scan,nextCursor) {
  props.setProperty(scan.key,JSON.stringify({day:scan.day,group:scan.group,cursor:nextCursor,
    retry:Array.from(scan.retry).slice(0,500)}));
}
function umTick5m(now) {
  now=now||new Date();const start=Date.now(),mode=umMode_();
  if(['ACTIVE','PREVIEW'].indexOf(mode)<0)return {status:'OFF'};
  if(!hmIsMarketWindow_(now)){
    try{if(mode==='ACTIVE'&&typeof pgMaybeClose_==='function')pgMaybeClose_(now);}
    catch(e){console.error('PAPER_CLOSE_ISOLATED: '+String(e));}
    if(mode==='ACTIVE'&&typeof e45SafeTick_==='function')e45SafeTick_(new Date(),false);
    return {status:'OUTSIDE_SESSION'};
  }
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'LOCKED'};
  const props=PropertiesService.getScriptProperties(),slot=String(Math.floor(start/300000));
  try {
    if(mode==='ACTIVE')umResumeDelivery_(props);
    if(props.getProperty('UM2_COMPLETED_'+mode)===slot)return {status:'SLOT_DONE'};
    const book=SpreadsheetApp.openById(UM.BOOK),all=umUniverse_(now),control=book.getSheetByName(UM.CONTROL);
    const universe=mode==='PREVIEW'?all.filter(x=>['069500','114800','373220','103590','001430','490590','007660'].indexOf(x.ticker)>=0):all;
    const legacy=mode==='ACTIVE'?umLegacySeed_(book,props,now):{};
    let evaluated=0,sent=0,errors=0;const audit=[],allPlans=[],paperSamples=[],paperDiagnostics=[],e45Samples=[];
    let e45Cfg={enabled:false};
    try{if(typeof e45Config_==='function')e45Cfg=e45Config_();}catch(e){console.error('E45_CONFIG_ISOLATED: '+String(e));}
    const marketRound={startedAt:now.getTime(),quotes:{}};
    // P0: urgent holdings and KOSPI200 risk are checked every slot. Rotate the
    // remaining watchlist so a partial 5-minute run never starves tail symbols.
    const scan=umP0Schedule_(universe,props,mode,now);
    let processed=0,nextCursor=scan.start,transportBlocked=false;
    const failedEntries=[];
    for(let pos=0;pos<scan.work.length;pos+=UM.BATCH) {
      if(Date.now()-start>UM.MAX_RUNTIME)break;
      const batch=scan.work.slice(pos,pos+UM.BATCH);
      const reqs=batch.map(entry=>umRequest_(entry.item,props));
      const fetched=umP0BatchFetch_(reqs),plans=[];
      batch.forEach(function(entry,i){
        const item=entry.item,packet=fetched.results[i]||{},response=packet.response;
        if(entry.kind==='NORMAL')
          nextCursor=entry.offset+1;
        let code=0,r={};
        try {
          if(response){code=response.getResponseCode();if(code===200)r=JSON.parse(response.getContentText());}
        }catch(e){r={};}
        if(code!==200||r.ok!==true||r.engine_version!==UM.VERSION){
          const reason=packet.error||('HTTP_'+code);
          failedEntries.push({entry:entry,item:item,reason:reason});
          return;
        }
        try {
          umCollectMarketQuote_(marketRound,item,r,Date.now());
          if(mode==='ACTIVE'){
            const p=umPlan_(item,r,umLoadState_(props,item.ticker)||legacy[item.ticker],now);
            if(p.events.length)plans.push(p);
            else props.setProperty(UM.STATE_PREFIX+item.ticker,JSON.stringify(p.state));
            umDisplay_(item,r);
            try{if(typeof e45Observation_==='function'){const pe=e45Observation_(item,r,now.toISOString(),new Date(),e45Cfg);if(pe)e45Samples.push(pe);}}
            catch(e){if(typeof e45ReportError_==='function')e45ReportError_('CAPTURE',e,new Date());}
            try{if(typeof pgCandidate_==='function'){const ps=pgCandidate_(item,r,now,new Date(),paperDiagnostics);if(ps)paperSamples.push(ps);}}
            catch(e){console.error('PAPER_CAPTURE_ISOLATED: '+String(e));if(typeof pgExecutionDiagnostic_==='function')paperDiagnostics.push(pgExecutionDiagnostic_('PG_CAPTURE',item.ticker,e));}
          }
          evaluated++;scan.retry.delete(item.ticker);
          audit.push([now,item.ticker,item.sources.join('+'),r.stage,r.send_allowed,r.live_price||'',
            (r.reasons||[]).join('|'),r.engine_version,(r.events||[]).map(e=>e.key).join('|')]);
        }catch(e){
          const reason='STATE_'+String(e).slice(0,160);
          failedEntries.push({entry:entry,item:item,reason:reason});
        }
      });
      if(plans.length)allPlans.push.apply(allPlans,plans);
      processed+=batch.length;
      umP0Checkpoint_(props,scan,nextCursor);
      if(fetched.blocked){
        transportBlocked=true;
        scan.work.slice(processed).forEach(e=>{scan.retry.add(e.item.ticker);});
        umP0Checkpoint_(props,scan,nextCursor);
        break;
      }
    }

    // P0-5: Bounded same-slot 1x retry for transient 503 / timeout failures
    if(!transportBlocked && failedEntries.length>0 && (Date.now()-start+25000<UM.MAX_RUNTIME)){
      const retryCandidates=failedEntries.sort(function(a,b){
        const rank=e=>e.item.ticker==='069500'?0:umIsPriority_(e.item)?1:2;
        return rank(a)-rank(b);
      }).slice(0,12);
      Utilities.sleep(1000);
      const retryReqs=retryCandidates.map(c=>umRequest_(c.item,props));
      const retryFetched=umP0BatchFetch_(retryReqs),retryPlans=[];
      const retriedSet=new Set();
      retryCandidates.forEach(function(cand,i){
        retriedSet.add(cand.item.ticker);
        const item=cand.item,packet=retryFetched.results[i]||{},response=packet.response;
        let code=0,r={};
        try {
          if(response){code=response.getResponseCode();if(code===200)r=JSON.parse(response.getContentText());}
        }catch(e){r={};}
        if(code!==200||r.ok!==true||r.engine_version!==UM.VERSION){
          errors++;scan.retry.add(item.ticker);
          const reason=packet.error||('HTTP_'+code);
          audit.push([now,item.ticker,item.sources.join('+'),'ERROR',false,'',String(reason).slice(0,180),UM.VERSION,'']);
          return;
        }
        try {
          umCollectMarketQuote_(marketRound,item,r,Date.now());
          if(mode==='ACTIVE'){
            const p=umPlan_(item,r,umLoadState_(props,item.ticker)||legacy[item.ticker],now);
            if(p.events.length)retryPlans.push(p);
            else props.setProperty(UM.STATE_PREFIX+item.ticker,JSON.stringify(p.state));
            umDisplay_(item,r);
            try{if(typeof e45Observation_==='function'){const pe=e45Observation_(item,r,now.toISOString(),new Date(),e45Cfg);if(pe)e45Samples.push(pe);}}
            catch(e){if(typeof e45ReportError_==='function')e45ReportError_('CAPTURE',e,new Date());}
            try{if(typeof pgCandidate_==='function'){const ps=pgCandidate_(item,r,now,new Date(),paperDiagnostics);if(ps)paperSamples.push(ps);}}
            catch(e){console.error('PAPER_CAPTURE_ISOLATED: '+String(e));if(typeof pgExecutionDiagnostic_==='function')paperDiagnostics.push(pgExecutionDiagnostic_('PG_CAPTURE',item.ticker,e));}
          }
          evaluated++;scan.retry.delete(item.ticker);
          audit.push([now,item.ticker,item.sources.join('+'),r.stage,r.send_allowed,r.live_price||'',
            (r.reasons||[]).join('|'),r.engine_version,(r.events||[]).map(e=>e.key).join('|')]);
        }catch(e){
          errors++;scan.retry.add(item.ticker);
          audit.push([now,item.ticker,item.sources.join('+'),'ERROR',false,'',
            'STATE_'+String(e).slice(0,160),UM.VERSION,'']);
        }
      });
      if(retryPlans.length)allPlans.push.apply(allPlans,retryPlans);
      failedEntries.forEach(function(cand){
        if(!retriedSet.has(cand.item.ticker)){
          errors++;scan.retry.add(cand.item.ticker);
          audit.push([now,cand.item.ticker,cand.item.sources.join('+'),'ERROR',false,'',String(cand.reason).slice(0,180),UM.VERSION,'']);
        }
      });
      umP0Checkpoint_(props,scan,nextCursor);
    } else if(failedEntries.length>0){
      failedEntries.forEach(function(cand){
        errors++;scan.retry.add(cand.item.ticker);
        audit.push([now,cand.item.ticker,cand.item.sources.join('+'),'ERROR',false,'',String(cand.reason).slice(0,180),UM.VERSION,'']);
      });
      umP0Checkpoint_(props,scan,nextCursor);
    }
    if(mode==='ACTIVE'&&allPlans.length){
      // Keep Gmail's draft/receipt reconciliation and per-ticker event ids.
      // Bounded digest also stays well below Apps Script 9-KB property limits.
      sent+=umDispatchGrouped_(allPlans,props,now,marketRound);
    }
    umRecord_(audit);
    try{if(mode==='ACTIVE'&&e45Cfg.enabled&&typeof e45Save_==='function')e45Save_(e45Samples,new Date());}
    catch(e){if(typeof e45ReportError_==='function')e45ReportError_('SAVE',e,new Date());}
    try{
      audit.filter(r=>r[3]==='ERROR'&&typeof PG!=='undefined'&&PG.CODES.indexOf(r[1])>=0)
        .forEach(r=>paperDiagnostics.push([new Date().toISOString(),'PG_TRANSPORT',r[1],String(r[6]),'EXEC_ERROR']));
      if(mode==='ACTIVE'&&typeof pgSaveDiagnostics_==='function')pgSaveDiagnostics_(paperDiagnostics);
    }catch(e){console.error('PAPER_DIAGNOSTIC_SAVE_ISOLATED: '+String(e));}
    try{if(mode==='ACTIVE'&&typeof pgSaveSamples_==='function')pgSaveSamples_(paperSamples,now);}
    catch(e){
      console.error('PAPER_SNAPSHOT_ISOLATED: '+String(e));
      try{if(typeof pgSaveDiagnostics_==='function'&&typeof pgExecutionDiagnostic_==='function')pgSaveDiagnostics_([pgExecutionDiagnostic_('PG_SAVE',PG.CODES.join('|'),e)]);}catch(ignored){}
    }
    const done=processed===scan.work.length&&!transportBlocked&&errors===0&&scan.retry.size===0;
    // Record a complete slot only after all ticker results (including retries)
    // have been safely checked. Never clear the P0 failed-ticker queue on error.
    if(done)props.setProperty('UM2_COMPLETED_'+mode,slot);
    control.getRange('B3:B7').setValues([[now],[mode+(done?'_COMPLETE':'_PARTIAL')],
      [JSON.stringify({universe:universe.length,slot_target:scan.work.length,slot_group:scan.group,
        priority_count:scan.priorityCount,group_counts:scan.groupCounts,evaluated:evaluated,sent:sent,
        errors:errors,processed:processed,remaining:scan.work.length-processed,
        retry_pending:scan.retry.size,rotation_cursor:nextCursor,transport_blocked:transportBlocked})],
      [UM.VERSION],[errors?'API_ERRORS':done?'OK':'INCOMPLETE_SCAN']]);
    // Diagnostics are observational and must only follow true full scans.
    if(mode==='ACTIVE' && done){
      try{
        const expected=scan.work.map(w=>w.item).filter(x=>(x.sources||[]).indexOf('ETF')>=0).length;
        const diag=umSaveEtfDiagnostics_(control,audit,now,expected);
        if(expected>0 && diag.status!=='OK')umNotifyFailure_('ETF 평가 누락: '+diag.details.evaluated+'/'+expected,props,now);
      }catch(e){console.error('ETF_DIAGNOSTICS_WRITE_FAILED: '+String(e));}
    }
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
  }finally{
    lock.releaseLock();
    // Gmail lookup / closing fallbacks only follow the existing monitor work.
    if(mode==='ACTIVE'&&typeof e45SafeTick_==='function')e45SafeTick_(new Date(),true);
  }
}
