/*
 * YouTube candidate ingest V12: ONLY two daily mail sources.
 * A. Regular [경제 Intelligence] dated report, published >=08:00 KST
 * B. [경제 Intelligence] [08:45 증권사 리포트 종목수집], mailed 08:30-09:30 KST
 * Excludes former 08:50/10:50 expert-TV recommendations and all other reports.
 * Reuses existing 5-minute scheduled tick, Naver identity validation, row reuse,
 * 1-calendar-month candidate TTL, and 45-minute monitoring unchanged.
 */
const YCV12 = Object.freeze({
  TZ: 'Asia/Seoul',
  SCAN_START: 8 * 60,
  SCAN_END: 15 * 60 + 30,
  BROKER_START: 8 * 60 + 30,
  BROKER_END_EXCLUSIVE: 9 * 60 + 31,
  POLL_INTERVAL_MS: 5 * 60 * 1000,
  POLL_KEY: 'TWO_MAIL_V12_LAST_POLL_MS',
  PROCESSED_DAY_KEY: 'TWO_MAIL_V12_PROCESSED_DAY',
  PROCESSED_IDS_KEY: 'TWO_MAIL_V12_PROCESSED_IDS',
  STATUS_KEY: 'TWO_MAIL_V12_STATUS',
  MAX_THREADS: 100,
  MAX_STOCKS_IN_MAIL: 100
});

let ycv12MailCache_ = null;

function ycv12Type_(subject) {
  const s=String(subject||'').trim();
  if (/^\[경제 Intelligence\]\s*(?:\d{1,2}월\s*\d{1,2}일|\d{4}-\d{2}-\d{2})\s*(?:\||$)/.test(s)) return 'REGULAR';
  if (/^\[경제 Intelligence\]\s*\[(?:08:45\s*)?증권사 리포트 종목수집\]/.test(s)) return 'BROKER';
  return '';
}

function ycv12ParseBlock_(rawBody) {
  const body=String(rawBody||'').replace(/\r\n?/g,'\n').replace(/\u00a0/g,' ').replace(/\u200b/g,'');
  const m=body.match(/\[YT_ALL_STOCKS_BEGIN\]([\s\S]*?)\[YT_ALL_STOCKS_END\]/);
  if (!m) return {found:false,stocks:[]};
  const result=[],seen=new Set();
  m[1].split('\n').forEach(function(raw){
    const line=String(raw||'').trim();
    if (!line) return;
    const match=line.match(/^(\d{6})\|([^|\n]{2,60})$/);
    if (!match) throw new Error('TWO_MAIL_V12_INVALID_STOCK_LINE');
    const code=normalizeCandidateCode_(match[1]),name=String(match[2]||'').trim();
    if (!code||!name) throw new Error('TWO_MAIL_V12_INVALID_CODE_OR_NAME');
    if (seen.has(code)) return;
    seen.add(code);result.push({code:code,name:name});
  });
  if (result.length>YCV12.MAX_STOCKS_IN_MAIL) throw new Error('TWO_MAIL_V12_EXCESS_STOCK_COUNT');
  return {found:true,stocks:result}; // An empty block is valid: 0 stocks today.
}

