/*
 * YouTube candidate lifecycle V11
 *
 * Policy:
 * - Active signal watch: first 10 completed KRX trading sessions from latest mention.
 * - Observation only: trading sessions 11-20.
 * - After session 20: clear the CANDIDATES row (preserve H and P:T formulas).
 * - One MENTION_HISTORY row per ticker + mention date; re-mentions create new events.
 * - D+10 / D+20 close and MA20 snapshots are captured from Naver daily bars.
 * - One market-session calendar request per Seoul day; evaluation quotes are
 *   fetched only for due events, in bounded batches. Failures never block ingest.
 */

const YCV11 = Object.freeze({
  TZ: 'Asia/Seoul',
  CALENDAR_CODE: '069500',
  CALENDAR_CACHE_KEY: 'YCV11_KRX_SESSIONS_JSON',
  CALENDAR_FETCH_DATE_KEY: 'YCV11_KRX_SESSIONS_FETCH_DATE',
  CALENDAR_AFTER_CLOSE_KEY: 'YCV11_KRX_AFTER_CLOSE_FETCH_DATE',
  MIGRATION_KEY: 'YCV11_LIFECYCLE_MIGRATION_DONE',
  LAST_RUN_DATE_KEY: 'YCV11_LIFECYCLE_LAST_RUN_DATE',
  HISTORY_SHEET: 'MENTION_HISTORY',
  ACTIVE_DAYS: 10,
  EVALUATION_DAYS: 20,
  MAX_CODES_PER_TICK: 8,
  MAX_RETRIES: 3,
  RETRY_COOLDOWN_MS: 30 * 60 * 1000,
  MAX_HISTORY_ROWS: 2000,
  HEADERS: [
    'EventKey','종목코드','종목명','언급일','수집원',
    '언급기준가','기준가 기준일','기준가 수집시각',
    'D+10 평가일','D+10 종가','D+10 수익률(%)','D+10 MA20','D+10 종가/MA20',
    'D+20 평가일','D+20 종가','D+20 수익률(%)','D+20 MA20','D+20 종가/MA20',
    '상태','D+10 마지막 시도','D+10 시도 횟수','D+20 마지막 시도','D+20 시도 횟수','비고'
  ]
});

let ycv11SessionCache_ = null;

function ycv11NowParts_() {
  const now = new Date();
  const date = Utilities.formatDate(now, YCV11.TZ, 'yyyy-MM-dd');
  const hour = Number(Utilities.formatDate(now, YCV11.TZ, 'HH'));
  const minute = Number(Utilities.formatDate(now, YCV11.TZ, 'mm'));
  return {now: now, date: date, minute: hour * 60 + minute};
}

function ycv11ParseFchart_(code, count) {
  const url = YCI.NAVER_FCHART + '?symbol=' + encodeURIComponent(code) +
    '&timeframe=day&count=' + String(count || 100) + '&requestType=0';
  const res = UrlFetchApp.fetch(url, {
    method: 'get',
    muteHttpExceptions: true,
    headers: {'User-Agent': 'Mozilla/5.0'}
  });
  if (res.getResponseCode() !== 200) throw new Error('YCV11_FCHART_HTTP_' + res.getResponseCode());
  const xml = XmlService.parse(res.getContentText());
  const items = xml.getRootElement().getDescendants()
    .filter(function(x) { return x.getType && x.getType() === XmlService.ContentTypes.ELEMENT; })
    .map(function(x) { return x.asElement(); })
    .filter(function(el) { return el.getName() === 'item'; });
  const bars = [];
  items.forEach(function(el) {
    const attr = el.getAttribute('data');
    if (!attr) return;
    const p = String(attr.getValue() || '').split('|');
    if (p.length < 6 || !/^\d{8}$/.test(p[0])) return;
    const bar = {
      date: p[0].substring(0,4) + '-' + p[0].substring(4,6) + '-' + p[0].substring(6,8),
      close: Number(p[4])
    };
    if (isFinite(bar.close) && bar.close > 0) bars.push(bar);
  });
  bars.sort(function(a,b) { return a.date.localeCompare(b.date); });
  return bars;
}

