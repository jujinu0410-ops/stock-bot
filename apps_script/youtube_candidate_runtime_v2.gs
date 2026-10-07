/* Runtime V2: formula-prefilled rows do not count as candidate rows. */

function monitorYouTubeCandidatesV2() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(15000)) return;
  const started = Date.now();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    if (!isMonitorEnabled_(ss)) return;
    const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
    if (!sheet) throw new Error('CANDIDATES sheet not found');

    const lastRow = lastCandidateRowByCode_(sheet);
    if (lastRow < YC.DATA_START_ROW) {
      ycRefreshAlertOutcomes_(ss);
      return;
    }

    SpreadsheetApp.flush();
    const range = sheet.getRange(YC.DATA_START_ROW, 1, lastRow - YC.DATA_START_ROW + 1, YC.LAST_COLUMN);
    const values = range.getValues();
    const displays = range.getDisplayValues();
    values.forEach((row, idx) => {
      const rowNumber = idx + YC.DATA_START_ROW;
      if (!normalizeCode_(displays[idx][3] || row[3])) return;
      try {
        processCandidateRow_(ss, sheet, rowNumber, row, displays[idx], started);
      } catch (err) {
        appendDispatchLog_(ss, {
          ticker: String(displays[idx][3] || row[3] || ''),
          name: String(row[1] || ''),
          tech: String(row[18] || ''),
          http: '', finalSignal: '', flowStatus: '', detail: '',
          triggerKey: String(row[19] || ''),
          elapsedMs: Date.now() - started,
          error: String(err && err.message ? err.message : err),
          source: 'APPS_SCRIPT_V2',
        });
      }
    });

    // SHADOW post-alert review; does not alter live signal decisions.
    ycRefreshAlertOutcomes_(ss);
  } finally {
    lock.releaseLock();
  }
}

function refreshYouTubeCandidatePoolV2() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  const started = Date.now();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
    if (!sheet) throw new Error('CANDIDATES sheet not found');

    const pool = collectIntelligenceCandidates_();
    const existing = existingCandidateRowsV2_(sheet);
    const today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd');
    const activeCodes = new Set();

    Object.keys(pool).sort().forEach(code => {
      const item = pool[code];
      const identity = resolveNaverIdentity_(code, item.name);

      if (identity.verified && identity.name && normalizeName_(identity.name) !== normalizeName_(item.name)) {
        appendDispatchLog_(ss, {
          ticker: code, name: item.name, tech: 'IDENTITY_QUARANTINE', http: '',
          finalSignal: '', flowStatus: '', detail: 'Naver=' + identity.name,
          triggerKey: '', elapsedMs: Date.now() - started,
          error: 'SOURCE_NAME_CODE_MISMATCH', source: 'GMAIL_INGEST_V2',
        });
        return;
      }

      const prior = existing[code] || null;
      const rowNumber = prior ? prior.row : firstBlankCandidateRow_(sheet);
      const market = identity.market || (prior ? prior.market : '');
      if (!market) {
        appendDispatchLog_(ss, {
          ticker: code, name: item.name, tech: 'IDENTITY_PENDING', http: '',
          finalSignal: '', flowStatus: '', detail: '', triggerKey: '',
          elapsedMs: Date.now() - started, error: 'MARKET_UNRESOLVED', source: 'GMAIL_INGEST_V2',
        });
        return;
      }

      const canonicalName = identity.name || item.name;
      const ticker = (market === 'KOSDAQ' ? 'KOSDAQ:' : 'KRX:') + code;
      const lastSeen = parseYmd_(item.lastSeen);
      const expiry = new Date(lastSeen.getTime());
      expiry.setDate(expiry.getDate() + YCI.TTL_DAYS);

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

    Object.keys(existing).forEach(code => {
      if (!activeCodes.has(code)) sheet.getRange(existing[code].row, 1).setValue('N');
    });

    SpreadsheetApp.flush();
    PropertiesService.getScriptProperties().setProperty('LAST_CANDIDATE_REFRESH_AT', new Date().toISOString());
  } finally {
    lock.releaseLock();
  }
}

