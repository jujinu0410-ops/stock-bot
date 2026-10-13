/**
 * Signal Review Mail v1 — standalone, READ-ONLY toward trading systems.
 * D0/D+3/D+5 trading-day outcomes; daily single Gmail after Korean regular close.
 * Source: actually sent Gmail signals, NOT proposed/suppressed orders.
 * Close: date-verified GoogleFinance historical KRX close; pending if unavailable.
 * Does not change alerts, orders, JEV, YouTube ingestion or any production sheet.
 * Install into a NEW STANDALONE Google Apps Script project; run srInstall() ONCE.
 */
const SR = Object.freeze({
  VERSION: '1.0.6', TZ: 'Asia/Seoul', LOOKBACK_DAYS: 45, RECHECK_DAYS: 21,
  SHEET_EVENTS: 'SIGNAL_EVENTS', SHEET_PRICES: 'PRICE_CACHE',
  SHEET_CALENDAR: 'TRADING_CALENDAR', SHEET_RUNS: 'REPORT_LOG',
  BENCHMARK: 'KRX:005930', MAX_THREADS_PER_CATEGORY: 400,
  PRICE_MAX_ROWS: 2000,
  ETF_BOOK_ID: '15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw',
  YT_BOOK_ID: '10R11CnOs0Gi3Ffag8aWOw8sNZw0E_ZFlM5_qqSyO2xw',
  EVENT_HEADER: ['EmailId','EventKey','Source','SignalDate','SignalTimeKST','Ticker','Name','Stage','Direction','AlertPrice','PriceStatus','GmailLink','Session','ImportedAt'],
  PRICE_HEADER: ['Ticker','TradeDate','VerifiedClose','FormulaStatus','CollectedAt'],
  LOG_HEADER: ['TimestampKST','Action','Detail'],
});

function srInstall() {
  const props = PropertiesService.getScriptProperties();
  let id = props.getProperty('SR_BOOK_ID');
  let ss;
  if (!id) {
    ss = SpreadsheetApp.create('주식신호_당일_3일_5일_복기');
    id = ss.getId();
    props.setProperty('SR_BOOK_ID', id);
  } else {
    ss = SpreadsheetApp.openById(id);
  }
  srEnsure_(ss);
  const recipient = String(props.getProperty('SR_REPORT_TO') || Session.getEffectiveUser().getEmail() || '').trim();
  if (recipient) props.setProperty('SR_REPORT_TO', recipient);
  const handlers = ['srPrepareDaily', 'srSendDaily'];
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (handlers.indexOf(t.getHandlerFunction()) >= 0) ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('srPrepareDaily').timeBased().everyDays(1)
    .atHour(16).nearMinute(25).inTimezone(SR.TZ).create();
  ScriptApp.newTrigger('srSendDaily').timeBased().everyDays(1)
    .atHour(18).nearMinute(15).inTimezone(SR.TZ).create();
  srLog_(ss, 'INSTALLED', 'Spreadsheet=' + ss.getUrl() + '; Report recipient=' + (recipient ? 'configured' : 'NOT_CONFIGURED'));
  Logger.log('REPORT_BOOK=' + ss.getUrl());
  Logger.log('Set Script Properties SR_REPORT_TO if no recipient was found.');
  return ss.getUrl();
}

function srEnsure_(ss) {
  [
    [SR.SHEET_EVENTS, SR.EVENT_HEADER],
    [SR.SHEET_PRICES, SR.PRICE_HEADER],
    [SR.SHEET_RUNS, SR.LOG_HEADER],
  ].forEach(function(spec) {
    let sh = ss.getSheetByName(spec[0]) || ss.insertSheet(spec[0]);
    if (sh.getMaxColumns() < spec[1].length) sh.insertColumnsAfter(sh.getMaxColumns(), spec[1].length - sh.getMaxColumns());
    sh.getRange(1,1,1,spec[1].length).setValues([spec[1]]);
    sh.setFrozenRows(1);
  });
  let cal = ss.getSheetByName(SR.SHEET_CALENDAR) || ss.insertSheet(SR.SHEET_CALENDAR);
  if (cal.getRange('A1').getFormula() === '') {
    cal.getRange('A1').setFormula('=GOOGLEFINANCE("' + SR.BENCHMARK + '","close",TODAY()-80,TODAY())');
  }
  if (!cal.isSheetHidden()) cal.hideSheet();
  return ss;
}

