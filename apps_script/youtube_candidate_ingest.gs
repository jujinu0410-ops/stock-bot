/*
 * YouTube Intelligence -> CANDIDATES ingest
 *
 * Apps Script is the lightweight radar:
 * - read recent [경제 Intelligence] Gmail reports
 * - parse ONLY the dedicated candidate-card section with explicit six-digit codes
 * - verify name/market with Naver
 * - refresh daily technical inputs used by the Sheet formulas
 *
 * Kiwoom is NOT called here. It is called only after a Sheet BUY_CANDIDATE signal.
 */

const YCI = Object.freeze({
  SUBJECT_PREFIX: '[경제 Intelligence]',
  LOOKBACK_DAYS: 35,
  TTL_DAYS: 30,
  MAX_THREADS: 100,
  NAVER_BASIC: 'https://m.stock.naver.com/api/stock/',
  NAVER_FCHART: 'https://fchart.stock.naver.com/sise.nhn',
});

/* Legacy entry point kept for compatibility. */
function refreshYouTubeCandidatePool() {
  return refreshYouTubeCandidatePoolV2();
}

function collectIntelligenceCandidates_() {
  // Gmail search is intentionally broader than the exact bracketed subject so
  // Apps Script/Gmail search tokenization cannot silently return zero threads.
  const query = 'subject:"경제 Intelligence" newer_than:' + YCI.LOOKBACK_DAYS + 'd -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, YCI.MAX_THREADS);
  const byCode = {};
  let structuredMailCount = 0;
  let parsedCardCount = 0;

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(message) {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;

      const body = String(message.getPlainBody() || '')
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')
        .replace(/\u00a0/g, ' ')
        .replace(/\u200b/g, '');

      // Current live reports use headings such as:
      // 📌 오늘 언급·주목 종목 (V8 분석 후보군)
      // Keep the matcher flexible so harmless heading wording changes do not
      // deactivate the whole rolling pool.
      const headingRe = /(?:^|\n)\s*(?:#{1,6}\s*)?(?:📌\s*)?(?:(?:오늘(?:의)?|주요|핵심|최종)\s*)?언급\s*(?:(?:[·ㆍ\/&+]\s*)?주목\s*)?종목(?:\s*(?:목록|리스트))?(?:\s*\([^\n)]{1,80}\))?[^\n]*/gi;
      let headingMatch = null;
      let m;
      while ((m = headingRe.exec(body)) !== null) headingMatch = m;
      if (!headingMatch) return;

      structuredMailCount += 1;
      const section = body.substring(headingMatch.index + headingMatch[0].length);
      const lines = section.split('\n').map(function(v) {
        return String(v || '').replace(/^\s*[-*+•▪◦‣▶▷►]+\s*/, '').replace(/\*\*|__|`/g, '').trim();
      });
      const dateKey = Utilities.formatDate(message.getDate(), 'Asia/Seoul', 'yyyy-MM-dd');
      const seenThisMessage = new Set();

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (!line) continue;

        // Stop at common report-footer headings. The candidate block is near the end.
        if (/^(?:📺|🧾|📝|🔚|Generated\s+by|본\s*리포트는|분석\s*기준\s*:)/i.test(line)) break;

        // Same-line form: 삼성전기 (009150)
        let same = line.match(/^(.{2,50}?)\s*[\[(]\s*(\d{6})\s*[\])]\s*$/);
        if (same) {
          const name = cleanCandidateName_(same[1]);
          const code = normalizeCandidateCode_(same[2]);
          const tag = nextNonEmptyLine_(lines, i + 1, 4);
          if (name && code && /^\[(직접 언급|관련 수혜주)\]$/.test(tag) && !seenThisMessage.has(code)) {
            addCandidateMention_(byCode, seenThisMessage, code, name, dateKey);
            parsedCardCount += 1;
          }
          continue;
        }

        // Vertical-card form used by current mails:
        // 삼성전기
        // (009150)
        // [직접 언급]
        if (!plausibleCandidateName_(line)) continue;
        const codeInfo = nextNonEmptyLineWithIndex_(lines, i + 1, 3);
        if (!codeInfo) continue;
        const cm = codeInfo.value.match(/^\(?\s*(\d{6})\s*\)?$/);
        if (!cm) continue;
        const tagInfo = nextNonEmptyLineWithIndex_(lines, codeInfo.index + 1, 3);
        if (!tagInfo || !/^\[(직접 언급|관련 수혜주)\]$/.test(tagInfo.value)) continue;

        const code = normalizeCandidateCode_(cm[1]);
        const name = cleanCandidateName_(line);
        if (!code || !name || seenThisMessage.has(code)) continue;
        addCandidateMention_(byCode, seenThisMessage, code, name, dateKey);
        parsedCardCount += 1;
      }
    });
  });

  // Fail closed: a recognized structured candidate section must never silently
  // produce an empty pool and deactivate every existing candidate.
  if (structuredMailCount > 0 && parsedCardCount === 0) {
    throw new Error('CANDIDATE_PARSE_EMPTY_WITH_STRUCTURED_MAILS_' + structuredMailCount);
  }

  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - (YCI.TTL_DAYS - 1));
  const cutoffKey = Utilities.formatDate(cutoff, 'Asia/Seoul', 'yyyy-MM-dd');

  Object.keys(byCode).forEach(function(code) {
    if (byCode[code].lastSeen < cutoffKey) {
      delete byCode[code];
    } else {
      byCode[code].mentionDays = byCode[code].dates.size;
    }
  });

  return byCode;
}

function addCandidateMention_(byCode, seenThisMessage, code, name, dateKey) {
  seenThisMessage.add(code);
  if (!byCode[code]) byCode[code] = {name: name, dates: new Set(), lastSeen: dateKey};
  byCode[code].dates.add(dateKey);
  if (dateKey >= byCode[code].lastSeen) {
    byCode[code].lastSeen = dateKey;
    byCode[code].name = name;
  }
}

function normalizeCandidateCode_(value) {
  const s = String(value || '').replace(/\D/g, '');
  return /^\d{6}$/.test(s) ? s : '';
}

function cleanCandidateName_(value) {
  const s = String(value || '').replace(/^\s*[-*+•▪◦‣▶▷►]+\s*/, '').replace(/\*\*|__|`/g, '').trim();
  return plausibleCandidateName_(s) ? s : '';
}

function plausibleCandidateName_(value) {
  const s = String(value || '').trim();
  if (s.length < 2 || s.length > 50) return false;
  if (/^(?:이유|근거|방향|종목명|언급\s*맥락|핵심\s*모멘텀|매수\s*추천|투자\s*판단|\d+\s*개\s*채널)/i.test(s)) return false;
  if (/^\[[^\]]+\]$/.test(s)) return false;
  if (/^\(?\d{6}\)?$/.test(s)) return false;
  if (/https?:\/\//i.test(s)) return false;
  if (/[.!?。]/.test(s)) return false;
  if (s.split(/\s+/).length > 5) return false;
  return /[A-Za-z가-힣]/.test(s);
}

function nextNonEmptyLine_(lines, start, maxLookahead) {
  const info = nextNonEmptyLineWithIndex_(lines, start, maxLookahead);
  return info ? info.value : '';
}

function nextNonEmptyLineWithIndex_(lines, start, maxLookahead) {
  const end = Math.min(lines.length, start + maxLookahead + 1);
  for (let i = start; i < end; i++) {
    if (String(lines[i] || '').trim()) return {index: i, value: String(lines[i]).trim()};
  }
  return null;
}

function existingCandidateRows_(sheet) {
  const out = {};
  const lastRow = sheet.getLastRow();
  if (lastRow < YC.DATA_START_ROW) return out;
  const rows = sheet.getRange(YC.DATA_START_ROW, 1, lastRow - 1, 5).getDisplayValues();
  rows.forEach(function(r, idx) {
    const code = normalizeCandidateCode_(r[3]);
    if (code) out[code] = {row: idx + YC.DATA_START_ROW, market: String(r[2] || '').trim()};
  });
  return out;
}

function nextCandidateRow_(sheet) {
  return Math.max(sheet.getLastRow(), YC.HEADER_ROW) + 1;
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
    if (exchangeText.indexOf('KOSDAQ') >= 0 || exchangeText.indexOf('코스닥') >= 0 || exchangeText.indexOf('KQ') >= 0) market = 'KOSDAQ';
    if (!market && (exchangeText.indexOf('KOSPI') >= 0 || exchangeText.indexOf('유가') >= 0 || exchangeText.indexOf('KS') >= 0)) market = 'KOSPI';
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
    .filter(function(x) { return x.getType && x.getType() === XmlService.ContentTypes.ELEMENT; })
    .map(function(x) { return x.asElement(); })
    .filter(function(el) { return el.getName() === 'item'; });

  const bars = [];
  items.forEach(function(el) {
    const attr = el.getAttribute('data');
    if (!attr) return;
    const p = String(attr.getValue() || '').split('|');
    if (p.length < 6) return;
    const bar = {date: p[0], open: Number(p[1]), high: Number(p[2]), low: Number(p[3]), close: Number(p[4]), volume: Number(p[5])};
    if (isFinite(bar.close) && isFinite(bar.high) && isFinite(bar.low)) bars.push(bar);
  });
  bars.sort(function(a, b) { return a.date.localeCompare(b.date); });
  if (bars.length < 65) throw new Error('NAVER_BARS_INSUFFICIENT_' + bars.length);

  const n = bars.length;
  const closes = bars.map(function(b) { return b.close; });
  function maAt(endIndex, period) {
    const start = endIndex - period + 1;
    if (start < 0) return null;
    let sum = 0;
    for (let i = start; i <= endIndex; i++) sum += closes[i];
    return sum / period;
  }

  const tr = new Array(n).fill(null);
  for (let i = 1; i < n; i++) {
    tr[i] = Math.max(bars[i].high - bars[i].low, Math.abs(bars[i].high - bars[i - 1].close), Math.abs(bars[i].low - bars[i - 1].close));
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
  if (![ma20, ma60, ma20Prev5, atr14, rsi14].every(function(x) { return typeof x === 'number' && isFinite(x); })) throw new Error('TECHNICAL_NAN');

  let firstPullback = -1;
  for (let i = Math.max(0, last - 4); i <= last; i++) {
    const mi20 = maAt(i, 20);
    const mi60 = maAt(i, 60);
    if (mi20 == null || mi60 == null || atr[i] == null) continue;
    if (bars[i].low <= mi20 + 0.3 * atr[i] && bars[i].close >= mi60) { firstPullback = i; break; }
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

function installYouTubeWatchTriggers() {
  if (typeof installYouTubeWatchTriggersV2 === 'function') return installYouTubeWatchTriggersV2();
}
