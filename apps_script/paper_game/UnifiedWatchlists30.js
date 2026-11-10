/** Three lists, one technical API, one persistent state, one Gmail dispatcher.
 * Existing hmTick5m owns scheduling; all old signal entry points delegate here.
 * UNIFIED_CONTROL B2 is PREVIEW or ACTIVE. Candidate ingestion stays independent.
 * No portfolio values, existing formulas or order APIs are changed.
 */
const UM30 = Object.freeze({
  VERSION:'three-lists-v3-m30', TZ:'Asia/Seoul', CONTROL:'UNIFIED_CONTROL', STATE_SHEET:'UNIFIED_STATE',
  BOOK:'15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw',
  YT:'10R11CnOs0Gi3Ffag8aWOw8sNZw0E_ZFlM5_qqSyO2xw',
  STATE_PREFIX:'UM2_', MAX_RUNTIME:260000, BATCH:12, GROUP_SIZE:6,
  LABELS:{WATCH:'· 관찰',BUY_SETUP:'△ 매수조짐',BUY_CONFIRMED:'▲ 매수확인',
    DOWN_EARLY:'▽ 하락조짐',DOWN_CONFIRMED:'▼ 하락확인',RISK:'⚠ 위험경고'}
});
function umMode_M30() {
  const sh=SpreadsheetApp.openById(UM30.BOOK).getSheetByName(UM30.CONTROL);
  return sh ? String(sh.getRange('B2').getValue()) : 'OFF';
}
function umEnabled_M30() { return umMode_M30()==='ACTIVE'; }
function umUseCoordinator_M30() { return ['ACTIVE','PREVIEW'].indexOf(umMode_M30())>=0; }
function umDay_M30(now) { return Utilities.formatDate(now,UM30.TZ,'yyyy-MM-dd'); }
function umNumber_M30(x) { const n=Number(x);return x!==''&&x!=null&&isFinite(n)&&n>0?n:null; }
function umCode_M30(x) {
  const s=String(x||'').replace(/^(KRX:|KOSDAQ:|A)/,'');
  return /^\d{1,6}$/.test(s)?s.padStart(6,'0'):'';
}
function umOn_M30(x) { return x===true||/^(Y|TRUE|1)$/i.test(String(x)); }
const UM30_PRIORITY_INDEX_ETFS = Object.freeze(['069500','114800','229200','251340']);
const UM30_MARKET_MAIL_NAMES = Object.freeze({
  '069500':'코스피', '229200':'코스닥',
  '114800':'인버스', '251340':'코스닥 인버스'
});
function umIsMarketMail_M30(item) {
  return !!(item && (item.sources||[]).indexOf('ETF')>=0 &&
    UM30_MARKET_MAIL_NAMES[item.ticker]);
}
function umIsPriority_M30(item) {
  if(!item)return false;
  if(item.sources && item.sources.indexOf('HELD')>=0)return true;
  return UM30_PRIORITY_INDEX_ETFS.indexOf(item.ticker)>=0;
}
function umRows_M30(book,name,width) {
  const sh=book.getSheetByName(name);if(!sh)throw new Error('MISSING_LIST '+name);
  return sh.getLastRow()>1?sh.getRange(2,1,Math.min(sh.getLastRow()-1,1000),width).getValues():[];
}
function umMerge_M30(rows) {
  const merged={};
  rows.forEach(function(row) {
    const code=umCode_M30(row.ticker);if(!code)return;
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
    const rank=x=>x.ticker==='069500'?0:umIsPriority_M30(x)?1:2;
    return rank(a)-rank(b)||a.ticker.localeCompare(b.ticker);
  });
}
const CANONICAL_V4_STOPS_M30 = Object.freeze({
  '058470': 60000,
  '138080': 25300,
  '203650': 2180,
  '253590': 14040,
  '295310': 57400,
  '298040': 1650000
});

function umUniverse_M30(now) {
  m30Book_();
    const book=SpreadsheetApp.openById(UM30.BOOK), yt=SpreadsheetApp.openById(UM30.YT), rows=[];
  const confSheet = book.getSheetByName('MONITOR_CONFIG');
  umRows_M30(book,'MONITOR_CONFIG',20).forEach(function(r,i){
    if(!umOn_M30(r[9])||!umNumber_M30(r[3])||String(r[7])==='CLOSED')return;
    const ticker = String(r[0]||'').padStart(6, '0');
    let stop = umNumber_M30(r[6]);
    if ((stop === null || stop === 0) && CANONICAL_V4_STOPS_M30[ticker]) {
      stop = CANONICAL_V4_STOPS_M30[ticker];
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
      quantity:r[3],average_price:r[4],stop_price:stop,atr14:umNumber_M30(r[16]),
      suspended:/SUSPENDED/.test(String(r[7])+' '+String(r[14])),row:i+2});
  });
  const etfs=[];
  umRows_M30(book,'ETF_WATCHLIST',35).forEach(function(r,i){
    if(!umOn_M30(r[0]))return;const code=umCode_M30(r[3]);if(!code)return;etfs.push(code);
    const sector=String(r[2]);
    rows.push({source:'ETF',ticker:code,name:String(r[4]),is_etf:true,
      market:sector.indexOf('코스닥')>=0?'KOSDAQ':String(r[1])==='국내'?'KOSPI':'UNKNOWN',
      inverse:/인버스/.test(String(r[4])),row:i+2});
  });
  umRows_M30(yt,'CANDIDATES',26).forEach(function(r,i){
    const expiry=r[6] instanceof Date?umDay_M30(r[6]):String(r[6]).slice(0,10);
    if(!umOn_M30(r[0])||(expiry&&/^\d{4}-\d{2}-\d{2}$/.test(expiry)&&expiry<umDay_M30(now)))return;
    rows.push({source:'YOUTUBE',ticker:r[3],name:String(r[1]),market:String(r[2]),row:i+2});
  });
  // Explicit, reviewable mapping only. No fuzzy company-name/sector guesses.
  const mapSh=book.getSheetByName(UM30.CONTROL), mapping={};
  if(mapSh&&mapSh.getLastRow()>8)mapSh.getRange(9,1,mapSh.getLastRow()-8,3).getDisplayValues().forEach(r=>{
    const code=umCode_M30(r[0]),sector=umCode_M30(r[2]);
    if(code)mapping[code]={market:r[1],sector_etf_code:etfs.indexOf(sector)>=0?sector:''};
  });
  const result=umMerge_M30(rows);
  result.forEach(r=>{
    if(mapping[r.ticker])Object.assign(r,mapping[r.ticker]);
    if(r.sector_etf_code===r.ticker)delete r.sector_etf_code;
    r.etf_universe=etfs;
  });
  return result;
}
function umCursorStart_M30(props,mode,slot) {
  // PREVIEW and ACTIVE may run in the same 5-minute slot; never let the
  // small preview cursor skip the priority head of a 201-symbol active scan.
  return props.getProperty('UM2_CURSOR_SLOT')===mode+':'+slot ?
    Number(props.getProperty('UM2_CURSOR')||0) : 0;
}
/** Display-only context. Never participates in event selection or sending. */
function umDirectionContext_M30(result,now) {
  const r=result||{},daily=r.technical||{},m=r.m30||{};
  const stamp=Utilities.formatDate(now,UM30.TZ,'yyyy-MM-dd HH:mm:ss');
  const day=stamp.slice(0,10),minutes=Number(stamp.slice(11,13))*60+Number(stamp.slice(14,16));
  const official=daily.basis==='COMPLETED_DAILY'&&
    /^\d{4}-\d{2}-\d{2}[ T]15:30:00$/.test(String(daily.as_of||''))&&String(daily.as_of).slice(0,10)<day;
  const regime=official?String(daily.regime||''):'';
  const dailyDirection=['BULL_CONFIRMED','PULLBACK_IN_UPREGIME'].indexOf(regime)>=0?'UP':
    ['BEAR_CONFIRMED','BOUNCE_IN_DOWNREGIME','OBV_REBOUND_BASE_DOWN'].indexOf(regime)>=0?'DOWN':'UNCONFIRMED';
  const bar=String(m.completed_bar||''),match=/^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2}):00(?:\+09:00)?$/.exec(bar);
  const barMinutes=match?Number(match[2])*60+Number(match[3]):-1;
  const expected=Math.min(900,Math.floor((minutes-540)/30)*30+540);
  const verified=m.available===true&&Boolean(m.source)&&match&&match[1]===day&&
    minutes>=570&&barMinutes>=570&&barMinutes<=900&&(barMinutes-570)%30===0&&barMinutes===expected;
  const shortDirection=verified?(m.vwap9_dir==='RISING'?'UP':m.vwap9_dir==='FALLING'?'DOWN':
    m.vwap9_dir==='FLAT'?'FLAT':'UNKNOWN'):'UNKNOWN';
  let relation='SHORT_UNVERIFIED',caption='30분봉 방향 미확인';
  if(shortDirection==='UP'||shortDirection==='DOWN') {
    relation=dailyDirection+'_'+shortDirection;
    const captions={UP_UP:'일봉 상승 추세 유지 · 30분봉 단기 강세',
      UP_DOWN:'일봉 상승 추세 속 30분봉 단기 약화',
      DOWN_UP:'일봉 약세 속 30분봉 반등 포착',
      DOWN_DOWN:'일봉 하락 추세 지속 · 30분봉 단기 약세',
      UNCONFIRMED_UP:'30분봉 단기 강세 · 상위 추세 미확정',
      UNCONFIRMED_DOWN:'30분봉 단기 약세 · 상위 추세 미확정'};
    caption=captions[relation];
  } else if(shortDirection==='FLAT') {relation='SHORT_NEUTRAL';caption='30분봉 방향 중립';}
  return {schema:'DIRECTION_CONTEXT_V1',daily_regime:regime,daily_direction:dailyDirection,
    daily_basis:official?'COMPLETED_DAILY':'UNVERIFIED',daily_as_of:official?daily.as_of:'',
    m30_direction:shortDirection,m30_alignment:verified&&typeof m.gold==='boolean'?(m.gold?'GOLD':'DEAD'):'UNKNOWN',
    m30_vwap9_dir:verified?String(m.vwap9_dir||''):'',completed_bar:verified?bar:'',
    source:verified?m.source:'',observed_at:now.toISOString(),relation:relation,caption:caption};
}
function umDirectionNote_M30(p,now) {
  const r=p.result||{},item=p.item||{},ctx=umDirectionContext_M30(r,now);
  const keys=(p.events||[]).map(e=>e.key),early=keys.indexOf('VWAP30_BULL_STACK_EARLY')>=0;
  const risk=r.stage==='RISK'||keys.some(k=>k==='STOP_BREACH'||k.startsWith('STOP_BREACH:'))||
    (Number(item.stop_price)>0&&Number(r.live_price)>0&&Number(r.live_price)<=Number(item.stop_price));
  if(risk)return ctx.m30_direction==='UP'?'30분봉 반등과 별개로 위험경고 유지':'';
  if(early&&ctx.m30_direction==='DOWN')return '정배열은 위치 관계 · 30분봉 VWAP9 방향은 하락';
  if(ctx.relation==='DOWN_UP'||ctx.relation==='UP_DOWN')return ctx.caption;
  if(ctx.daily_direction==='UNCONFIRMED'&&(early||r.stage==='BUY_SETUP'||r.stage==='BUY_CONFIRMED'))
    return '기술적 매수 관심 — 상위 추세 확인 필요';
  if(ctx.daily_direction==='UNCONFIRMED'&&ctx.m30_direction!=='UNKNOWN'&&ctx.m30_direction!=='FLAT')return ctx.caption;
  return '';
}

