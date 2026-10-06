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
  JEV_URL: 'https://api.typesafe.ai/v1/systemone',
  JEV_MODEL: 'jev-latest',
  JEV_PRE_HOLD_THRESHOLD: 0.35,
  JEV_PRE_MAX_URGENCY_FOR_HOLD: 1,
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

  const preGate = ycJevPreGate_(payload);
  if (preGate.decision === 'HOLD') {
    // PRE-GATE는 명백히 약한 BUY_CANDIDATE만 Cloud Run 전에 종료한다.
    // 기존 TriggerKey를 기록해 같은 날 같은 후보를 반복 호출하지 않는다.
    sheet.getRange(rowNumber, 21, 1, 5).setValues([[
      triggerKey,
      new Date(),
      'WATCH_ONLY',
      'JEV_PRE_HOLD',
      ycJevPreGateSummary_(preGate),
    ]]);
    appendDispatchLog_(ss, {
      ticker: code,
      name: name,
      tech: techStatus,
      http: '',
      finalSignal: 'WATCH_ONLY',
      flowStatus: 'JEV_PRE_HOLD',
      detail: ycJevPreGateSummary_(preGate),
      triggerKey: triggerKey,
      elapsedMs: Date.now() - started,
      error: '',
      source: 'JEV_PRE_GATE',
    });
    return;
  }
  payload.jev_pre_gate = preGate;

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