function ycv11GetSessions_() {
  if (ycv11SessionCache_) return ycv11SessionCache_;
  const parts = ycv11NowParts_();
  const props = PropertiesService.getScriptProperties();
  let cached = null;
  try {
    cached = JSON.parse(props.getProperty(YCV11.CALENDAR_CACHE_KEY) || 'null');
  } catch (e) {
    cached = null;
  }
  const cachedDate = String(props.getProperty(YCV11.CALENDAR_FETCH_DATE_KEY) || '');
  const afterClose = parts.minute >= (15 * 60 + 35);
  const afterCloseFetched = String(props.getProperty(YCV11.CALENDAR_AFTER_CLOSE_KEY) || '') === parts.date;
  const latest = cached && cached.sessions && cached.sessions.length
    ? String(cached.sessions[cached.sessions.length - 1]) : '';
  const refresh = !cached || !Array.isArray(cached.sessions) || cached.sessions.length < 30 ||
    cachedDate !== parts.date || (afterClose && latest < parts.date && !afterCloseFetched);

  if (refresh) {
    const bars = ycv11ParseFchart_(YCV11.CALENDAR_CODE, 100);
    const sessions = bars.map(function(b) { return b.date; }).filter(function(d,i,a) {
      return a.indexOf(d) === i;
    });
    if (sessions.length < 30) throw new Error('YCV11_KRX_CALENDAR_INSUFFICIENT_' + sessions.length);
    cached = {sessions: sessions.slice(-80), latest: sessions[sessions.length - 1]};
    props.setProperty(YCV11.CALENDAR_CACHE_KEY, JSON.stringify(cached));
    props.setProperty(YCV11.CALENDAR_FETCH_DATE_KEY, parts.date);
    if (afterClose) props.setProperty(YCV11.CALENDAR_AFTER_CLOSE_KEY, parts.date);
  }
  ycv11SessionCache_ = cached.sessions.slice().sort();
  return ycv11SessionCache_;
}

function ycv11CompletedSessions_(sessions) {
  const parts = ycv11NowParts_();
  return (sessions || []).filter(function(d) {
    return d < parts.date || (d === parts.date && parts.minute >= (15 * 60 + 35));
  });
}