function srBook_() {
  const id = PropertiesService.getScriptProperties().getProperty('SR_BOOK_ID');
  if (!id) throw new Error('Not installed: run srInstall first');
  return SpreadsheetApp.openById(id);
}
function srYmd_(d) { return Utilities.formatDate(d, SR.TZ, 'yyyy-MM-dd'); }
function srClock_(d) { return Utilities.formatDate(d, SR.TZ, 'HH:mm:ss'); }
function srNumber_(s) { const n = Number(String(s || '').replace(/,/g,'')); return Number.isFinite(n) && n > 0 ? n : null; }
function srSide_(stage) { return /^(▲|△)/.test(stage) ? 'BUY' : /^(▼|▽|⚠)/.test(stage) ? 'SELL' : 'UNKNOWN'; }
function srStage_(s) {
  const a = String(s || '').trim();
  if (/▲/.test(a)) return '▲ 매수·상승확인';
  if (/△/.test(a)) return '△ 매수·상승조짐';
  if (/⚠/.test(a)) return '⚠ 매도경고';
  if (/▼/.test(a)) return '▼ 하락확인';
  if (/▽/.test(a)) return '▽ 하락조짐';
  return '';
}
function srCode_(s) { const d = String(s || '').replace(/\D/g, ''); return d.length <= 6 ? d.padStart(6,'0') : ''; }
function srNorm_(s) { return String(s || '').replace(/\s+/g,'').replace(/[-·]/g,'').toLowerCase(); }
function srHtml_(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function(ch){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch];}); }
function srLog_(ss, action, detail) { ss.getSheetByName(SR.SHEET_RUNS).appendRow([srYmd_(new Date())+' '+srClock_(new Date()),action,String(detail).slice(0,500)]); }
function srDateValue_(s) { if (s instanceof Date) return srYmd_(s); return /^\d{4}-\d{2}-\d{2}$/.test(String(s || '')) ? String(s) : ''; }

function srEtfMap_() {
  const map = {};
  try {
    const sh = SpreadsheetApp.openById(SR.ETF_BOOK_ID).getSheetByName('ETF_WATCHLIST');
    if (!sh || sh.getLastRow() < 2) return map;
    sh.getRange(2,4,sh.getLastRow()-1,2).getDisplayValues().forEach(function(r) {
      const code = srCode_(r[0]); if (code && r[1]) map[srNorm_(r[1])] = code;
    });
  } catch (e) { Logger.log('ETF lookup unavailable: ' + e); }
  return map;
}