function umPlan_M30(item,result,old,now) {
  const day=umDay_M30(now),slot=Math.floor(now.getTime()/300000);
  if(old&&old.quarantined===true) {
    const quarantinedState=JSON.parse(JSON.stringify(old));
    quarantinedState.day=day;
    if(Array.isArray(result.reasons)&&!result.reasons.includes('STATE_CORRUPT_QUARANTINED')) {
      result.reasons.push('STATE_CORRUPT_QUARANTINED');
    }
    return {item:item,result:result,state:quarantinedState,events:[]};
  }
  const state=old&&old.day===day?JSON.parse(JSON.stringify(old)):{day:day,sent:{},count:0};
  const stage=String(result.stage||'WATCH');
  const isPriority=umIsPriority_M30(item);
  const isConsecutive=isPriority ?
    (state.slot===slot-1) :
    (state.slot!=null && slot>state.slot && slot-state.slot<=5);
  if(state.slot!==slot) {
    state.count=state.stage===stage&&isConsecutive?Math.min(2,(state.count||0)+1):1;
    state.stage=stage;state.slot=slot;
  }
  state.lastResult={stage:stage,price:result.live_price||null,at:now.toISOString(),
    sources:item.sources,reasons:(result.reasons||[]).slice(-5)};
  const dailyState=result.technical||{},provisional=dailyState.provisional||{};
  state.lastResult.dailyBasis={basis:dailyState.basis||'',as_of:dailyState.as_of||'',
    regime:dailyState.regime||'',stage:dailyState.stage||''};
  state.lastResult.dailyProvisional={available:provisional.available===true,
    basis:provisional.basis||'',as_of:provisional.as_of||'',
    regime:provisional.regime||'',stage:provisional.stage||'',volume_scaled:false};
  state.lastResult.directionContext=umDirectionContext_M30(result,now);
  if(item.stop_price&&result.live_price>item.stop_price) {
    if(state.lastStopAt||(state.sent&&state.sent.STOP_BREACH)||state.stopBreachSeq) {
      state.stopRecovered=true;
    }
  }
  const candidates=[];
  if(result.send_allowed===true)(result.events||[]).forEach(function(e){
    // One shared calculation, source-aware notifications only:
    // HELD -> both directions / stop / OBV risk; market ETFs -> both directions;
    // all other watchlists keep their BUY-only policy.
    const isHeld=(item.sources||[]).indexOf('HELD')>=0;
    const isMarketProxy=item.ticker==='069500'&&(item.sources||[]).indexOf('ETF')>=0;
    const isMarketMail=umIsMarketMail_M30(item);
    if(!isHeld && !(
      (e.key==='BUY_SETUP' && e.stage==='BUY_SETUP') ||
      (e.key==='BUY_CONFIRMED' && e.stage==='BUY_CONFIRMED') ||
      (isMarketProxy && (e.key==='MARKET_CRASH'||e.key==='MARKET_WEAK_EARLY')) ||
      (isMarketMail && (e.key==='DOWN_EARLY'||e.key==='DOWN_CONFIRMED'||
        e.key==='OBV15_WARNING'||e.key==='SELL_PREP_WARNING'))
    ))return;
    // A completed previous-day bearish daily bar is still historical evidence,
    // not a new decline confirmation during an independently verified live rebound.
    // Hold only the stale daily DOWN notifications; STOP, MARKET_CRASH, ATR and
    // completed intraday OBV/exit risk continue through their existing guards.
    if((e.key==='DOWN_CONFIRMED'||e.key==='DOWN_EARLY')&&
       result.ok===true&&result.session==='KRX_SESSION'&&
       result.live_price_source==='KIWOOM_KA10001'&&
       typeof result.live_change_pct==='number'&&
       isFinite(result.live_change_pct)&&result.live_change_pct>0&&
       umDirectionContext_M30(result,now).daily_basis==='COMPLETED_DAILY')return;
    let key=e.key+(e.key==='MARKET_CRASH'?':'+e.level:'');
    let breachSeq=1;
    if(e.key==='STOP_BREACH') {
      const hasBreachedBefore=Boolean(state.lastStopAt||(state.sent&&state.sent.STOP_BREACH)||state.stopBreachSeq);
      if(!hasBreachedBefore) {
        breachSeq=1;
        key='STOP_BREACH';
      } else {
        const recovered=state.stopRecovered===true;
        const timeSinceLastStop=state.lastStopAt?(now.getTime()-Date.parse(state.lastStopAt)):Infinity;
        if(!recovered||timeSinceLastStop<30*60000) {
          return;
        }
        breachSeq=(Number(state.stopBreachSeq)||1)+1;
        key='STOP_BREACH:'+breachSeq;
      }
    }
    if(state.sent[key])return;
    if(isMarketMail&&e.key==='DOWN_EARLY'&&
      (state.sent.MARKET_WEAK_EARLY||candidates.some(x=>x.key==='MARKET_WEAK_EARLY')))return;
    if(e.key==='STOP_BREACH'&&breachSeq===1&&state.lastStopAt&&now.getTime()-Date.parse(state.lastStopAt)<30*60000)return;
    const urgent=e.stage==='RISK'||e.key==='ATR_DOWN_1_0';
    if(!urgent&&state.count<2)return;
    // An earlier crash level must never be resent after escalation.
    if(e.key==='MARKET_CRASH'&&Number(state.crashLevel||0)>=Number(e.level))return;
    const candidateEvent=Object.assign({},e,{stateKey:key});
    if(e.key==='STOP_BREACH'&&breachSeq>1) {
      candidateEvent.seq=breachSeq;
      candidateEvent.reason='실시간 손절선 '+Number(item.stop_price||0).toLocaleString('ko-KR')+'원 재침범('+breachSeq+'회차)';
    }
    candidates.push(candidateEvent);
  });
  // Observe a NEW 30m bullish stack for every monitored source (HELD, ETF,
  // YOUTUBE), including WATCH/neutral names. This is an MTS inspection alert,
  // not a buy approval; engine buy rules, JEV and 2-pass ▲ remain unchanged.
  // Only completed current-day 30m bars and existing quote data are used.
  const m30=result.m30||{},jev=result.jev_gate||{},earlyKey='VWAP30_BULL_STACK_EARLY';
  const price=Number(result.live_price),v9=Number(m30.vwap9),v26=Number(m30.vwap26);
  const currentSource=String(m30.source||'');
  const currentBar=String(m30.completed_bar||'');
  const fresh=m30.available===true&&
    currentBar.slice(0,10)===day&&
    String(result.live_price_source||'')==='KIWOOM_KA10001'&&
    isFinite(price)&&price>0&&isFinite(v9)&&v9>0&&isFinite(v26)&&v26>0;
  const stacked=fresh&&price>v9&&v9>v26&&m30.gold===true;
  const previouslySeen=state.vwap30StackValid===true;
  const wasStacked=state.vwap30Stacked===true;
  const prevSource=state.vwap30Source;
  const currentWatermark=String(m30.watermark||'');
  // Comparability guard: when source changes or legacy state lacks source info,
  // direct Boolean comparison between feeds is invalid. Re-baseline without false alarms.
  const comparable=previouslySeen&&Boolean(prevSource)&&(prevSource===currentSource);
  // Same-source data correction guard:
  // When completed bar hasn't advanced, any VWAP change is caused by past bar data restatement.
  // A cross on the same bar is ONLY genuine if the live price broke out above the prior VWAP hurdle.
  // If price didn't move or failed to exceed prior VWAP9, the cross was induced by data revision.
  const sameBar=comparable&&Boolean(state.vwap30Bar)&&(state.vwap30Bar===currentBar);
  const vwapDrift=sameBar&&Boolean(state.vwap30Vwap9)&&(Math.abs(v9-state.vwap30Vwap9)>1e-4||Math.abs(v26-state.vwap30Vwap26)>1e-4);
  const watermarkDrift=sameBar&&Boolean(state.vwap30Watermark)&&Boolean(currentWatermark)&&(state.vwap30Watermark!==currentWatermark);
  const isDataCorrectionCross=sameBar&&(vwapDrift||watermarkDrift)&&(
    price===state.vwap30Price||(state.vwap30Vwap9!=null&&price<=state.vwap30Vwap9)
  );
  const freshCross=previouslySeen&&comparable&&!wasStacked&&stacked&&!isDataCorrectionCross;
  const isBuyStage=stage==='BUY_SETUP'||stage==='BUY_CONFIRMED';
  const buyGateOK=!isBuyStage||(result.send_allowed===true&&jev.status==='OK'&&jev.decision==='SEND');
  const firstStrong=stage==='BUY_CONFIRMED'&&result.send_allowed===true&&state.count===1&&
    jev.status==='OK'&&jev.decision==='SEND'&&stacked;
  const unsafe=stage==='RISK'||(item.stop_price&&price<=item.stop_price)||
    (result.reasons||[]).some(x=>/ABOVE_OVERHEAT_LIMIT|RECENT_1M_WEAKENING|LIVE_MINUTES_UNAVAILABLE_OR_STALE/.test(String(x)));

  // Record complete state upon every fresh observation
  if(fresh) {
    state.vwap30StackValid=true;
    state.vwap30Stacked=stacked;
    state.vwap30Source=currentSource;
    state.vwap30Bar=currentBar;
    state.vwap30Price=price;
    state.vwap30Vwap9=v9;
    state.vwap30Vwap26=v26;
    state.vwap30Watermark=currentWatermark;
    state.vwap30LastChecked=now.toISOString();
  }

  // Evaluate new ▲ Uptrend Confirmation condition (Simulation only, never email/order)
  const tech=result.technical||{};
  const dailyVwap9=Number(tech.vwap9);
  const prevHigh=Number(tech.prev_high);
  const priceGtDailyVwap9=isFinite(dailyVwap9)&&dailyVwap9>0&&price>dailyVwap9;
  const priceGtPrevHigh=isFinite(prevHigh)&&prevHigh>0&&price>prevHigh;
  const bullConfirmedSim=stacked&&priceGtDailyVwap9&&priceGtPrevHigh;

  const obsTime=now.toISOString();
  const dailyAsOf=tech.as_of||obsTime;
  const prevHighAsOf=tech.prev_high_as_of||null;

  if(bullConfirmedSim) {
    state.simBullConfirmed = {
      at: obsTime,
      ticker: item.ticker,
      price: price,
      price_as_of: obsTime,
      daily_vwap9: dailyVwap9,
      daily_vwap9_as_of: dailyAsOf,
      prev_high: prevHigh,
      prev_high_as_of: prevHighAsOf,
      m30_vwap9: v9,
      m30_vwap26: v26,
      m30_slope: Number(m30.vwap9_slope||0),
      m30_dir: String(m30.vwap9_dir||''),
      completed_bar: currentBar
    };
    result.sim_bull_confirmed = Object.assign({qualified: true}, state.simBullConfirmed);
    if(Array.isArray(result.reasons) && !result.reasons.some(r=>String(r).startsWith('SIM_BULL_CONFIRMED:'))) {
      result.reasons.push('SIM_BULL_CONFIRMED:P='+price+'@'+obsTime+'>DV9='+dailyVwap9+'@'+dailyAsOf+'>PHIGH='+prevHigh+'@'+(prevHighAsOf||'COMPLETED')+';SLOPE='+Number(m30.vwap9_slope||0)+'('+String(m30.vwap9_dir||'')+')');
    }
  } else {
    result.sim_bull_confirmed = {
      qualified: false,
      at: obsTime,
      ticker: item.ticker,
      price: price,
      price_as_of: obsTime,
      stacked_m30: stacked,
      price_gt_daily_vwap9: priceGtDailyVwap9,
      price_gt_prev_high: priceGtPrevHigh,
      daily_vwap9: dailyVwap9,
      daily_vwap9_as_of: dailyAsOf,
      prev_high: prevHigh,
      prev_high_as_of: prevHighAsOf,
      m30_vwap9: v9,
      m30_vwap26: v26,
      m30_slope: Number(m30.vwap9_slope||0),
      m30_dir: String(m30.vwap9_dir||''),
      completed_bar: currentBar
    };
  }
  if((freshCross||firstStrong)&&buyGateOK&&!unsafe&&
    candidates.length===0&&
    !state.sent[earlyKey]&&!state.sent.BUY_SETUP&&!state.sent.BUY_CONFIRMED) {
    candidates.push({key:earlyKey,stage:'WATCH',level:1,
      reason:'30분봉 정배열 신규 포착 · 현재가 > VWAP9 > VWAP26 · MTS 확인용',
      stateKey:earlyKey});
  }
  return {item:item,result:result,state:state,events:candidates};
}
function umCommit_M30(plan,now,messageId,eventId) {
  plan.events.forEach(function(e){
    plan.state.sent[e.stateKey]={at:now.toISOString(),messageId:messageId,eventId:eventId};
    if(e.key==='MARKET_CRASH')plan.state.crashLevel=e.level;
    if(e.key==='STOP_BREACH') {
      plan.state.lastStopAt=now.toISOString();
      if(e.seq) plan.state.stopBreachSeq=e.seq;
      else if(!plan.state.stopBreachSeq) plan.state.stopBreachSeq=1;
      plan.state.stopRecovered=false;
    }
  });
  return plan.state;
}
function umStateSheet_M30(book) {
  const ss=book||SpreadsheetApp.openById(UM30.BOOK),name=UM30.STATE_SHEET||'UNIFIED_STATE';
  let sh=ss.getSheetByName(name);
  if(!sh){
    sh=ss.insertSheet(name);
    sh.getRange(1,1,1,4).setValues([['TICKER','DAY','UPDATED_AT','STATE_JSON']]);
    if(typeof sh.setFrozenRows==='function')sh.setFrozenRows(1);
    if(typeof sh.hideSheet==='function')sh.hideSheet();
  } else if(sh.getLastRow()===0){
    sh.getRange(1,1,1,4).setValues([['TICKER','DAY','UPDATED_AT','STATE_JSON']]);
    if(typeof sh.setFrozenRows==='function')sh.setFrozenRows(1);
  }
  return sh;
}