function ycv11AgeFromSessions_(mentionDate, sessions) {
  const date = String(mentionDate || '').slice(0,10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;
  return (sessions || []).filter(function(d) { return d >= date; }).length;
}

function ycv11LifecycleStateFromAge_(age) {
  if (age == null || age < 0) return 'UNKNOWN';
  if (age >= YCV11.EVALUATION_DAYS) return 'EXPIRED';
  if (age >= YCV11.ACTIVE_DAYS) return 'OBSERVATION';
  return 'ACTIVE';
}

function ycv11EventKey_(code, date) {
  const c = normalizeCandidateCode_(code);
  const d = String(date || '').slice(0,10);
  return c && /^\d{4}-\d{2}-\d{2}$/.test(d) ? c + '|' + d : '';
}

function ycv11EnsureHistorySheet_(ss) {
  let sh = ss.getSheetByName(YCV11.HISTORY_SHEET);
  if (!sh) sh = ss.insertSheet(YCV11.HISTORY_SHEET);
  if (sh.getMaxColumns() < YCV11.HEADERS.length) {
    sh.insertColumnsAfter(sh.getMaxColumns(), YCV11.HEADERS.length - sh.getMaxColumns());
  }
  const current = sh.getRange(1,1,1,YCV11.HEADERS.length).getDisplayValues()[0];
  let mismatch = false;
  for (let i=0;i<YCV11.HEADERS.length;i++) {
    if (String(current[i] || '') !== YCV11.HEADERS[i]) { mismatch = true; break; }
  }
  if (mismatch) sh.getRange(1,1,1,YCV11.HEADERS.length).setValues([YCV11.HEADERS]);
  sh.setFrozenRows(1);
  sh.getRange(1,1,1,YCV11.HEADERS.length)
    .setBackground('#1e3a72').setFontColor('#ffffff').setFontWeight('bold')
    .setHorizontalAlignment('center');
  return sh;
}

function ycv11TargetSession_(mentionDate, ordinal, sessions) {
  const date = String(mentionDate || '').slice(0,10);
  const start = (sessions || []).findIndex(function(d) { return d >= date; });
  if (start < 0) return '';
  return sessions[start + ordinal - 1] || '';
}

function ycv11BaselineSession_(mentionDate, sessions) {
  const date = String(mentionDate || '').slice(0,10);
  const prior = (sessions || []).filter(function(d) { return d < date; });
  return prior.length ? prior[prior.length - 1] : '';
}

function ycv11AppendRows_(sheet, rows) {
  if (!rows || !rows.length) return;
  const start = Math.max(2, sheet.getLastRow() + 1);
  sheet.getRange(start,1,rows.length,YCV11.HEADERS.length).setValues(rows);
  sheet.getRange(start,6,rows.length,1).setNumberFormat('#,##0.00');
  sheet.getRange(start,10,rows.length,1).setNumberFormat('#,##0.00');
  sheet.getRange(start,11,rows.length,1).setNumberFormat('0.00');
  sheet.getRange(start,12,rows.length,1).setNumberFormat('#,##0.00');
  sheet.getRange(start,15,rows.length,1).setNumberFormat('#,##0.00');
  sheet.getRange(start,16,rows.length,1).setNumberFormat('0.00');
  sheet.getRange(start,17,rows.length,1).setNumberFormat('#,##0.00');
}

function ycv11MigrateLegacyHistoryV11_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
  if (!sheet) throw new Error('YCV11_CANDIDATES_SHEET_MISSING');
  const history = ycv11EnsureHistorySheet_(ss);
  const sessions = ycv11CompletedSessions_(ycv11GetSessions_());
  const last = sheet.getLastRow();
  if (last < YC.DATA_START_ROW) {
    PropertiesService.getScriptProperties().setProperty(YCV11.MIGRATION_KEY,'Y');
    return 'MIGRATED_EMPTY';
  }
  const rows = sheet.getRange(YC.DATA_START_ROW,1,last-YC.DATA_START_ROW+1,8).getValues();
  const existingKeys = new Set();
  if (history.getLastRow() >= 2) {
    history.getRange(2,1,history.getLastRow()-1,1).getDisplayValues().forEach(function(r) {
      if (r[0]) existingKeys.add(String(r[0]));
    });
  }
  const append = [];
  rows.forEach(function(r) {
    const state = String(r[0] || '').trim().toUpperCase();
    const code = normalizeCandidateCode_(r[3]);
    const name = String(r[1] || '').trim();
    const mentionDate = ymdFromAnyV8_(r[5]);
    if (!code || !name || !mentionDate || (state !== 'Y' && state !== 'E')) return;
    const key = ycv11EventKey_(code,mentionDate);
    if (!key || existingKeys.has(key)) return;
    const age = ycv11AgeFromSessions_(mentionDate,sessions);
    const lifecycle = ycv11LifecycleStateFromAge_(age);
    const status = lifecycle === 'EXPIRED' ? 'COMPLETED_LEGACY' :
      lifecycle === 'OBSERVATION' ? 'OBSERVATION_LEGACY' : 'ACTIVE_LEGACY';
    const note = '기존 종목 이관: 과거 언급 시점 기준가 미보존. 과거 D+10 결과는 복원하지 않으며 가능한 D+20부터 관찰.';
    append.push([
      key,code,name,mentionDate,'LEGACY_EXISTING_POOL',
      '',ycv11BaselineSession_(mentionDate,sessions),'',
      lifecycle !== 'ACTIVE' ? ycv11TargetSession_(mentionDate,10,sessions) : '',
      '', '', '', lifecycle !== 'ACTIVE' ? 'LEGACY_NOT_CAPTURED' : '',
      lifecycle === 'EXPIRED' ? ycv11TargetSession_(mentionDate,20,sessions) : '',
      '', '', '', lifecycle === 'EXPIRED' ? 'LEGACY_NOT_CAPTURED' : '',
      status,'',0,'',0,note
    ]);
    existingKeys.add(key);
  });
  ycv11AppendRows_(history,append);
  const status = reclaimExpiredRowsV8_(sheet,todayKeyV8_());
  SpreadsheetApp.flush();
  PropertiesService.getScriptProperties().setProperty(YCV11.MIGRATION_KEY,'Y');
  return 'MIGRATED_LEGACY=' + append.length + '; ' + status;
}

function migrateCandidateLifecycleV11() {
  const props = PropertiesService.getScriptProperties();
  if (String(props.getProperty(YCV11.MIGRATION_KEY) || '') === 'Y') return 'MIGRATION_ALREADY_DONE';
  try {
    return ycv11MigrateLegacyHistoryV11_();
  } catch (e) {
    const msg = String(e && e.message ? e.message : e);
    props.setProperty('YCV11_LAST_ERROR',msg.slice(0,180));
    return 'MIGRATION_PENDING: ' + msg.slice(0,140);
  }
}

