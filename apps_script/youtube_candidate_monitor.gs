/*
 * YouTube 언급종목 매수감시 - Google Sheets Apps Script
 *
 * 역할
 * 1) CANDIDATES 탭을 5분마다 확인
 * 2) TechStatus가 BUY_CANDIDATE로 진입한 행만 HTTP backend 호출
 * 3) backend가 Kiwoom ka10059 수급 확인 + Gmail 발송
 * 4) 성공한 TriggerKey만 기록하여 같은 날 같은 신호 중복 호출 방지
 *
 * 보안
 * - Kiwoom 키를 시트/Apps Script에 저장하지 않음
 * - Script Properties에는 SIGNAL_API_URL / SIGNAL_API_TOKEN만 저장
 * - 주문 API 없음
 */

const YC = Object.freeze({
  CANDIDATE_SHEET: 'CANDIDATES',
  CONFIG_SHEET: 'CONFIG',
  LOG_SHEET: 'DISPATCH_LOG',
  HEADER_ROW: 1,
  DATA_START_ROW: 2,
  LAST_COLUMN: 26,
  ELIGIBLE_TECH: 'BUY_CANDIDATE',
});

function monitorYouTubeCandidates() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(15000)) return;

  const started = Date.now();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    if (!isMonitorEnabled_(ss)) return;

    const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
    if (!sheet) throw new Error('CANDIDATES sheet not found');

    const lastRow = sheet.getLastRow();
    if (lastRow < YC.DATA_START_ROW) return;

    SpreadsheetApp.flush();
    const range = sheet.getRange(YC.DATA_START_ROW, 1, lastRow - 1, YC.LAST_COLUMN);
    const values = range.getValues();
    const displays = range.getDisplayValues();

    values.forEach((row, idx) => {
      const rowNumber = idx + YC.DATA_START_ROW;
      try {
        processCandidateRow_(ss, sheet, rowNumber, row, displays[idx], started);
      } catch (err) {
        appendDispatchLog_(ss, {
          ticker: String(row[3] || ''),
          name: String(row[1] || ''),
          tech: String(row[18] || ''),
          http: '',
          finalSignal: '',
          flowStatus: '',
          detail: '',
          triggerKey: String(row[19] || ''),
          elapsedMs: Date.now() - started,
          error: String(err && err.message ? err.message : err),
          source: 'APPS_SCRIPT',
        });
      }
    });
  } finally {
    lock.releaseLock();
  }
}

function processCandidateRow_(ss, sheet, rowNumber, row, displayRow, started) {
  const active = String(row[0] || '').trim().toUpperCase();
  const name = String(row[1] || '').trim();
  const market = String(row[2] || '').trim();
  const code = normalizeCode_(displayRow[3] || row[3]);
  const ticker = String(row[4] || '').trim();
  const techStatus = String(row[18] || '').trim().toUpperCase();
  const triggerKey = String(row[19] || '').trim();
  const lastDispatchKey = String(row[20] || '').trim();

  if (active !== 'Y') return;
  if (!code || !ticker) return;
  if (techStatus !== YC.ELIGIBLE_TECH) return;
  if (!triggerKey || triggerKey === lastDispatchKey) return;

  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty('SIGNAL_API_URL') || '').trim();
  const apiToken = String(props.getProperty('SIGNAL_API_TOKEN') || '').trim();
  if (!apiUrl || !apiToken) throw new Error('SIGNAL_API_URL/TOKEN missing in Script Properties');

  const payload = {
    ticker: code,
    name: name || code,
    market: market,
    tech_status: techStatus,
    trigger_key: triggerKey,
    as_of_date: Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd'),
    current_price: numberOrNull_(row[7]),
    reference_low: numberOrNull_(row[8]),
    atr14: numberOrNull_(row[9]),
    ma20: numberOrNull_(row[10]),
    ma60: numberOrNull_(row[11]),
    ma20_slope_5d: numberOrNull_(row[12]),
    rsi14: numberOrNull_(row[13]),
    pullback_ready: row[14] === true,
    buy_trigger_05: numberOrNull_(row[15]),
    confirm_trigger_06: numberOrNull_(row[16]),
  };

  const response = UrlFetchApp.fetch(apiUrl.replace(/\/$/, '') + '/confirm', {
    method: 'post',
    contentType: 'application/json',
    headers: {'X-StockBot-Token': apiToken},
    payload: JSON.stringify(payload),
    muteHttpExceptions: true,
    followRedirects: true,
  });

  const http = response.getResponseCode();
  let body = {};
  try {
    body = JSON.parse(response.getContentText() || '{}');
  } catch (e) {
    body = {ok: false, error: 'INVALID_BACKEND_JSON'};
  }

  if (http >= 200 && http < 300 && body.ok === true) {
    // U: LastDispatchKey, V: LastDispatchAt, W: FinalSignal, X: FlowStatus, Y: FlowSummary
    sheet.getRange(rowNumber, 21, 1, 5).setValues([[
      triggerKey,
      new Date(),
      String(body.final_signal || ''),
      String(body.flow_status || ''),
      String(body.flow_summary || ''),
    ]]);

    appendDispatchLog_(ss, {
      ticker: code,
      name: name,
      tech: techStatus,
      http: http,
      finalSignal: String(body.final_signal || ''),
      flowStatus: String(body.flow_status || ''),
      detail: String(body.flow_summary || ''),
      triggerKey: triggerKey,
      elapsedMs: Date.now() - started,
      error: '',
      source: 'KIWOOM_KA10059',
    });
    return;
  }

  // 실패 시 LastDispatchKey를 쓰지 않는다. 다음 5분 트리거에서 자동 재시도한다.
  appendDispatchLog_(ss, {
    ticker: code,
    name: name,
    tech: techStatus,
    http: http,
    finalSignal: '',
    flowStatus: '',
    detail: '',
    triggerKey: triggerKey,
    elapsedMs: Date.now() - started,
    error: String(body.error || ('HTTP_' + http)),
    source: 'BACKEND_ERROR',
  });
}

