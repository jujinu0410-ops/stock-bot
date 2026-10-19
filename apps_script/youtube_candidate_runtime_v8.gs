/*
 * Runtime V8: one-calendar-month candidate TTL + row reuse.
 *
 * Identity policy:
 * - six-digit stock code is the primary key.
 * - exact/canonical name match passes.
 * - known aliases are accepted only for the same verified code.
 * - unknown name/code mismatches remain quarantined (no fuzzy matching).
 * - canonical Naver name is stored in CANDIDATES when available.
 * - repeated identical identity errors are logged at most once per Seoul day.
 *
 * Alias source:
 * - built-in aliases cover known historical cases.
 * - CONFIG rows with Key=STOCK_ALIAS_<6digit> and Value=alias1|alias2 extend the map.
 *
 * Candidate policy:
 * - Candidate validity is one calendar month from the latest direct mention.
 * - A re-mention resets the mention date and expiry from that new date.
 * - Expired candidate rows are cleared and recycled before new rows are added.
 * - Sheet formulas in H and P:T are preserved when rows are recycled.
 */

const YCV8 = Object.freeze({
  TIMEZONE: 'Asia/Seoul',
  ALIAS_CONFIG_PREFIX: 'STOCK_ALIAS_',
  IDENTITY_LOG_STATE_KEY: 'IDENTITY_LOG_STATE_V8',
  BUILTIN_ALIASES: Object.freeze({
    '005380': Object.freeze(['현대차', '현대자동차']),
    '031980': Object.freeze(['피에스케이홀딩스', 'PSK홀딩스']),
    '035420': Object.freeze(['NAVER', '네이버']),
  }),
});

function ymdFromAnyV8_(value) {
  if (value instanceof Date && !isNaN(value.getTime())) {
    return Utilities.formatDate(value, YCV8.TIMEZONE, 'yyyy-MM-dd');
  }
  const s = String(value || '').trim();
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  return m ? s : '';
}

function addOneCalendarMonthV8_(ymd) {
  const s = ymdFromAnyV8_(ymd);
  if (!s) throw new Error('INVALID_YMD_' + String(ymd || ''));
  const p = s.split('-').map(Number);
  const y = p[0], m = p[1], d = p[2];

  let ty = y;
  let tm = m + 1;
  if (tm === 13) {
    tm = 1;
    ty += 1;
  }

  const lastDay = new Date(ty, tm, 0, 12, 0, 0).getDate();
  const td = Math.min(d, lastDay);
  return new Date(ty, tm - 1, td, 12, 0, 0);
}

function todayKeyV8_() {
  return Utilities.formatDate(new Date(), YCV8.TIMEZONE, 'yyyy-MM-dd');
}

function expiryKeyFromMentionV8_(lastSeen) {
  return Utilities.formatDate(addOneCalendarMonthV8_(lastSeen), YCV8.TIMEZONE, 'yyyy-MM-dd');
}

function isExpiredMentionV8_(lastSeen, todayKey) {
  const last = ymdFromAnyV8_(lastSeen);
  if (!last) return true;
  return expiryKeyFromMentionV8_(last) < String(todayKey || todayKeyV8_());
}

function addAliasValuesV8_(map, code, values) {
  const normalizedCode = normalizeCandidateCode_(code);
  if (!normalizedCode) return;
  if (!map[normalizedCode]) map[normalizedCode] = new Set();
  (values || []).forEach(function(value) {
    const n = normalizeName_(value);
    if (n) map[normalizedCode].add(n);
  });
}

function loadStockAliasMapV8_(ss) {
  const map = {};

  Object.keys(YCV8.BUILTIN_ALIASES).forEach(function(code) {
    addAliasValuesV8_(map, code, YCV8.BUILTIN_ALIASES[code]);
  });

  const config = ss.getSheetByName('CONFIG');
  if (!config || config.getLastRow() < 2) return map;

  const rows = config.getRange(2, 1, config.getLastRow() - 1, 2).getDisplayValues();
  rows.forEach(function(r) {
    const key = String(r[0] || '').trim().toUpperCase();
    const m = key.match(/^STOCK_ALIAS_(\d{6})$/);
    if (!m) return;
    const values = String(r[1] || '')
      .split(/[|,;\n]/)
      .map(function(v) { return String(v || '').trim(); })
      .filter(Boolean);
    addAliasValuesV8_(map, m[1], values);
  });

  return map;
}