function reclaimExpiredRowsV8_(sheet, todayKey) {
  let sessions;
  try {
    sessions = ycv11CompletedSessions_(ycv11GetSessions_());
  } catch (e) {
    Logger.log('YCV11_LIFECYCLE_CALENDAR_HOLD ' + String(e && e.message || e));
    return 'LIFECYCLE_HOLD_CALENDAR_UNAVAILABLE';
  }
  const last = sheet.getLastRow();
  if (last < YC.DATA_START_ROW) return 'LIFECYCLE_EMPTY';
  const rows = sheet.getRange(YC.DATA_START_ROW,1,last-YC.DATA_START_ROW+1,7).getValues();
  let observed = 0, cleared = 0;
  rows.forEach(function(r,i) {
    const code = normalizeCandidateCode_(r[3]);
    if (!code) return;
    const mentionDate = ymdFromAnyV8_(r[5]);
    const rowNumber = i + YC.DATA_START_ROW;
    if (!mentionDate) {
      clearCandidatePayloadV8_(sheet,rowNumber);
      cleared++;
      return;
    }
    const age = ycv11AgeFromSessions_(mentionDate,sessions);
    const state = String(r[0] || '').trim().toUpperCase();
    const lifecycle = ycv11LifecycleStateFromAge_(age);
    if (lifecycle === 'EXPIRED') {
      clearCandidatePayloadV8_(sheet,rowNumber);
      cleared++;
    } else if (lifecycle === 'OBSERVATION' && state === 'Y') {
      sheet.getRange(rowNumber,1).setValue('E');
      observed++;
    }
  });
  SpreadsheetApp.flush();
  return 'ACTIVE=' + Math.max(0,rows.filter(function(r) {
    const age = ycv11AgeFromSessions_(ymdFromAnyV8_(r[5]),sessions);
    return String(r[0] || '').toUpperCase() === 'Y' && age != null && age < 10;
  }).length) + '; OBSERVED=' + observed + '; CLEARED=' + cleared;
}

function isExpiredMentionV8_(lastSeen, todayKey) {
  try {
    return ycv11AgeFromSessions_(lastSeen,ycv11CompletedSessions_(ycv11GetSessions_())) >= YCV11.EVALUATION_DAYS;
  } catch (e) {
    // Calendar uncertainty must never prematurely discard a valid candidate.
    return false;
  }
}

const ycv11BaseRefreshV8_ = refreshYouTubeCandidatePoolV2;
refreshYouTubeCandidatePoolV2 = function() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
  const preserved = {};
  if (sheet && sheet.getLastRow() >= YC.DATA_START_ROW) {
    sheet.getRange(YC.DATA_START_ROW,1,sheet.getLastRow()-YC.DATA_START_ROW+1,6).getValues().forEach(function(r,i) {
      if (String(r[0] || '').trim().toUpperCase() === 'E') {
        const code = normalizeCandidateCode_(r[3]);
        if (code) preserved[code] = {row:i+YC.DATA_START_ROW,mention:ymdFromAnyV8_(r[5])};
      }
    });
  }
  let result;
  try {
    result = ycv11BaseRefreshV8_();
  } finally {
    if (sheet && Object.keys(preserved).length) {
      const last = sheet.getLastRow();
      if (last >= YC.DATA_START_ROW) {
        const rows = sheet.getRange(YC.DATA_START_ROW,1,last-YC.DATA_START_ROW+1,6).getValues();
        rows.forEach(function(r,i) {
          const code = normalizeCandidateCode_(r[3]);
          const old = code ? preserved[code] : null;
          if (!old || String(r[0] || '').trim().toUpperCase() === 'Y') return;
          if (ymdFromAnyV8_(r[5]) === old.mention) sheet.getRange(i+YC.DATA_START_ROW,1).setValue('E');
        });
      }
      SpreadsheetApp.flush();
    }
  }
  return result;
};

