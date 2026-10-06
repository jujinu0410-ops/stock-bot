/*
 * Runtime V7 compatibility parser: source is ONLY the explicit 3-column
 * "장전 시황" block at the TOP of each [경제 Intelligence] message.
 *   장전 시황
 *   009150|삼성전기|AI MLCC 수요 증가
 *   ...
 *
 * IMPORTANT: Ignore [YT_ALL_STOCKS_BEGIN] ... END. That is a broad
 * 87-name analytical index, NOT the approved YouTube buy-watch intake.
 * Previous active candidates are preserved until their existing expiry.
 */

const YCV7 = Object.freeze({
  HEADING: '장전 시황',
});

function parseYtAllStocksBlockV7_(body) {
  const text = String(body || '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/[\u00a0\u200b\ufeff\u200e\u200f]/g, ' ')
    .replace(/[\u202a-\u202e\u2066-\u2069]/g, '');
  const lines = text.split('\n');
  const start = lines.findIndex(function(line) {
    return /^장전 시황[：:]?$/.test(String(line || '').trim());
  });
  if (start < 0) return {found: false, stocks: []};

  const stocks = [];
  const seen = new Set();
  let started = false;
  for (let i = start + 1; i < lines.length; i++) {
    const line = String(lines[i] || '').trim();
    if (!line) {
      if (started) break;  // End of dedicated contiguous feed block.
      continue;
    }
    const m = /^(\d{6})\|([^|]+)\|(.+)$/.exec(line);
    if (!m) {
      throw new Error('YT_MORNING_STOCKS_INVALID_LINE_' + (i + 1) + ': ' + line);
    }
    const code = m[1];
    const name = m[2].trim();
    const reason = m[3].trim();
    if (!name || !reason) throw new Error('YT_MORNING_STOCKS_INCOMPLETE_' + code);
    if (!seen.has(code)) {
      stocks.push({code: code, name: name});
      seen.add(code);
    }
    started = true;
    if (stocks.length > 50) throw new Error('YT_MORNING_STOCKS_OVERSIZED');
  }
  if (!stocks.length) throw new Error('YT_MORNING_STOCKS_EMPTY');
  return {found: true, stocks: stocks};
}

function carryExistingUnexpiredCandidatesV7_(byCode) {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
  if (!sheet) return;

  const maxRows = sheet.getMaxRows();
  if (maxRows < YC.DATA_START_ROW) return;

  const rows = sheet.getRange(YC.DATA_START_ROW, 1, maxRows - YC.DATA_START_ROW + 1, 26).getDisplayValues();
  const today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd');

  rows.forEach(function(r) {
    const active = String(r[0] || '').trim().toUpperCase();
    const name = String(r[1] || '').trim();
    const code = normalizeCandidateCode_(r[3]);
    const lastSeen = String(r[5] || '').trim();
    const expiry = String(r[6] || '').trim();
    if (active !== 'Y' || !code || !name || !lastSeen || !expiry) return;
    if (expiry < today) return;
    if (byCode[code]) return;

    byCode[code] = {
      name: name,
      nameTrusted: true,
      dates: new Set([lastSeen]),
      lastSeen: lastSeen,
      carryover: true,
      mentionDays: 1,
    };
  });
}

/*
 * Override the legacy parser. Only fixed blocks are parsed from Gmail.
 * Recent block-bearing mails are aggregated over the existing lookback window.
 */
function collectIntelligenceCandidates_() {
  const query = 'subject:"경제 Intelligence" newer_than:' + YCI.LOOKBACK_DAYS + 'd -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, YCI.MAX_THREADS);
  const byCode = {};
  let blockMailCount = 0;

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(message) {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;

      const parsed = parseYtAllStocksBlockV7_(message.getPlainBody());
      if (!parsed.found) return;
      blockMailCount += 1;

      const dateKey = Utilities.formatDate(message.getDate(), 'Asia/Seoul', 'yyyy-MM-dd');
      parsed.stocks.forEach(function(stock) {
        const code = stock.code;
        const sourceName = stock.name;
        if (!byCode[code]) {
          byCode[code] = {
            name: sourceName,
            nameTrusted: true,
            dates: new Set(),
            lastSeen: dateKey,
          };
        }
        byCode[code].dates.add(dateKey);
        if (dateKey >= byCode[code].lastSeen) {
          byCode[code].lastSeen = dateKey;
          byCode[code].name = sourceName;
          byCode[code].nameTrusted = true;
        }
      });
    });
  });

  /* Migration safety: do not erase still-valid candidates merely because old
   * historical mails predate the fixed block format. No legacy mail section is
   * parsed; we only carry the sheet's already-known rows until their expiry.
   */
  carryExistingUnexpiredCandidatesV7_(byCode);

  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - (YCI.TTL_DAYS - 1));
  const cutoffKey = Utilities.formatDate(cutoff, 'Asia/Seoul', 'yyyy-MM-dd');

  Object.keys(byCode).forEach(function(code) {
    if (byCode[code].lastSeen < cutoffKey) {
      delete byCode[code];
    } else if (!byCode[code].mentionDays) {
      byCode[code].mentionDays = byCode[code].dates.size;
    }
  });

  return byCode;
}

/*
 * Override V5's today-mail detector: today's mail counts as collectible only
 * when the fixed block exists. Empty blocks are valid and mean no direct stock
 * mentions that day.
 */
function findTodayIntelligenceMailV5_() {
  const parts = seoulNowPartsV5_(new Date());
  const query = 'subject:"경제 Intelligence" newer_than:2d -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, 20);
  let newest = null;

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(message) {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;
      const messageDateKey = Utilities.formatDate(message.getDate(), YCV5.TIMEZONE, 'yyyy-MM-dd');
      if (messageDateKey !== parts.dateKey) return;

      const parsed = parseYtAllStocksBlockV7_(message.getPlainBody());
      if (!parsed.found) return;
      if (!newest || message.getDate().getTime() > newest.date.getTime()) {
        newest = {
          subject: subject,
          date: message.getDate(),
          stockCount: parsed.stocks.length,
        };
      }
    });
  });

  return newest;
}
