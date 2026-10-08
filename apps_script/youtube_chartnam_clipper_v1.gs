/*
 * Chartnam 08:50 KST community clipper, separate from the dawn Economic mail.
 *
 * Runs inside the existing 5-minute ycScheduledMonitor trigger; 08:50-09:59
 * is an idempotent retry window (the channel occasionally publishes at 09:46).
 * Only same-KST-day "오늘의 예상 대장테마" posts are eligible.
 * Exact name/code identity is checked with Naver before writing.
 *
 * CHARTNAM_CLIPS is an append-only source. The Gmail candidate pool is unioned
 * with this source before the EXISTING V8 refresh writes CANDIDATES. Never
 * independently delete or deactivate CANDIDATES rows.
 */
const YCC = Object.freeze({
  CHANNEL_ID: 'UClCqSWhwSCNd7jUUwg41pUQ',
  POST_URL: 'https://www.youtube.com/channel/UClCqSWhwSCNd7jUUwg41pUQ/posts',
  SHEET: 'CHARTNAM_CLIPS',
  TZ: 'Asia/Seoul',
  START_MINUTE: 8 * 60 + 50,
  END_MINUTE: 9 * 60 + 59,
  MAX_STOCKS: 50,
  MAX_LOOKBACK_DAYS: 32,
  DONE_PREFIX: 'CHARTNAM_CLIPPED_',
  COLS: ['Date','Code','Name','Theme','Reason','PublishedKST','PostId','Source','ClippedKST'],
});

function ycChartnamNow_() {
  const now = new Date();
  const dateKey = Utilities.formatDate(now, YCC.TZ, 'yyyy-MM-dd');
  const report = ycFetchChartnam_();
  const candidates = report.filter(function(post) {
    if (!/^오늘의\s*예상\s*대장테마/.test(post.text)) return false;
    if (post.publishedKey !== dateKey) return false;
    const header = String(post.text.split('\n')[0] || '');
    const md = header.match(/\((\d{1,2})\/(\d{1,2})\)/);
    if (!md) return false;
    const day = dateKey.split('-').map(Number);
    return Number(md[1]) === day[1] && Number(md[2]) === day[2];
  });
  if (!candidates.length) return {status:'NO_TODAY_POST', newRows:0, parsed:0};

  const clipRows = [];
  const seen = new Set();
  const unresolved = [];
  candidates.forEach(function(post) {
    ycParseChartnamText_(post.text).forEach(function(entry) {
      const resolved = ycResolveChartnamStock_(entry.name, entry.explicitCode);
      if (!resolved) {unresolved.push(entry.name); return;}
      if (seen.has(resolved.code)) return;
      seen.add(resolved.code);
      clipRows.push({
        date:dateKey, code:resolved.code, name:resolved.name,
        theme:entry.theme, reason:entry.reason, published:post.publishedKst,
        postId:post.id, source:'CHARTNAM_0850',
        clipped:Utilities.formatDate(now,YCC.TZ,'yyyy-MM-dd HH:mm:ss'),
      });
    });
  });
  if (!clipRows.length) {
    throw new Error('CHARTNAM_NO_VALID_TICKER; UNRESOLVED=' + unresolved.slice(0,5).join(','));
  }
  if (clipRows.length > YCC.MAX_STOCKS) throw new Error('CHARTNAM_TOO_MANY_STOCKS');
  const sheet = ycGetChartnamSheet_();
  const existing = new Set();
  if (sheet.getLastRow() >= 2) {
    sheet.getRange(2,1,sheet.getLastRow()-1,2).getDisplayValues().forEach(function(row) {
      existing.add(String(row[0]).trim()+'|'+normalizeCandidateCode_(row[1]));
    });
  }
  const incoming = clipRows.filter(function(r) {return !existing.has(r.date+'|'+r.code);});
  if (incoming.length) {
    const first = sheet.getLastRow()+1;
    sheet.getRange(first,1,incoming.length,9).setValues(incoming.map(function(r) {
      return [r.date,r.code,r.name,r.theme,r.reason,r.published,r.postId,r.source,r.clipped];
    }));
    sheet.getRange(first,2,incoming.length,1).setNumberFormat('@');
    SpreadsheetApp.flush();
  }
  return {status:'FOUND',newRows:incoming.length,parsed:clipRows.length,unresolved:unresolved.length};
}

function ycGetChartnamSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(YCC.SHEET);
  if (!sheet) sheet = ss.insertSheet(YCC.SHEET);
  if (sheet.getLastRow() < 1) {
    sheet.getRange(1,1,1,YCC.COLS.length).setValues([YCC.COLS]);
    sheet.setFrozenRows(1);
    sheet.getRange(1,1,sheet.getMaxRows(),2).setNumberFormat('@');
  }
  return sheet;
}

/*
 * YouTube can send ytInitialData as an object literal or a JavaScript-escaped
 * quoted JSON string (e.g. '\x7b\x22responseContext\x22...') to UrlFetchApp.
 * Decode only known string escapes; do NOT eval untrusted page JavaScript.
 */
function ycParseChartnamInitialData_(page) {
  const m=/(?:var\s+)?ytInitialData\s*=\s*/.exec(String(page||''));
  if (!m) throw new Error('CHARTNAM_INITIAL_DATA_MISSING');
  let i=m.index+m[0].length;
  while (/\s/.test(page.charAt(i))) i++;
  if (page.slice(i,i+11)==='JSON.parse(') {
    i+=11;
    while (/\s/.test(page.charAt(i))) i++;
  }
  const first=page.charAt(i);
  if (first==='{' || first==='[') {
    let depth=0,quoted=false,escaped=false,end=-1;
    for (let j=i;j<page.length;j++) {
      const c=page.charAt(j);
      if (quoted) {
        if (escaped) escaped=false;
        else if (c==='\\') escaped=true;
        else if (c==='"') quoted=false;
      } else if (c==='"') quoted=true;
      else if (c==='{' || c==='[') depth++;
      else if (c==='}' || c===']') {
        if (--depth===0) {end=j+1;break;}
      }
    }
    if (end<0) throw new Error('CHARTNAM_BAD_JSON_BOUNDARY');
    return JSON.parse(page.slice(i,end));
  }
  if (first!=='"' && first!=="'") {
    throw new Error('CHARTNAM_UNSUPPORTED_INITIAL_DATA:'+String(first).slice(0,6));
  }
  const quote=first;
  let output='',closed=false;
  for (i++;i<page.length;i++) {
    const c=page.charAt(i);
    if (c===quote) {closed=true;break;}
    if (c!=='\\') {output+=c;continue;}
    if (++i>=page.length) break;
    const e=page.charAt(i);
    if (e==='x' || e==='u') {
      const n=e==='x'?2:4;
      const hex=page.slice(i+1,i+1+n);
      if (!new RegExp('^[0-9a-fA-F]{'+n+'}$').test(hex)) {
        throw new Error('CHARTNAM_BAD_HEX_ESCAPE');
      }
      output+=String.fromCharCode(parseInt(hex,16));
      i+=n;
    } else {
      const escapes={'n':'\n','r':'\r','t':'\t','b':'\b','f':'\f','v':'\v','0':'\0'};
      output+=Object.prototype.hasOwnProperty.call(escapes,e)?escapes[e]:e;
    }
  }
  if (!closed) throw new Error('CHARTNAM_UNTERMINATED_JS_STRING');
  const parsed=JSON.parse(output);
  if (!parsed || typeof parsed!=='object') throw new Error('CHARTNAM_INITIAL_DATA_NOT_OBJECT');
  return parsed;
}