function ycv11RegisterFreshMentionsV11_(fresh) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const candidates = ss.getSheetByName(YC.CANDIDATE_SHEET);
  if (!candidates) throw new Error('YCV11_CANDIDATES_SHEET_MISSING');
  const history = ycv11EnsureHistorySheet_(ss);
  SpreadsheetApp.flush();
  const candidateMap = {};
  const lastCandidate = candidates.getLastRow();
  if (lastCandidate >= YC.DATA_START_ROW) {
    candidates.getRange(YC.DATA_START_ROW,1,lastCandidate-YC.DATA_START_ROW+1,8).getValues().forEach(function(r) {
      const code = normalizeCandidateCode_(r[3]);
      if (!code) return;
      candidateMap[code] = {
        active:String(r[0] || '').trim().toUpperCase(),
        name:String(r[1] || '').trim(),
        mention:ymdFromAnyV8_(r[5]),
        price:Number(r[7])
      };
    });
  }
  const historyMap = {};
  if (history.getLastRow() >= 2) {
    history.getRange(2,1,history.getLastRow()-1,1).getDisplayValues().forEach(function(r,i) {
      if (r[0]) historyMap[String(r[0])] = i + 2;
    });
  }
  let sessions = [];
  try { sessions = ycv11GetSessions_(); } catch (e) {}
  const append = [], sourceUpdates = {};
  const nowText = Utilities.formatDate(new Date(),YCV11.TZ,'yyyy-MM-dd HH:mm:ss');
  (fresh || []).forEach(function(mail) {
    (mail.stocks || []).forEach(function(stock) {
      const code = normalizeCandidateCode_(stock.code);
      const item = code ? candidateMap[code] : null;
      if (!item || item.active !== 'Y' || item.mention !== mail.date) return;
      const key = ycv11EventKey_(code,mail.date);
      if (!key) return;
      const source = String(mail.type || 'REGULAR');
      if (historyMap[key] === -1) {
        const idx = append.findIndex(function(row) { return row[0] === key; });
        if (idx >= 0) append[idx][4] = Array.from(new Set(String(append[idx][4] || '').split('+').concat([source]).filter(Boolean))).join('+');
        return;
      }
      if (historyMap[key]) {
        const old = String(history.getRange(historyMap[key],5).getDisplayValue() || '');
        const merged = Array.from(new Set(old.split('+').concat([source]).filter(Boolean))).join('+');
        sourceUpdates[historyMap[key]] = merged;
        return;
      }
      const price = isFinite(item.price) && item.price > 0 ? item.price : '';
      const baseDate = ycv11BaselineSession_(mail.date,sessions);
      const note = price !== '' ?
        '기준가: 언급 시점 GOOGLEFINANCE 현재가(정규 수집은 장전 기준 직전 종가).' :
        '기준가 미확보: D+10/D+20 종가·MA20은 기록하되 수익률은 계산하지 않음.';
      append.push([
        key,code,item.name,mail.date,source,price,baseDate,price !== '' ? nowText : '',
        '', '', '', '', '', '', '', '', '', '',
        price !== '' ? 'ACTIVE' : 'ACTIVE_BASELINE_MISSING','',0,'',0,note
      ]);
      historyMap[key] = -1;
    });
  });
  Object.keys(sourceUpdates).forEach(function(row) {
    history.getRange(Number(row),5).setValue(sourceUpdates[row]);
  });
  ycv11AppendRows_(history,append);
  if (append.length) Logger.log('YCV11_MENTION_EVENTS_ADDED ' + append.length);
  return 'MENTION_EVENTS_ADDED=' + append.length;
}