function isMonitorEnabled_(ss) {
  const sheet = ss.getSheetByName(YC.CONFIG_SHEET);
  if (!sheet || sheet.getLastRow() < 2) return true;
  const rows = sheet.getRange(2, 1, sheet.getLastRow() - 1, 2).getValues();
  for (let i = 0; i < rows.length; i++) {
    if (String(rows[i][0] || '').trim() === 'MONITOR_ENABLED') {
      const value = rows[i][1];
      return value === true || String(value).toUpperCase() === 'TRUE' || String(value) === '1';
    }
  }
  return true;
}

function appendDispatchLog_(ss, item) {
  const sheet = ss.getSheetByName(YC.LOG_SHEET);
  if (!sheet) return;
  sheet.appendRow([
    new Date(),
    item.ticker || '',
    item.name || '',
    item.tech || '',
    item.http || '',
    item.finalSignal || '',
    item.flowStatus || '',
    item.detail || '',
    item.triggerKey || '',
    item.elapsedMs || '',
    item.error || '',
    item.source || '',
  ]);
}

function normalizeCode_(value) {
  const digits = String(value == null ? '' : value).replace(/\D/g, '');
  return digits ? digits.padStart(6, '0').slice(-6) : '';
}

function numberOrNull_(value) {
  return (typeof value === 'number' && isFinite(value)) ? value : null;
}

/* 최초 1회 실행: 5분 트리거 설치 */
function installFiveMinuteTrigger() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'monitorYouTubeCandidates')
    .forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('monitorYouTubeCandidates')
    .timeBased()
    .everyMinutes(5)
    .create();
}

/* 최초 1회 실행: Cloud Run URL/공유토큰 저장. 토큰은 시트 셀에 쓰지 않는다. */
function setSignalApiConfig(apiUrl, apiToken) {
  if (!apiUrl || !apiToken) throw new Error('apiUrl/apiToken required');
  PropertiesService.getScriptProperties().setProperties({
    SIGNAL_API_URL: String(apiUrl).replace(/\/$/, ''),
    SIGNAL_API_TOKEN: String(apiToken),
  }, false);
}

/* 연결 확인용. 메일/키움 호출은 하지 않는다. */
function checkSignalApiHealth() {
  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty('SIGNAL_API_URL') || '').trim();
  if (!apiUrl) throw new Error('SIGNAL_API_URL missing');
  const response = UrlFetchApp.fetch(apiUrl.replace(/\/$/, '') + '/health', {muteHttpExceptions: true});
  Logger.log(response.getResponseCode() + ' ' + response.getContentText());
}
