/*
 * YouTube Intelligence -> CANDIDATES 자동 동기화
 *
 * - Gmail의 [경제 Intelligence] 최근 35일 메일에서
 *   '📌 오늘 언급·주목 종목 (V8 분석 후보군)' 섹션만 파싱한다.
 * - 같은 종목/같은 날짜 중복 메일은 1회로 집계한다.
 * - Naver basic API로 종목명/시장(KOSPI/KOSDAQ)을 확인한다.
 * - Naver 일봉으로 하루 1회 MA20/MA60/ATR14/RSI14/pullback 기준을 갱신한다.
 * - 장중 현재가는 CANDIDATES!H의 GOOGLEFINANCE가 담당한다.
 * - Kiwoom은 여기서 호출하지 않는다. BUY_CANDIDATE 발생 시 monitor 파일이 호출한다.
 */

const YCI = Object.freeze({
  SUBJECT_PREFIX: '[경제 Intelligence]',
  SECTION_MARKER: '📌 오늘 언급·주목 종목 (V8 분석 후보군)',
  LOOKBACK_DAYS: 35,
  TTL_DAYS: 30,
  MAX_THREADS: 100,
  NAVER_BASIC: 'https://m.stock.naver.com/api/stock/',
  NAVER_FCHART: 'https://fchart.stock.naver.com/sise.nhn',
});

function refreshYouTubeCandidatePool() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  const started = Date.now();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
    if (!sheet) throw new Error('CANDIDATES sheet not found');

    const pool = collectIntelligenceCandidates_();
    const existing = existingCandidateRows_(sheet);
    const today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd');
    const activeCodes = new Set();

    Object.keys(pool).sort().forEach(code => {
      const item = pool[code];
      const identity = resolveNaverIdentity_(code, item.name);

      if (identity.verified && identity.name && normalizeName_(identity.name) !== normalizeName_(item.name)) {
        appendDispatchLog_(ss, {
          ticker: code,
          name: item.name,
          tech: 'IDENTITY_QUARANTINE',
          http: '',
          finalSignal: '',
          flowStatus: '',
          detail: 'Naver=' + identity.name,
          triggerKey: '',
          elapsedMs: Date.now() - started,
          error: 'SOURCE_NAME_CODE_MISMATCH',
          source: 'GMAIL_INGEST',
        });
        return;
      }

      const prior = existing[code] || null;
      const rowNumber = prior ? prior.row : nextCandidateRow_(sheet);
      const market = identity.market || (prior ? prior.market : '');
      if (!market) {
        appendDispatchLog_(ss, {
          ticker: code,
          name: item.name,
          tech: 'IDENTITY_PENDING',
          http: '',
          finalSignal: '',
          flowStatus: '',
          detail: '',
          triggerKey: '',
          elapsedMs: Date.now() - started,
          error: 'MARKET_UNRESOLVED',
          source: 'GMAIL_INGEST',
        });
        return;
      }

      const canonicalName = identity.name || item.name;
      const ticker = (market === 'KOSDAQ' ? 'KOSDAQ:' : 'KRX:') + code;
      const lastSeen = parseYmd_(item.lastSeen);
      const expiry = new Date(lastSeen.getTime());
      expiry.setDate(expiry.getDate() + YCI.TTL_DAYS);

      // A:G만 갱신. H와 P:T는 미리 깔아둔 Sheet 수식이다.
      sheet.getRange(rowNumber, 1, 1, 7).setValues([[
        'Y', canonicalName, market, code, ticker, lastSeen, expiry,
      ]]);
      sheet.getRange(rowNumber, 4).setNumberFormat('@');
      sheet.getRange(rowNumber, 6, 1, 2).setNumberFormat('yyyy-mm-dd');
      activeCodes.add(code);

      const note = String(sheet.getRange(rowNumber, 26).getDisplayValue() || '');
      const needsTech = note.indexOf('TECH_ASOF=' + today) < 0;
      if (needsTech) {
        try {
          const tech = fetchDailyTechnicalSnapshot_(code);
          if (tech.staleDays > 4) throw new Error('STALE_DAILY_BAR_' + tech.staleDays);
          // I:O = ReferenceLow, ATR14, MA20, MA60, MA20Slope5D, RSI14, PullbackReady
          sheet.getRange(rowNumber, 9, 1, 7).setValues([[
            tech.referenceLow,
            tech.atr14,
            tech.ma20,
            tech.ma60,
            tech.ma20Slope5d,
            tech.rsi14,
            tech.pullbackReady,
          ]]);
          sheet.getRange(rowNumber, 26).setValue(
            'TECH_ASOF=' + today + '; LAST_BAR=' + tech.lastDate + '; MENTION_DAYS=' + item.mentionDays
          );
        } catch (err) {
          // 기존 값은 지우지 않는다. 다음 30분 동기화에서 재시도한다.
          sheet.getRange(rowNumber, 26).setValue(
            'TECH_REFRESH_ERROR=' + String(err && err.message ? err.message : err)
          );
        }
      }
    });

    // 30일 pool에서 빠진 기존 행은 비활성화한다. 이력(U:Y)은 보존한다.
    Object.keys(existing).forEach(code => {
      if (!activeCodes.has(code)) {
        sheet.getRange(existing[code].row, 1).setValue('N');
      }
    });

    SpreadsheetApp.flush();
    PropertiesService.getScriptProperties().setProperty('LAST_CANDIDATE_REFRESH_AT', new Date().toISOString());
  } finally {
    lock.releaseLock();
  }
}