function ycv12TodayMails_(){
  if(ycv12MailCache_!==null)return ycv12MailCache_;
  const today=todayKeyV8_(), found=new Map();
  // Search the broker source independently; general intelligence traffic can
  // push its mail outside the top-N Gmail threads on busy mornings.
  const queries=[
    'subject:"경제 Intelligence" newer_than:2d -in:trash -in:spam',
    'subject:"증권사 리포트 종목수집" newer_than:2d -in:trash -in:spam'
  ];
  queries.forEach(function(q){
    GmailApp.search(q,0,YCV12.MAX_THREADS).forEach(function(thread){
      thread.getMessages().forEach(function(msg){
      const id=String(msg.getId()||''),type=ycv12Type_(msg.getSubject());
      if(!id||!type||found.has(id))return;
      const date=msg.getDate();
      if(Utilities.formatDate(date,YCV12.TZ,'yyyy-MM-dd')!==today)return;
      const hour=Number(Utilities.formatDate(date,YCV12.TZ,'HH'));
      const min=Number(Utilities.formatDate(date,YCV12.TZ,'mm'));
      const minute=hour*60+min;
      if(type==='REGULAR' && (minute<YCV12.SCAN_START||minute>YCV12.SCAN_END))return;
      if(type==='BROKER' && (minute<YCV12.BROKER_START||minute>=YCV12.BROKER_END_EXCLUSIVE))return;
      // A stale subject-date must never be treated as today's recommendations.
      if(type==='BROKER'){
        const match=String(msg.getSubject()).match(/\]\s*(\d{4}-\d{2}-\d{2})(?:\s*[·|]|$)/);
        if(!match||match[1]!==today)return;
      }
      const parsed=ycv12ParseBlock_(msg.getPlainBody());
      if(!parsed.found) {
        Logger.log('TWO_MAIL_V12_SKIP_MISSING_BLOCK type='+type+' id='+id);
        return;
      }
      found.set(id,{id:id,type:type,date:today,time:minute,stocks:parsed.stocks});
      });
    });
  });
  ycv12MailCache_=Array.from(found.values()).sort(function(a,b){return a.time-b.time||a.id.localeCompare(b.id);});
  return ycv12MailCache_;
}

function collectIntelligenceCandidates_(){
  const today=todayKeyV8_(), pool={};
  // Keep already monitored candidates for their established one-month TTL.
  const ss=SpreadsheetApp.getActiveSpreadsheet(),sh=ss.getSheetByName(YC.CANDIDATE_SHEET);
  if(sh && sh.getLastRow()>=YC.DATA_START_ROW){
    sh.getRange(YC.DATA_START_ROW,1,sh.getLastRow()-YC.DATA_START_ROW+1,7).getDisplayValues().forEach(function(r){
      const name=String(r[1]||'').trim(),code=normalizeCandidateCode_(r[3]);
      const day=ymdFromAnyV8_(r[5]);
      if(String(r[0]||'').trim().toUpperCase()!=='Y'||!name||!code||!day||isExpiredMentionV8_(day,today))return;
      pool[code]={name:name,nameTrusted:true,dates:new Set([day]),lastSeen:day,carryover:true,sourceTag:'SHEET_TTL'};
    });
  }
  ycv12TodayMails_().forEach(function(mail){
    mail.stocks.forEach(function(stock){
      let item=pool[stock.code];
      if(!item){
        item={name:stock.name,nameTrusted:true,dates:new Set(),lastSeen:mail.date,sourceTag:mail.type};
        pool[stock.code]=item;
      }
      item.dates.add(mail.date);
      if(mail.date>=item.lastSeen){
        item.name=stock.name;item.lastSeen=mail.date;item.nameTrusted=true;
        item.sourceTag=mail.type;
      }
      item.mentionDays=item.dates.size;
    });
  });
  Object.keys(pool).forEach(function(code){
    if(isExpiredMentionV8_(pool[code].lastSeen,today))delete pool[code];
    else pool[code].mentionDays=pool[code].dates.size;
  });
  return pool;
}

// Mark a mail processed only after every listed ticker is present and active
// with today's mention date. A timestamp-only refresh is not sufficient.
function ycv12ConfirmFresh_(fresh,today){
  const required=new Set();
  fresh.forEach(function(mail){mail.stocks.forEach(function(stock){required.add(stock.code);});});
  if(!required.size)return;
  const ss=SpreadsheetApp.getActiveSpreadsheet(),sh=ss.getSheetByName(YC.CANDIDATE_SHEET);
  if(!sh)throw new Error('TWO_MAIL_V12_CANDIDATES_MISSING');
  SpreadsheetApp.flush();
  const present=new Set();
  const last=sh.getLastRow();
  if(last>=YC.DATA_START_ROW){
    sh.getRange(YC.DATA_START_ROW,1,last-YC.DATA_START_ROW+1,7).getDisplayValues().forEach(function(row){
      const code=normalizeCandidateCode_(row[3]);
      if(required.has(code)&&String(row[0]).trim().toUpperCase()==='Y'&&ymdFromAnyV8_(row[5])===today)
        present.add(code);
    });
  }
  const missing=Array.from(required).filter(function(code){return !present.has(code);});
  if(missing.length)throw new Error('TWO_MAIL_V12_UNCONFIRMED_'+missing.join(','));
}