function umBackupStateSheet_M30(book,now) {
  const ss=book||SpreadsheetApp.openById(UM30.BOOK);
  const srcName=UM30.STATE_SHEET||'UNIFIED_STATE';
  const srcSheet=ss.getSheetByName(srcName);
  if(!srcSheet) return null;
  const stamp=now instanceof Date?now:new Date();
  const pad=n=>String(n).padStart(2,'0');
  const backupName=srcName+'_BACKUP_'+stamp.getFullYear()+pad(stamp.getMonth()+1)+pad(stamp.getDate())+'_'+pad(stamp.getHours())+pad(stamp.getMinutes());
  let targetSheet=ss.getSheetByName(backupName);
  if(!targetSheet) {
    targetSheet=srcSheet.copyTo(ss);
    targetSheet.setName(backupName);
  }
  return backupName;
}
function umOpenStateStore_M30(book,props,activeItems) {
  const sh=umStateSheet_M30(book),last=sh.getLastRow();
  const rows=last>1?sh.getRange(2,1,last-1,4).getValues():[];
  const map={},updatedAt={},corrupt={},rawCorrupt={};
  rows.forEach(function(r){
    const code=umCode_M30(r[0]);if(!code)return;
    const raw=String(r[3]||'').trim();if(!raw)return;
    try{
      map[code]=JSON.parse(raw);
      updatedAt[code]=r[2] instanceof Date?r[2].toISOString():String(r[2]||'');
    }catch(e){
      corrupt[code]=true;
      rawCorrupt[code]=raw;
    }
  });
  const activeSet=Array.isArray(activeItems)&&activeItems.length?
    new Set(activeItems.map(x=>umCode_M30(x.ticker||x)).filter(Boolean)):null;
  return {sheet:sh,map:map,updatedAt:updatedAt,corrupt:corrupt,rawCorrupt:rawCorrupt,
    rowCount:Math.max(0,last-1),dirty:false,activeSet:activeSet};
}
function umExtractDay_M30(raw) {
  if(!raw||typeof raw!=='string')return null;
  const m=raw.match(/"day"\s*:\s*"([^"]+)"/);
  return m?m[1]:null;
}
function umExtractSent_M30(raw) {
  if(!raw||typeof raw!=='string')return {};
  const idx=raw.indexOf('"sent"');if(idx<0)return {};
  const start=raw.indexOf('{',idx);if(start<0)return {};
  let depth=0,end=-1;
  for(let i=start;i<raw.length;i++){
    if(raw[i]==='{')depth++;
    else if(raw[i]==='}'){
      depth--;
      if(depth===0){end=i;break;}
    }
  }
  if(end>start){
    try{return JSON.parse(raw.slice(start,end+1));}catch(e){}
  }
  return {};
}
function umLoadState_M30(props,ticker,store) {
  const code=umCode_M30(ticker)||String(ticker||'');
  if(store){
    if(store.corrupt[code]){
      const raw=store.rawCorrupt&&store.rawCorrupt[code];
      const recoveredSent=umExtractSent_M30(raw);
      const recoveredDay=umExtractDay_M30(raw);
      return {day:recoveredDay,sent:recoveredSent,count:2,quarantined:true,corruptRecovered:true};
    }
    if(Object.prototype.hasOwnProperty.call(store.map,code))return store.map[code];
  }
  const raw=props&&typeof props.getProperty==='function'?props.getProperty(UM30.STATE_PREFIX+code):null;
  if(!raw)return null;
  let parsed;
  try{parsed=JSON.parse(raw);}catch(e){
    const recoveredSent=umExtractSent_M30(raw);
    const recoveredDay=umExtractDay_M30(raw);
    return {day:recoveredDay,sent:recoveredSent,count:2,quarantined:true,corruptRecovered:true};
  }
  if(store){
    store.map[code]=parsed;
    store.updatedAt[code]=(parsed.lastResult&&parsed.lastResult.at)||new Date().toISOString();
    store.dirty=true;
  }
  return parsed;
}
function umPutState_M30(store,ticker,state,now) {
  const code=umCode_M30(ticker)||String(ticker||'');
  if(!store||!code||!state)return;
  store.map[code]=state;
  store.updatedAt[code]=now instanceof Date?now.toISOString():(state.lastResult&&state.lastResult.at)||new Date().toISOString();
  delete store.corrupt[code];
  store.dirty=true;
}
function umFlushStateStore_M30(store,now) {
  if(!store||!store.dirty||!store.sheet)return {saved:0};
  const fallbackStamp=(now instanceof Date?now:new Date()).toISOString();
  const codes=Object.keys(store.map).filter(function(code){
    if(!store.map[code])return false;
    if(store.activeSet&&store.activeSet.size>0&&!store.activeSet.has(code))return false;
    return true;
  }).sort();
  const sh=store.sheet;
  if(codes.length){
    const rows=codes.map(function(code){
      const st=store.map[code];
      return [code,String(st.day||''),store.updatedAt[code]||fallbackStamp,JSON.stringify(st)];
    });
    if(typeof sh.getMaxRows==='function'&&typeof sh.insertRowsAfter==='function'&&sh.getMaxRows()<rows.length+1){
      sh.insertRowsAfter(sh.getMaxRows(),rows.length+1-sh.getMaxRows());
    }
    sh.getRange(2,1,rows.length,4).setValues(rows);
  }
  if(store.rowCount>codes.length){
    sh.getRange(codes.length+2,1,store.rowCount-codes.length,4).clearContent();
  }
  store.rowCount=codes.length;
  store.dirty=false;
  return {saved:codes.length};
}
function umLegacySeed_M30(book,props,now) {
  const day=umDay_M30(now),map={};
  const mark=(code,key)=>{if(!code||!key)return;const x=map[code]||(map[code]={day:day,sent:{},count:0});x.sent[key]={legacy:true};};
  const aliases={SIGNAL_CONF_UP:'BUY_CONFIRMED',SIGNAL_EARLY_UP:'BUY_SETUP',BASE_PAIR_UP:'BUY_SETUP',
    SIGNAL_CONF_DOWN:'DOWN_CONFIRMED',SIGNAL_EARLY_DOWN:'DOWN_EARLY',BASE_PAIR_DOWN:'DOWN_EARLY',
    STOP_BREACH_INITIAL:'STOP_BREACH',STOP_BREACH:'STOP_BREACH',OBV15_WARNING:'OBV15_WARNING',
    SELL_PREP_WARNING:'SELL_PREP_WARNING',ATR_DOWN_1_0:'ATR_DOWN_1_0'};
  umRows_M30(book,'ALERT_LOG',11).forEach(r=>{
    if(r[0] instanceof Date&&umDay_M30(r[0])===day)String(r[3]).split('|').forEach(k=>{
      const baseKey=k.split(':')[0];
      const alias=aliases[k]||aliases[baseKey];
      if(alias) mark(umCode_M30(r[1]),k.indexOf(':')>=0?k:alias);
      else if(k.startsWith('STOP_BREACH')) mark(umCode_M30(r[1]),k);
    });
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
function umDisplay_M30(item,result) {
  // Immutable source sheets: ETF_WATCHLIST!K and REGIME!Q:R contain formulas,
  // while CANDIDATES!W:Y stores historical analysis. NEVER overwrite them.
  // umRecord_M30 writes all current unified verdicts into UNIFIED_DECISIONS.
  return {ticker:item.ticker,stage:result.stage,recordedBy:'umRecord_M30'};
}
function umEventId_M30(plan,now) {
  return ['U2',umDay_M30(now),plan.result.session||'KRX_SESSION',plan.item.ticker,
    plan.events.map(e=>e.stateKey).sort().join('+')].join('_');
}
function umEscape_M30(s) { return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
// One glance first: coloured ▲/△ (up) and ▼/▽ (down).
// Keep the existing plain-text machine footer for downstream replay and
// Gmail getPlainBody() delivery reconciliation; never show it in HTML.
function umSignalGlyph_M30(stage) {
  if(stage==='BUY_CONFIRMED')return {mark:'▲',color:'#c62828'};
  if(stage==='BUY_SETUP')return {mark:'△',color:'#c62828'};
  if(stage==='DOWN_EARLY')return {mark:'▽',color:'#1565c0'};
  return {mark:'▼',color:'#1565c0'};
}
function umDisplayStage_M30(p) {
  // The underlying engine can be BUY_CONFIRMED while the emitted event is
  // deliberately only an early △ observation awaiting the next evaluation.
  return p.events&&p.events.length===1&&p.events[0].key==='VWAP30_BULL_STACK_EARLY'?
    'BUY_SETUP':p.result.stage;
}
function umSignalBrief_M30(p,now) {
  const r=p.result,item=p.item,keys=(p.events||[]).map(e=>e.key);
  const stop=Number(item.stop_price||0);
  const atr=Number(r.atr14||0);
  const price=Number(r.live_price||0);
  const inverseInfo=r.jev_gate&&r.jev_gate.status==='EARLY_MARKET_INFO';
  const officialDaily=umDirectionContext_M30(r,now).daily_basis==='COMPLETED_DAILY';
  let basis='';
  if(keys.indexOf('VWAP30_BULL_STACK_EARLY')>=0&&r.stage!=='RISK'&&!(stop>0&&price>0&&price<=stop))
    basis='30분봉 정배열 새로 포착 · 현재가 > VWAP9 > VWAP26 · MTS 관심종목';
  else if(keys.some(k=>k==='STOP_BREACH'||k.startsWith('STOP_BREACH:'))) {
    const breachEvent=(p.events||[]).find(e=>e.key==='STOP_BREACH');
    if(breachEvent&&breachEvent.reason&&breachEvent.reason.indexOf('재침범')>=0)
      basis=breachEvent.reason;
    else
      basis='실시간 손절선 '+stop.toLocaleString('ko-KR')+'원 도달·이탈';
  }
  else if(keys.indexOf('MARKET_CRASH')>=0)
    basis='시장 급락 위험 · 실시간 지수 대표 ETF';
  else if(keys.indexOf('MARKET_WEAK_EARLY')>=0)
    basis='시장 하락 조짐 · 실시간 지수 대표 ETF';
  else if(keys.indexOf('OBV15_WARNING')>=0)
    basis='30분봉 OBV 지속 약세 · 단기 매도 경고';
  else if(keys.indexOf('SELL_PREP_WARNING')>=0)
    basis='30분봉 약세 · 단기 매도 경고';
  else if(keys.indexOf('ATR_DOWN_1_0')>=0)
    basis='전일 기준 1 ATR 이상 하락 · 실시간 시세';
  else if(inverseInfo)
    basis='인버스 상승 조짐 · 기초지수 하락 확인 (매수확정 아님)';
  else if(r.stage==='BUY_CONFIRMED')
    basis=officialDaily?'완성 일봉 매수확인 · 단기 조건 별도 검증':'기술적 매수확인 · 판단 기준 미확인';
  else if(r.stage==='BUY_SETUP')
    basis=officialDaily?'완성 일봉 매수조짐 · 기술적 관심':'기술적 관심';
  else if(r.stage==='RISK')
    basis='위험경고 · 기존 위험조건 확인';
  else if(r.stage==='DOWN_CONFIRMED')
    basis=officialDaily?'하락 확인 · 완성 일봉 기술지표':'하락 확인 · 상위 추세 미확정';
  else if(r.stage==='DOWN_EARLY')
    basis=officialDaily?'하락 조짐 · 완성 일봉 기술지표':'하락 조짐 · 상위 추세 미확정';
  else
    basis='기술 신호';
  // Older completed bars are never presented as today's confirmation.
  const bar=String(r.m30&&r.m30.completed_bar||'');
  if(!keys.some(k=>k==='STOP_BREACH'||k.startsWith('STOP_BREACH:')) && bar.slice(0,10)===umDay_M30(now) &&
      !/30분봉/.test(basis))basis+=' · 30분봉 '+bar.slice(11,16)+' 참조';
  const note=umDirectionNote_M30(p,now);
  if(note)basis+=' · '+note;
  return basis;
}
function umSignalNews_M30(p) {
  const r=p.result||{}, context=r.market_context||{};
  const news=Array.isArray(r.news)?r.news:(Array.isArray(context.news)?context.news:[]);
  return news.filter(n=>n&&typeof n.title==='string'&&n.title.trim())
    .slice(0,2).map(n=>n.title.trim());
}
function umQuoteTime_M30(value) {
  if(typeof value!=='string'||
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?(?:Z|[+-]\d\d:\d\d)$/.test(value))return NaN;
  const at=Date.parse(value),zone=value.slice(-6);
  const offset=value.endsWith('Z')?0:(zone.charAt(0)==='-'?-1:1)*
    (Number(zone.slice(1,3))*60+Number(zone.slice(4,6)));
  // Reject normalized invalid dates (e.g. February 30).
  return isFinite(at)&&new Date(at+offset*60000).toISOString().slice(0,19)===value.slice(0,19)?at:NaN;
}
function umCollectMarketQuote_M30(round,item,r,receivedAt) {
  // Optional display-only data. Never fetch, persist or replace a signal verdict.
  // live_quote_time must be the provider's quote timestamp. The current API
  // omits it: fail closed, never substitute scan as_of / receipt / 30m bar time.
  try {
    if(!round||!item||(item.sources||[]).indexOf('ETF')<0)return;
    delete round.quotes[item.ticker];
    if(!isFinite(round.startedAt)||!isFinite(receivedAt)||receivedAt<round.startedAt)return;
    if(['069500','229200','114800','251340','091160','305720'].indexOf(item.ticker)<0)return;
    const at=umQuoteTime_M30(r.live_quote_time),asOf=umQuoteTime_M30(r.as_of);
    if(r.ok!==true||r.engine_version!==UM30.VERSION||r.ticker!==item.ticker||
      r.live_price_source!=='KIWOOM_KA10001'||r.session!=='KRX_SESSION'||
      typeof r.live_price!=='number'||!isFinite(r.live_price)||r.live_price<=0||
      typeof r.live_change_pct!=='number'||!isFinite(r.live_change_pct)||
      !isFinite(at)||!isFinite(asOf)||at<round.startedAt||asOf<round.startedAt||
      at>receivedAt||asOf>receivedAt||receivedAt-round.startedAt>UM30.MAX_RUNTIME)return;
    round.quotes[item.ticker]={at:at,asOf:asOf,price:r.live_price,pct:r.live_change_pct};
  } catch(e) { /* Optional context cannot interrupt technical mail delivery. */ }
}
function umMarketSituation_M30(p,round,now) {
  try {
    if(!round||round.startedAt!==now.getTime())return '';
    const q=round.quotes,own=q[p.item.ticker],r=p.result;
    if(!own||own.at!==umQuoteTime_M30(r.live_quote_time)||own.asOf!==umQuoteTime_M30(r.as_of)||
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
function umMarketSubject_M30(p,now,round) {
  const item=p.item,stage=umDisplayStage_M30(p);
  const titleStage=(p.events||[]).some(e=>e.key==='VWAP30_BULL_STACK_EARLY')?
    '30분봉 정배열 관심':({BUY_SETUP:'상승조짐',BUY_CONFIRMED:'상승확인',
      DOWN_EARLY:'하락조짐',DOWN_CONFIRMED:'하락확인',RISK:'위험경고'}[stage]||'기술신호');
  const context=umMarketSituation_M30(p,round,now);
  return '[주식 통합신호] '+umSignalGlyph_M30(stage).mark+' '+
    UM30_MARKET_MAIL_NAMES[item.ticker]+' '+titleStage+
    (context?' | '+context:'')+' · '+Utilities.formatDate(now,UM30.TZ,'MM-dd HH:mm');
}
function umMessage_M30(plans,now,round) {
  const human=[],html=[],metadata=[],marks=[];
  plans.forEach(function(p){
    const r=p.result,item=p.item,displayStage=umDisplayStage_M30(p);
    const label=(p.events||[]).some(e=>e.key==='VWAP30_BULL_STACK_EARLY')?
      '30분봉 정배열 관심':(UM30.LABELS[displayStage]||UM30.LABELS.WATCH);
    const visual=umSignalGlyph_M30(displayStage),id=umEventId_M30(p,now);
    const price=Number(r.live_price||0).toLocaleString('ko-KR')+'원';
    const change=Number(r.live_change_pct||0)*100;
    const pct=(change>0?'+':'')+change.toFixed(2)+'%';
    const brief=umSignalBrief_M30(p,now),news=umSignalNews_M30(p);
    const first=visual.mark+' '+item.name+' ('+item.ticker+')  '+price+' · 전일비 '+pct;
    human.push(first,'  '+brief);
    if(news.length)human.push('  뉴스: '+news.join(' / '));
    human.push('');
    const pctColor=change>0?'#c62828':change<0?'#1565c0':'#444444';
    const newsHtml=news.length?
      '<div style="font-size:13px;color:#555;margin-top:5px">뉴스 · '+news.map(umEscape_M30).join(' / ')+'</div>':'';
    html.push('<div style="padding:10px 0 12px;border-bottom:1px solid #eeeeee">'+
      '<div style="font-size:17px;font-weight:700;line-height:1.45">'+
      '<span style="color:'+visual.color+'">'+visual.mark+'</span> '+
      umEscape_M30(item.name)+' <span style="font-weight:400;color:#666;font-size:12px">('+umEscape_M30(item.ticker)+')</span> '+
      '<span style="white-space:nowrap">'+umEscape_M30(price)+'</span> '+
      '<span style="font-size:14px;color:'+pctColor+';white-space:nowrap">(전일비 '+umEscape_M30(pct)+')</span></div>'+
      '<div style="font-size:14px;color:#303030;margin-top:4px">'+umEscape_M30(brief)+'</div>'+
      newsHtml+'</div>');
    marks.push(visual.mark);
    metadata.push({id:id,source:item.sources.join('+'),ticker:item.ticker,name:item.name,
      stage:label,price:r.live_price,session:r.session||'KRX_SESSION'});
  });
  // Plain MIME part retains machine ids/JSON for existing parsers and retry safety.
  // The visible HTML MIME part intentionally contains only the investor's signals.
  const body=human.join('\n')+'\nUNIFIED_EVENTS_JSON='+JSON.stringify(metadata);
  const subjectHint=plans.length===1?marks[0]+' '+plans[0].item.name+' · ':Array.from(new Set(marks)).join('')+' ';
  const subject=plans.length===1&&umIsMarketMail_M30(plans[0].item)?
    umMarketSubject_M30(plans[0],now,round):
    '[주식 통합신호] '+subjectHint+plans.length+'종목 · '+Utilities.formatDate(now,UM30.TZ,'MM-dd HH:mm');
  return {subject:subject,
    body:body,htmlBody:'<div style="font-family:Arial,sans-serif;line-height:1.5">'+html.join('')+'</div>'};
}
function umRecoverDraft_M30(pending) {
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
function umDispatch_M30(plans,props,now,round,store) {
  if(!plans.length)return {sent:0};
  const recipient=hmAlertRecipient_(),msg=umMessage_M30(plans,now,round),ids=plans.map(p=>umEventId_M30(p,now));
  const pendingKey='UM2_PENDING';let pending=JSON.parse(props.getProperty(pendingKey)||'null'),sent;
  if(pending)throw new Error('PENDING_DELIVERY_MUST_RECOVER_FIRST');
  const draft=GmailApp.createDraft(recipient,msg.subject,msg.body,{htmlBody:msg.htmlBody,name:'주식 통합감시'});
  pending={ids:ids,draftId:draft.getId(),created:Date.now(),phase:'SENDING',
    plans:plans.map(p=>({item:p.item,result:p.result,state:p.state,events:p.events})),at:now.toISOString()};
  // Full data stays in a sheet so a single ScriptProperty never exceeds 9 KB.
  const sh=SpreadsheetApp.openById(UM30.BOOK).getSheetByName(UM30.CONTROL);
  const pendingRows=pending.plans.map(p=>[JSON.stringify(p)]);
  sh.getRange('E1').setValue('PENDING_DELIVERY_JSON');
  sh.getRange(2,5,pendingRows.length,1).setValues(pendingRows);
  delete pending.plans;pending.count=pendingRows.length;
  props.setProperty(pendingKey,JSON.stringify(pending));
  sent=draft.send();
  umFinishDelivery_M30(plans,sent.getId(),props,now,store);
  props.deleteProperty(pendingKey);
  sh.getRange(2,5,pendingRows.length,1).clearContent();
  return {sent:plans.length,messageId:sent.getId()};
}
function umDispatchGrouped_M30(plans,props,now,round,store) {
  // Preserve the existing draft/receipt/state transaction for every message.
  const market=[],ordinary=[],seen={};
  plans.forEach(function(p){
    const id=umEventId_M30(p,now);
    if(seen[id])return;
    seen[id]=true;
    if(umIsMarketMail_M30(p.item)){
      market.push(p);
    } else ordinary.push(p);
  });
  let sent=0;
  market.forEach(p=>{sent+=umDispatch_M30([p],props,now,round,store).sent;});
  for(let i=0;i<ordinary.length;i+=20)
    sent+=umDispatch_M30(ordinary.slice(i,i+20),props,now,null,store).sent;
  return sent;
}
function umFinishDelivery_M30(plans,messageId,props,now,store) {
  // A successful Gmail send is persisted before optional/audit operations.
  const ss=SpreadsheetApp.openById(UM30.BOOK);
  const activeStore=store||umOpenStateStore_M30(ss,props,null);
  plans.forEach(p=>{
    umPutState_M30(activeStore,p.item.ticker,umCommit_M30(p,now,messageId,umEventId_M30(p,now)),now);
  });
  umFlushStateStore_M30(activeStore,now);
  const log=ss.getSheetByName('ALERT_LOG'),existing=log.getLastRow()>1?
    log.getRange(Math.max(2,log.getLastRow()-500),11,Math.min(500,log.getLastRow()-1),1).getDisplayValues().flat().join('\n'):'';
  const rows=[];
  plans.forEach(p=>{
    const id=umEventId_M30(p,now);if(existing.indexOf(id)>=0)return;
    rows.push([now,p.item.ticker,p.item.name,p.events.map(e=>e.key).join('|'),p.result.stage,
      p.result.live_price,p.result.live_change_pct||0,p.item.stop_price||'','',now,
      id+' | '+p.item.sources.join('+')+' | '+p.events.map(e=>e.reason).join(' / ')+' | GmailId='+messageId]);
  });
  if(rows.length)log.getRange(log.getLastRow()+1,1,rows.length,11).setValues(rows);
}
function umResumeDelivery_M30(props,store) {
  const raw=props.getProperty('UM2_PENDING');if(!raw)return;
  const pending=JSON.parse(raw),sh=SpreadsheetApp.openById(UM30.BOOK).getSheetByName(UM30.CONTROL);
  const plans=sh.getRange(2,5,pending.count,1).getValues().map(r=>JSON.parse(r[0]));
  const message=umRecoverDraft_M30(pending);
  umFinishDelivery_M30(plans,message.getId(),props,new Date(pending.at),store);
  props.deleteProperty('UM2_PENDING');sh.getRange(2,5,pending.count,1).clearContent();
}
function umRequest_M30(item,props) {
  const url=String(props.getProperty('SIGNAL_API_URL')||'').replace(/\/$/,''),token=props.getProperty('SIGNAL_API_TOKEN');
  if(!url||!token)throw new Error('UNIFIED_API_NOT_CONFIGURED');
  const data=Object.assign({},item);delete data.refs;delete data.quantity;delete data.average_price;
  return {url:url+'/unified-scan',method:'post',contentType:'application/json',
    headers:{'X-StockBot-Token':token},payload:JSON.stringify(data),muteHttpExceptions:true};
}
function umRecord_M30(rows) {
  if(!rows.length)return;
  const ss=SpreadsheetApp.openById(UM30.BOOK),name='UNIFIED_DECISIONS';
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
function umEtfDiagnostics_M30(audit) {
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
function umSaveEtfDiagnostics_M30(control,audit,now,expected) {
  const d=umEtfDiagnostics_M30(audit),healthy=(expected===0||d.evaluated===expected);
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
function umNotifyFailure_M30(err,props,now) {
  const last=Number(props.getProperty('UM2_LAST_ERROR_MAIL')||0);
  if(now.getTime()-last<3600000)return;
  MailApp.sendEmail({to:hmAlertRecipient_(),subject:'[주식 통합감시 장애] 점검 필요',
    body:'통합감시 실행 또는 발송이 완료되지 않았습니다. 미발송 경보는 다음 실행에서 재시도합니다.\n'+String(err).slice(0,500)});
  props.setProperty('UM2_LAST_ERROR_MAIL',String(now.getTime()));
}
/** P0: isolate UrlFetchApp transport errors instead of aborting an entire watch pass. */
function umP0Quota_M30(err) { return /Bandwidth quota exceeded|Service invoked too many times/i.test(String(err||'')); }
function umP0Fetch_M30(requests) {
  const out=requests.map(()=>({response:null,error:'NOT_ATTEMPTED'}));
  let blocked=false;
  try {
    const responses=UrlFetchApp.fetchAll(requests);
    responses.forEach((response,i)=>{out[i]={response:response,error:''};});
  } catch(err) {
    if(umP0Quota_M30(err))return {results:out.map(()=>({response:null,error:'BANDWIDTH_QUOTA'})),blocked:true};
    // A batch-level exception does not identify the bad ticker. Isolate calls.
    for(let i=0;i<requests.length;i++) {
      try {out[i]={response:UrlFetchApp.fetch(requests[i]),error:''};}
      catch(e) {out[i]={response:null,error:String(e).slice(0,180)};if(umP0Quota_M30(e)){blocked=true;break;}}
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
      if(umP0Quota_M30(e)){blocked=true;break;}
    }
  }
  return {results:out,blocked:blocked};
}
/** P0 bandwidth: one URL Fetch for up to six existing per-symbol scan calls.
 * Response parity is enforced per ticker; no missing or reordered ticker can
 * silently produce a buy signal. Quota breaker and retry queue remain intact.
 */
function umP0BatchFetch_M30(requests) {
  const groups=[],out=requests.map(()=>({response:null,error:'NOT_ATTEMPTED'}));
  for(let start=0;start<requests.length;start+=UM30.GROUP_SIZE) {
    const members=requests.slice(start,start+UM30.GROUP_SIZE);
    const first=members[0];
    if(!first.url.endsWith('/unified-scan'))throw new Error('P0_EXPECTED_UNIFIED_SCAN_URL');
    const req=Object.assign({},first,{
      url:first.url.slice(0,-('/unified-scan'.length))+'/unified-scan-batch',
      payload:JSON.stringify({items:members.map(r=>JSON.parse(r.payload))})
    });
    groups.push({start:start,requests:members,request:req});
  }
  if(!groups.length)return {results:out,blocked:false};
  const fetched=umP0Fetch_M30(groups.map(g=>g.request));
  groups.forEach(function(g,idx) {
    const packet=fetched.results[idx]||{},response=packet.response;
    let payload=null,reason=packet.error||'';
    try{
      if(response&&response.getResponseCode()===200)
        payload=JSON.parse(response.getContentText());
      else if(response)reason='BATCH_HTTP_'+response.getResponseCode();
    }catch(e){reason='BATCH_INVALID_JSON';}
    if(!payload||payload.ok!==true||payload.engine_version!==UM30.VERSION||
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
function umP0Schedule_M30(universe,props,mode,now) {
  const key='UM2_P0_SCAN_'+mode,day=umDay_M30(now);
  let prev={};try{prev=JSON.parse(props.getProperty(key)||'{}');}catch(e){}
  if(prev.day!==day)prev={day:day,cursor:0,retry:[]};

  const urgent=universe.filter(x=>umIsPriority_M30(x));
  const normal=universe.filter(x=>!umIsPriority_M30(x));
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
    const rank=c=>umIsPriority_M30(known[c])?0:1;
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
function umP0Checkpoint_M30(props,scan,nextCursor) {
  props.setProperty(scan.key,JSON.stringify({day:scan.day,group:scan.group,cursor:nextCursor,
    retry:Array.from(scan.retry).slice(0,500)}));
}
function umTick5mM30(now) {
  now=now||new Date();const start=Date.now(),mode=umMode_M30();
  if(['ACTIVE','PREVIEW'].indexOf(mode)<0)return {status:'OFF'};
  if(!hmIsMarketWindow_(now)){
    try{if(mode==='ACTIVE'&&typeof pgMaybeClose_M30==='function')pgMaybeClose_M30(now);}
    catch(e){console.error('PAPER_CLOSE_ISOLATED: '+String(e));}
    if(mode==='ACTIVE'&&typeof e30SafeTick_==='function')e30SafeTick_(new Date(),false);
    if(mode==='ACTIVE')m30SafeMaintenance_(new Date(),false);
    return {status:'OUTSIDE_SESSION'};
  }
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'LOCKED'};
  const props=PropertiesService.getScriptProperties(),slot=String(Math.floor(start/300000));
  try {
    if(mode==='ACTIVE')umResumeDelivery_M30(props);
    if(props.getProperty('UM2_COMPLETED_'+mode)===slot)return {status:'SLOT_DONE'};
    m30Book_();
    const book=SpreadsheetApp.openById(UM30.BOOK),all=umUniverse_M30(now),control=book.getSheetByName(UM30.CONTROL);
    const universe=mode==='PREVIEW'?all.filter(x=>['069500','114800','373220','103590','001430','490590','007660'].indexOf(x.ticker)>=0):all;
    const stateStore=mode==='ACTIVE'?umOpenStateStore_M30(book,props,all):null;
    const legacy=mode==='ACTIVE'?umLegacySeed_M30(book,props,now):{};
    let evaluated=0,sent=0,errors=0;const audit=[],allPlans=[],paperSamples=[],paperDiagnostics=[],e30Samples=[];
    let e30Cfg={enabled:false};
    try{if(typeof e30Config_==='function')e30Cfg=e30Config_();}catch(e){console.error('E30_CONFIG_ISOLATED: '+String(e));}
    const marketRound={startedAt:now.getTime(),quotes:{}};
    // P0: urgent holdings and KOSPI200 risk are checked every slot. Rotate the
    // remaining watchlist so a partial 5-minute run never starves tail symbols.
    const scan=umP0Schedule_M30(universe,props,mode,now);
    let processed=0,nextCursor=scan.start,transportBlocked=false;
    const failedEntries=[];
    for(let pos=0;pos<scan.work.length;pos+=UM30.BATCH) {
      if(Date.now()-start>UM30.MAX_RUNTIME)break;
      const batch=scan.work.slice(pos,pos+UM30.BATCH);
      const reqs=batch.map(entry=>umRequest_M30(entry.item,props));
      const fetched=umP0BatchFetch_M30(reqs),plans=[];
      batch.forEach(function(entry,i){
        const item=entry.item,packet=fetched.results[i]||{},response=packet.response;
        if(entry.kind==='NORMAL')
          nextCursor=entry.offset+1;
        let code=0,r={};
        try {
          if(response){code=response.getResponseCode();if(code===200)r=JSON.parse(response.getContentText());}
        }catch(e){r={};}
        if(code!==200||r.ok!==true||r.engine_version!==UM30.VERSION){
          const reason=packet.error||('HTTP_'+code);
          failedEntries.push({entry:entry,item:item,reason:reason});
          return;
        }
        try {
          umCollectMarketQuote_M30(marketRound,item,r,Date.now());
          if(mode==='ACTIVE'){
            const p=umPlan_M30(item,r,umLoadState_M30(props,item.ticker,stateStore)||legacy[item.ticker],now);
            if(p.events.length)plans.push(p);
            else umPutState_M30(stateStore,item.ticker,p.state,now);
            umDisplay_M30(item,r);
            try{if(typeof e30Observation_==='function'){const pe=e30Observation_(item,r,now.toISOString(),new Date(),e30Cfg);if(pe)e30Samples.push(pe);}}
            catch(e){if(typeof e30ReportError_==='function')e30ReportError_('CAPTURE',e,new Date());}
            try{if(typeof pgCandidate_M30==='function'){const ps=pgCandidate_M30(item,r,now,new Date(),paperDiagnostics);if(ps)paperSamples.push(ps);}}
            catch(e){console.error('PAPER_CAPTURE_ISOLATED: '+String(e));if(typeof pgExecutionDiagnostic_M30==='function')paperDiagnostics.push(pgExecutionDiagnostic_M30('PG_CAPTURE',item.ticker,e));}
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
      umP0Checkpoint_M30(props,scan,nextCursor);
      if(fetched.blocked){
        transportBlocked=true;
        scan.work.slice(processed).forEach(e=>{scan.retry.add(e.item.ticker);});
        umP0Checkpoint_M30(props,scan,nextCursor);
        break;
      }
    }

    // P0-5: Bounded same-slot 1x retry for transient 503 / timeout failures
    if(!transportBlocked && failedEntries.length>0 && (Date.now()-start+25000<UM30.MAX_RUNTIME)){
      const retryCandidates=failedEntries.sort(function(a,b){
        const rank=e=>e.item.ticker==='069500'?0:umIsPriority_M30(e.item)?1:2;
        return rank(a)-rank(b);
      }).slice(0,12);
      Utilities.sleep(1000);
      const retryReqs=retryCandidates.map(c=>umRequest_M30(c.item,props));
      const retryFetched=umP0BatchFetch_M30(retryReqs),retryPlans=[];
      const retriedSet=new Set();
      retryCandidates.forEach(function(cand,i){
        retriedSet.add(cand.item.ticker);
        const item=cand.item,packet=retryFetched.results[i]||{},response=packet.response;
        let code=0,r={};
        try {
          if(response){code=response.getResponseCode();if(code===200)r=JSON.parse(response.getContentText());}
        }catch(e){r={};}
        if(code!==200||r.ok!==true||r.engine_version!==UM30.VERSION){
          errors++;scan.retry.add(item.ticker);
          const reason=packet.error||('HTTP_'+code);
          audit.push([now,item.ticker,item.sources.join('+'),'ERROR',false,'',String(reason).slice(0,180),UM30.VERSION,'']);
          return;
        }
        try {
          umCollectMarketQuote_M30(marketRound,item,r,Date.now());
          if(mode==='ACTIVE'){
            const p=umPlan_M30(item,r,umLoadState_M30(props,item.ticker,stateStore)||legacy[item.ticker],now);
            if(p.events.length)retryPlans.push(p);
            else umPutState_M30(stateStore,item.ticker,p.state,now);
            umDisplay_M30(item,r);
            try{if(typeof e30Observation_==='function'){const pe=e30Observation_(item,r,now.toISOString(),new Date(),e30Cfg);if(pe)e30Samples.push(pe);}}
            catch(e){if(typeof e30ReportError_==='function')e30ReportError_('CAPTURE',e,new Date());}
            try{if(typeof pgCandidate_M30==='function'){const ps=pgCandidate_M30(item,r,now,new Date(),paperDiagnostics);if(ps)paperSamples.push(ps);}}
            catch(e){console.error('PAPER_CAPTURE_ISOLATED: '+String(e));if(typeof pgExecutionDiagnostic_M30==='function')paperDiagnostics.push(pgExecutionDiagnostic_M30('PG_CAPTURE',item.ticker,e));}
          }
          evaluated++;scan.retry.delete(item.ticker);
          audit.push([now,item.ticker,item.sources.join('+'),r.stage,r.send_allowed,r.live_price||'',
            (r.reasons||[]).join('|'),r.engine_version,(r.events||[]).map(e=>e.key).join('|')]);
        }catch(e){
          errors++;scan.retry.add(item.ticker);
          audit.push([now,item.ticker,item.sources.join('+'),'ERROR',false,'',
            'STATE_'+String(e).slice(0,160),UM30.VERSION,'']);
        }
      });
      if(retryPlans.length)allPlans.push.apply(allPlans,retryPlans);
      failedEntries.forEach(function(cand){
        if(!retriedSet.has(cand.item.ticker)){
          errors++;scan.retry.add(cand.item.ticker);
          audit.push([now,cand.item.ticker,cand.item.sources.join('+'),'ERROR',false,'',String(cand.reason).slice(0,180),UM30.VERSION,'']);
        }
      });
      umP0Checkpoint_M30(props,scan,nextCursor);
    } else if(failedEntries.length>0){
      failedEntries.forEach(function(cand){
        errors++;scan.retry.add(cand.item.ticker);
        audit.push([now,cand.item.ticker,cand.item.sources.join('+'),'ERROR',false,'',String(cand.reason).slice(0,180),UM30.VERSION,'']);
      });
      umP0Checkpoint_M30(props,scan,nextCursor);
    }
    if(mode==='ACTIVE')umFlushStateStore_M30(stateStore,now);
    if(mode==='ACTIVE'&&allPlans.length){
      // Keep Gmail's draft/receipt reconciliation and per-ticker event ids.
      // Bounded digest also stays well below Apps Script 9-KB property limits.
      sent+=umDispatchGrouped_M30(allPlans,props,now,marketRound,stateStore);
    }
    umRecord_M30(audit);
    try{if(mode==='ACTIVE'&&e30Cfg.enabled&&typeof e30Save_==='function')e30Save_(e30Samples,new Date());}
    catch(e){if(typeof e30ReportError_==='function')e30ReportError_('SAVE',e,new Date());}
    try{
      audit.filter(r=>r[3]==='ERROR'&&typeof PG30!=='undefined'&&PG30.CODES.indexOf(r[1])>=0)
        .forEach(r=>paperDiagnostics.push([new Date().toISOString(),'PG_TRANSPORT',r[1],String(r[6]),'EXEC_ERROR']));
      if(mode==='ACTIVE'&&typeof pgSaveDiagnostics_M30==='function')pgSaveDiagnostics_M30(paperDiagnostics);
    }catch(e){console.error('PAPER_DIAGNOSTIC_SAVE_ISOLATED: '+String(e));}
    try{if(mode==='ACTIVE'&&typeof pgSaveSamples_M30==='function')pgSaveSamples_M30(paperSamples,now);}
    catch(e){
      console.error('PAPER_SNAPSHOT_ISOLATED: '+String(e));
      try{if(typeof pgSaveDiagnostics_M30==='function'&&typeof pgExecutionDiagnostic_M30==='function')pgSaveDiagnostics_M30([pgExecutionDiagnostic_M30('PG_SAVE',PG30.CODES.join('|'),e)]);}catch(ignored){}
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
      [UM30.VERSION],[errors?'API_ERRORS':done?'OK':'INCOMPLETE_SCAN']]);
    // Diagnostics are observational and must only follow true full scans.
    if(mode==='ACTIVE' && done){
      try{
        const expected=scan.work.map(w=>w.item).filter(x=>(x.sources||[]).indexOf('ETF')>=0).length;
        const diag=umSaveEtfDiagnostics_M30(control,audit,now,expected);
        if(expected>0 && diag.status!=='OK')umNotifyFailure_M30('ETF 평가 누락: '+diag.details.evaluated+'/'+expected,props,now);
      }catch(e){console.error('ETF_DIAGNOSTICS_WRITE_FAILED: '+String(e));}
    }
    if(errors&&mode==='ACTIVE')umNotifyFailure_M30('API 확인 실패 '+errors+'개 종목',props,now);
    if(done&&!errors&&mode==='ACTIVE'){
      // Keep original trigger installations through cutover verification.
      // Their entrypoints delegate to umTick5mM30 in ACTIVE and existing slot/lock
      // gates prevent duplicate sends. Deletion requires a separate verified
      // maintenance step, never the first successful active run.
      console.log('UNIFIED_ACTIVE_OK original triggers preserved for rollback');
    }
    return {status:done&&!errors?'OK':'PARTIAL',mode:mode,universe:universe.length,evaluated:evaluated,sent:sent,errors:errors};
  }catch(err){
    if(mode==='ACTIVE')try{umNotifyFailure_M30(err,props,now);}catch(notifyErr){console.error(String(notifyErr));}
    throw err;
  }finally{
    lock.releaseLock();
    // Gmail lookup / closing fallbacks only follow the existing monitor work.
    if(mode==='ACTIVE'&&typeof e30SafeTick_==='function')e30SafeTick_(new Date(),true);
    if(mode==='ACTIVE')m30SafeMaintenance_(new Date(),true);
  }
}
