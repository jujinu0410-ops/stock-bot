/*
 * YouTube alert post-outcome SHADOW evaluator.
 * - Registers only actually mailed BUY_ALERT / BUY_ALERT_STRONG signals.
 * - Reviews 10m / 30m / 60m MFE and MAE through Cloud Run.
 * - Does not change live signal, mail delivery, portfolio, or orders.
 */

const YC_OUTCOME = Object.freeze({
  SHEET: 'ALERT_OUTCOME',
  MAX_REFRESH_PER_TICK: 4,
  HEADERS: [
    'AlertTime','Ticker','종목명','Signal','TriggerKey','AlertPrice','ATR14',
    '10m_MFE%','10m_MAE%','30m_MFE%','30m_MAE%','60m_MFE%','60m_MAE%',
    'Grade','판정','A도달분','Stop도달분','Status','UpdatedAt'
  ]
});

function ycEnsureOutcomeSheet_(ss) {
  let sheet = ss.getSheetByName(YC_OUTCOME.SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(YC_OUTCOME.SHEET);
  }
  if (sheet.getMaxColumns() < YC_OUTCOME.HEADERS.length) {
    sheet.insertColumnsAfter(sheet.getMaxColumns(), YC_OUTCOME.HEADERS.length - sheet.getMaxColumns());
  }
  const current = sheet.getRange(1, 1, 1, YC_OUTCOME.HEADERS.length).getDisplayValues()[0];
  let mismatch = false;
  for (let i = 0; i < YC_OUTCOME.HEADERS.length; i++) {
    if (String(current[i] || '') !== YC_OUTCOME.HEADERS[i]) {
      mismatch = true;
      break;
    }
  }
  if (mismatch) {
    sheet.getRange(1, 1, 1, YC_OUTCOME.HEADERS.length).setValues([YC_OUTCOME.HEADERS]);
  }
  sheet.setFrozenRows(1);
  return sheet;
}

function ycRegisterAlertOutcome_(ss, item) {
  const sheet = ycEnsureOutcomeSheet_(ss);
  const triggerKey = String(item.triggerKey || '').trim();
  if (!triggerKey) return;

  const lastRow = sheet.getLastRow();
  if (lastRow >= 2) {
    const keys = sheet.getRange(2, 5, lastRow - 1, 1).getDisplayValues();
    for (let i = 0; i < keys.length; i++) {
      if (String(keys[i][0] || '').trim() === triggerKey) return;
    }
  }

  const alertPrice = Number(item.alertPrice);
  const atr14 = Number(item.atr14);
  if (!isFinite(alertPrice) || alertPrice <= 0 || !isFinite(atr14) || atr14 <= 0) return;

  sheet.appendRow([
    item.alertTime instanceof Date ? item.alertTime : new Date(),
    String(item.ticker || ''),
    String(item.name || ''),
    String(item.signal || ''),
    triggerKey,
    alertPrice,
    atr14,
    '', '', '', '', '', '',
    '', '', '', '', 'REGISTERED', new Date()
  ]);
}

function ycRefreshAlertOutcomes_(ss) {
  const sheet = ycEnsureOutcomeSheet_(ss);
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return;

  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty('SIGNAL_API_URL') || '').trim().replace(/\/$/, '');
  const apiToken = String(props.getProperty('SIGNAL_API_TOKEN') || '').trim();
  if (!apiUrl || !apiToken) return;

  const rows = sheet.getRange(2, 1, lastRow - 1, YC_OUTCOME.HEADERS.length).getValues();
  const now = new Date();
  let refreshed = 0;

  for (let i = 0; i < rows.length && refreshed < YC_OUTCOME.MAX_REFRESH_PER_TICK; i++) {
    const row = rows[i];
    const rowNumber = i + 2;
    const alertTime = row[0] instanceof Date ? row[0] : new Date(row[0]);
    if (!(alertTime instanceof Date) || isNaN(alertTime.getTime())) continue;

    const ageMin = (now.getTime() - alertTime.getTime()) / 60000;
    if (ageMin < 10) continue;

    const grade = String(row[13] || '').trim();
    const status = String(row[17] || '').trim();
    if (grade || status === 'EVALUATED_60M') continue;

    const has10 = row[7] !== '' && row[7] != null;
    const has30 = row[9] !== '' && row[9] != null;
    const has60 = row[11] !== '' && row[11] != null;
    if (!has10 && ageMin < 10) continue;
    if (has10 && !has30 && ageMin < 30) continue;
    if (has30 && !has60 && ageMin < 60) continue;

    const payload = {
      ticker: normalizeCode_(row[1]),
      alert_time: alertTime.toISOString(),
      alert_price: Number(row[5]),
      atr14: Number(row[6])
    };
    if (!payload.ticker || !isFinite(payload.alert_price) || !isFinite(payload.atr14)) continue;

    let body = {};
    let http = 0;
    try {
      const response = UrlFetchApp.fetch(apiUrl + '/alert-outcome', {
        method: 'post',
        contentType: 'application/json',
        headers: {'X-StockBot-Token': apiToken},
        payload: JSON.stringify(payload),
        muteHttpExceptions: true,
        followRedirects: true,
      });
      http = response.getResponseCode();
      try { body = JSON.parse(response.getContentText() || '{}'); }
      catch (e) { body = {ok:false, error:'INVALID_JSON'}; }
    } catch (err) {
      body = {ok:false, error:String(err && err.message ? err.message : err)};
    }

    if (!(http >= 200 && http < 300 && body.ok === true)) {
      sheet.getRange(rowNumber, 18, 1, 2).setValues([[
        'ERROR:' + String(body.error || ('HTTP_' + http)),
        new Date()
      ]]);
      refreshed++;
      continue;
    }

    const h = body.horizons || {};
    const h10 = h['10m'] || null;
    const h30 = h['30m'] || null;
    const h60 = h['60m'] || null;
    const hits = body.first_hit_min || {};

    const updated = [
      h10 ? h10.mfe_pct : row[7],
      h10 ? h10.mae_pct : row[8],
      h30 ? h30.mfe_pct : row[9],
      h30 ? h30.mae_pct : row[10],
      h60 ? h60.mfe_pct : row[11],
      h60 ? h60.mae_pct : row[12],
      body.grade || row[13] || '',
      body.label || row[14] || '',
      hits.plus_3pct_or_0_6atr == null ? row[15] : hits.plus_3pct_or_0_6atr,
      hits.stop_risk_minus_2_5pct_or_0_8atr == null ? row[16] : hits.stop_risk_minus_2_5pct_or_0_8atr,
      String(body.status || 'UNKNOWN'),
      new Date()
    ];
    sheet.getRange(rowNumber, 8, 1, 12).setValues([updated]);
    refreshed++;
  }
}

function manualAlertOutcomeRefreshV1() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  ycRefreshAlertOutcomes_(ss);
}