/** PURE extractor for Gmail message fields; unsupported price is null, never guessed. */
function srExtractSignals_(subject, body, etfCodes) {
  subject=String(subject||'');
  // GmailApp.getPlainBody() often preserves Markdown *emphasis* around prices,
  // headings, and ETF labels. Remove decoration before parsing stock signal text.
  body=String(body||'').replace(/\r\n?/g,'\n').replace(/\*/g,'').replace(/\u00a0/g,' ').replace(/[\u200b-\u200d\ufeff]/g,'');
  const out=[];
  // Text-only mail bodies may retain Markdown *price* emphasis.
  const readPrice=function(line) {
    const m=String(line||'').match(/현재가\s*[:：]?\s*[*_~\s]*([\d,]+)\s*[*_~]?/);
    return m ? srNumber_(m[1]):null;
  };
  if(subject.indexOf('[주식 통합신호]')>=0) {
    const marker=body.indexOf('UNIFIED_EVENTS_JSON=');
    if(marker>=0) {
      try {
        const data=JSON.parse(body.slice(marker+'UNIFIED_EVENTS_JSON='.length).split('\n')[0].trim());
        data.forEach(function(e){
          if(/^U2_/.test(e.id||'')&&/^\d{6}$/.test(e.ticker||''))out.push({
            source:String(e.source||'UNIFIED'),code:e.ticker,name:String(e.name||e.ticker),
            stage:srStage_(e.stage),price:srNumber_(e.price),eventId:e.id,session:e.session
          });
        });
      }catch(err){Logger.log('UNIFIED_PARSE_ERROR '+String(err));}
    }
  } else if(subject.indexOf('[YouTube 매수감시]')>=0) {
    const m=subject.match(/\[YouTube 매수감시\]\[([^\]]+)\]\s*(.*?)\s+(\d{6})(?=\s|·|$)/);
    const price=readPrice(body);
    if(m)out.push({source:'YOUTUBE',code:m[3],name:m[2],stage:srStage_(m[1]),price:price});
  } else if(subject.indexOf('[보유종목 5분경보]')>=0) {
    // Actual GmailApp plain body may collapse header and name onto one line:
    // "△ 상승조짐  흥아해운 (003280)\n현재가 1,896원".
    const pattern=/(?:^|\n)\s*([▲△▼▽⚠])\s*(상승조짐|상승확인|하락조짐|하락확인|손절위험|손절경고|매도 대비|매도주의|매수조짐|매수확인|급락주의|단기매도경고)\s+([^()\n]{1,100}?)\s*\((\d{6})\)\s*현재가\s*[:：]?\s*([\d,]+)/g;
    let m;
    while((m=pattern.exec(body))!==null){
      const label=m[2];
      const stage=/손절|위험|경고|급락/.test(label) ? m[1]+' '+label : srStage_(m[1]+' '+label);
      out.push({source:'HELD',code:m[4],name:m[3].trim(),stage:stage,price:srNumber_(m[5])});
    }
  } else if(subject.indexOf('ETF 매수·매도 신호')>=0) {
    // GmailApp plain text renders HTML ETF rows as pipe-delimited tables:
    // "미국나스닥100 | 133690 TIGER 미국나스닥100 | 0.00% | +1.91% | +5.32% | ▲"
    // followed by "상승확인" on the next line.
    // Gmail HTML plain-text extraction sometimes wraps a single table row at a pipe.
    const lines=body.replace(/\n\s*\|\s*/g,' | ').split('\n');
    for(let i=0;i<lines.length;i++){
      let name='',code='',signal=null;
      const row=lines[i].trim();
      const table=row.match(/^\|?\s*[^|\n]{1,75}\|\s*(\d{6})\s+([^|\n]+?)\s*\|.*?([▲△▼▽])\s*(?:상승확인|상승조짐|하락확인|하락조짐)?\s*\|?\s*$/);
      if(table){
        code=table[1];name=table[2].trim();
        const next=(lines[i+1]||'').trim();
        const label=next.match(/^(상승확인|상승조짐|하락확인|하락조짐)/);
        const inline=row.match(/([▲△▼▽])\s*(상승확인|상승조짐|하락확인|하락조짐)/);
        if(inline)signal=inline;
        else if(label)signal=[null,table[3],label[1]];
        // Require agreement with configured ETF universe; do not guess tickers.
        if((etfCodes||{})[srNorm_(name)]!==code)continue;
      } else {
        // Legacy plain text slash-format, if present.
        const legacy=row.match(/^([^/\n]+?)\s*\/\s*(.*?)\s+[-+]?\d+(?:\.\d+)?%\s*$/);
        if(!legacy)continue;
        name=legacy[2].trim();code=(etfCodes||{})[srNorm_(name)]||'';
        const next=(lines[i+1]||'');
        signal=next.match(/([▲△▼▽])\s*(상승확인|상승조짐|하락확인|하락조짐)/);
      }
      if(!code||!signal)continue;
      out.push({source:'ETF',code:code,name:name,
        stage:srStage_(signal[1]+' '+signal[2]),price:null});
    }
  }
  return out.filter(function(e){return e.code&&e.stage&&srSide_(e.stage)!=='UNKNOWN';});
}

