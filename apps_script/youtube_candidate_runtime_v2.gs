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
    if (lastRow < YC.DATA_START_ROW) return;

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