function collectIntelligenceCandidates_() {
  const query = 'subject:"' + YCI.SUBJECT_PREFIX + '" newer_than:' + YCI.LOOKBACK_DAYS + 'd -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, YCI.MAX_THREADS);
  const byCode = {};

  threads.forEach(thread => {
    thread.getMessages().forEach(message => {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;
      const body = String(message.getPlainBody() || '');
      const markerPos = body.indexOf(YCI.SECTION_MARKER);
      if (markerPos < 0) return;

      const section = body.substring(markerPos);
      const dateKey = Utilities.formatDate(message.getDate(), 'Asia/Seoul', 'yyyy-MM-dd');
      const regex = /^([^\n\r]{1,50})\s*[\r\n]+\((\d{6})\)\s*[\r\n]+\[(직접 언급|관련 수혜주)\]/gm;
      let match;
      const seenThisMessage = new Set();
      while ((match = regex.exec(section)) !== null) {
        const name = String(match[1] || '').trim();
        const code = normalizeCode_(match[2]);
        if (!name || !code || seenThisMessage.has(code)) continue;
        seenThisMessage.add(code);

        if (!byCode[code]) {
          byCode[code] = {name: name, dates: new Set(), lastSeen: dateKey};
        }
        byCode[code].dates.add(dateKey);
        if (dateKey >= byCode[code].lastSeen) {
          byCode[code].lastSeen = dateKey;
          byCode[code].name = name;
        }
      }
    });
  });

  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - (YCI.TTL_DAYS - 1));
  const cutoffKey = Utilities.formatDate(cutoff, 'Asia/Seoul', 'yyyy-MM-dd');

  Object.keys(byCode).forEach(code => {
    if (byCode[code].lastSeen < cutoffKey) {
      delete byCode[code];
    } else {
      byCode[code].mentionDays = byCode[code].dates.size;
    }
  });
  return byCode;
}

function existingCandidateRows_(sheet) {
  const out = {};
  const lastRow = sheet.getLastRow();
  if (lastRow < YC.DATA_START_ROW) return out;
  const rows = sheet.getRange(YC.DATA_START_ROW, 1, lastRow - 1, 5).getDisplayValues();
  rows.forEach((r, idx) => {
    const code = normalizeCode_(r[3]);
    if (code) out[code] = {row: idx + YC.DATA_START_ROW, market: String(r[2] || '').trim()};
  });
  return out;
}

function nextCandidateRow_(sheet) {
  const lastRow = Math.max(sheet.getLastRow(), YC.HEADER_ROW);
  return lastRow + 1;
}

function resolveNaverIdentity_(code, sourceName) {
  try {
    const res = UrlFetchApp.fetch(YCI.NAVER_BASIC + encodeURIComponent(code) + '/basic', {
      method: 'get',
      muteHttpExceptions: true,
      headers: {'User-Agent': 'Mozilla/5.0'},
    });
    if (res.getResponseCode() !== 200) return {verified: false, name: sourceName, market: ''};
    const data = JSON.parse(res.getContentText() || '{}');
    const name = String(data.stockName || data.itemName || data.name || '').trim();
    const exchangeText = JSON.stringify(data.stockExchangeType || data.marketType || data.market || data.exchange || '').toUpperCase();
    let market = '';
    if (exchangeText.indexOf('KOSDAQ') >= 0 || exchangeText.indexOf('코스닥') >= 0) market = 'KOSDAQ';
    if (!market && (exchangeText.indexOf('KOSPI') >= 0 || exchangeText.indexOf('유가') >= 0)) market = 'KOSPI';
    return {verified: Boolean(name), name: name || sourceName, market: market};
  } catch (e) {
    return {verified: false, name: sourceName, market: ''};
  }
}

