/*
 * Runtime V10: PREMARKET_ONLY Gmail ingest.
 *
 * Purpose:
 * - parse ONLY the "장전 시황" section from [경제 Intelligence] mails
 * - accept explicit CODE|NAME|reason lines only
 * - keep the one-calendar-month rolling pool
 * - reject same-day sheet carryover that is not present in today's premarket section
 *   so a previous YT_ALL_STOCKS contamination cannot persist as a fresh mention
 *
 * Loaded after runtime_v9 so collectIntelligenceCandidates_() here is canonical.
 */

const YCV10 = Object.freeze({
  TIMEZONE: 'Asia/Seoul',
  MAX_PREMARKET_STOCKS: 30,
  MIN_PREMARKET_STOCKS: 1,
  RUNTIME_TAG: 'PREMARKET_V10',
});

function parsePremarketBlockV10_(rawBody) {
  const body = String(rawBody || '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/\u00a0/g, ' ')
    .replace(/\u200b/g, '');

  const heading = body.match(/(?:^|\n)\s*\[?장전\s*시황\]?\s*\n/i);
  if (!heading) return {found: false, stocks: []};

  const start = heading.index + heading[0].length;
  const tail = body.substring(start);
  const blank = tail.search(/\n\s*\n/);
  const section = blank >= 0 ? tail.substring(0, blank) : tail;

  const stocks = [];
  const seen = new Set();

  section.split('\n').forEach(function(rawLine) {
    const line = String(rawLine || '').trim();
    if (!line) return;

    const m = line.match(/^(\d{6})\|([^|\n]{1,60})\|(.+)$/);
    if (!m) return;

    const code = normalizeCandidateCode_(m[1]);
    const name = String(m[2] || '').trim();
    const reason = String(m[3] || '').trim();
    if (!code || !name || !reason) {
      throw new Error('PREMARKET_V10_INVALID_LINE:' + line);
    }
    if (seen.has(code)) {
      throw new Error('PREMARKET_V10_DUPLICATE_CODE:' + code);
    }
    seen.add(code);
    stocks.push({code: code, name: name, reason: reason});
  });

  if (stocks.length < YCV10.MIN_PREMARKET_STOCKS) {
    throw new Error('PREMARKET_V10_PARSE_EMPTY');
  }
  if (stocks.length > YCV10.MAX_PREMARKET_STOCKS) {
    throw new Error('PREMARKET_V10_TOO_MANY:' + stocks.length);
  }

  return {found: true, stocks: stocks};
}

function collectIntelligenceCandidates_() {
  const query = 'subject:"경제 Intelligence" newer_than:' + YCI.LOOKBACK_DAYS + 'd -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, YCI.MAX_THREADS);
  const byCode = {};
  const today = todayKeyV8_();
  const todayPremarketCodes = new Set();
  let structuredMailCount = 0;
  let parsedStockCount = 0;

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(message) {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;

      const parsed = parsePremarketBlockV10_(message.getPlainBody());
      if (!parsed.found) return;

      structuredMailCount += 1;
      const dateKey = Utilities.formatDate(message.getDate(), YCV10.TIMEZONE, 'yyyy-MM-dd');
      const seenThisMessage = new Set();

      parsed.stocks.forEach(function(stock) {
        const code = stock.code;
        const sourceName = stock.name;
        if (seenThisMessage.has(code)) {
          throw new Error('PREMARKET_V10_DUPLICATE_MESSAGE_CODE:' + code);
        }
        seenThisMessage.add(code);
        parsedStockCount += 1;

        if (!byCode[code]) {
          byCode[code] = {
            name: sourceName,
            nameTrusted: true,
            dates: new Set(),
            lastSeen: dateKey,
            sourceTag: YCV10.RUNTIME_TAG,
          };
        }
        byCode[code].dates.add(dateKey);
        if (dateKey >= byCode[code].lastSeen) {
          byCode[code].lastSeen = dateKey;
          byCode[code].name = sourceName;
          byCode[code].nameTrusted = true;
          byCode[code].sourceTag = YCV10.RUNTIME_TAG;
        }
        if (dateKey === today) todayPremarketCodes.add(code);
      });
    });
  });

  if (structuredMailCount > 0 && parsedStockCount === 0) {
    throw new Error('PREMARKET_V10_EMPTY_WITH_STRUCTURED_MAILS_' + structuredMailCount);
  }

  /*
   * Keep older valid pool members inside TTL, but DO NOT carry a row whose
   * sheet lastSeen is today unless today's premarket mail actually contains it.
   * This is the one-time/self-healing guard for YT_ALL_STOCKS contamination.
   */
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
  if (sheet && sheet.getMaxRows() >= YC.DATA_START_ROW) {
    const rows = sheet.getRange(
      YC.DATA_START_ROW,
      1,
      sheet.getMaxRows() - YC.DATA_START_ROW + 1,
      7
    ).getDisplayValues();

    rows.forEach(function(r) {
      const active = String(r[0] || '').trim().toUpperCase();
      const name = String(r[1] || '').trim();
      const code = normalizeCandidateCode_(r[3]);
      const lastSeen = ymdFromAnyV8_(r[5]);
      if (active !== 'Y' || !code || !name || !lastSeen) return;
      if (isExpiredMentionV8_(lastSeen, today)) return;
      if (byCode[code]) return;

      if (lastSeen === today && !todayPremarketCodes.has(code)) {
        return;
      }

      byCode[code] = {
        name: name,
        nameTrusted: true,
        dates: new Set([lastSeen]),
        lastSeen: lastSeen,
        carryover: true,
        sourceTag: YCV10.RUNTIME_TAG,
      };
    });
  }

  Object.keys(byCode).forEach(function(code) {
    if (isExpiredMentionV8_(byCode[code].lastSeen, today)) {
      delete byCode[code];
    } else {
      byCode[code].mentionDays = byCode[code].dates.size;
    }
  });

  return byCode;
}