function isSameStockNameV8_(code, sourceName, canonicalName, aliasMap) {
  const src = normalizeName_(sourceName);
  const canon = normalizeName_(canonicalName);
  if (!src || !canon) return false;
  if (src === canon) return true;

  const allowed = aliasMap[normalizeCandidateCode_(code)];
  if (!allowed) return false;

  /* Both sides must be members for this exact code. This intentionally avoids
   * fuzzy matching and prevents a wrong company name from passing merely
   * because the stock code exists. */
  return allowed.has(src) && allowed.has(canon);
}

function shouldLogIdentityIssueV8_(code, errorCode) {
  const props = PropertiesService.getScriptProperties();
  const today = todayKeyV8_();
  let state = {date: today, keys: []};

  try {
    const raw = String(props.getProperty(YCV8.IDENTITY_LOG_STATE_KEY) || '');
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && parsed.date === today && Array.isArray(parsed.keys)) state = parsed;
  } catch (e) {
    state = {date: today, keys: []};
  }

  const key = normalizeCandidateCode_(code) + '|' + String(errorCode || '');
  if (state.keys.indexOf(key) >= 0) return false;

  state.keys.push(key);
  props.setProperty(YCV8.IDENTITY_LOG_STATE_KEY, JSON.stringify(state));
  return true;
}

function appendIdentityIssueV8_(ss, payload) {
  if (!shouldLogIdentityIssueV8_(payload.ticker, payload.error)) return;
  appendDispatchLog_(ss, payload);
}

/*
 * Fixed-block parser remains the only Gmail source.
 * Existing rows are carried only while still inside the one-calendar-month TTL.
 */
