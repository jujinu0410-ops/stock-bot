/*
 * Runtime V10: canonical YouTube all-stocks Gmail ingest.
 *
 * Purpose:
 * - use [YT_ALL_STOCKS_BEGIN] ... [YT_ALL_STOCKS_END] as the source of truth
 * - accept explicit CODE|NAME lines inside that machine-readable block
 * - keep the one-calendar-month rolling pool
 * - retain the old heading/premarket parsers only as backward-compatible fallbacks
 *
 * Loaded after runtime_v9 so collectIntelligenceCandidates_() here is canonical.
 */

const YCV10 = Object.freeze({
  TIMEZONE: 'Asia/Seoul',
  MAX_PREMARKET_STOCKS: 30,
  MIN_PREMARKET_STOCKS: 1,
  RUNTIME_TAG: 'YT_ALL_STOCKS_V10',
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
    if (/직접\s*언급\s*종목\s*없음|언급\s*종목\s*없음|종목\s*없음/i.test(section)) {
      return {found:true, stocks:[]};
    }
    throw new Error('PREMARKET_V10_PARSE_EMPTY');
  }
  if (stocks.length > YCV10.MAX_PREMARKET_STOCKS) {
    throw new Error('PREMARKET_V10_TOO_MANY:' + stocks.length);
  }

  return {found: true, stocks: stocks};
}


function parseFullMentionListV10_(rawBody) {
  const body = String(rawBody || '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/\u00a0/g, ' ')
    .replace(/\u200b/g, '');

  const beginToken = '[YT_ALL_STOCKS_BEGIN]';
  const endToken = '[YT_ALL_STOCKS_END]';
  const begin = body.indexOf(beginToken);
  const end = body.indexOf(endToken, begin >= 0 ? begin + beginToken.length : 0);

  let section = '';
  let declared = 0;
  let source = '';

  if (begin >= 0 && end > begin) {
    section = body.substring(begin + beginToken.length, end);
    source = 'YT_ALL_STOCKS_MARKERS';

    const before = body.substring(Math.max(0, begin - 500), begin);
    const countMatch = before.match(/오늘\s*(?:유튜브\s*)?전체\s*언급\s*종목\s*\(\s*(\d+)\s*개\s*\)/i);
    if (countMatch) declared = Number(countMatch[1] || 0);
  } else {
    // Backward-compatible fallback for already delivered legacy mails.
    const heading = body.match(/(?:^|\n)\s*(?:📋\s*)?오늘\s*(?:유튜브\s*)?전체\s*언급\s*종목\s*\(\s*(\d+)\s*개\s*\)[^\n]*/i);
    if (!heading) return {found:false, stocks:[], declared:0, source:''};

    declared = Number(heading[1] || 0);
    section = body.substring(heading.index + heading[0].length);
    source = 'LEGACY_HEADING';
  }

  const stocks = [];
  const seen = new Set();

  section.split('\n').forEach(function(rawLine) {
    const line = String(rawLine || '').trim();
    if (!line) return;

    const m = line.match(/^(\d{6})\s*\|\s*([^|\n]{1,80})\s*$/);
    if (!m) return;

    const code = normalizeCandidateCode_(m[1]);
    const name = String(m[2] || '').trim();
    if (!code || !name) return;

    if (seen.has(code)) {
      throw new Error('YT_ALL_STOCKS_V10_DUPLICATE_CODE:' + code);
    }
    seen.add(code);
    stocks.push({code:code, name:name, reason:''});
  });

  if (stocks.length === 0) {
    throw new Error('YT_ALL_STOCKS_V10_PARSE_EMPTY');
  }
  if (declared > 0 && declared !== stocks.length) {
    throw new Error(
      'YT_ALL_STOCKS_V10_COUNT_MISMATCH_declared_' +
      declared + '_parsed_' + stocks.length
    );
  }

  return {
    found:true,
    stocks:stocks,
    declared:declared || stocks.length,
    source:source
  };
}

function collectIntelligenceCandidatesFromMailV10_() {
  const query = 'subject:"경제 Intelligence" newer_than:' + YCI.LOOKBACK_DAYS + 'd -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, YCI.MAX_THREADS);
  const byCode = {};
  const today = todayKeyV8_();
  const todayMentionCodes = new Set();
  let structuredMailCount = 0;
  let parsedStockCount = 0;

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(message) {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;

      const fullParsed = parseFullMentionListV10_(message.getPlainBody());
      const parsed = fullParsed.found ? fullParsed : parsePremarketBlockV10_(message.getPlainBody());
      if (!parsed.found) return;

      structuredMailCount += 1;
      const dateKey = Utilities.formatDate(message.getDate(), YCV10.TIMEZONE, 'yyyy-MM-dd');
      const seenThisMessage = new Set();

      parsed.stocks.forEach(function(stock) {
        const code = stock.code;
        const sourceName = stock.name;
        if (seenThisMessage.has(code)) {
          throw new Error('CANDIDATE_V10_DUPLICATE_MESSAGE_CODE:' + code);
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
        if (dateKey === today) todayMentionCodes.add(code);
      });
    });
  });

  if (structuredMailCount > 0 && parsedStockCount === 0) {
    throw new Error('CANDIDATE_V10_EMPTY_WITH_STRUCTURED_MAILS_' + structuredMailCount);
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

      if (lastSeen === today && !todayMentionCodes.has(code)) {
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