function srCollectMail_(ss) {
  const sh = ss.getSheetByName(SR.SHEET_EVENTS);
  const last = sh.getLastRow();
  const existing = new Set(last >= 2 ? sh.getRange(2,2,last-1,1).getDisplayValues().map(function(x){return x[0];}) : []);
  const etfCodes = srEtfMap_();
  const props = PropertiesService.getScriptProperties();
  const days = props.getProperty('SR_INGEST_INITIALIZED') === 'Y' ? SR.RECHECK_DAYS : SR.LOOKBACK_DAYS;
  const queries = [
    'in:sent subject:"주식 통합신호" newer_than:'+days+'d -in:trash -in:spam',
    'subject:"YouTube 매수감시" newer_than:'+days+'d -in:trash -in:spam',
    'subject:5분경보 newer_than:'+days+'d -in:trash -in:spam',
    'subject:ETF newer_than:'+days+'d -in:trash -in:spam',
  ];
  const rows = [];
  Logger.log('ETF_SYMBOL_MAP_SIZE='+Object.keys(etfCodes).length);
  queries.forEach(function(q) {
    let scanned=0, parsed=0, matchedThreads=0, firstMiss='';
    for (let start=0;start<SR.MAX_THREADS_PER_CATEGORY;start+=100) {
      const threads = GmailApp.search(q, start, Math.min(100,SR.MAX_THREADS_PER_CATEGORY-start));
      if (!threads.length) break;
      matchedThreads += threads.length;
      threads.forEach(function(thread) {
        thread.getMessages().forEach(function(m) {
          scanned++;
          const emitted = srExtractSignals_(m.getSubject(), m.getPlainBody(), etfCodes);
          parsed += emitted.length;
          if (!emitted.length && !firstMiss &&
              ((q.indexOf('5분경보')>=0 && m.getSubject().indexOf('[보유종목 5분경보]')>=0) ||
               (q.indexOf('subject:ETF')>=0 && m.getSubject().indexOf('ETF 매수·매도 신호')>=0))) {
            firstMiss='subject='+m.getSubject().slice(0,95)+' body='+m.getPlainBody().slice(0,320).replace(/\n/g,' | ');
          }
          const date = m.getDate(); const d = srYmd_(date);
          const hhmm = Number(Utilities.formatDate(date,SR.TZ,'HHmm'));
          emitted.forEach(function(e) {
            const key = e.eventId || m.getId()+'|'+e.source+'|'+e.code+'|'+e.stage;
            if (existing.has(key)) return;
            existing.add(key);
            const session = e.session || (hhmm > 1530 || hhmm < 900 ? 'OUTSIDE_KRX' : 'KRX_SESSION');
            rows.push([m.getId(), key, e.source, d, srClock_(date), e.code,
              e.name, e.stage, srSide_(e.stage), e.price || '',e.price ? 'SOURCE_PRICE' : 'PRICE_MISSING',
              'https://mail.google.com/mail/u/0/#all/'+m.getId(), session, new Date()]);
          });
        });
      });
      if (threads.length < 100) break;
    }
    const summary='SOURCE='+q.split(' newer_than:')[0]+' THREADS='+matchedThreads+' MESSAGES='+scanned+' PARSED='+parsed;
    Logger.log('SOURCE_SCAN '+summary);
    srLog_(ss, 'SOURCE_SCAN', summary);
    if(firstMiss)srLog_(ss,'PARSER_SAMPLE',firstMiss);
  });
  if (rows.length) sh.getRange(last+1,1,rows.length,SR.EVENT_HEADER.length).setValues(rows);
  props.setProperty('SR_INGEST_INITIALIZED', 'Y');
  srLog_(ss,'INGEST','New actual emailed signal rows: '+rows.length);
  return rows.length;
}

function srMarketDays_(ss) {
  const vals = ss.getSheetByName(SR.SHEET_CALENDAR).getRange('A2:A250').getValues();
  const days = new Set();
  vals.forEach(function(row) {
    if (row[0] instanceof Date && !isNaN(row[0].getTime())) days.add(srYmd_(row[0]));
  });
  return Array.from(days).sort();
}

function srTargets_(events, days, today) {
  const set = new Set(days);
  const all = new Set();
  events.forEach(function(e) {
    if (!e.price || e.session !== 'KRX_SESSION') return;
    // Prepare today's D0 prices even before GOOGLEFINANCE publishes today's row.
    if (e.date === today) all.add(e.code + '|' + today);
    const index = days.indexOf(e.date);
    if (index < 0) return;
    [0,3,5].forEach(function(h) {
      const d = days[index + h];
      if (d && d <= today && set.has(d)) all.add(e.code + '|' + d);
      // If today is the next unconfirmed session, prefetch its close for D+3/+5.
      if (!d && index + h === days.length && today > days[days.length-1]) all.add(e.code + '|' + today);
    });
  });
  return Array.from(all).sort();
}

function srEvents_(ss) {
  const sh = ss.getSheetByName(SR.SHEET_EVENTS), last=sh.getLastRow();
  if (last<2) return [];
  return sh.getRange(2,1,last-1,SR.EVENT_HEADER.length).getValues().map(function(r){
    return {id:String(r[0]),key:String(r[1]),source:String(r[2]),date:srDateValue_(r[3]),
      time:String(r[4]),code:srCode_(r[5]),name:String(r[6]),stage:String(r[7]),
      direction:String(r[8]),price:srNumber_(r[9]),session:String(r[12])};
  }).filter(function(e){return e.date && e.code;});
}

