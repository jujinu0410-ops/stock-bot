/** E45 paper only. Append-only inputs are authoritative; projections are repairable.
 * No order API, no trigger installation, no A-D state or ledger writes.
 */
const E45 = Object.freeze({BOOK:'18TM6tjbu6IvK4zYDs9JnZhGikGhRQeedOGSTvhTkA_E',
  CAPITAL:10000000,BUDGET:3000000,FEE:.0006,SLIP:.001,
  CODES:['069500','114800'],STRATEGIES:['E45-T','E45-R','E45-G','E45-H'],
  TABS:{E45_INPUTS:['ID','Day','Kind','ReceivedAt','JSON'],
    E45_TRADES:['ID','Day','Strategy','Code','Side','Qty','RawPrice','FillPrice','Fee','CashFlow','MarketTime','ObservedAt','Model'],
    E45_NAV:['ID','Day','Strategy','Cash','NAV','Status'],
    E45_STATUS:['ID','Day','Strategy','Status','Detail']}});
function e45Time_(x){return typeof x==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(x)?Date.parse(x):NaN;}
function e45Day_(x){const t=x instanceof Date?x.getTime():e45Time_(x);return isFinite(t)?new Date(t+9*3600000).toISOString().slice(0,10):'';}
function e45At_(day,time){return Date.parse(day+'T'+time+':00+09:00');}
function e45Positive_(x){return typeof x==='number'&&isFinite(x)&&x>0;}
function e45Config_(){
  const p=PropertiesService.getScriptProperties(),start=p.getProperty('E45_START_DATE')||'';
  return {enabled:p.getProperty('E45_ENABLED')==='ON'&&/^\d{4}-\d\d-\d\d$/.test(start),
    start:start,end:p.getProperty('E45_END_DATE')||'2026-11-30',
    sender:p.getProperty('E45_FORECAST_SENDER')||'jujinu0410@gmail.com'};
}
function e45InScope_(day,cfg){return day>=cfg.start&&day<=cfg.end;}
function e45Event_(day,kind,id,data,received){return {id:id,day:day,kind:kind,at:received.toISOString(),data:data};}
function e45Observation_(item,r,round,received,cfg){
  if(!cfg.enabled||E45.CODES.indexOf(item.ticker)<0||!e45InScope_(e45Day_(received),cfg))return null;
  const pd=r.paper_data||{};
  return e45Event_(e45Day_(received),'OBS','OBS|'+round+'|'+item.ticker,
    {code:item.ticker,round:round,ok:r.ok===true,price:r.live_price,
      source:r.live_price_source,paper:pd},received);
}
function e45FirstValid_(event){
  const d=event.data,p=d.paper||{},b=p.first45||{},day=event.day,at=e45Time_(event.at),computed=e45Time_(p.computed_at);
  return d.code==='069500'&&d.ok&&b.completed===true&&b.quality==='VALID'&&
    e45Time_(b.start)===e45At_(day,'09:00')&&e45Time_(b.end)===e45At_(day,'09:45')&&
    computed>=e45At_(day,'09:45')&&computed<=at&&at<=e45At_(day,'10:00')&&
    ['open','high','low','close'].every(k=>e45Positive_(b[k]))&&
    b.high>=Math.max(b.open,b.close,b.low)&&b.low<=Math.min(b.open,b.close,b.high);
}
function e45QuoteValid_(event,signal){
  const d=event.data,p=d.paper||{},qt=e45Time_(p.quote_received_at),at=e45Time_(event.at);
  return d.ok&&d.source==='KIWOOM_KA10001'&&e45Positive_(d.price)&&
    e45Day_(p.quote_received_at)===event.day&&qt>e45Time_(signal.at)&&qt<=at&&
    at-qt<=120000&&at<=e45At_(event.day,'10:00')&&d.round!==signal.data.round;
}
function e45ForecastValid_(f,day){
  const t=e45Time_(f.received_at);
  return f.day===day&&!!f.source_id&&/^[a-f0-9]{64}$/.test(f.sha256||'')&&
    (f.direction==='UP'||f.direction==='DOWN')&&t>=e45At_(day,'00:00')&&t<e45At_(day,'09:00');
}
function e45CloseValid_(c,day,now){
  const t=e45Time_(c.observed_at),end=e45At_(day,'15:30');
  return c.day===day&&E45.CODES.indexOf(c.ticker)>=0&&e45Positive_(c.close)&&
    c.quality==='CONFIRMED_DAILY_CLOSE'&&c.paper_fill===true&&
    ['NAVER_DAILY_AFTER_1610','GOOGLEFINANCE_COMPLETED_DAILY'].indexOf(c.source)>=0&&
    e45Time_(c.market_time)===end&&t>=e45At_(day,'16:10')&&t<=now;
}
function e45Compute_(input,now,cfg){
  const nowMs=now.getTime(),seen=new Set(),events=input.filter(e=>{
    if(!e||seen.has(e.id)||!isFinite(e45Time_(e.at))||e45Time_(e.at)>nowMs)return false;
    seen.add(e.id);return /^\d{4}-\d\d-\d\d$/.test(e.day)&&e45InScope_(e.day,cfg);
  }).sort((a,b)=>e45Time_(a.at)-e45Time_(b.at)||a.id.localeCompare(b.id));
  const days=Array.from(new Set(events.map(e=>e.day))).sort(),cash={},positions={},releasedAt={},trades=[],nav=[],status=[];
  E45.STRATEGIES.forEach(s=>{cash[s]=E45.CAPITAL;releasedAt[s]=0;});
  days.forEach(day=>{
    const rows=events.filter(e=>e.day===day),obs=rows.filter(e=>e.kind==='OBS'),signal=obs.find(e45FirstValid_);
    let pair=null;
    if(signal){
      const rounds={};
      obs.filter(e=>e45QuoteValid_(e,signal)).forEach(e=>{
        const g=rounds[e.data.round]||(rounds[e.data.round]={});if(!g[e.data.code])g[e.data.code]=e;
      });
      pair=Object.values(rounds).filter(g=>E45.CODES.every(c=>g[c])&&
        Math.abs(e45Time_(g['069500'].data.paper.quote_received_at)-e45Time_(g['114800'].data.paper.quote_received_at))<=120000)
        .sort((a,b)=>Math.max(...E45.CODES.map(c=>e45Time_(a[c].at)))-Math.max(...E45.CODES.map(c=>e45Time_(b[c].at))))[0];
    }
    const decisionAt=pair?Math.max(...E45.CODES.map(c=>e45Time_(pair[c].at))):NaN;
    const forecast=rows.find(e=>e.kind==='FORECAST'&&e45Time_(e.at)<=decisionAt);
    const f=forecast&&e45ForecastValid_(forecast.data,day)?forecast.data.direction:'';
    const bar=signal&&signal.data.paper.first45;
    const direction=bar?(bar.close>bar.open?'UP':bar.close<bar.open?'DOWN':'FLAT'):'';
    E45.STRATEGIES.forEach(s=>{
      let state=nowMs>e45At_(day,'10:00')?'DATA_MISSING':'WAIT_SIGNAL',detail='';
      let desired='';
      if(pair){
        desired=s==='E45-G'?f:s==='E45-H'?(f===direction?f:''):s==='E45-R'?(direction==='UP'?'DOWN':direction==='DOWN'?'UP':''):direction;
        if((s==='E45-G'||s==='E45-H')&&!f)state='FORECAST_MISSING';
        else if(s!=='E45-G'&&direction==='FLAT')state='NO_TRADE_FLAT';
        else if(s==='E45-H'&&!desired)state='NO_MATCH';
        else if(desired==='UP'||desired==='DOWN'){
          if(positions[s]||decisionAt<releasedAt[s]){state='PRIOR_CLOSE_PENDING';}
          else{
            const code=desired==='UP'?'069500':'114800',q=pair[code],raw=q.data.price,fill=raw*(1+E45.SLIP);
            const qty=Math.floor(Math.min(E45.BUDGET,cash[s])/(fill*(1+E45.FEE)));
            if(qty<1)state='NO_BUDGET';
            else{
              const amount=qty*fill,fee=amount*E45.FEE;
              cash[s]-=amount+fee;positions[s]={day:day,code:code,qty:qty,cost:amount+fee};
              trades.push([day+'|'+s+'|BUY',day,s,code,'BUY',qty,raw,fill,fee,-amount-fee,
                '',q.data.paper.quote_received_at,'POST_SIGNAL_OBSERVED_PAPER_FILL']);state='OPEN';
            }
          }
        }
      }else if(signal){state=nowMs>e45At_(day,'10:00')?'QUOTE_MISSING':'WAIT_NEXT_COLLECTION';}
      const pos=positions[s];
      if(pos&&pos.day===day){
        // A later collection may resolve an older day's pending close. Preserve
        // input received time as well as the provider-observation time.
        const ce=events.find(e=>e.kind==='CLOSE'&&e.data.day===day&&e.data.ticker===pos.code&&
          e45CloseValid_(e.data,day,nowMs)&&e45Time_(e.data.observed_at)<=e45Time_(e.at));
        if(ce){
          const c=ce.data,fill=c.close*(1-E45.SLIP),revenue=pos.qty*fill,fee=revenue*E45.FEE;
          cash[s]+=revenue-fee;
          trades.push([day+'|'+s+'|SELL',day,s,pos.code,'SELL',pos.qty,c.close,fill,fee,revenue-fee,
            c.market_time,c.observed_at,'ASSUMED_1530_CLOSE_NOT_EXCHANGE_FILL']);delete positions[s];releasedAt[s]=e45Time_(ce.at);state='CLOSED';
        }else if(nowMs>=e45At_(day,'15:30'))state='CLOSE_PENDING';
      }
      detail=JSON.stringify({first45:direction||'MISSING',signal_at:signal?signal.at:null,
        forecast_id:forecast?forecast.data.source_id||null:null,paper_only:true});
      status.push([day+'|'+s,day,s,state,detail]);
      // Pending positions have no invented NAV / mark-to-market price.
      nav.push([day+'|'+s,day,s,cash[s],positions[s]?'':cash[s],state]);
    });
  });
  return {trades:trades,nav:nav,status:status};
}
function e45Book_(){
  const ss=SpreadsheetApp.openById(E45.BOOK);
  Object.keys(E45.TABS).forEach(name=>{
    const sh=ss.getSheetByName(name)||ss.insertSheet(name),headers=E45.TABS[name];
    if(!sh.getLastRow())sh.getRange(1,1,1,headers.length).setValues([headers]);
    else if(JSON.stringify(sh.getRange(1,1,1,headers.length).getValues()[0])!==JSON.stringify(headers))throw Error('E45_SCHEMA_'+name);
  });return ss;
}
function e45Read_(ss){
  const sh=ss.getSheetByName('E45_INPUTS');if(sh.getLastRow()<2)return [];
  return sh.getRange(2,1,sh.getLastRow()-1,5).getValues().map(r=>({id:String(r[0]),day:String(r[1]),kind:String(r[2]),at:String(r[3]),data:JSON.parse(r[4])}));
}
function e45Append_(ss,events){
  const existing=e45Read_(ss),ids=new Set(existing.map(e=>e.id)),fresh=[];
  events.filter(Boolean).forEach(e=>{if(!ids.has(e.id)){ids.add(e.id);fresh.push([e.id,e.day,e.kind,e.at,JSON.stringify(e.data)]);}});
  if(fresh.length){const sh=ss.getSheetByName('E45_INPUTS');sh.getRange(sh.getLastRow()+1,1,fresh.length,5).setValues(fresh);}
}
function e45Project_(ss,name,rows){
  const sh=ss.getSheetByName(name),width=E45.TABS[name].length,last=sh.getLastRow();
  const old=last>1?sh.getRange(2,1,last-1,width).getValues():[],index={};
  old.forEach((r,i)=>{if(index[r[0]])throw Error('E45_DUPLICATE_PROJECTION');index[r[0]]=i+2;});
  rows.forEach(r=>{
    if(index[r[0]]){
      // Trade IDs are immutable. A mismatch is corruption, never a second fill.
      const previous=old[index[r[0]]-2];
      if(name==='E45_TRADES'&&JSON.stringify(previous)!==JSON.stringify(r))throw Error('E45_TRADE_CONFLICT');
      if(JSON.stringify(previous)!==JSON.stringify(r))sh.getRange(index[r[0]],1,1,width).setValues([r]);
    }else{sh.getRange(sh.getLastRow()+1,1,1,width).setValues([r]);}
  });
}
function e45Repair_(ss,now,cfg){
  const result=e45Compute_(e45Read_(ss),now,cfg);
  e45Project_(ss,'E45_TRADES',result.trades);e45Project_(ss,'E45_NAV',result.nav);e45Project_(ss,'E45_STATUS',result.status);return result;
}
function e45Save_(events,now){
  const cfg=e45Config_();if(!cfg.enabled)return {status:'OFF'};
  const ss=e45Book_();e45Append_(ss,events);return e45Repair_(ss,now,cfg);
}
function e45ParseForecast_(plain,day){
  const matches=Array.from(String(plain).matchAll(/오늘\s*시가\s*대비\s*종가\s*전망\s*[:：]\s*(상승|하락)/g));
  const directions=Array.from(new Set(matches.map(x=>x[1])));
  return directions.length===1?(directions[0]==='상승'?'UP':'DOWN'):'';
}
function e45FetchForecast_(day,cfg,now){
  const parts=day.split('-').map(Number),subject='[모바일 대시보드] '+parts[0]+'년 '+parts[1]+'월 '+parts[2]+'일 일일 모닝 브리핑';
  const threads=GmailApp.search('from:'+cfg.sender+' subject:"'+subject+'" newer_than:3d -in:trash -in:spam',0,10),eligible=[];
  threads.forEach(t=>t.getMessages().forEach(m=>{
    if(m.getSubject()!==subject||m.getFrom().indexOf(cfg.sender)<0)return;
    // getDate/header/footer are NOT evidence of actual Gmail receipt time.
    const res=UrlFetchApp.fetch('https://gmail.googleapis.com/gmail/v1/users/me/messages/'+encodeURIComponent(m.getId())+'?format=metadata&metadataHeaders=Subject',
      {headers:{Authorization:'Bearer '+ScriptApp.getOAuthToken()},muteHttpExceptions:true});
    if(res.getResponseCode()!==200)throw Error('E45_FORECAST_RECEIPT_UNAVAILABLE');
    const meta=JSON.parse(res.getContentText()),at=Number(meta.internalDate);
    if(!isFinite(at)||at<e45At_(day,'00:00')||at>=e45At_(day,'09:00')||at>now.getTime())return;
    const plain=m.getPlainBody(),direction=e45ParseForecast_(plain,day);
    const hash=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,plain,Utilities.Charset.UTF_8)
      .map(b=>('0'+((b+256)%256).toString(16)).slice(-2)).join('');
    eligible.push({day:day,source_id:m.getId(),received_at:new Date(at).toISOString(),direction:direction,sha256:hash});
  }));
  eligible.sort((a,b)=>e45Time_(a.received_at)-e45Time_(b.received_at)||a.source_id.localeCompare(b.source_id));
  const unique=new Set(eligible.map(f=>f.direction));
  const f=eligible.length&&unique.size===1&&eligible[0].direction?eligible[0]:{day:day,direction:'',reason:eligible.length?'AMBIGUOUS':'MISSING'};
  return e45Event_(day,'FORECAST','FORECAST|'+day,f,now);
}
function e45ExistingCloses_(day,now){
  // Read the existing DAILY A:F history only, never today's live G:L overlay.
  const sh=SpreadsheetApp.openById(UM.BOOK).getSheetByName('ETF_DATA');if(!sh)return [];
  const rows=sh.getRange(1,1,Math.min(sh.getLastRow(),2480),6).getValues(),out=[];let code='';
  rows.forEach(r=>{
    const raw=String(r[0]).trim(),s=raw.padStart(6,'0');
    if(/^\d{1,6}$/.test(raw)&&typeof r[1]==='string'&&r[1].length>0){code=E45.CODES.indexOf(s)>=0?s:'';return;}
    if(!code)return;
    let d='';if(r[0] instanceof Date)d=Utilities.formatDate(r[0],'Asia/Seoul','yyyy-MM-dd');
    else if(typeof r[0]==='number'&&r[0]>40000&&r[0]<80000)d=new Date((Math.floor(r[0])-25569)*86400000).toISOString().slice(0,10);
    if(d!==day||!e45Positive_(r[4])||!e45Positive_(r[5]))return;
    out.push({ticker:code,day:day,close:r[4],quality:'CONFIRMED_DAILY_CLOSE',source:'GOOGLEFINANCE_COMPLETED_DAILY',
      observed_at:now.toISOString(),market_time:day+'T15:30:00+09:00',paper_fill:true});code='';
  });return out;
}
function e45CloseInputs_(ss,now,cfg){
  const inputs=e45Read_(ss),computed=e45Compute_(inputs,now,cfg),events=[];
  const days=Array.from(new Set(computed.status.filter(r=>r[3]==='CLOSE_PENDING').map(r=>r[1])));
  const props=PropertiesService.getScriptProperties();
  days.forEach(day=>{
    if(now.getTime()<e45At_(day,'16:10'))return;
    const needed=Array.from(new Set(computed.trades.filter(r=>r[1]===day&&r[4]==='BUY'&&!computed.trades.some(s=>s[0]===day+'|'+r[2]+'|SELL')).map(r=>r[3])));
    let closes=e45ExistingCloses_(day,now).filter(c=>needed.indexOf(c.ticker)>=0),missing=needed.filter(c=>!closes.some(x=>x.ticker===c));
    if(missing.length){
      const today=e45Day_(now),key='E45_CLOSE_ATTEMPTS_'+day+'_'+today;
      let attempt=JSON.parse(props.getProperty(key)||'{"count":0,"last":0}');
      // At most 3 HTTP requests per day, 15m apart, over five calendar days.
      if(attempt.count<3&&now.getTime()-attempt.last>=15*60000&&now.getTime()-e45At_(day,'00:00')<5*86400000){
        props.setProperty(key,JSON.stringify({count:attempt.count+1,last:now.getTime()}));
        const url=String(props.getProperty('SIGNAL_API_URL')||'').replace(/\/$/,''),token=props.getProperty('SIGNAL_API_TOKEN');
        if(!/^https:\/\//.test(url)||!token)throw Error('E45_CLOSE_NOT_CONFIGURED');
        const res=UrlFetchApp.fetch(url+'/paper-e45-close',{method:'post',contentType:'application/json',headers:{'X-StockBot-Token':token},
          payload:JSON.stringify({day:day,tickers:missing}),muteHttpExceptions:true});
        if(res.getResponseCode()===200){const data=JSON.parse(res.getContentText());if(data.ok===true&&data.paper_only===true&&Array.isArray(data.closes))closes=closes.concat(data.closes);}
      }
    }
    const received=new Date(Math.max(now.getTime(),Date.now()));
    closes.filter(c=>needed.indexOf(c.ticker)>=0&&e45CloseValid_(c,day,received.getTime())).forEach(c=>events.push(e45Event_(day,'CLOSE','CLOSE|'+day+'|'+c.ticker,c,received)));
  });return events;
}
function e45Tick_(now,verifiedSession){
  const cfg=e45Config_();if(!cfg.enabled)return {status:'OFF'};
  const day=e45Day_(now),ss=e45Book_(),events=[],inputs=e45Read_(ss);
  const hasSession=verifiedSession===true||inputs.some(e=>e.day===day&&(e.kind==='SESSION'||e.kind==='OBS'));
  if(verifiedSession===true&&e45InScope_(day,cfg))events.push(e45Event_(day,'SESSION','SESSION|'+day,{},now));
  if(hasSession&&e45InScope_(day,cfg)&&now.getTime()>=e45At_(day,'09:00')&&now.getTime()<=e45At_(day,'10:00')&&!inputs.some(e=>e.id==='FORECAST|'+day)){
    events.push(e45FetchForecast_(day,cfg,now));
  }
  if(hasSession&&e45InScope_(day,cfg)&&now.getTime()>e45At_(day,'10:00'))events.push(e45Event_(day,'TICK','DEADLINE|'+day,{},now));
  events.push.apply(events,e45CloseInputs_(ss,now,cfg));
  e45Append_(ss,events);return e45Repair_(ss,new Date(Math.max(now.getTime(),Date.now())),cfg);
}
function e45SafeTick_(now,verifiedSession){
  try{
    if(!e45Config_().enabled)return {status:'OFF'};
    const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'LOCKED'};
    try{return e45Tick_(now||new Date(),verifiedSession);}finally{lock.releaseLock();}
  }catch(e){e45ReportError_('TICK',e,now||new Date());return {status:'EXEC_ERROR'};}
}
function e45ReportError_(phase,error,now){
  console.error('E45_ISOLATED: '+phase+' '+String(error));
  try{if(e45Config_().enabled)e45Project_(e45Book_(),'E45_STATUS',[[e45Day_(now)+'|SYSTEM|'+phase,e45Day_(now),'SYSTEM','EXEC_ERROR',String(error).slice(0,500)]]);}catch(ignored){}
}