function existingCandidateRowsV2_(sheet) {
  const out = {};
  const maxRows = sheet.getMaxRows();
  if (maxRows < YC.DATA_START_ROW) return out;
  const rows = sheet.getRange(YC.DATA_START_ROW, 1, maxRows - YC.DATA_START_ROW + 1, 5).getDisplayValues();
  rows.forEach((r, idx) => {
    const code = normalizeCode_(r[3]);
    if (code) out[code] = {row: idx + YC.DATA_START_ROW, market: String(r[2] || '').trim()};
  });
  return out;
}

function firstBlankCandidateRow_(sheet) {
  const maxRows = sheet.getMaxRows();
  const codes = sheet.getRange(YC.DATA_START_ROW, 4, maxRows - YC.DATA_START_ROW + 1, 1).getDisplayValues();
  for (let i = 0; i < codes.length; i++) {
    if (!normalizeCode_(codes[i][0])) return i + YC.DATA_START_ROW;
  }
  sheet.insertRowsAfter(maxRows, 100);
  return maxRows + 1;
}

function lastCandidateRowByCode_(sheet) {
  const maxRows = sheet.getMaxRows();
  const codes = sheet.getRange(YC.DATA_START_ROW, 4, maxRows - YC.DATA_START_ROW + 1, 1).getDisplayValues();
  for (let i = codes.length - 1; i >= 0; i--) {
    if (normalizeCode_(codes[i][0])) return i + YC.DATA_START_ROW;
  }
  return YC.HEADER_ROW;
}