function ycv12MaybeCollect_(){
  const parts=seoulNowPartsV5_(new Date());
  if(!isWeekdayV5_(parts))return 'SKIP_NON_WEEKDAY';
  if(!isWithinMinuteWindowV5_(parts,YCV12.SCAN_START,YCV12.SCAN_END))return 'SKIP_COLLECTION_WINDOW';
  const props=PropertiesService.getScriptProperties(),now=Date.now();
  const last=Number(props.getProperty(YCV12.POLL_KEY)||'0');
  if(last>0 && now-last<YCV12.POLL_INTERVAL_MS)return 'SKIP_POLL_THROTTLE';
  props.setProperty(YCV12.POLL_KEY,String(now));
  try{
    const mails=ycv12TodayMails_();
    const processed=props.getProperty(YCV12.PROCESSED_DAY_KEY)===parts.dateKey
      ?String(props.getProperty(YCV12.PROCESSED_IDS_KEY)||'').split(',').filter(Boolean):[];
    const prior=new Set(processed),fresh=mails.filter(function(m){return !prior.has(m.id);});
    if(!fresh.length){
      const status='NO_NEW_SOURCE_MAIL source_count='+mails.length;
      props.setProperty(YCV12.STATUS_KEY,status);
      return status;
    }
    const stockCount=fresh.reduce(function(n,m){return n+m.stocks.length;},0);
    if(stockCount>0){
      const before=String(props.getProperty('LAST_CANDIDATE_REFRESH_AT')||'');
      refreshYouTubeCandidatePoolV2();
      const after=String(props.getProperty('LAST_CANDIDATE_REFRESH_AT')||'');
      if(!after||after===before)throw new Error('TWO_MAIL_V12_REFRESH_NOT_CONFIRMED');
      ycv12ConfirmFresh_(fresh,parts.dateKey);
    } else {
      // Zero-stock mail: clean expired rows without costly re-fetch of every live ticker.
      const ss=SpreadsheetApp.getActiveSpreadsheet();
      const sheet=ss.getSheetByName(YC.CANDIDATE_SHEET);
      if(sheet)reclaimExpiredRowsV8_(sheet,parts.dateKey);
    }

    const ids=Array.from(new Set(processed.concat(fresh.map(function(m){return m.id;})))).slice(-40);
    props.setProperty(YCV12.PROCESSED_DAY_KEY,parts.dateKey);
    props.setProperty(YCV12.PROCESSED_IDS_KEY,ids.join(','));
    const regular=fresh.filter(function(m){return m.type==='REGULAR';});
    const broker=fresh.filter(function(m){return m.type==='BROKER';});
    const count=function(a){return a.reduce(function(n,m){return n+m.stocks.length;},0);};
    const status='COLLECTED REGULAR='+regular.length+' mails/'+count(regular)+' stocks'+
      ' BROKER='+broker.length+' mails/'+count(broker)+' stocks';
    props.setProperty(YCV12.STATUS_KEY,status);
    Logger.log('TWO_MAIL_V12 '+status);
    return status;
  }catch(e){
    props.deleteProperty(YCV12.POLL_KEY); // retry on next tick, do not mark processed.
    props.setProperty(YCV12.STATUS_KEY,'ERROR '+String(e&&e.message||e).slice(0,160));
    throw e;
  }
}

// Overwrite only the mail-refresh portion. V9 combined tick still executes
// its daily cleanup and the established market-guarded monitoring engine.
maybeScheduledCandidateRefreshV6=function(){return ycv12MaybeCollect_();};