function collectIntelligenceCandidates_() {
  const query = 'subject:"경제 Intelligence" newer_than:' + YCI.LOOKBACK_DAYS + 'd -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, YCI.MAX_THREADS);
  const byCode = {};

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(message) {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;

      const parsed = parseYtAllStocksBlockV7_(message.getPlainBody());
      if (!parsed.found) return;

      const dateKey = Utilities.formatDate(message.getDate(), YCV8.TIMEZONE, 'yyyy-MM-dd');
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

  /* Carry the current sheet pool through the format migration / sparse mail history. */
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
  const today = todayKeyV8_();
  if (sheet && sheet.getMaxRows() >= YC.DATA_START_ROW) {
    const rows = sheet.getRange(YC.DATA_START_ROW, 1, sheet.getMaxRows() - YC.DATA_START_ROW + 1, 7).getDisplayValues();
    rows.forEach(function(r) {
      const active = String(r[0] || '').trim().toUpperCase();
      const name = String(r[1] || '').trim();
      const code = normalizeCandidateCode_(r[3]);
      const lastSeen = ymdFromAnyV8_(r[5]);
      if (active !== 'Y' || !code || !name || !lastSeen) return;
      if (isExpiredMentionV8_(lastSeen, today)) return;
      if (byCode[code]) return;

      byCode[code] = {
        name: name,
        nameTrusted: true,
        dates: new Set([lastSeen]),
        lastSeen: lastSeen,
        carryover: true,
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

function clearCandidatePayloadV8_(sheet, rowNumber) {
  /* Preserve formula columns H and P:T. */
  sheet.getRange(rowNumber, 1, 1, 7).clearContent();      // A:G
  sheet.getRange(rowNumber, 9, 1, 7).clearContent();      // I:O
  sheet.getRange(rowNumber, 21, 1, 6).clearContent();     // U:Z
}

function reclaimExpiredRowsV8_(sheet, todayKey) {
  const maxRows = sheet.getMaxRows();
  if (maxRows < YC.DATA_START_ROW) return;
  const rows = sheet.getRange(YC.DATA_START_ROW, 1, maxRows - YC.DATA_START_ROW + 1, 7).getDisplayValues();

  rows.forEach(function(r, idx) {
    const code = normalizeCandidateCode_(r[3]);
    if (!code) return;
    const lastSeen = ymdFromAnyV8_(r[5]);
    if (!lastSeen || isExpiredMentionV8_(lastSeen, todayKey)) {
      clearCandidatePayloadV8_(sheet, idx + YC.DATA_START_ROW);
    }
  });
}

function firstReusableCandidateRowV8_(sheet) {
  const maxRows = sheet.getMaxRows();
  const codes = sheet.getRange(YC.DATA_START_ROW, 4, maxRows - YC.DATA_START_ROW + 1, 1).getDisplayValues();
  for (let i = 0; i < codes.length; i++) {
    if (!normalizeCandidateCode_(codes[i][0])) return i + YC.DATA_START_ROW;
  }

  /* Extremely unlikely with the current 500-row sheet, but keep the fallback safe. */
  const oldMax = maxRows;
  sheet.insertRowsAfter(oldMax, 50);
  const source = sheet.getRange(oldMax, 1, 1, YC.LAST_COLUMN);
  const target = sheet.getRange(oldMax + 1, 1, 50, YC.LAST_COLUMN);
  source.copyTo(target, SpreadsheetApp.CopyPasteType.PASTE_FORMULA, false);
  return oldMax + 1;
}

/*
 * Override V2 refresh so expiry is one calendar month and expired slots are reused.
 */
function refreshYouTubeCandidatePoolV2() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  const started = Date.now();

  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
    if (!sheet) throw new Error('CANDIDATES sheet not found');

    const today = todayKeyV8_();
    const pool = collectIntelligenceCandidates_();

    /* Reclaim first so old codes cannot be re-used and then deactivated later. */
    reclaimExpiredRowsV8_(sheet, today);
    const existing = existingCandidateRowsV2_(sheet);
    const aliasMap = loadStockAliasMapV8_(ss);
    const activeCodes = new Set();

    Object.keys(pool).sort().forEach(function(code) {
      const item = pool[code];
      const identity = resolveNaverIdentity_(code, item.name);
      const prior = existing[code] || null;

      if (
        identity.verified &&
        identity.name &&
        !isSameStockNameV8_(code, item.name, identity.name, aliasMap)
      ) {
        appendIdentityIssueV8_(ss, {
          ticker: code, name: item.name, tech: 'IDENTITY_QUARANTINE', http: '',
          finalSignal: '', flowStatus: '', detail: 'Naver=' + identity.name,
          triggerKey: '', elapsedMs: Date.now() - started,
          error: 'SOURCE_NAME_CODE_MISMATCH', source: 'GMAIL_INGEST_V8',
        });
        return;
      }

      const rowNumber = prior ? prior.row : firstReusableCandidateRowV8_(sheet);
      const market = identity.market || (prior ? prior.market : '');
      if (!market) {
        appendIdentityIssueV8_(ss, {
          ticker: code, name: item.name, tech: 'IDENTITY_PENDING', http: '',
          finalSignal: '', flowStatus: '', detail: '', triggerKey: '',
          elapsedMs: Date.now() - started, error: 'MARKET_UNRESOLVED', source: 'GMAIL_INGEST_V8',
        });
        return;
      }

      if (!prior) clearCandidatePayloadV8_(sheet, rowNumber);

      const priorName = prior ? String(sheet.getRange(prior.row, 2).getDisplayValue() || '').trim() : '';
      const canonicalName = identity.verified && identity.name ? identity.name : (priorName || item.name);
      const ticker = (market === 'KOSDAQ' ? 'KOSDAQ:' : 'KRX:') + code;
      const lastSeenKey = ymdFromAnyV8_(item.lastSeen);
      const lastSeen = parseYmd_(lastSeenKey);
      const expiry = addOneCalendarMonthV8_(lastSeenKey);

      sheet.getRange(rowNumber, 1, 1, 7).setValues([[
        'Y', canonicalName, market, code, ticker, lastSeen, expiry,
      ]]);
      sheet.getRange(rowNumber, 4).setNumberFormat('@');
      sheet.getRange(rowNumber, 6, 1, 2).setNumberFormat('yyyy-mm-dd');
      activeCodes.add(code);

      const note = String(sheet.getRange(rowNumber, 26).getDisplayValue() || '');
      if (note.indexOf('TECH_ASOF=' + today) < 0) {
        try {
          const tech = fetchDailyTechnicalSnapshot_(code);
          if (tech.staleDays > 4) throw new Error('STALE_DAILY_BAR_' + tech.staleDays);
          sheet.getRange(rowNumber, 9, 1, 7).setValues([[
            tech.referenceLow, tech.atr14, tech.ma20, tech.ma60,
            tech.ma20Slope5d, tech.rsi14, tech.pullbackReady,
          ]]);
          sheet.getRange(rowNumber, 26).setValue(
            'TECH_ASOF=' + today + '; LAST_BAR=' + tech.lastDate + '; MENTION_DAYS=' + item.mentionDays
          );
        } catch (err) {
          sheet.getRange(rowNumber, 26).setValue('TECH_REFRESH_ERROR=' + String(err && err.message ? err.message : err));
        }
      }
    });

    Object.keys(existing).forEach(function(code) {
      if (!activeCodes.has(code)) sheet.getRange(existing[code].row, 1).setValue('N');
    });

    SpreadsheetApp.flush();
    PropertiesService.getScriptProperties().setProperty('LAST_CANDIDATE_REFRESH_AT', new Date().toISOString());
  } finally {
    lock.releaseLock();
  }
}
