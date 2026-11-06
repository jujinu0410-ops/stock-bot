/** PAPER-ONLY HOLJJAK 2026-11. No order API, separate Sheet. */
const PG = Object.freeze({
  ID:'18TM6tjbu6IvK4zYDs9JnZhGikGhRQeedOGSTvhTkA_E',
  START:'2026-11-02',END:'2026-11-30',
  CODES:['069500','114800','229200','251340'],
  NAMES:{'069500':'KODEX 200','114800':'KODEX 인버스','229200':'KODEX 코스닥150','251340':'KODEX 코스닥150선물인버스'},
  PAIRS:[['069500','114800',1000000],['229200','251340',500000]],
  INITIAL:10000000,FEE:.0006,SLIP:.001,MAX_EXPOSURE:.60,
  TAKE_PROFIT:.025,ADD_WIN:.018,ADD_LOSS:.03,TZ:'Asia/Seoul'
});
function pgDay_(date){return Utilities.formatDate(date,PG.TZ,'yyyy-MM-dd');}
function pgNum_(x){const n=Number(x);return isFinite(n)&&n>0?n:0;}
function pgCandidateLegacy_(item,result,now){
  const code=String(item.ticker||'');
  if(PG.CODES.indexOf(code)<0||pgDay_(now)<PG.START||pgDay_(now)>PG.END)return null;
  const m=result.m45||{},price=pgNum_(result.live_price),
        v9=pgNum_(m.vwap9),v26=pgNum_(m.vwap26),bar=String(m.completed_bar||'');
  if(!m.available||!price||!v9||!v26||bar.slice(0,10)!==pgDay_(now)||
     bar.length<16||result.live_price_source!=='KIWOOM_KA10001')return null;
  // Distinguish a completed-candle signal from the *later* observed live quote.
  return [pgDay_(now),code,PG.NAMES[code],bar,v9,v26,price,
          m.gold===true?'GOLD':'DEAD',result.stage||'WATCH',
          result.live_price_source,now.toISOString(),'VALID'];
}
function pgCandidate_(item,result,now,receivedAt,diagnostics){
  const received=receivedAt||new Date(),paperResult=pgPaperResult_(result,now,received);
  const row=pgCandidateLegacy_(item,paperResult,now);
  try{if(diagnostics)diagnostics.push(pgDiagnostic_(item,paperResult,now,received,row));}
  catch(e){console.error('PAPER_DIAGNOSTIC_ISOLATED: '+String(e));}
  return row;
}
function pgPaperResult_(result,now,received){
  // Paper-only bridge. Never enrich/mutate the shared m45 used by umPlan_.
  // Restore ONLY the known compact batch shape, not an explicit failed verdict.
  const wire=result.m45||{},p=result.paper_data||{},m=p.m45||{};
  if(Object.keys(wire).length!==1||!Object.prototype.hasOwnProperty.call(wire,'completed_bar'))return result;
  if(p.schema!==1||p.quality!=='VALID'||!Number.isInteger(p.row_count)||p.row_count<28||
     m.available!==true||typeof m.gold!=='boolean'||
     typeof m.vwap9!=='number'||!pgNum_(m.vwap9)||typeof m.vwap26!=='number'||!pgNum_(m.vwap26)||
     m.gold!==(m.vwap9>m.vwap26)||m.completed_bar!==wire.completed_bar)return result;
  const bar=String(m.completed_bar||'');
  if(!/^\d{4}-\d\d-\d\d (09:45|10:30|11:15|12:00|12:45|13:30|14:15|15:00):00$/.test(bar)||bar.slice(0,10)!==pgDay_(now))return result;
  const end=Date.parse(bar.replace(' ','T')+'+09:00');
  const stamp=x=>typeof x==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(x)?Date.parse(x):NaN;
  const quote=stamp(p.quote_received_at),computed=stamp(p.computed_at);
  if(![end,quote,computed,received.getTime(),now.getTime()].every(Number.isFinite)||
     end>now.getTime()||end>quote||quote>computed||computed>received.getTime()||now.getTime()>received.getTime()||pgDay_(received)!==pgDay_(now))return result;
  return Object.assign({},result,{m45:Object.assign({},m),_pg_m45_delivery:'VALIDATED_PAPER_DATA'});
}
function pgDiagnostic_(item,result,now,received,row){
  const code=String(item.ticker||'');if(PG.CODES.indexOf(code)<0||pgDay_(now)<PG.START||pgDay_(now)>PG.END)return null;
  const m=result.m45||{},p=result.paper_data||{},first=p.first45||{},day=pgDay_(now),reasons=[];
  const before=received.getTime()<Date.parse(day+'T09:45:00+09:00');
  if(!m.available)reasons.push('M45_UNAVAILABLE');
  if(!pgNum_(result.live_price))reasons.push('PRICE_MISSING');
  if(!pgNum_(m.vwap9))reasons.push('VWAP9_MISSING');
  if(!pgNum_(m.vwap26))reasons.push('VWAP26_MISSING');
  if(String(m.completed_bar||'').slice(0,10)!==day)reasons.push('BAR_DAY_MISMATCH');
  if(result.live_price_source!=='KIWOOM_KA10001')reasons.push('QUOTE_SOURCE_INVALID');
  if(m.completed_bar&&!Object.prototype.hasOwnProperty.call(m,'available')&&!Object.prototype.hasOwnProperty.call(m,'vwap9'))reasons.unshift('BATCH_M45_FIELDS_OMITTED');
  return [received.toISOString(),'PG_CANDIDATE',code,JSON.stringify({reasons:reasons,
    scan_started_at:now.toISOString(),received_at:received.toISOString(),bar:m.completed_bar||null,
    quality:p.quality||'LEGACY_QUALITY_NOT_EXPOSED',first45_completed:first.completed===true,
    first45_quality:first.quality||'UNKNOWN',first45_end:first.end||null,server_computed_at:p.computed_at||null,
    quote_received_at:p.quote_received_at||null,m45_delivery:result._pg_m45_delivery||'ORIGINAL_WIRE',stage:result.stage||'WATCH'}),row?'INFO_VALID_OBSERVATION':before?'INFO_WAIT_FIRST45':'DATA_MISSING'];
}
function pgSaveDiagnostics_(rows){
  if(PropertiesService.getScriptProperties().getProperty('PG_DIAGNOSTICS_ENABLED')!=='ON')return {status:'OFF'};
  rows=rows.filter(Boolean);if(!rows.length)return {saved:0};
  const sh=SpreadsheetApp.openById(PG.ID).getSheetByName('ERROR_LOG');if(!sh)throw Error('PG_ERROR_LOG_MISSING');
  sh.getRange(sh.getLastRow()+1,1,rows.length,5).setValues(rows);return {saved:rows.length};
}
function pgExecutionDiagnostic_(phase,code,error){
  return [new Date().toISOString(),phase,String(code||''),JSON.stringify({error:String(error).slice(0,500)}),'EXEC_ERROR'];
}
function pgSaveSamples_(items,now){
  if(!items.length||pgDay_(now)<PG.START||pgDay_(now)>PG.END)return {saved:0};
  const sh=SpreadsheetApp.openById(PG.ID).getSheetByName('MARKET_DATA');
  if(!sh)throw Error('PG_MARKET_DATA_MISSING');
  const last=sh.getLastRow(),start=Math.max(2,last-200);
  const seen=new Set();
  if(last>=start)sh.getRange(start,1,last-start+1,4).getDisplayValues()
    .forEach(r=>seen.add(String(r[1])+'|'+String(r[3])));
  const fresh=items.filter(x=>{
    const key=String(x[1])+'|'+String(x[3]);
    if(seen.has(key))return false;seen.add(key);return true;
  });
  if(fresh.length)sh.getRange(last+1,1,fresh.length,12).setValues(fresh);
  return {saved:fresh.length};
}
function pgAccount_(name){
  return {name,cash:PG.INITIAL,positions:{},realized:0,fees:0,peak:PG.INITIAL,
          mdd:0,maxExposure:0,trades:[],daily:[],lastFlags:{},maxDate:''};
}
function pgExposure_(a,prices){
  return PG.CODES.reduce((s,c)=>s+(a.positions[c]?a.positions[c].qty*(prices[c]||0):0),0);
}
function pgNav_(a,prices){return a.cash+pgExposure_(a,prices);}
function pgBuy_(a,code,budget,price,bar,day,reason,prices,isAdd){
  const p=pgNum_(price),mark=pgNav_(a,prices);
  if(!p||!budget||mark<=0)return false;
  if(pgExposure_(a,prices)>=mark*PG.MAX_EXPOSURE-1)return false;
  const ceiling=Math.max(0,mark*PG.MAX_EXPOSURE-pgExposure_(a,prices));
  const fill=p*(1+PG.SLIP);
  const qty=Math.floor(Math.min(budget,ceiling,a.cash)/(fill*(1+PG.FEE)));
  if(qty<1)return false;
  const cost=qty*fill,fee=cost*PG.FEE,old=a.positions[code]||{qty:0,avg:0,adds:0};
  old.avg=(old.qty*old.avg+qty*fill)/(old.qty+qty);
  old.qty+=qty;if(isAdd)old.adds++;
  a.positions[code]=old;a.cash-=cost+fee;a.realized-=fee;a.fees+=fee;
  a.trades.push([day,a.name,code,PG.NAMES[code],'BUY',reason,qty,Math.round(fill*100)/100,
                 Math.round(cost),Math.round(fee),Math.round(-fee),bar]);
  return true;
}
function pgSell_(a,code,qty,price,bar,day,reason){
  const old=a.positions[code],p=pgNum_(price);
  if(!old||!old.qty||!p)return false;
  qty=Math.min(old.qty,Math.max(1,Math.floor(qty)));
  const fill=p*(1-PG.SLIP),revenue=qty*fill,fee=revenue*PG.FEE;
  const pnl=qty*(fill-old.avg)-fee;
  a.cash+=revenue-fee;a.realized+=pnl;a.fees+=fee;old.qty-=qty;
  if(old.qty===0)delete a.positions[code];
  a.trades.push([day,a.name,code,PG.NAMES[code],'SELL',reason,qty,
                 Math.round(fill*100)/100,Math.round(revenue),Math.round(fee),Math.round(pnl),bar]);
  return true;
}
function pgRunBar_(a,byCode,bar,day){
  const prices={},flags={};
  PG.CODES.forEach(c=>{
    const r=byCode[c];prices[c]=Number(r[6]);
    flags[c]=Number(r[6])>Number(r[4])&&Number(r[4])>Number(r[5])&&r[7]==='GOLD';
  });
  const nav=pgNav_(a,prices);if(nav<=0)return;
  const dd=1-nav/Math.max(a.peak,nav);
  const canAdd=dd<.06;
  for(const [up,down,base] of PG.PAIRS){
    const pair=[up,down],fup=flags[up],fdn=flags[down];
    const trend=fup!==fdn?(fup?up:down):'';
    if(a.name==='C'){
      if(trend){
        const opposite=trend===up?down:up;
        if(a.positions[opposite])pgSell_(a,opposite,a.positions[opposite].qty,prices[opposite],bar,day,'DIRECTION_SWITCH');
        if(!a.positions[trend]&&canAdd)pgBuy_(a,trend,base*1.5,prices[trend],bar,day,'TREND_ONLY',prices,false);
      }else {
        for(const c of pair)if(a.positions[c])pgSell_(a,c,a.positions[c].qty,prices[c],bar,day,'NEUTRAL_EXIT');
      }
      continue;
    }
    for(const c of pair)if(!a.positions[c]){
      // Base legs are bought once on the first complete observation; no automatic
      // repurchase after exit, avoiding artificial infinite churn.
      if(!a.lastFlags['BASE_'+c]&&canAdd){
        if(pgBuy_(a,c,base,prices[c],bar,day,'INITIAL_DUAL_LEG',prices,false))a.lastFlags['BASE_'+c]=true;
      }
    }
    for(const c of pair){
      const pos=a.positions[c];if(!pos||!pos.qty)continue;
      const gain=prices[c]/pos.avg-1;
      if(gain>=PG.TAKE_PROFIT&&pos.qty>=4){
        pgSell_(a,c,Math.floor(pos.qty/4),prices[c],bar,day,'PARTIAL_TAKE_PROFIT');
      }
    }
    if(canAdd){
      for(const c of pair){
        const pos=a.positions[c];if(!pos||!pos.qty||pos.adds>=2)continue;
        const gain=prices[c]/pos.avg-1;
        const reversal=flags[c]&&!a.lastFlags[c];
        if(a.name==='B'&&gain<=-PG.ADD_LOSS&&reversal&&pos.adds<1){
          // B requires the losing instrument itself to become 45m bullish.
          pgBuy_(a,c,base*.25,prices[c],bar,day,'REVERSAL_AVERAGE_DOWN',prices,true);
        }else if(c===trend&&gain>=PG.ADD_WIN){
          pgBuy_(a,c,base*.25,prices[c],bar,day,'PYRAMID_WINNER',prices,true);
        }else if(a.name==='A'&&c!==trend&&trend&&gain<=-PG.ADD_LOSS){
          pgBuy_(a,c,base*.25,prices[c],bar,day,'BOUNDED_AVERAGE_DOWN',prices,true);
        }
      }
    }
  }
  PG.CODES.forEach(c=>{a.lastFlags[c]=flags[c];});
  const after=pgNav_(a,prices);
  a.peak=Math.max(a.peak,after);
  a.mdd=Math.max(a.mdd,1-after/a.peak);
  a.maxExposure=Math.max(a.maxExposure,pgExposure_(a,prices)/after);
  const unreal=PG.CODES.reduce((s,c)=>s+(a.positions[c]?a.positions[c].qty*(prices[c]-a.positions[c].avg):0),0);
  a.daily.push([day,a.name,Math.round(a.cash),Math.round(pgExposure_(a,prices)),Math.round(after),
    after/PG.INITIAL-1,Math.round(a.peak),after/a.peak-1,
    Math.round(a.realized),Math.round(unreal),pgExposure_(a,prices)/after,
    '45M_SIGNAL','VALID']);
  a.maxDate=day;
}
function pgCompute_(raw,seedRows){
  const account={A:pgAccount_('A'),B:pgAccount_('B'),C:pgAccount_('C'),D:pgAccount_('D')};
  if(typeof pgDLoadSeed_==='function')pgDLoadSeed_(account.D,seedRows||[]);
  const byBar={};
  for(const r of raw){
    if(r.length<12||r[11]!=='VALID'||PG.CODES.indexOf(String(r[1]))<0)continue;
    const day=String(r[0]),bar=String(r[3]),ticker=String(r[1]);
    if(day<PG.START||day>PG.END||bar.slice(0,10)!==day)continue;
    const key=day+'|'+bar;
    const g=byBar[key]||(byBar[key]={});
    if(!g[ticker]||String(r[10])<String(g[ticker][10]))g[ticker]=r;
  }
  const perDay={};
  Object.keys(byBar).sort().forEach(key=>{
    const g=byBar[key];if(!PG.CODES.every(c=>!!g[c]))return;
    const hours=PG.CODES.map(c=>Date.parse(g[c][10]));
    if(hours.some(t=>!isFinite(t))||Math.max(...hours)-Math.min(...hours)>20*60000)return;
    const day=key.slice(0,10),bar=key.substring(11);
    for(const a of Object.values(account)){
      if(a.name==='D')pgDStep_(a,g,bar,day);
      else pgRunBar_(a,g,bar,day);
    }
    perDay[day]=true;
  });
  // Keep one end-of-day NAV per account, not a daily row per 45-minute bar.
  Object.values(account).forEach(a=>{
    const latest={};
    a.daily.forEach(r=>latest[r[0]]=r);
    a.daily=Object.keys(latest).sort().map(d=>latest[d]);
  });
  return {accounts:account,dates:Object.keys(perDay).sort(),
    trades:Object.values(account).flatMap(a=>a.trades),
    regimes:account.D.dRegimes||[],
    daily:Object.values(account).flatMap(a=>a.daily).sort((a,b)=>a[0].localeCompare(b[0])||a[1].localeCompare(b[1]))};
}
function pgRewrite_(ss,name,rows,width){
  const sh=ss.getSheetByName(name);if(!sh)throw Error('PG_MISSING_'+name);
  const last=sh.getLastRow();if(last>1)sh.getRange(2,1,last-1,width).clearContent();
  if(rows.length){
    if(sh.getMaxRows()<rows.length+1)sh.insertRowsAfter(sh.getMaxRows(),rows.length+1-sh.getMaxRows());
    sh.getRange(2,1,rows.length,width).setValues(rows);
  }
}
function pgRecompute_(now){
  const ss=SpreadsheetApp.openById(PG.ID),sh=ss.getSheetByName('MARKET_DATA');
  const last=sh.getLastRow();
  const raw=last>1?sh.getRange(2,1,last-1,12).getValues():[];
  const seedSheet=ss.getSheetByName('D_SEED');
  const seedRows=seedSheet&&seedSheet.getLastRow()>1?seedSheet.getRange(2,1,seedSheet.getLastRow()-1,6).getValues():[];
  const p=pgCompute_(raw,seedRows);
  pgRewrite_(ss,'TRADES',p.trades,12);
  pgRewrite_(ss,'NAV_DAILY',p.daily,13);
  pgRewrite_(ss,'D_REGIME',p.regimes,10);
  const scores=['A','B','C','D'].map(k=>{
    const a=p.accounts[k],last=a.daily.length?a.daily[a.daily.length-1]:null;
    const nav=last?Number(last[4]):PG.INITIAL;
    const sales=a.trades.filter(t=>t[4]==='SELL');
    return [k,PG.INITIAL,nav,nav/PG.INITIAL-1,a.mdd,
      a.trades.length,sales.filter(t=>Number(t[10])>0).length,
      a.maxExposure,nav>0?a.cash/nav:1,
      last?(k==='D'&&last[12]==='WARMUP'?'ER20_WARMUP':'PAPER_ACTIVE'):'NO_DATA','45분봉 완료 후 관찰가 체결, 비용 포함'];
  });
  ss.getSheetByName('SCOREBOARD').getRange(2,1,4,11).setValues(scores);
  const values=[3,4,2].map(i=>scores.map(r=>r[i]));
  values.push(scores.map((r,i)=>p.accounts[['A','B','C','D'][i]].fees));
  values.push(scores.map(r=>r[5]));
  values.push(scores.map(r=>r[7]));
  const review=ss.getSheetByName('REVIEW');
  review.getRange(2,2,6,4).setValues(values);
  // Ranked by return less twice drawdown; provisional research, not trade advice.
  const ranked=scores.filter(r=>r[0]!=='D'||p.accounts.D.daily.length&&p.accounts.D.daily[p.accounts.D.daily.length-1][12]==='VALID').sort((a,b)=>(b[3]-2*b[4])-(a[3]-2*a[4]));
  const best=p.dates.length?ranked[0][0]:'';
  review.getRange(9,2,1,5).setValues([[...['A','B','C','D'].map(x=>x===best?'잠정 1위':'-'),
    '누적 수익률 - 2×최대낙폭 (1개월 연구용)']]);
  return {...p,scores:scores,best:best};
}
function pgMaybeClose_(now){
  const day=pgDay_(now),h=Number(Utilities.formatDate(now,PG.TZ,'HH')),
        mn=Number(Utilities.formatDate(now,PG.TZ,'mm'));
  if(day<PG.START||day>PG.END||h!==16||mn<10||mn>55)return {status:'NOT_DUE'};
  const props=PropertiesService.getScriptProperties(),key='PG_SENT_'+day;
  if(props.getProperty(key))return {status:'ALREADY_SENT'};
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'LOCK_BUSY'};
  try{
    if(props.getProperty(key))return {status:'ALREADY_SENT'};
    const result=pgRecompute_(now);
    // No mail on KRX holidays (absence of any verified same-day source bars).
    if(!result.dates.includes(day)){
      // Trading day with missing 4-way snapshots: send honest data-quality
      // alert rather than silently skipping the user's daily score email.
      const control=SpreadsheetApp.openById(UM.BOOK).getSheetByName(UM.CONTROL);
      const last=control.getRange('B3').getValue();
      const monitorDay=last instanceof Date?pgDay_(last):String(last).replace(/\./g,'-').replace(/\s/g,'').slice(0,10);
      if(monitorDay!==day)return {status:'NO_TRADING_DAY_EVIDENCE'};
      MailApp.sendEmail({to:hmAlertRecipient_(),
        subject:'[홀짝 ETF 모의계좌] '+day+' · 데이터 부족',
        body:'오늘 통합감시 실행은 확인됐으나 4개 ETF의 동일 완료45분봉과 유효 현재가가 모두 모이지 않아 모의체결·수익률을 계산하지 않았습니다. 임의 가격을 사용하지 않습니다.\n시트: https://docs.google.com/spreadsheets/d/'+PG.ID+'/edit'});
      props.setProperty(key,now.toISOString());
      return {status:'INCOMPLETE_SENT',day};
    }
    const previous=result.dates.filter(d=>d<day).pop();
    const lines=['[홀짝 ETF 모의계좌] '+day+' 장마감 성적표',
      'A/B/C/D 각 1,000만원 · 코스피200/코스닥150 + 1배 인버스 · 실주문 없음',
      '완료 45분봉 확인 뒤 처음 확보한 장중 가격으로 가상체결. 거래비용·슬리피지 반영.'];
    for(const row of result.scores){
      const [name,initial,nav,pct,mdd,count,wins,exposure,cash]=row;
      const a=result.accounts[name],today=a.daily.find(r=>r[0]===day);
      const prior=previous?a.daily.find(r=>r[0]===previous):null;
      if(name==='D'&&today&&today[12]==='WARMUP')lines.push('D MODE: ER20 WARMUP '+Math.min(21,a.dReadiness['069500']||0)+'/21 synchronized 45m observations; no simulated D trades yet.');
      const dayPnl=today?(Number(today[4])-(prior?Number(prior[4]):initial)):null;
      lines.push(name+'형  순자산 '+Math.round(nav).toLocaleString('ko-KR')+'원'
        +' | 오늘 '+(dayPnl===null?'미확인':Math.round(dayPnl).toLocaleString('ko-KR')+'원')
        +' | 누적 '+(pct*100).toFixed(2)+'% | 최대낙폭 '+(mdd*100).toFixed(2)+'%'
        +' | 총 거래 '+count+'회 | 현금 '+(cash*100).toFixed(1)+'%');
    }
    lines.push('오늘 잠정 우위: '+result.best+'형 (누적수익률 - 2×최대낙폭 기준)');
    lines.push('주의: 미실현손실까지 합산한 총자산 기준. 45분봉 확정 뒤 관찰가이므로 호가 실제 체결과 다릅니다.',
      '1개월은 우위 확정이 아닌 탐색 실험입니다.',
      '시트: https://docs.google.com/spreadsheets/d/'+PG.ID+'/edit');
    lines.push('D method: verified historic 45m close seed through 2026-10-30 12:00 KST plus later observed prices. Historical seed is indicators only, no past trades. 10/30 afternoon bars are not in seed. During initial bars this mixed-price ER20 is not official 45m OHLCV closes.');
    MailApp.sendEmail({to:hmAlertRecipient_(),
      subject:'[홀짝 ETF 모의계좌] '+day+' · A/B/C/D 장마감 성적표',
      body:lines.join('\n')});
    props.setProperty(key,now.toISOString());
    return {status:'SENT',day};
  }finally{lock.releaseLock();}
}