function srPriceFormula_(row) {
  // PRICE_CACHE A is auto-coerced to a number, B to a Sheets date serial.
  // Preserve leading zero tickers; use B as a real date (not LEFT(B,4) on its serial).
  const code='TEXT(A'+row+',"000000")', d='B'+row;
  const gf='IFERROR(GOOGLEFINANCE("KRX:"&'+code+',"close",'+d+','+d+'),GOOGLEFINANCE("KOSDAQ:"&'+code+',"close",'+d+','+d+'))';
  // Demand provider observation for the EXACT requested date.
  return '=IFERROR(LET(g,'+gf+',IF(TEXT(INDEX(g,2,1),"yyyy-mm-dd")=TEXT(B'+row+',"yyyy-mm-dd"),INDEX(g,2,2),"")),"")';
}
function srPriceMap_(ss) {
  const sh=ss.getSheetByName(SR.SHEET_PRICES), last=sh.getLastRow(),map={};
  if(last<2)return map;
  sh.getRange(2,1,last-1,3).getValues().forEach(function(r){
    const key=srCode_(r[0])+'|'+srDateValue_(r[1]);
    const v=srNumber_(r[2]);
    map[key]=v;
  });
  return map;
}
function srBuildPriceCache_(ss,days,events) {
  const sh=ss.getSheetByName(SR.SHEET_PRICES),last=sh.getLastRow(),known=new Set();
  if(last>1)sh.getRange(2,1,last-1,2).getDisplayValues().forEach(function(r){known.add(srCode_(r[0])+'|'+r[1]);});
  const today=srYmd_(new Date()), targets=srTargets_(events,days,today);
  const add=targets.filter(function(k){return !known.has(k);}).slice(0,SR.PRICE_MAX_ROWS);
  if(!add.length) return 0;
  const first=last+1;
  const rows=add.map(function(k,i){const parts=k.split('|');return [parts[0],parts[1],srPriceFormula_(first+i),'HISTORICAL_EXACT_DATE',new Date()];});
  if(sh.getMaxRows()<first+rows.length-1)sh.insertRowsAfter(sh.getMaxRows(),first+rows.length-1-sh.getMaxRows());
  sh.getRange(first,1,rows.length,SR.PRICE_HEADER.length).setValues(rows);
  return rows.length;
}

function srPrepareDaily() {
  const today=srYmd_(new Date()), dow=Number(Utilities.formatDate(new Date(),SR.TZ,'u'));
  if(dow===6||dow===7)return 'WEEKEND';
  const ss=srEnsure_(srBook_());
  const added=srCollectMail_(ss);
  SpreadsheetApp.flush();
  const days=srMarketDays_(ss), ev=srEvents_(ss);
  const priced=srBuildPriceCache_(ss,days,ev);
  srLog_(ss,'PREPARED','day='+today+' new_events='+added+' new_price_requests='+priced+' trading_days='+days.length);
  return 'new_events='+added+', price_queries='+priced;
}