function installYouTubeWatchTriggersV2() {
  const handlers = new Set([
    'monitorYouTubeCandidates', 'refreshYouTubeCandidatePool',
    'monitorYouTubeCandidatesV2', 'refreshYouTubeCandidatePoolV2'
  ]);
  ScriptApp.getProjectTriggers().forEach(t => {
    if (handlers.has(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('monitorYouTubeCandidatesV2')
    .timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('refreshYouTubeCandidatePoolV2')
    .timeBased().everyMinutes(30).create();
}

/*
 * Robust live-mail candidate parser override.
 * This file is loaded after youtube_candidate_ingest.gs, so this declaration
 * intentionally replaces the stricter earlier parser in the same eval scope.
 *
 * Inside the dedicated candidate section, the explicit six-digit ticker is the
 * canonical identity. A [직접 언급]/[관련 수혜주] tag is useful metadata but is
 * not required for candidate recognition because Gmail plain-text rendering may
 * alter or separate those tag lines.
 */
function collectIntelligenceCandidates_() {
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
        .replace(/[\u00a0\u200b\ufeff\u200e\u200f]/g, ' ')
        .replace(/[\u202a-\u202e\u2066-\u2069]/g, '');

      // Authoritative V2 source: the explicit end-of-mail full mention list.
      // Example:
      // 📋 오늘 전체 언급 종목 (37개)
      // 005930|삼성전자
      // 000660|SK하이닉스
      //
      // This list is the candidate-pool source of truth. The top "장전 시황"
      // block is only a catalyst/priority signal and may legitimately say
      // "직접 언급 종목 없음" even when the full report mentions many stocks.
      const fullHeading = body.match(/(?:^|\n)\s*(?:📋\s*)?오늘\s*전체\s*언급\s*종목\s*\(\s*(\d+)\s*개\s*\)[^\n]*/i);
      if (fullHeading) {
        structuredMailCount += 1;
        const declaredCount = Number(fullHeading[1] || 0);
        const fullSection = body.substring(fullHeading.index + fullHeading[0].length);
        const fullLines = fullSection.split('\n').map(function(v) {
          return String(v || '')
            .replace(/[\u00a0\u200b\ufeff\u200e\u200f]/g, ' ')
            .replace(/[\u202a-\u202e\u2066-\u2069]/g, '')
            .replace(/^\s*[-*+•▪◦‣▶▷►]+\s*/, '')
            .replace(/\*\*|__|`/g, '')
            .trim();
        });
        const fullDateKey = Utilities.formatDate(message.getDate(), 'Asia/Seoul', 'yyyy-MM-dd');
        const fullSeen = new Set();
        let fullParsed = 0;

        for (let fi = 0; fi < fullLines.length; fi++) {
          const line = fullLines[fi];
          if (!line) continue;

          const pipe = line.match(/^(\d{6})\s*\|\s*(.{1,80}?)\s*$/);
          if (!pipe) {
            // Once the list has started, a new report/footer heading ends it.
            if (fullParsed > 0 && /^(?:📺|🧾|📝|🔚|Generated\s+by|본\s*리포트는|분석\s*기준\s*:|#{1,6}\s+)/i.test(line)) break;
            continue;
          }

          const code = normalizeCandidateCode_(pipe[1]);
          const name = cleanCandidateName_(pipe[2]);
          if (!code || !name || fullSeen.has(code)) continue;
          addCandidateMention_(byCode, fullSeen, code, name, fullDateKey);
          fullParsed += 1;
          parsedCardCount += 1;
        }

        if (declaredCount > 0 && fullParsed !== declaredCount) {
          throw new Error('FULL_MENTION_COUNT_MISMATCH_declared_' + declaredCount + '_parsed_' + fullParsed);
        }
        return;
      }

      const headingRe = /(?:^|\n)\s*(?:#{1,6}\s*)?(?:📌\s*)?(?:(?:오늘(?:의)?|주요|핵심|최종)\s*)?언급\s*(?:(?:[·ㆍ\/&+]\s*)?주목\s*)?종목(?:\s*(?:목록|리스트))?(?:\s*\([^\n)]{1,80}\))?[^\n]*/gi;
      let headingMatch = null;
      let hm;
      while ((hm = headingRe.exec(body)) !== null) headingMatch = hm;
      if (!headingMatch) return;

      structuredMailCount += 1;
      const section = body.substring(headingMatch.index + headingMatch[0].length);
      const lines = section.split('\n').map(function(v) {
        return String(v || '')
          .replace(/[\u00a0\u200b\ufeff\u200e\u200f]/g, ' ')
          .replace(/[\u202a-\u202e\u2066-\u2069]/g, '')
          .replace(/^\s*[-*+•▪◦‣▶▷►]+\s*/, '')
          .replace(/\*\*|__|`/g, '')
          .trim();
      });
      const dateKey = Utilities.formatDate(message.getDate(), 'Asia/Seoul', 'yyyy-MM-dd');
      const seenThisMessage = new Set();

      for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (!line) continue;
        if (/^(?:📺|🧾|📝|🔚|Generated\s+by|본\s*리포트는|분석\s*기준\s*:)/i.test(line)) break;

        // One-line form: 삼성전기 (009150)
        const same = line.match(/^(.{2,50}?)\s*[\[(]\s*(\d{6})\s*[\])]\s*$/);
        if (same) {
          const sameName = cleanCandidateName_(same[1]);
          const sameCode = normalizeCandidateCode_(same[2]);
          if (sameName && sameCode && !seenThisMessage.has(sameCode)) {
            addCandidateMention_(byCode, seenThisMessage, sameCode, sameName, dateKey);
            parsedCardCount += 1;
          }
          continue;
        }

        // Current vertical form: a six-digit code line identifies the card.
        // Find the closest plausible preceding non-empty line as its company name.
        const compact = line.replace(/\s/g, '');
        const cm = compact.match(/^\(?(\d{6})\)?$/);
        if (!cm) continue;

        const code = normalizeCandidateCode_(cm[1]);
        if (!code || seenThisMessage.has(code)) continue;

        let name = '';
        for (let j = i - 1, looked = 0; j >= 0 && looked < 4; j--) {
          const prior = String(lines[j] || '').trim();
          if (!prior) continue;
          looked += 1;
          if (plausibleCandidateName_(prior)) {
            name = cleanCandidateName_(prior);
            break;
          }
        }
        if (!name) continue;

        addCandidateMention_(byCode, seenThisMessage, code, name, dateKey);
        parsedCardCount += 1;
      }
    });
  });

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
