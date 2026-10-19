/*
 * Runtime V7: fixed [YT_ALL_STOCKS_BEGIN] ... [YT_ALL_STOCKS_END] parser.
 *
 * Source of truth for candidate ingestion is now ONLY the fixed Gmail block:
 *   [YT_ALL_STOCKS_BEGIN]
 *   005930|삼성전자
 *   000660|SK하이닉스
 *   ...
 *   [YT_ALL_STOCKS_END]
 *
 * Legacy "주요 언급 종목" / "V8 분석 후보군" sections are intentionally ignored.
 * Existing active sheet rows are carried until their existing expiry date so
 * the format migration does not erase still-valid candidates on day one.
 */

const YCV7 = Object.freeze({
  BEGIN: '[YT_ALL_STOCKS_BEGIN]',
  END: '[YT_ALL_STOCKS_END]',
});

function parseYtAllStocksBlockV7_(body) {
  const text = String(body || '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/[\u00a0\u200b\ufeff\u200e\u200f]/g, ' ')
    .replace(/[\u202a-\u202e\u2066-\u2069]/g, '');

  const begin = text.indexOf(YCV7.BEGIN);
  if (begin < 0) return {found: false, stocks: []};
  const end = text.indexOf(YCV7.END, begin + YCV7.BEGIN.length);
  if (end < 0) throw new Error('YT_ALL_STOCKS_END_MISSING');

  const raw = text.substring(begin + YCV7.BEGIN.length, end);
  const stocks = [];
  const seen = new Set();
  const lines = raw.split('\n');

  lines.forEach(function(rawLine, idx) {
    const line = String(rawLine || '').trim();
    if (!line) return;
    const m = line.match(/^(\d{6})\|(.+)$/);
    if (!m) throw new Error('YT_ALL_STOCKS_INVALID_LINE_' + (idx + 1) + ': ' + line);

    const code = String(m[1]);
    const name = String(m[2] || '').trim();
    if (!name) throw new Error('YT_ALL_STOCKS_EMPTY_NAME_' + code);
    if (seen.has(code)) return;
    seen.add(code);
    stocks.push({code: code, name: name});
  });

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