function srCohorts_(events,days,today,prices) {
  const groups={0:[],3:[],5:[]};
  events.forEach(function(e) {
    const idx=days.indexOf(e.date); if(idx<0) return;
    [0,3,5].forEach(function(h) {
      if(days[idx+h]!==today)return;
      const close=prices[e.code+'|'+today]||null;
      const active=e.price && e.session==='KRX_SESSION';
      const ret=close&&active ? (close/e.price-1)*100*(e.direction==='SELL'?-1:1):null;
      groups[h].push(Object.assign({},e,{close:close,returnPct:ret,
        reason:!e.price?'ALERT_PRICE_MISSING':e.session!=='KRX_SESSION'?'NXT_PRICE_NOT_VERIFIED':!close?'DATED_CLOSE_PENDING':''}));
    });
  });
  return groups;
}
function srLegacy60Map_() {
  const out={};
  try {
    const sh=SpreadsheetApp.openById(SR.YT_BOOK_ID).getSheetByName('ALERT_OUTCOME');
    if(!sh || sh.getLastRow()<2) return out;
    const rows=sh.getRange(2,1,sh.getLastRow()-1,18).getValues();
    rows.forEach(function(r){
      const day=r[0] instanceof Date ? srYmd_(r[0]) : srDateFromDisplay_(r[0]);
      const code=srCode_(r[1]);
      if(!day||!code)return;
      const hhmmss=r[0] instanceof Date ? srClock_(r[0]) : srTimeFromDisplay_(r[0]);
      if (!hhmmss) return;
      const key=code+'|'+day;
      (out[key]||(out[key]=[])).push({time:hhmmss,grade:String(r[13]||''),mfe:r[11],mae:r[12]});
    });
  } catch(e) {Logger.log('Legacy 60m history unavailable: '+e);}
  return out;
}
function srDateFromDisplay_(v){
  const s=String(v||'');
  const m=s.match(/(20\d{2})[.\-/ ]+\s*(\d{1,2})[.\-/ ]+\s*(\d{1,2})/);
  if(!m)return '';
  return m[1]+'-'+m[2].padStart(2,'0')+'-'+m[3].padStart(2,'0');
}
function srTimeFromDisplay_(v){
  const m=String(v||'').match(/(?:^|\s)(\d{1,2}):(\d{2})(?::(\d{2}))?/);
  return m ? m[1].padStart(2,'0')+':'+m[2]+':'+(m[3]||'00') : '';
}
function srLegacyRef_(legacy,e){
  // Match the SAME emailed event, not merely the same ticker/day; repeated alerts must not share grades.
  if(e.source!=='YOUTUBE')return null;
  const rows=(legacy||{})[e.code+'|'+e.date]||[];
  const toSeconds=function(t){const x=String(t||'').split(':').map(Number);return x.length===3&&x.every(Number.isFinite)?x[0]*3600+x[1]*60+x[2]:NaN;};
  const t=toSeconds(e.time);
  if(!Number.isFinite(t))return null;
  let best=null,dist=Infinity;
  rows.forEach(function(r){
    const d=Math.abs(toSeconds(r.time)-t);
    if(d<dist){best=r;dist=d;}
  });
  return dist<=600?best:null;
}
function srMetrics_(rows) {
  const ok=rows.filter(function(e){return e.returnPct!==null;});
  const wins=ok.filter(function(e){return e.returnPct>0;}).length;
  const avg=ok.length?ok.reduce(function(a,e){return a+e.returnPct;},0)/ok.length:null;
  return {total:rows.length,valid:ok.length,wins:wins,avg:avg};
}
function srFmtPct_(p) {return p===null?'미확인':(p>=0?'+':'')+p.toFixed(2)+'%';}
function srRender_(today,groups,legacy) {
  legacy=legacy||{};
  const html=['<div style="font-family:Arial, sans-serif;color:#202124;max-width:950px">',
    '<h2>📊 주식신호 D0·D+3·D+5 복기 | '+srHtml_(today)+'</h2>',
    '<p>실제 Gmail 발송 신호만 집계 · 매수는 상승, 매도경고는 하락이 유리한 방향 · 실거래 손익 아님</p>'];
  const plain=['[주식신호 복기] '+today, 'Gmail 발송 신호 기준 / 수수료 및 슬리피지 미반영'];
  [0,3,5].forEach(function(h) {
    const list=groups[h]||[],s=srMetrics_(list);
    const title=h===0?'당일(D0)':h===3?'3거래일(D+3)':'5거래일(D+5)';
    const summary='신호 '+s.total+'건 / 평가가능 '+s.valid+'건 / 방향적중 '+(s.valid?Math.round(s.wins/s.valid*100)+'%':'미산정')+' / 평균 방향수익률 '+srFmtPct_(s.avg);
    html.push('<h3 style="margin:24px 0 6px">'+title+'</h3><p>'+summary+'</p>');
    plain.push('\n'+title+' - '+summary);
    html.push('<table style="border-collapse:collapse;width:100%;font-size:12px"><tr style="background:#f0f2f5"><th>구분</th><th>종목</th><th>신호</th><th>신호가</th><th>평가종가</th><th>방향수익률</th><th>기존 60분 평가</th><th>상태</th></tr>');
    list.sort(function(a,b){return (a.returnPct===null?1:0)-(b.returnPct===null?1:0)||(a.returnPct||0)-(b.returnPct||0);});
    list.slice(0,60).forEach(function(e) {
      const p=e.returnPct; const color=p===null?'#777':p>=0?'#147d51':'#cb3d42';
      const old=srLegacyRef_(legacy,e);
      const ref=e.source==='YOUTUBE'&&old&&old.grade ? old.grade+' (MFE '+old.mfe+'%, MAE '+old.mae+'%)' : '—';
      const cells=[e.source,e.name+' ('+e.code+')',e.stage,e.price||'—',e.close||'—',srFmtPct_(p),ref,e.reason||'평가완료'];
      html.push('<tr>'+cells.map(function(x,i){return '<td style="border-bottom:1px solid #ddd;padding:6px;'+(i===5?'color:'+color+';':'')+'">'+srHtml_(x)+'</td>';}).join('')+'</tr>');
      plain.push(e.source+' '+e.name+' '+e.stage+' '+srFmtPct_(p)+(e.reason?' ['+e.reason+']':''));
    });
    html.push('</table>');
    if(list.length>60)html.push('<p>표시 60건 / 전체 '+list.length+'건; 원본 시트에서 상세 확인</p>');
  });
  const byType={}; Object.keys(groups).forEach(function(h){groups[h].forEach(function(e){
    if(e.returnPct===null)return; const k=e.source+' / '+e.stage+' / D+'+h;
    (byType[k]||(byType[k]=[])).push(e.returnPct);
  });});
  const suspicious=Object.keys(byType).filter(function(k){return byType[k].length>=5;}).map(function(k){
    const z=byType[k],m=z.reduce(function(a,b){return a+b;},0)/z.length;
    return {k:k,n:z.length,avg:m};
  }).sort(function(a,b){return a.avg-b.avg;}).slice(0,4);
  html.push('<h3>🔁 개선 검토 후보(자동 변경 없음)</h3>');
  if(suspicious.length){html.push('<ul>');suspicious.forEach(function(x){html.push('<li>'+srHtml_(x.k)+': '+x.n+'건, 평균 '+srFmtPct_(x.avg)+' — 필터·신호시점 추가 검증</li>');});html.push('</ul>');}
  else html.push('<p>동일 유형·기간의 평가가능 표본 5건 미만: 섣부른 규칙변경 보류</p>');
  html.push('<p style="font-size:11px;color:#666">시가·고가가 아닌 기준 신호가 대비 날짜 검증된 KRX 종가로 계산합니다. 유튜브 60분 MFE·MAE는 기존 복기 참고값이며 당일 전체 고가·저가가 아닙니다. NXT 장후 신호는 별도 확인 전까지 평가 보류합니다. 가격 미확인 건을 성공·실패로 세지 않습니다.</p></div>');
  return {html:html.join(''),text:plain.join('\n')};
}
function srPreviewDaily() {
  srPrepareDaily();
  const ss=srBook_(),today=srYmd_(new Date());
  const groups=srCohorts_(srEvents_(ss),srMarketDays_(ss),today,srPriceMap_(ss));
  const text=srRender_(today,groups,srLegacy60Map_()).text;
  Logger.log(text);
  return text;
}
function srSendDaily() {
  const today=srYmd_(new Date()),props=PropertiesService.getScriptProperties();
  const dow=Number(Utilities.formatDate(new Date(),SR.TZ,'u'));
  if(dow===6||dow===7)return 'WEEKEND';
  if(props.getProperty('SR_SENT_'+today)==='Y')return 'ALREADY_SENT';
  // The optional ChatGPT daily review may already have sent this exact report.
  // Cross-check Gmail as well as Script Properties to prevent duplicate delivery.
  const subject='[주식신호 복기] '+today+' | 당일·3거래일·5거래일';
  const duplicate=GmailApp.search('in:sent subject:"[주식신호 복기]" newer_than:3d',0,30)
    .some(function(th){return th.getMessages().some(function(m){return m.getSubject()===subject;});});
  if(duplicate){props.setProperty('SR_SENT_'+today,'Y');return 'ALREADY_SENT_GMAIL';}
  const to=String(props.getProperty('SR_REPORT_TO')||'').trim();
  if(!to)throw new Error('Set SR_REPORT_TO in Script Properties');
  const ss=srEnsure_(srBook_());
  srCollectMail_(ss);
  const days=srMarketDays_(ss);
  if(days.indexOf(today)<0){srLog_(ss,'SKIPPED','Market day unconfirmed (holiday OR GOOGLEFINANCE delay): '+today);return 'MARKET_UNVERIFIED';}
  const ev=srEvents_(ss);
  const created=srBuildPriceCache_(ss,days,ev);
  if(created>0){ // Allow formulas to populate on the next daily cycle; never silently use stale quotes.
    srLog_(ss,'PRICE_PENDING','New historical price queries '+created+' on report run');
  }
  SpreadsheetApp.flush();
  const groups=srCohorts_(ev,days,today,srPriceMap_(ss));
  const report=srRender_(today,groups,srLegacy60Map_());
  GmailApp.sendEmail(to,subject,report.text,{htmlBody:report.html,name:'StockBot 신호복기'});
  props.setProperty('SR_SENT_'+today,'Y');
  srLog_(ss,'SENT','Report sent. D0='+groups[0].length+' D3='+groups[3].length+' D5='+groups[5].length);
  return 'SENT';
}