function ycv11ProcessDueEvaluationsV11_(history, sessions) {
  const last = history.getLastRow();
  if (last < 2) return 'NO_HISTORY';
  const rows = history.getRange(2,1,last-1,YCV11.HEADERS.length).getValues();
  const dueByCode = {};
  rows.forEach(function(r,i) {
    const status = String(r[18] || '');
    if (/^COMPLETED/.test(status)) return;
    const code = normalizeCandidateCode_(r[1]);
    const mention = String(r[3] || '').slice(0,10);
    if (!code || !mention) return;
    const age = ycv11AgeFromSessions_(mention,sessions);
    if (age == null) return;
    const d10 = ycv11TargetSession_(mention,10,sessions);
    const d20 = ycv11TargetSession_(mention,20,sessions);
    const legacyD10 = String(r[12] || '') === 'LEGACY_NOT_CAPTURED';
    const legacyD20 = String(r[17] || '') === 'LEGACY_NOT_CAPTURED';
    const missingD10 = String(r[12] || '') === 'PRICE_MISSING';
    const missingD20 = String(r[17] || '') === 'PRICE_MISSING';
    const need10 = age >= 10 && !r[9] && !legacyD10 && !missingD10;
    const need20 = age >= 20 && !r[14] && !legacyD20 && !missingD20;
    if (!need10 && !need20) {
      if (age >= 20) {
        r[18] = String(r[5] || '') === '' ? 'COMPLETED_BASELINE_MISSING' : 'COMPLETED';
        history.getRange(i+2,1,1,YCV11.HEADERS.length).setValues([r]);
      } else if (age >= 10 && !/^LEGACY/.test(status)) {
        r[18] = 'OBSERVATION';
        history.getRange(i+2,1,1,YCV11.HEADERS.length).setValues([r]);
      }
      return;
    }
    const lastAttempt10 = r[19] instanceof Date ? r[19].getTime() : Date.parse(String(r[19] || ''));
    const lastAttempt20 = r[21] instanceof Date ? r[21].getTime() : Date.parse(String(r[21] || ''));
    const recent10 = isFinite(lastAttempt10) && Date.now()-lastAttempt10 < YCV11.RETRY_COOLDOWN_MS;
    const recent20 = isFinite(lastAttempt20) && Date.now()-lastAttempt20 < YCV11.RETRY_COOLDOWN_MS;
    const run10 = need10 && !recent10;
    const run20 = need20 && !recent20;
    if (!run10 && !run20) return;
    if (!dueByCode[code]) dueByCode[code] = [];
    dueByCode[code].push({index:i,rowNumber:i+2,row:r,age:age,d10:d10,d20:d20,need10:run10,need20:run20});
  });
  const codes = Object.keys(dueByCode).sort(function(a,b) {
    const da = Math.min.apply(null,dueByCode[a].map(function(x){return x.age;}));
    const db = Math.min.apply(null,dueByCode[b].map(function(x){return x.age;}));
    return db-da;
  }).slice(0,YCV11.MAX_CODES_PER_TICK);
  if (!codes.length) return 'NO_DUE_EVALUATIONS';
  let completed = 0, failed = 0;
  codes.forEach(function(code) {
    const events = dueByCode[code];
    let bars = null, error = '';
    try { bars = ycv11ParseFchart_(code,100); }
    catch (e) { error = String(e && e.message || e).slice(0,120); }
    events.forEach(function(ev) {
      const r = ev.row;
      let changed = false;
      if (!bars) {
        if (ev.need10) {
          r[19] = new Date();
          r[20] = Number(r[20] || 0) + 1;
          if (Number(r[20]) >= YCV11.MAX_RETRIES && !r[9]) r[12] = 'PRICE_MISSING';
        }
        if (ev.need20) {
          r[21] = new Date();
          r[22] = Number(r[22] || 0) + 1;
          if (Number(r[22]) >= YCV11.MAX_RETRIES && !r[14]) r[17] = 'PRICE_MISSING';
        }
        r[23] = error;
        changed = true;
        if (ev.age >= 20 && (r[14] || r[17] === 'PRICE_MISSING')) r[18] = 'COMPLETED_PRICE_MISSING';
        failed++;
      } else {
        function applyTarget(target, dateCol, closeCol, returnCol, maCol, relationCol, lastAttemptCol, attemptCol, need) {
          if (!need || !target) return;
          const idx = bars.findIndex(function(b){return b.date===target;});
          if (idx < 0 || idx < 19) {
            r[lastAttemptCol] = new Date();
            r[attemptCol] = Number(r[attemptCol] || 0) + 1;
            r[23] = 'TARGET_BAR_MISSING_' + target;
            if (Number(r[attemptCol]) >= YCV11.MAX_RETRIES) r[relationCol] = 'PRICE_MISSING';
            changed = true;
            failed++;
            return;
          }
          let sum=0;for(let j=idx-19;j<=idx;j++)sum+=bars[j].close;
          const close=bars[idx].close,ma20=sum/20,base=Number(r[5]);
          r[dateCol]=target;r[closeCol]=close;r[returnCol]=isFinite(base)&&base>0?((close/base)-1)*100:'';r[maCol]=ma20;
          r[relationCol]=close>ma20?'ABOVE':close<ma20?'BELOW':'AT';
          r[lastAttemptCol]=new Date();r[attemptCol]=Number(r[attemptCol]||0)+1;r[23]='';
          changed=true;completed++;
        }
        applyTarget(ev.d10,8,9,10,11,12,19,20,ev.need10);
        applyTarget(ev.d20,13,14,15,16,17,21,22,ev.need20);
        if (ev.age >= 20 && (r[14] || String(r[17] || '') === 'PRICE_MISSING')) {
          r[18] = r[14] ? (String(r[5] || '') === '' ? 'COMPLETED_BASELINE_MISSING' : 'COMPLETED') : 'COMPLETED_PRICE_MISSING';
        } else if (ev.age >= 10 && !/^LEGACY/.test(String(r[18] || ''))) {
          r[18] = 'OBSERVATION';
        }
      }
      if (changed) history.getRange(ev.rowNumber,1,1,YCV11.HEADERS.length).setValues([r]);
    });
  });
  return 'EVALUATED=' + completed + '; FAILED=' + failed + '; CODES=' + codes.length;
}