function ycJevPreGate_(payload) {
  const props = PropertiesService.getScriptProperties();
  const apiKey = String(props.getProperty('JEV_API_KEY') || '').trim();
  const base = {
    status: 'NOT_CONFIGURED',
    mode: 'PRE_GATE',
    decision: 'PROCEED',
    reason: 'JEV_API_KEY_MISSING',
    needs_final_review: true,
    answers: {}
  };
  if (!apiKey) return base;

  const requestBody = {
    model: YC.JEV_MODEL,
    state: {
      source: 'YOUTUBE',
      as_of: String(payload.as_of_date || ''),
      ticker: String(payload.ticker || ''),
      name: String(payload.name || ''),
      existing_rule_candidate: true,
      event_keys: ['YOUTUBE_BUY_CANDIDATE'],
      event_level: 'WATCH',
      technical: {
        current_price: payload.current_price,
        reference_low: payload.reference_low,
        atr14: payload.atr14,
        ma20: payload.ma20,
        ma60: payload.ma60,
        ma20_slope_5d: payload.ma20_slope_5d,
        rsi14: payload.rsi14,
        pullback_ready: payload.pullback_ready,
        buy_trigger_05: payload.buy_trigger_05,
        confirm_trigger_06: payload.confirm_trigger_06
      }
    },
    questions: {
      signal_direction: {
        type: 'choice',
        instructions: 'Classify the supplied BUY_CANDIDATE evidence only. Do not infer missing flow, news, disclosure, or intraday evidence.',
        criteria: {
          UP: 'Supplied technical evidence is predominantly constructive.',
          DOWN: 'Supplied technical evidence is predominantly deteriorating.',
          NEUTRAL: 'Evidence is mixed or insufficient.'
        }
      },
      signal_stage: {
        type: 'choice',
        instructions: 'Rate only whether this candidate merits further validation, not whether to place an order.',
        criteria: {
          NONE: 'No credible upward setup from the supplied evidence.',
          DEVELOPING: 'Early or incomplete setup; evidence is limited.',
          BUY_WATCH: 'Enough evidence to justify further validation.',
          BUY_CONFIRMED: 'Strong supplied evidence; further validation is clearly worthwhile.'
        }
      },
      alert_urgency: {
        type: 'score',
        instructions: 'How urgently should this candidate proceed to the more expensive validation pipeline?',
        criteria: [
          '0: no material reason to continue now.',
          '1: weak/noisy and can wait.',
          '2: useful candidate worth validating.',
          '3: timely validation is warranted.',
          '4: unusually strong/time-sensitive candidate.'
        ]
      },
      send_alert_now: {
        type: 'noul',
        instructions: 'Probability that this existing BUY_CANDIDATE is worth continuing to the expensive validation pipeline now. Under uncertainty, prefer continuing rather than rejecting.'
      }
    }
  };

  try {
    const res = UrlFetchApp.fetch(YC.JEV_URL, {
      method: 'post',
      contentType: 'application/json',
      headers: {Authorization: 'Bearer ' + apiKey},
      payload: JSON.stringify(requestBody),
      muteHttpExceptions: true,
      followRedirects: true
    });
    const http = res.getResponseCode();
    if (http < 200 || http >= 300) {
      return Object.assign({}, base, {status: 'ERROR', reason: 'JEV_HTTP_' + http});
    }
    let body = {};
    try { body = JSON.parse(res.getContentText() || '{}'); } catch (e) {
      return Object.assign({}, base, {status: 'ERROR', reason: 'JEV_INVALID_JSON'});
    }
    const answers = body && body.answers ? body.answers : {};
    const stage = ycJevChoice_(answers.signal_stage, 'UNKNOWN');
    const direction = ycJevChoice_(answers.signal_direction, 'UNKNOWN');
    const urgency = ycJevNumber_(answers.alert_urgency, 'score', 0);
    const probability = ycJevNumber_(answers.send_alert_now, 'noul', 1);

    // Conservative fail-open PRE-GATE:
    // only clearly weak NONE/DEVELOPING + low urgency + low probability is rejected.
    const weakStage = stage === 'NONE' || stage === 'DEVELOPING';
    const hold = weakStage &&
      urgency <= YC.JEV_PRE_MAX_URGENCY_FOR_HOLD &&
      probability < YC.JEV_PRE_HOLD_THRESHOLD;

    // Strong pre-gate evidence does not need another Jev call after expensive validation.
    // Middling/uncertain cases may still use the existing final Jev gate.
    const decisiveProceed = !hold &&
      (stage === 'BUY_WATCH' || stage === 'BUY_CONFIRMED') &&
      urgency >= 2 &&
      probability >= 0.55;

    return {
      status: 'OK',
      mode: 'PRE_GATE',
      decision: hold ? 'HOLD' : 'PROCEED',
      reason: hold ? 'CLEARLY_WEAK_PRE_GATE' : (decisiveProceed ? 'DECISIVE_PROCEED' : 'PROCEED_UNCERTAIN'),
      needs_final_review: !decisiveProceed,
      model: String(body.model || YC.JEV_MODEL),
      answers: answers,
      summary: {
        direction: direction,
        stage: stage,
        urgency: urgency,
        probability: probability
      }
    };
  } catch (err) {
    return Object.assign({}, base, {
      status: 'ERROR',
      reason: 'JEV_TRANSPORT:' + String(err && err.message ? err.message : err)
    });
  }
}

function ycJevChoice_(answer, fallback) {
  return String(answer && answer.choice ? answer.choice : (fallback || 'UNKNOWN')).toUpperCase();
}

function ycJevNumber_(answer, field, fallback) {
  const value = answer && answer[field] != null ? Number(answer[field]) : Number(fallback || 0);
  return isFinite(value) ? value : Number(fallback || 0);
}

function ycJevPreGateSummary_(gate) {
  const s = gate && gate.summary ? gate.summary : {};
  return 'Jev PRE ' + String(gate && gate.decision || 'PROCEED') +
    ' · ' + String(s.direction || 'UNKNOWN') +
    ' / ' + String(s.stage || 'UNKNOWN') +
    ' / urgency ' + String(s.urgency == null ? '-' : s.urgency) +
    ' / proceed ' + Math.round(Number(s.probability == null ? 1 : s.probability) * 100) + '%' +
    ' · ' + String(gate && gate.reason || '');
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