function ycFetchChartnam_() {
  const opts = {
    method:'get',muteHttpExceptions:true,followRedirects:true,
    headers:{'User-Agent':'Mozilla/5.0','Accept-Language':'ko-KR,ko;q=0.9'},
  };
  const res = UrlFetchApp.fetch(YCC.POST_URL,opts);
  if (res.getResponseCode() !== 200) throw new Error('CHARTNAM_HTTP_'+res.getResponseCode());
  const html = res.getContentText('UTF-8');
  const initial=ycParseChartnamInitialData_(html);
  const posts=new Map();
  function text(value) {
    if (!value || typeof value!=='object') return '';
    return value.simpleText||((value.runs||[]).map(function(v) {return v.text||'';}).join(''));
  }
  function walk(value) {
    if (Array.isArray(value)) {value.forEach(walk);return;}
    if (!value || typeof value!=='object') return;
    const p=value.backstagePostRenderer;
    if (p && p.postId) posts.set(String(p.postId),{
      id:String(p.postId),text:text(p.contentText),relative:text(p.publishedTimeText),
    });
    Object.keys(value).forEach(function(k) {walk(value[k]);});
  }
  walk(initial);
  if (!posts.size) throw new Error('CHARTNAM_POSTS_EMPTY');
  const out=[];
  Array.from(posts.values()).filter(function(p) {
    return /^오늘의\s*예상\s*대장테마/.test(p.text);
  }).slice(0,6).forEach(function(p) {
    const response=UrlFetchApp.fetch('https://www.youtube.com/post/'+encodeURIComponent(p.id),opts);
    if (response.getResponseCode()!==200) return;
    const m=/"datePublished":"([^"]+)"/.exec(response.getContentText('UTF-8'));
    if (!m) return;
    const d=new Date(m[1]);
    if (isNaN(d.getTime())) return;
    p.publishedKey=Utilities.formatDate(d,YCC.TZ,'yyyy-MM-dd');
    p.publishedKst=Utilities.formatDate(d,YCC.TZ,'yyyy-MM-dd HH:mm:ss');
    out.push(p);
  });
  return out;
}

function ycParseChartnamText_(body) {
  const result=[],lines=String(body||'').split(/\r?\n/);
  let theme='',reason='';
  lines.forEach(function(raw) {
    const line=raw.trim();
    const group=line.match(/^\[(?:\d+위)\]\s*(.+)$/);
    if (group) {
      theme=group[1].replace(/\s*\((?:美|국내|글로벌)[^)]*\)\s*$/,'').trim();
      reason='';
      return;
    }
    if (/^\[주목\s*개별주\]/.test(line)) {theme='주목 개별주';reason='';return;}
    if (/^\[신규상장/.test(line)) {theme='';reason='';return;}
    if (/^→/.test(line) && theme) {
      line.slice(1).split(/\s*·\s*/).forEach(function(part) {
        const name=part.trim();
        if (name) result.push({name:name,theme:theme,reason:reason.slice(0,160)});
      });
      return;
    }
    if (/^·/.test(line) && theme==='주목 개별주') {
      const single=/^·\s*(.+?)\((\d{6})\)\s*(.*)$/.exec(line);
      if (single) result.push({
        name:single[1].trim(),explicitCode:single[2],
        theme:theme,reason:single[3].slice(0,160),
      });
      return;
    }
    if (theme && line && !/^\*|^①|^\[/.test(line)) reason=line;
  });
  return result;
}

function ycResolveChartnamStock_(sourceName, explicitCode) {
  const raw=String(sourceName||'').trim();
  if (!raw || raw.length>60) return null;
  let matches=[];
  if (explicitCode) {
    const code=normalizeCandidateCode_(explicitCode);
    if (!code) return null;
    const identity=resolveNaverIdentity_(code,raw);
    if (!identity.verified || normalizeName_(raw)!==normalizeName_(identity.name)) return null;
    return {code:code,name:identity.name};
  }
  const url='https://m.stock.naver.com/front-api/search/autoComplete?query='+
    encodeURIComponent(raw)+'&target=stock';
  try {
    const r=UrlFetchApp.fetch(url,{
      muteHttpExceptions:true,
      headers:{'User-Agent':'Mozilla/5.0'},
    });
    if (r.getResponseCode()!==200) return null;
    const parsed=JSON.parse(r.getContentText()||'{}');
    matches=((parsed.result||{}).items||[]).filter(function(item) {
      return /^(KOSPI|KOSDAQ)$/.test(item.typeCode||'') &&
        /^\d{6}$/.test(String(item.code||'')) &&
        normalizeName_(item.name)===normalizeName_(raw);
    });
  } catch(e) {return null;}
  if (matches.length!==1) return null;
  return {code:String(matches[0].code),name:String(matches[0].name)};
}

function collectIntelligenceCandidates_() {
  // Preserve the exact canonical Gmail parser as-is, adding only chartnam rows.
  const pool=collectIntelligenceCandidatesFromMailV10_();
  const ss=SpreadsheetApp.getActiveSpreadsheet();
  const sheet=ss.getSheetByName(YCC.SHEET);
  if (!sheet || sheet.getLastRow()<2) return pool;
  const today=Utilities.formatDate(new Date(),YCC.TZ,'yyyy-MM-dd');
  const rows=sheet.getRange(2,1,sheet.getLastRow()-1,3).getDisplayValues();
  rows.forEach(function(r) {
    const date=String(r[0]||'').trim();
    const code=normalizeCandidateCode_(r[1]);
    const name=String(r[2]||'').trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date) || !code || !name ||
        date>today || isExpiredMentionV8_(date,today)) return;
    if (!pool[code]) pool[code]={
      name:name,nameTrusted:true,dates:new Set(),lastSeen:date,
      sourceTag:'CHARTNAM_0850',
    };
    pool[code].dates.add(date);
    if (date>pool[code].lastSeen) {
      pool[code].lastSeen=date;pool[code].name=name;
      pool[code].sourceTag='CHARTNAM_0850';
    }
    pool[code].mentionDays=pool[code].dates.size;
  });
  return pool;
}