function fetchDailyTechnicalSnapshot_(code) {
  const url = YCI.NAVER_FCHART + '?symbol=' + encodeURIComponent(code) + '&timeframe=day&count=100&requestType=0';
  const res = UrlFetchApp.fetch(url, {
    method: 'get',
    muteHttpExceptions: true,
    headers: {'User-Agent': 'Mozilla/5.0'},
  });
  if (res.getResponseCode() !== 200) throw new Error('NAVER_FCHART_HTTP_' + res.getResponseCode());

  const xml = XmlService.parse(res.getContentText());
  const items = xml.getRootElement().getDescendants()
    .filter(x => x.getType && x.getType() === XmlService.ContentTypes.ELEMENT)
    .map(x => x.asElement())
    .filter(el => el.getName() === 'item');

  const bars = [];
  items.forEach(el => {
    const attr = el.getAttribute('data');
    if (!attr) return;
    const p = String(attr.getValue() || '').split('|');
    if (p.length < 6) return;
    const bar = {
      date: p[0],
      open: Number(p[1]),
      high: Number(p[2]),
      low: Number(p[3]),
      close: Number(p[4]),
      volume: Number(p[5]),
    };
    if (isFinite(bar.close) && isFinite(bar.high) && isFinite(bar.low)) bars.push(bar);
  });
  bars.sort((a, b) => a.date.localeCompare(b.date));
  if (bars.length < 65) throw new Error('NAVER_BARS_INSUFFICIENT_' + bars.length);

  const n = bars.length;
  const closes = bars.map(b => b.close);
  const maAt = (endIndex, period) => {
    const start = endIndex - period + 1;
    if (start < 0) return null;
    let sum = 0;
    for (let i = start; i <= endIndex; i++) sum += closes[i];
    return sum / period;
  };

  const tr = new Array(n).fill(null);
  for (let i = 1; i < n; i++) {
    tr[i] = Math.max(
      bars[i].high - bars[i].low,
      Math.abs(bars[i].high - bars[i - 1].close),
      Math.abs(bars[i].low - bars[i - 1].close)
    );
  }

  const atr = new Array(n).fill(null);
  let trSum = 0;
  for (let i = 1; i <= 14; i++) trSum += tr[i];
  atr[14] = trSum / 14;
  for (let i = 15; i < n; i++) atr[i] = ((atr[i - 1] * 13) + tr[i]) / 14;

  const rsi = new Array(n).fill(null);
  let gain = 0, loss = 0;
  for (let i = 1; i <= 14; i++) {
    const d = closes[i] - closes[i - 1];
    if (d > 0) gain += d; else loss += -d;
  }
  let avgGain = gain / 14;
  let avgLoss = loss / 14;
  rsi[14] = avgLoss === 0 ? (avgGain === 0 ? 50 : 100) : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = 15; i < n; i++) {
    const d = closes[i] - closes[i - 1];
    const g = d > 0 ? d : 0;
    const l = d < 0 ? -d : 0;
    avgGain = (avgGain * 13 + g) / 14;
    avgLoss = (avgLoss * 13 + l) / 14;
    rsi[i] = avgLoss === 0 ? (avgGain === 0 ? 50 : 100) : 100 - 100 / (1 + avgGain / avgLoss);
  }

  const last = n - 1;
  const ma20 = maAt(last, 20);
  const ma60 = maAt(last, 60);
  const ma20Prev5 = maAt(last - 5, 20);
  const atr14 = atr[last];
  const rsi14 = rsi[last];
  if (![ma20, ma60, ma20Prev5, atr14, rsi14].every(x => typeof x === 'number' && isFinite(x))) {
    throw new Error('TECHNICAL_NAN');
  }

  let firstPullback = -1;
  for (let i = Math.max(0, last - 4); i <= last; i++) {
    const mi20 = maAt(i, 20);
    const mi60 = maAt(i, 60);
    if (mi20 == null || mi60 == null || atr[i] == null) continue;
    if (bars[i].low <= mi20 + 0.3 * atr[i] && bars[i].close >= mi60) {
      firstPullback = i;
      break;
    }
  }
  const pullbackReady = firstPullback >= 0;
  const refStart = pullbackReady ? firstPullback : Math.max(0, last - 4);
  let referenceLow = Infinity;
  for (let i = refStart; i <= last; i++) referenceLow = Math.min(referenceLow, bars[i].low);

  const lastDate = bars[last].date.substring(0, 4) + '-' + bars[last].date.substring(4, 6) + '-' + bars[last].date.substring(6, 8);
  const lastDay = parseYmd_(lastDate);
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  lastDay.setHours(0, 0, 0, 0);
  const staleDays = Math.floor((now.getTime() - lastDay.getTime()) / 86400000);

  return {
    referenceLow: referenceLow,
    atr14: atr14,
    ma20: ma20,
    ma60: ma60,
    ma20Slope5d: ma20 - ma20Prev5,
    rsi14: rsi14,
    pullbackReady: pullbackReady,
    lastDate: lastDate,
    staleDays: staleDays,
  };
}

function normalizeName_(value) {
  return String(value || '').replace(/\s+/g, '').replace(/㈜|주식회사|\(주\)/g, '').toUpperCase();
}

function parseYmd_(ymd) {
  const p = String(ymd || '').split('-').map(Number);
  if (p.length !== 3 || !p[0] || !p[1] || !p[2]) throw new Error('INVALID_DATE_' + ymd);
  return new Date(p[0], p[1] - 1, p[2]);
}

/* 최초 1회: 후보 동기화 30분 + 장중 감시 5분 트리거 설치 */
function installYouTubeWatchTriggers() {
  const handlers = new Set(['monitorYouTubeCandidates', 'refreshYouTubeCandidatePool']);
  ScriptApp.getProjectTriggers().forEach(t => {
    if (handlers.has(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('monitorYouTubeCandidates')
    .timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('refreshYouTubeCandidatePool')
    .timeBased().everyMinutes(30).create();
}