/** Diagnostic only: log text format and parsing counts for the newest actual alert per category. */
function srAuditMessageFormats() {
  const samples = [
    ['YOUTUBE', 'subject:"YouTube 매수감시" newer_than:15d'],
    ['HELD', 'subject:"보유종목 5분경보" newer_than:15d'],
    ['ETF', 'subject:"ETF 매수·매도 신호" newer_than:15d']
  ];
  const codes = srEtfMap_();
  samples.forEach(function(pair) {
    const threads = GmailApp.search(pair[1],0,1);
    if (!threads.length) { Logger.log(pair[0]+' mail NOT_FOUND'); return; }
    const m = threads[0].getMessages().slice(-1)[0];
    const p = m.getPlainBody(), h = m.getBody();
    const ix = p.indexOf('현재가');
    const around = ix>=0 ? p.slice(ix,ix+50).replace(/\s+/g,' ') : 'NO_PLAIN_CURRENT_PRICE';
    Logger.log(pair[0]+' plainLength='+p.length+' htmlLength='+h.length+
      ' plainPriceIndex='+ix+' htmlPriceIndex='+h.indexOf('현재가')+
      ' plainMatch='+!!p.match(/현재가\s*[:：]?\s*([\d,]+)/)+
      ' parsed='+srExtractSignals_(m.getSubject(),p,codes).length+
      ' snippet='+around);
  });
}