function ycv11TrimHistory_(history) {
  const last = history.getLastRow();
  const excess = last - 1 - YCV11.MAX_HISTORY_ROWS;
  if (excess <= 0) return 0;
  const rows = history.getRange(2,1,Math.min(excess,100),YCV11.HEADERS.length).getValues();
  let remove = 0;
  for (let i=0;i<rows.length;i++) {
    if (!/^COMPLETED/.test(String(rows[i][18] || ''))) break;
    remove++;
  }
  if (remove > 0) history.deleteRows(2,remove);
  return remove;
}

function ycv11RunLifecycleV11_() {
  const props = PropertiesService.getScriptProperties();
  const parts = ycv11NowParts_();
  if (String(props.getProperty(YCV11.MIGRATION_KEY) || '') !== 'Y') {
    return migrateCandidateLifecycleV11();
  }
  if (parts.minute < (15 * 60 + 35)) return 'SKIP_BEFORE_MARKET_CLOSE';
  if (String(props.getProperty(YCV11.LAST_RUN_DATE_KEY) || '') === parts.date) return 'SKIP_ALREADY_COMPLETE_TODAY';
  let sessions;
  try { sessions = ycv11CompletedSessions_(ycv11GetSessions_()); }
  catch (e) {
    props.setProperty('YCV11_LAST_ERROR',String(e && e.message || e).slice(0,180));
    return 'LIFECYCLE_HOLD_CALENDAR_UNAVAILABLE';
  }
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const candidate = ss.getSheetByName(YC.CANDIDATE_SHEET);
  const history = ycv11EnsureHistorySheet_(ss);
  const poolStatus = reclaimExpiredRowsV8_(candidate,todayKeyV8_());
  const evalStatus = ycv11ProcessDueEvaluationsV11_(history,sessions);
  ycv11TrimHistory_(history);
  const pending = history.getLastRow() >= 2 ? history.getRange(2,1,history.getLastRow()-1,YCV11.HEADERS.length).getValues().some(function(r) {
    const age = ycv11AgeFromSessions_(String(r[3]||''),sessions);
    if (age == null || age < 10 || /^COMPLETED/.test(String(r[18]||''))) return false;
    const d10Pending = !r[9] && String(r[12]||'') !== 'LEGACY_NOT_CAPTURED' && String(r[12]||'') !== 'PRICE_MISSING' && Number(r[20]||0) < YCV11.MAX_RETRIES;
    const d20Pending = age >= 20 && !r[14] && String(r[17]||'') !== 'LEGACY_NOT_CAPTURED' && String(r[17]||'') !== 'PRICE_MISSING' && Number(r[22]||0) < YCV11.MAX_RETRIES;
    return d10Pending || d20Pending;
  }) : false;
  if (!pending) props.setProperty(YCV11.LAST_RUN_DATE_KEY,parts.date);
  return poolStatus + '; ' + evalStatus + '; PENDING=' + (pending ? 'Y' : 'N');
}

function scheduledCandidateLifecycleAndTickV11() {
  const lifecycle = ycv11RunLifecycleV11_();
  const tick = scheduledCombinedTickV6();
  return 'LIFECYCLE=' + lifecycle + '; ' + tick;
}

function manualCandidateRefreshWithLifecycleV11() {
  const migration = migrateCandidateLifecycleV11();
  const refresh = manualCandidateRefreshV5();
  return 'LIFECYCLE=' + migration + '; REFRESH=' + refresh;
}