function ycMaybeChartnamClipAndSync_() {
  const now=new Date();
  const time=Utilities.formatDate(now,YCC.TZ,'HH:mm').split(':').map(Number);
  const minute=time[0]*60+time[1];
  if (minute<YCC.START_MINUTE || minute>YCC.END_MINUTE) return 'SKIP_TIME';
  const day=Utilities.formatDate(now,YCC.TZ,'yyyy-MM-dd');
  const weekday=Number(Utilities.formatDate(now,YCC.TZ,'u'));
  if (weekday===6 || weekday===7) return 'SKIP_WEEKEND';
  const props=PropertiesService.getScriptProperties();
  const key=YCC.DONE_PREFIX+day;
  if (props.getProperty(key)==='Y') return 'SKIP_DONE';
  const lock=LockService.getScriptLock();
  if (!lock.tryLock(1000)) return 'SKIP_LOCKED';
  let clip;
  try {
    if (props.getProperty(key)==='Y') return 'SKIP_DONE';
    clip=ycChartnamNow_();
    if (clip.status==='FOUND') props.setProperty(key,'Y');
  } catch(e) {
    props.setProperty('CHARTNAM_CLIP_LAST_ERROR',String(e&&e.message||e).slice(0,350));
    return 'ERROR_'+String(e&&e.message||e).slice(0,120);
  } finally {lock.releaseLock();}
  if (clip.status!=='FOUND') return clip.status;
  // No CANDIDATES updates occur before CHARTNAM_CLIPS is durable.
  // Later routine Gmail refresh always unions the clip source, so lock contention
  // or API errors cannot permanently erase these mentions.
  try {refreshYouTubeCandidatePoolV2();}
  catch(e) {return 'CLIPPED_REFRESH_PENDING_'+String(e&&e.message||e).slice(0,80);}
  return 'CLIPPED_'+clip.newRows+'_NEW_'+clip.parsed+'_TOTAL';
}

/* Override the V9 tick, retaining its log-clean / mail-refresh / monitoring. */
function scheduledCombinedTickV6() {
  const clip=ycMaybeChartnamClipAndSync_();
  const logStatus=cleanupDispatchLogOncePerDayV9_();
  const refreshStatus=maybeScheduledCandidateRefreshV6();
  const monitorStatus=scheduledCandidateMonitorV5();
  return 'CHARTNAM='+clip+'; LOG='+logStatus+'; REFRESH='+refreshStatus+
    '; MONITOR='+monitorStatus;
}

function ycChartnamManualTest() {
  return ycMaybeChartnamClipAndSync_();
}