/** Restore only missing alert prices from original messages; never guess ETF entry prices. */
function srRepairMissingPrices() {
  const ss=srBook_(), sh=ss.getSheetByName(SR.SHEET_EVENTS), last=sh.getLastRow();
  if(last<2)return 'NO_EVENTS';
  const rows=sh.getRange(2,1,last-1,SR.EVENT_HEADER.length).getValues();
  const updates=rows.map(function(r){return [r[9],r[10]];});
  const etfCodes=srEtfMap_(),cache={};
  let restored=0,missing=0,errors=0;
  rows.forEach(function(r,i){
    if(srNumber_(r[9]))return;
    missing++;
    const id=String(r[0]||'');if(!id)return;
    try {
      if(!(id in cache)){
        const m=GmailApp.getMessageById(id);
        cache[id]=m?srExtractSignals_(m.getSubject(),m.getPlainBody(),etfCodes):[];
      }
      const code=srCode_(r[5]), source=String(r[2]||''),stage=String(r[7]||'');
      const found=cache[id].find(function(x){return x.code===code && x.source===source &&
        x.stage===stage && x.price;});
      if(found){
        updates[i]=[found.price,'SOURCE_PRICE_REPAIRED'];
        restored++;
      }
    }catch(e){errors++;Logger.log('Backfill row '+(i+2)+' failed: '+e.message);}
  });
  if(restored)sh.getRange(2,10,updates.length,2).setValues(updates);
  srLog_(ss,'PRICE_BACKFILL','Missing='+missing+' restored='+restored+' errors='+errors);
  Logger.log('PRICE_BACKFILL missing='+missing+' restored='+restored+' errors='+errors);
  return 'missing='+missing+' restored='+restored+' errors='+errors;
}

/** Safe diagnostic and prepare: DOES NOT SEND MAIL. */
function srRepairAndPreview() {
  Logger.log(srRepairMissingPrices());
  Logger.log(srPrepareDaily());
  const ss=srBook_(),events=srEvents_(ss),today=srYmd_(new Date());
  const c=events.reduce(function(a,e){a[e.source]=(a[e.source]||0)+1;return a;},{});
  Logger.log('EVENTS_TOTAL='+events.length+' SOURCE_COUNTS='+JSON.stringify(c));
  const p=srPriceMap_(ss);
  Logger.log('VERIFIED_CLOSE_VALUES='+Object.keys(p).filter(function(k){return p[k];}).length);
  const marketDays=srMarketDays_(ss);
  const evalDate=marketDays.indexOf(today)>=0 ? today : (marketDays[marketDays.length-1] || today);
  Logger.log('PREVIEW_EVAL_DATE='+evalDate+(evalDate===today?'':' (latest completed session)'));
  const groups=srCohorts_(events,marketDays,evalDate,p);
  Logger.log(srRender_(evalDate,groups,srLegacy60Map_()).text);
}
