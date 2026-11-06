/** E30 paper only. Append-only inputs are authoritative; projections are repairable.
 * No order API, no trigger installation, no A-D state or ledger writes.
 */
const E30 = Object.freeze({BOOK:'18TM6tjbu6IvK4zYDs9JnZhGikGhRQeedOGSTvhTkA_E',
  CAPITAL:10000000,BUDGET:3000000,FEE:.0006,SLIP:.001,
  CODES:['069500','114800'],STRATEGIES:['E30-T','E30-R','E30-G','E30-H'],
  TABS:{E30_INPUTS:['ID','Day','Kind','ReceivedAt','JSON'],
    E30_TRADES:['ID','Day','Strategy','Code','Side','Qty','RawPrice','FillPrice','Fee','CashFlow','MarketTime','ObservedAt','Model'],
    E30_NAV:['ID','Day','Strategy','Cash','NAV','Status'],
    E30_STATUS:['ID','Day','Strategy','Status','Detail']}});
function e30Time_(x){return typeof x==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(x)?Date.parse(x):NaN;}
function e30Day_(x){const t=x instanceof Date?x.getTime():e30Time_(x);return isFinite(t)?new Date(t+9*3600000).toISOString().slice(0,10):'';}
function e30At_(day,time){return Date.parse(day+'T'+time+':00+09:00');}
function e30Positive_(x){return typeof x==='number'&&isFinite(x)&&x>0;}
function e30Config_(){
  const p=PropertiesService.getScriptProperties(),start='2026-11-05';
  return {enabled:p.getProperty('E30_ENABLED')==='ON'&&/^\d{4}-\d\d-\d\d$/.test(start),
    start:start,end:p.getProperty('E30_END_DATE')||'2026-11-30',
    sender:p.getProperty('E45_FORECAST_SENDER')||'jujinu0410@gmail.com'};
}
function e30InScope_(day,cfg){return day>=cfg.start&&day<=cfg.end;}
function e30Event_(day,kind,id,data,received){return {id:id,day:day,kind:kind,at:received.toISOString(),data:data};}
function e30Observation_(item,r,round,received,cfg){
  if(!cfg.enabled||['069500','114800','229200','251340'].indexOf(item.ticker)<0||!e30InScope_(e30Day_(received),cfg))return null;
  const pd=r.paper_data||{};
  return e30Event_(e30Day_(received),'OBS','OBS|'+round+'|'+item.ticker,
    {code:item.ticker,round:round,ok:r.ok===true,price:r.live_price,
      source:r.live_price_source,paper:pd},received);
}
function e30FirstValid_(event){
  const d=event.data,p=d.paper||{},b=p.first30||{},day=event.day,at=e30Time_(event.at),computed=e30Time_(p.computed_at);
  return p.source==='KIWOOM_15M_BAR_START'&&p.schema===2&&p.bar_interval_minutes===30&&d.code==='069500'&&d.ok&&b.completed===true&&b.quality==='VALID'&&
    e30Time_(b.start)===e30At_(day,'09:00')&&e30Time_(b.end)===e30At_(day,'09:30')&&
    computed>=e30At_(day,'09:30')&&computed<=at&&at<=e30At_(day,'10:00')&&
    ['open','high','low','close'].every(k=>e30Positive_(b[k]))&&
    b.high>=Math.max(b.open,b.close,b.low)&&b.low<=Math.min(b.open,b.close,b.high);
}
function e30QuoteValid_(event,signal){
  const d=event.data,p=d.paper||{},qt=e30Time_(p.quote_received_at),at=e30Time_(event.at);
  return d.ok&&d.source==='KIWOOM_KA10001'&&e30Positive_(d.price)&&
    e30Day_(p.quote_received_at)===event.day&&qt>e30Time_(signal.at)&&qt<=at&&
    at-qt<=120000&&at<=e30At_(event.day,'10:00')&&d.round!==signal.data.round;
}
function e30ForecastValid_(f,day){
  const t=e30Time_(f.received_at);
  return f.day===day&&!!f.source_id&&/^[a-f0-9]{64}$/.test(f.sha256||'')&&
    (f.direction==='UP'||f.direction==='DOWN')&&t>=e30At_(day,'00:00')&&t<e30At_(day,'09:00');
}
function e30CloseValid_(c,day,now){
  const t=e30Time_(c.observed_at),end=e30At_(day,'15:30');
  return c.day===day&&E30.CODES.indexOf(c.ticker)>=0&&e30Positive_(c.close)&&
    c.quality==='CONFIRMED_DAILY_CLOSE'&&c.paper_fill===true&&
    ['NAVER_DAILY_AFTER_1610','GOOGLEFINANCE_COMPLETED_DAILY','KIWOOM_KA10081_KRX_DAILY'].indexOf(c.source)>=0&&
    e30Time_(c.market_time)===end&&t>=e30At_(day,'16:10')&&t<=now;
}
function e30Compute_(input,now,cfg){
  const nowMs=now.getTime(),seen=new Set(),events=input.filter(e=>{
    if(!e||seen.has(e.id)||!isFinite(e30Time_(e.at))||e30Time_(e.at)>nowMs)return false;
    seen.add(e.id);return /^\d{4}-\d\d-\d\d$/.test(e.day)&&e30InScope_(e.day,cfg);
  }).sort((a,b)=>e30Time_(a.at)-e30Time_(b.at)||a.id.localeCompare(b.id));
  const days=Array.from(new Set(events.map(e=>e.day))).sort(),cash={},positions={},releasedAt={},trades=[],nav=[],status=[];
  E30.STRATEGIES.forEach(s=>{cash[s]=E30.CAPITAL;releasedAt[s]=0;});
  days.forEach(day=>{
    if(m30QuarantinedDay_(day)){E30.STRATEGIES.forEach(s=>{status.push([day+'|'+s,day,s,'DATA_INTEGRITY_HOLD','First30 source watermark invalid; existing raw trades preserved but excluded']);nav.push([day+'|'+s,day,s,cash[s],cash[s],'DATA_INTEGRITY_HOLD']);});return;}
    const rows=events.filter(e=>e.day===day),obs=rows.filter(e=>e.kind==='OBS'),signal=obs.find(e30FirstValid_);
    let pair=null;
    if(signal){
      const rounds={};
      obs.filter(e=>e30QuoteValid_(e,signal)).forEach(e=>{
        const g=rounds[e.data.round]||(rounds[e.data.round]={});if(!g[e.data.code])g[e.data.code]=e;
      });
      pair=Object.values(rounds).filter(g=>E30.CODES.every(c=>g[c])&&
        Math.abs(e30Time_(g['069500'].data.paper.quote_received_at)-e30Time_(g['114800'].data.paper.quote_received_at))<=120000)
        .sort((a,b)=>Math.max(...E30.CODES.map(c=>e30Time_(a[c].at)))-Math.max(...E30.CODES.map(c=>e30Time_(b[c].at))))[0];
    }
    const decisionAt=pair?Math.max(...E30.CODES.map(c=>e30Time_(pair[c].at))):NaN;
    const forecast=rows.find(e=>e.kind==='FORECAST'&&e30Time_(e.at)<=decisionAt&&e30ForecastValid_(e.data,day));
    const f=forecast&&e30ForecastValid_(forecast.data,day)?forecast.data.direction:'';
    const bar=signal&&signal.data.paper.first30;
    const direction=bar?(bar.close>bar.open?'UP':bar.close<bar.open?'DOWN':'FLAT'):'';
    const technicalPair=pair,receipt=rows.find(e=>e.kind==='FORECAST'&&e30ForecastValid_(e.data,day));
    const forecastPair=receipt&&signal?e30PairAfterReceipt_(obs,signal,e30Time_(receipt.at)):null;
    E30.STRATEGIES.forEach(s=>{
      const pair=(s==='E30-G'||s==='E30-H')?forecastPair:technicalPair;
      const decisionAt=pair?Math.max(...E30.CODES.map(c=>e30Time_(pair[c].at))):NaN;
      const forecast=receipt&&e30Time_(receipt.at)<=decisionAt?receipt:null;
      const f=forecast?forecast.data.direction:'';
      let state=nowMs>e30At_(day,'10:00')?'DATA_MISSING':'WAIT_SIGNAL',detail='';
      let desired='';
      if(pair){
        desired=s==='E30-G'?f:s==='E30-H'?(f===direction?f:''):s==='E30-R'?(direction==='UP'?'DOWN':direction==='DOWN'?'UP':''):direction;
        if((s==='E30-G'||s==='E30-H')&&!f)state='FORECAST_MISSING';
        else if(s!=='E30-G'&&direction==='FLAT')state='NO_TRADE_FLAT';
        else if(s==='E30-H'&&!desired)state='NO_MATCH';
        else if(desired==='UP'||desired==='DOWN'){
          if(positions[s]||decisionAt<releasedAt[s]){state='PRIOR_CLOSE_PENDING';}
          else{
            const code=desired==='UP'?'069500':'114800',q=pair[code],raw=q.data.price,fill=raw*(1+E30.SLIP);
            const qty=Math.floor(Math.min(E30.BUDGET,cash[s])/(fill*(1+E30.FEE)));
            if(qty<1)state='NO_BUDGET';
            else{
              const amount=qty*fill,fee=amount*E30.FEE;
              cash[s]-=amount+fee;positions[s]={day:day,code:code,qty:qty,cost:amount+fee};
              trades.push([day+'|'+s+'|BUY',day,s,code,'BUY',qty,raw,fill,fee,-amount-fee,
                '',q.data.paper.quote_received_at,'POST_SIGNAL_OBSERVED_PAPER_FILL']);state='OPEN';
            }
          }
        }
      }else if(signal){state=nowMs>e30At_(day,'10:00')?'QUOTE_MISSING':'WAIT_NEXT_COLLECTION';}
      const pos=positions[s];
      if(pos&&pos.day===day){
        // A later collection may resolve an older day's pending close. Preserve
        // input received time as well as the provider-observation time.
        const ce=events.find(e=>e.kind==='CLOSE'&&e.data.day===day&&e.data.ticker===pos.code&&
          e30CloseValid_(e.data,day,nowMs)&&e30Time_(e.data.observed_at)<=e30Time_(e.at));
        const order=rows.find(e=>e.kind==='AUCTION_ORDER'&&e30Time_(e.at)>=e30At_(day,'15:20')&&e30Time_(e.at)<e30At_(day,'15:30'));
        if(ce&&order){
          const c=ce.data,fill=c.close*(1-E30.SLIP),revenue=pos.qty*fill,fee=revenue*E30.FEE;
          cash[s]+=revenue-fee;
          trades.push([day+'|'+s+'|SELL',day,s,pos.code,'SELL',pos.qty,c.close,fill,fee,revenue-fee,
            c.market_time,c.observed_at,'VIRTUAL_AUCTION_MARKET_SUBMITTED_1520_1530_ASSUMED_CLOSE']);delete positions[s];releasedAt[s]=e30Time_(ce.at);state='CLOSED';
        }else if(nowMs>=e30At_(day,'15:30'))state='CLOSE_PENDING';
      }
      detail=JSON.stringify({first30:direction||'MISSING',signal_at:signal?signal.at:null,
        forecast_id:forecast?forecast.data.source_id||null:null,paper_only:true});
      status.push([day+'|'+s,day,s,state,detail]);
      // Pending positions have no invented NAV / mark-to-market price.
      nav.push([day+'|'+s,day,s,cash[s],positions[s]?'':cash[s],state]);
    });
  });
  return {trades:trades,nav:nav,status:status};
}
function e30Book_(){
  const ss=SpreadsheetApp.openById(E30.BOOK);
  Object.keys(E30.TABS).forEach(name=>{
    const sh=ss.getSheetByName(name)||ss.insertSheet(name),headers=E30.TABS[name];
    if(!sh.getLastRow())sh.getRange(1,1,1,headers.length).setValues([headers]);
    else if(JSON.stringify(sh.getRange(1,1,1,headers.length).getValues()[0])!==JSON.stringify(headers))throw Error('E30_SCHEMA_'+name);
  });return ss;
}
function e30Read_(ss){
  const sh=ss.getSheetByName('E30_INPUTS');if(sh.getLastRow()<2)return [];
  return sh.getRange(2,1,sh.getLastRow()-1,5).getValues().map(r=>({id:String(r[0]),day:pgStoredDate_M30(r[1],ss.getSpreadsheetTimeZone(),false),kind:String(r[2]),at:r[3] instanceof Date?r[3].toISOString():String(r[3]),data:JSON.parse(r[4])}));
}
function e30Append_(ss,events){
  const existing=e30Read_(ss),ids=new Set(existing.map(e=>e.id)),fresh=[];
  events.filter(Boolean).forEach(e=>{if(!ids.has(e.id)){ids.add(e.id);fresh.push([e.id,e.day,e.kind,e.at,JSON.stringify(e.data)]);}});
  if(fresh.length){const sh=ss.getSheetByName('E30_INPUTS');sh.getRange(sh.getLastRow()+1,1,fresh.length,4).setNumberFormat('@');sh.getRange(sh.getLastRow()+1,1,fresh.length,5).setValues(fresh);}
}
function e30Project_(ss,name,rows){
  const sh=ss.getSheetByName(name),width=E30.TABS[name].length,last=sh.getLastRow();
  const norm=r=>r.map((v,i)=>i===1?pgStoredDate_M30(v,ss.getSpreadsheetTimeZone(),false):name==='E30_TRADES'&&i===3?pgStoredCode_M30(v):name==='E30_TRADES'&&(i===10||i===11)&&v&&isFinite(Date.parse(v))?new Date(v).toISOString():v instanceof Date?v.toISOString():v);
  const old=last>1?sh.getRange(2,1,last-1,width).getValues().map(norm):[],index={};
  const put=(row,r)=>{sh.getRange(row,1,1,3).setNumberFormat('@');if(name==='E30_TRADES'){sh.getRange(row,4,1,2).setNumberFormat('@');sh.getRange(row,11,1,3).setNumberFormat('@');}sh.getRange(row,1,1,width).setValues([r]);};
  old.forEach((r,i)=>{if(index[r[0]])throw Error('E30_DUPLICATE_PROJECTION');index[r[0]]=i+2;});
  rows.forEach(raw=>{const r=norm(raw);
    if(index[r[0]]){
      // Trade IDs are immutable. A mismatch is corruption, never a second fill.
      const previous=old[index[r[0]]-2];
      if(name==='E30_TRADES'&&JSON.stringify(previous)!==JSON.stringify(r))throw Error('E30_TRADE_CONFLICT');
      if(JSON.stringify(previous)!==JSON.stringify(r))put(index[r[0]],r);
    }else{put(sh.getLastRow()+1,r);}
  });
}
function e30Repair_(ss,now,cfg){
  const result=e30Compute_(e30Read_(ss),now,cfg);
  e30Project_(ss,'E30_TRADES',result.trades);e30Project_(ss,'E30_NAV',result.nav);e30Project_(ss,'E30_STATUS',result.status);return result;
}
function e30Save_(events,now){
  const cfg=e30Config_();if(!cfg.enabled)return {status:'OFF'};
  const ss=e30Book_();e30Append_(ss,events);return e30Repair_(ss,now,cfg);
}
function e30ParseForecast_(plain,day){
  const matches=Array.from(String(plain).replace(/<[^>]*>/g,' ').replace(/&nbsp;|&#160;/gi,' ').matchAll(/오늘\s*시가\s*대비\s*종가\s*전망\s*[:：]\s*(상승|하락)/g));
  const directions=Array.from(new Set(matches.map(x=>x[1])));
  return directions.length===1?(directions[0]==='상승'?'UP':'DOWN'):'';
}
function e30FetchForecast_(day,cfg,now){
  const parts=day.split('-').map(Number),subject='[모바일 대시보드] '+parts[0]+'년 '+parts[1]+'월 '+parts[2]+'일 일일 모닝 브리핑';
  const threads=GmailApp.search('from:'+cfg.sender+' subject:"'+subject+'" newer_than:3d -in:trash -in:spam',0,10),eligible=[];
  threads.forEach(t=>t.getMessages().forEach(m=>{
    if(m.getSubject()!==subject||m.getFrom().indexOf(cfg.sender)<0)return;
    // getDate/header/footer are NOT evidence of actual Gmail receipt time.
    const meta=Gmail.Users.Messages.get('me',m.getId(),{format:'metadata',fields:'internalDate'}),at=Number(meta.internalDate);
    if(!isFinite(at)||at<e30At_(day,'00:00')||at>=e30At_(day,'09:00')||at>now.getTime())return;
    const plain=m.getPlainBody(),html=m.getBody(),choices=Array.from(new Set([e30ParseForecast_(plain,day),e30ParseForecast_(html,day)].filter(Boolean))),direction=choices.length===1?choices[0]:'';
    if(!direction)console.log('E30_FORECAST_PARSE_DIAGNOSTIC '+JSON.stringify(String(plain).match(/.{0,20}시가.{0,40}전망.{0,100}/g)));
    const hash=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,JSON.stringify({plain:plain,html:html}),Utilities.Charset.UTF_8)
      .map(b=>('0'+((b+256)%256).toString(16)).slice(-2)).join('');
    eligible.push({day:day,source_id:m.getId(),received_at:new Date(at).toISOString(),direction:direction,sha256:hash});
  }));
  eligible.sort((a,b)=>e30Time_(a.received_at)-e30Time_(b.received_at)||a.source_id.localeCompare(b.source_id));
  const unique=new Set(eligible.map(f=>f.direction));
  const f=eligible.length&&unique.size===1&&eligible[0].direction?eligible[0]:{day:day,direction:'',reason:eligible.length?'AMBIGUOUS':'MISSING'};
  return e30Event_(day,'FORECAST','FORECAST|'+day+'|'+(f.source_id||f.reason),f,now);
}
function e30RepairForecastReceipt(){
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'LOCKED'};
  try{const now=new Date(),day=e30Day_(now),ss=e30Book_(),old=e30Read_(ss);
    if(old.some(e=>e.kind==='FORECAST'&&e30ForecastValid_(e.data,day)))return {status:'ALREADY_VALID'};
    const event=e30FetchForecast_(day,e30Config_(),now);
    if(!e30ForecastValid_(event.data,day))throw Error('FORECAST_NOT_VALID_'+JSON.stringify(event.data));
    e30Append_(ss,[event]);console.log(JSON.stringify({status:'FORECAST_RECEIPT_STORED',direction:event.data.direction,received_at:event.data.received_at,source_id:event.data.source_id}));
    return {status:'FORECAST_RECEIPT_STORED'};
  }finally{lock.releaseLock();}
}
function e30ExistingCloses_(day,now){
  // Read the existing DAILY A:F history only, never today's live G:L overlay.
  const sh=SpreadsheetApp.openById(UM30.BOOK).getSheetByName('ETF_DATA');if(!sh)return [];
  const rows=sh.getRange(1,1,Math.min(sh.getLastRow(),2480),6).getValues(),out=[];let code='';
  rows.forEach(r=>{
    const raw=String(r[0]).trim(),s=raw.padStart(6,'0');
    if(/^\d{1,6}$/.test(raw)&&typeof r[1]==='string'&&r[1].length>0){code=E30.CODES.indexOf(s)>=0?s:'';return;}
    if(!code)return;
    let d='';if(r[0] instanceof Date)d=Utilities.formatDate(r[0],'Asia/Seoul','yyyy-MM-dd');
    else if(typeof r[0]==='number'&&r[0]>40000&&r[0]<80000)d=new Date((Math.floor(r[0])-25569)*86400000).toISOString().slice(0,10);
    if(d!==day||!e30Positive_(r[4])||!e30Positive_(r[5]))return;
    out.push({ticker:code,day:day,close:r[4],quality:'CONFIRMED_DAILY_CLOSE',source:'GOOGLEFINANCE_COMPLETED_DAILY',
      observed_at:now.toISOString(),market_time:day+'T15:30:00+09:00',paper_fill:true});code='';
  });return out;
}
function e30CloseInputs_(ss,now,cfg){
  const inputs=e30Read_(ss),computed=e30Compute_(inputs,now,cfg),events=[];
  const days=Array.from(new Set(computed.status.filter(r=>r[3]==='CLOSE_PENDING').map(r=>r[1])));
  const props=PropertiesService.getScriptProperties();
  days.forEach(day=>{
    if(now.getTime()<e30At_(day,'16:10'))return;
    const needed=Array.from(new Set(computed.trades.filter(r=>r[1]===day&&r[4]==='BUY'&&!computed.trades.some(s=>s[0]===day+'|'+r[2]+'|SELL')).map(r=>r[3])));
    let closes=e30ExistingCloses_(day,now).filter(c=>needed.indexOf(c.ticker)>=0),missing=needed.filter(c=>!closes.some(x=>x.ticker===c));
    if(missing.length){
      const today=e30Day_(now),slotKey=day+'_'+today,legacyKey='E30_CLOSE_ATTEMPTS_'+slotKey;
      let store={};try{store=JSON.parse(props.getProperty('E30_CLOSE_ATTEMPTS')||'{}');}catch(e){store={};}
      Object.keys(store).forEach(k=>{const d=k.slice(0,10);if(!/^\d{4}-\d\d-\d\d$/.test(d)||now.getTime()-e30At_(d,'00:00')>=5*86400000)delete store[k];});
      let attempt=store[slotKey]||JSON.parse(props.getProperty(legacyKey)||'{"count":0,"last":0}');
      // At most 3 HTTP requests per day, 15m apart, over five calendar days.
      if(attempt.count<3&&now.getTime()-attempt.last>=15*60000&&now.getTime()-e30At_(day,'00:00')<5*86400000){
        store[slotKey]={count:attempt.count+1,last:now.getTime()};
        props.setProperty('E30_CLOSE_ATTEMPTS',JSON.stringify(store));
        if(typeof props.deleteProperty==='function'&&props.getProperty(legacyKey))props.deleteProperty(legacyKey);
        const url=String(props.getProperty('SIGNAL_API_URL')||'').replace(/\/$/,''),token=props.getProperty('SIGNAL_API_TOKEN');
        if(!/^https:\/\//.test(url)||!token)throw Error('E30_CLOSE_NOT_CONFIGURED');
        const res=UrlFetchApp.fetch(url+'/paper-m30-close',{method:'post',contentType:'application/json',headers:{'X-StockBot-Token':token},
          payload:JSON.stringify({day:day,tickers:missing}),muteHttpExceptions:true});
        if(res.getResponseCode()===200){const data=JSON.parse(res.getContentText());if(data.ok===true&&data.paper_only===true&&Array.isArray(data.closes))closes=closes.concat(data.closes);}
      }
    }
    const received=new Date(Math.max(now.getTime(),Date.now()));
    closes.filter(c=>needed.indexOf(c.ticker)>=0&&e30CloseValid_(c,day,received.getTime())).forEach(c=>events.push(e30Event_(day,'CLOSE','CLOSE|'+day+'|'+c.ticker,c,received)));
  });return events;
}
function e30Tick_(now,verifiedSession){
  const cfg=e30Config_();if(!cfg.enabled)return {status:'OFF'};
  const day=e30Day_(now);if(m30QuarantinedDay_(day))return {status:'DATA_INTEGRITY_HOLD'};
  const ss=e30Book_(),events=[],inputs=e30Read_(ss);
  if(now.getTime()>=e30At_(day,'15:20')&&now.getTime()<e30At_(day,'15:30'))events.push(e30Event_(day,'AUCTION_ORDER','AUCTION_ORDER|'+day,{paper_only:true,side:'SELL',model:'MARKET_AUCTION_ASSUMPTION'},now));
  const hasSession=verifiedSession===true||inputs.some(e=>e.day===day&&(e.kind==='SESSION'||e.kind==='OBS'));
  if(verifiedSession===true&&e30InScope_(day,cfg))events.push(e30Event_(day,'SESSION','SESSION|'+day,{},now));
  if(hasSession&&e30InScope_(day,cfg)&&now.getTime()>=e30At_(day,'09:00')&&now.getTime()<=e30At_(day,'10:00')&&!inputs.some(e=>e.kind==='FORECAST'&&e30ForecastValid_(e.data,day))){
    events.push(e30FetchForecast_(day,cfg,now));
  }
  if(hasSession&&e30InScope_(day,cfg)&&now.getTime()>e30At_(day,'10:00'))events.push(e30Event_(day,'TICK','DEADLINE|'+day,{},now));
  events.push.apply(events,e30CloseInputs_(ss,now,cfg));
  e30Append_(ss,events);return e30Repair_(ss,new Date(Math.max(now.getTime(),Date.now())),cfg);
}
function e30SafeTick_(now,verifiedSession){
  try{
    if(!e30Config_().enabled)return {status:'OFF'};
    const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'LOCKED'};
    try{const result=e30Tick_(now||new Date(),verifiedSession);
      const stamp=now||new Date();e30Project_(e30Book_(),'E30_STATUS',[[e30Day_(stamp)+'|SYSTEM|TICK',e30Day_(stamp),'SYSTEM',result.status==='DATA_INTEGRITY_HOLD'?'DATA_INTEGRITY_HOLD':'HEALTH_OK','Previous failure preserved in execution logs; current tick succeeded']]);
      return result;
    }finally{lock.releaseLock();}
  }catch(e){e30ReportError_('TICK',e,now||new Date());return {status:'EXEC_ERROR'};}
}
function e30ReportError_(phase,error,now){
  console.error('E30_ISOLATED: '+phase+' '+String(error));
  try{if(e30Config_().enabled)e30Project_(e30Book_(),'E30_STATUS',[[e30Day_(now)+'|SYSTEM|'+phase,e30Day_(now),'SYSTEM','EXEC_ERROR',String(error).slice(0,500)]]);}catch(ignored){}
}

function e30PairAfterReceipt_(obs,signal,after){
  const rounds={};
  obs.filter(e=>e30QuoteValid_(e,signal)&&e30Time_(e.at)>=after&&e30Time_(e.data.paper.quote_received_at)>=after).forEach(e=>{
    const group=rounds[e.data.round]||(rounds[e.data.round]={});if(!group[e.data.code])group[e.data.code]=e;
  });
  return Object.values(rounds).filter(g=>E30.CODES.every(c=>g[c])&&Math.abs(e30Time_(g['069500'].data.paper.quote_received_at)-e30Time_(g['114800'].data.paper.quote_received_at))<=120000)
    .sort((a,b)=>Math.max(...E30.CODES.map(c=>e30Time_(a[c].at)))-Math.max(...E30.CODES.map(c=>e30Time_(b[c].at))))[0]||null;
}
