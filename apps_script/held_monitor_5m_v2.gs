/**
 * Held Position 5-Minute Monitor v2
 * 보유종목_INTRADAY_GF_MONITOR용
 * v2: 의미있는 경보 시 기존 Cloud Run /held-context로 Kiwoom 수급 + DART + 뉴스 보강
 *
 * - 평일 09:00~15:30, 5분 감시
 * - 같은 상태 반복메일 금지, 의미 있는 상태전이만 경보
 * - VWAP9/26, OBV/OBV9의 방향을 우선하고 크로스는 보조
 * - CLOSED 행은 제거하고 현재 보유종목 슬롯만 재사용
 * - ALERT_STATE는 현재 보유종목만 유지
 * - ALERT_LOG는 최근 90일 / 최대 2,000건 유지
 * - 자동주문 없음
 */

const HM = Object.freeze({
  SPREADSHEET_ID: '15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw',
  TZ: 'Asia/Seoul',
  CONFIG: 'MONITOR_CONFIG',
  LIVE: 'LIVE_QUOTES',
  REGIME: 'REGIME',
  STATE: 'ALERT_STATE',
  LOG: 'ALERT_LOG',

  CONFIG_COLS: 20,
  LIVE_COLS: 13,
  REGIME_COLS: 20,
  STATE_COLS: 22,
  LOG_COLS: 11,

  MARKET_START: 900,
  MARKET_END: 1530,
  LOG_RETENTION_DAYS: 90,
  LOG_MAX_ROWS: 2000,

  PROP_ALERT_EMAIL: 'HELD_ALERT_EMAIL',
  PROP_LAST_LOG_CLEANUP: 'HELD_LAST_LOG_CLEANUP',
  PROP_API_URL: 'SIGNAL_API_URL',
  PROP_API_TOKEN: 'SIGNAL_API_TOKEN'
});

function hmSetAlertEmail(email) {
  const value = String(email || '').trim();
  if (!value || value.indexOf('@') < 1) throw new Error('유효한 이메일 주소가 필요합니다.');
  PropertiesService.getScriptProperties().setProperty(HM.PROP_ALERT_EMAIL, value);
  return value;
}

/**
 * YouTube 매수감시가 이미 쓰는 동일 Cloud Run URL/토큰을 이 프로젝트에도 1회 저장한다.
 * Apps Script 프로젝트별 Script Properties는 서로 공유되지 않으므로 최초 1회 복사가 필요하다.
 */
function hmSetContextApiConfig(apiUrl, apiToken) {
  const url = String(apiUrl || '').trim().replace(/\/$/, '');
  const token = String(apiToken || '').trim();
  if (!url || !/^https:\/\//i.test(url)) throw new Error('유효한 HTTPS Cloud Run URL이 필요합니다.');
  if (!token) throw new Error('SIGNAL_API_TOKEN이 필요합니다.');
  PropertiesService.getScriptProperties().setProperties({
    SIGNAL_API_URL: url,
    SIGNAL_API_TOKEN: token
  }, false);
  return 'OK';
}

function hmCheckContextApiHealth() {
  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty(HM.PROP_API_URL) || '').trim().replace(/\/$/, '');
  if (!apiUrl) throw new Error('SIGNAL_API_URL이 없습니다.');

  const res = UrlFetchApp.fetch(apiUrl + '/health', {
    method: 'get',
    muteHttpExceptions: true
  });
  const text = res.getResponseCode() + ' ' + res.getContentText();
  SpreadsheetApp.getUi().alert('Cloud Run 상태', text, SpreadsheetApp.getUi().ButtonSet.OK);
  return text;
}

function hmInstall5mTrigger() {
  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (t.getHandlerFunction() === 'hmTick5m') ScriptApp.deleteTrigger(t);
  });
  ScriptApp.newTrigger('hmTick5m').timeBased().everyMinutes(5).create();
  SpreadsheetApp.getUi().alert(
    '설치 완료',
    '평일 09:00~15:30에 5분마다 확인합니다.\n같은 상태를 반복 메일로 보내지 않고 의미 있는 상태 변화만 알립니다.\n자동주문은 하지 않습니다.',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

function hmRunNow() {
  return hmEvaluate_(false);
}

function hmTick5m() {
  return hmEvaluate_(true);
}

function hmCompactNow() {
  const ss = SpreadsheetApp.openById(HM.SPREADSHEET_ID);
  const result = hmCompactMonitorConfig_(ss);
  SpreadsheetApp.getUi().alert(
    '현재종목 정리',
    '현재 감시종목 ' + result.count + '개 / 과거 CLOSED 행 제거 ' + (result.changed ? '완료' : '불필요(이미 정리됨)'),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
  return result;
}

function hmCleanupLogNow() {
  const ss = SpreadsheetApp.openById(HM.SPREADSHEET_ID);
  const result = hmCleanupAlertLog_(ss, true);
  SpreadsheetApp.getUi().alert(
    '로그 정리',
    '유지 로그 ' + result.kept + '건 / 삭제 ' + result.removed + '건',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
  return result;
}

function hmEvaluate_(enforceMarketWindow) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) return {status: 'LOCKED_SKIP'};

  try {
    const now = new Date();
    if (enforceMarketWindow && !hmIsMarketWindow_(now)) {
      return {status: 'OUTSIDE_MARKET_WINDOW'};
    }

    const ss = SpreadsheetApp.openById(HM.SPREADSHEET_ID);
    const compact = hmCompactMonitorConfig_(ss);
    if (compact.changed) {
      SpreadsheetApp.flush();
      Utilities.sleep(2500);
    }

    const configSheet = ss.getSheetByName(HM.CONFIG);
    const liveSheet = ss.getSheetByName(HM.LIVE);
    const regimeSheet = ss.getSheetByName(HM.REGIME);
    if (!configSheet || !liveSheet || !regimeSheet) {
      throw new Error('필수 시트(MONITOR_CONFIG/LIVE_QUOTES/REGIME)를 찾을 수 없습니다.');
    }

    const count = compact.count;
    if (count === 0) {
      hmRewriteState_(ss, []);
      hmMaybeCleanupLog_(ss, now);
      return {status: 'NO_CURRENT_HOLDINGS'};
    }

    const configs = configSheet.getRange(2, 1, count, HM.CONFIG_COLS).getValues();
    const live = liveSheet.getRange(2, 1, count, HM.LIVE_COLS).getValues();
    const regime = regimeSheet.getRange(2, 1, count, HM.REGIME_COLS).getValues();
    const prevMap = hmLoadState_(ss);

    const nextStates = [];
    const logRows = [];
    let emailsSent = 0;

    for (let i = 0; i < count; i++) {
      const cur = hmBuildSnapshot_(configs[i], live[i], regime[i], now);
      if (!cur.ticker) continue;

      const prev = prevMap[cur.ticker] || null;
      const events = hmDetectEvents_(prev, cur);

      if (!prev && cur.dataQuality === 'VALID' && cur.stopBroken) {
        events.push(hmEvent_(
          'STOP_BREACH_INITIAL', 'RISK',
          '손절선 아래에서 감시 시작',
          '현재가가 설정 손절선 아래입니다. 보유전략을 즉시 확인하십시오.'
        ));
      }

      let sentKey = prev ? (prev.lastAlertKey || '') : '';
      let sentAt = prev ? (prev.lastAlertAt || '') : '';

      if (events.length > 0) {
        const key = hmAlertKey_(events);
        const recipient = hmAlertRecipient_();
        const marketContext = hmFetchMarketContext_(cur, events, now);

        hmSendAlertMail_(recipient, cur, events, marketContext);
        emailsSent++;
        sentKey = key;
        sentAt = now;

        const contextLabel = marketContext && marketContext.catalyst
          ? String(marketContext.catalyst.label || '')
          : '';

        logRows.push([
          now,
          cur.ticker,
          cur.name,
          key,
          hmStrongestLevel_(events),
          cur.price == null ? '' : cur.price,
          cur.changePct == null ? '' : cur.changePct,
          cur.stopPrice == null ? '' : cur.stopPrice,
          cur.volumePace == null ? '' : cur.volumePace,
          cur.tradeTime || '',
          events.map(function(e) { return e.title + ': ' + e.message; }).join(' | ') +
            (contextLabel ? ' | 촉매: ' + contextLabel : '')
        ]);
      }

      cur.lastAlertKey = sentKey;
      cur.lastAlertAt = sentAt;
      nextStates.push(cur);
    }

    if (logRows.length) hmAppendLog_(ss, logRows);
    hmRewriteState_(ss, nextStates);
    hmMaybeCleanupLog_(ss, now);

    return {
      status: 'OK',
      holdings: nextStates.length,
      emailsSent: emailsSent,
      compacted: compact.changed
    };
  } finally {
    lock.releaseLock();
  }
}

function hmCompactMonitorConfig_(ss) {
  const sh = ss.getSheetByName(HM.CONFIG);
  if (!sh) throw new Error(HM.CONFIG + ' 시트를 찾을 수 없습니다.');
  const lastRow = Math.max(sh.getLastRow(), 1);
  const rowCount = Math.max(lastRow - 1, 0);
  if (rowCount === 0) return {count: 0, changed: false};

  const rows = sh.getRange(2, 1, rowCount, HM.CONFIG_COLS).getValues();
  const currentByTicker = new Map();
  rows.forEach(function(row) {
    const ticker = hmTicker_(row[0]);
    const qty = hmNum_(row[3]) || 0;
    const status = String(row[7] || '').trim().toUpperCase();
    const enabled = hmBool_(row[9]);
    if (!ticker || !enabled || qty <= 0 || status === 'CLOSED') return;
    currentByTicker.set(ticker, row.slice(0, HM.CONFIG_COLS));
  });

  const current = Array.from(currentByTicker.values());
  const existingNonBlank = rows.filter(function(row) {
    return row.some(function(v) { return v !== '' && v !== null; });
  });
  const changed = existingNonBlank.length !== current.length || !hmMatrixSame_(existingNonBlank.slice(0, current.length), current);

  if (changed) {
    sh.getRange(2, 1, rowCount, HM.CONFIG_COLS).clearContent();
    if (current.length) sh.getRange(2, 1, current.length, HM.CONFIG_COLS).setValues(current);
  }
  return {count: current.length, changed: changed};
}

function hmMatrixSame_(a, b) {
  if (a.length !== b.length) return false;
  for (let r = 0; r < a.length; r++) {
    for (let c = 0; c < HM.CONFIG_COLS; c++) {
      const av = a[r][c];
      const bv = b[r][c];
      if (av instanceof Date && bv instanceof Date) {
        if (av.getTime() !== bv.getTime()) return false;
      } else if (String(av == null ? '' : av) !== String(bv == null ? '' : bv)) {
        return false;
      }
    }
  }
  return true;
}

function hmBuildSnapshot_(cfg, live, reg, now) {
  const ticker = hmTicker_(cfg[0]);
  const name = String(cfg[2] || '').trim();
  const status = String(cfg[7] || '').trim().toUpperCase();

  const price = hmNum_(live[3]);
  const changePct = hmNum_(live[5]);
  const volumePace = hmNum_(live[12]);
  const tradeTimeRaw = live[10];
  const tradeDate = hmAsDate_(tradeTimeRaw);
  const refClose = hmNum_(cfg[5]);
  const stopPrice = hmNum_(cfg[6]);
  const atr14 = hmNum_(cfg[16]);

  let dataQuality = String(reg[19] || '').trim().toUpperCase();
  if (status === 'SUSPENDED_HOLD') dataQuality = 'SUSPENDED';
  else if (!dataQuality) dataQuality = (price != null && price > 0) ? 'VALID' : 'DATA_REVIEW';

  if (dataQuality === 'VALID') {
    if (!tradeDate || hmDateKey_(tradeDate) !== hmDateKey_(now)) dataQuality = 'STALE';
  }

  return {
    ticker: ticker,
    name: name,
    price: price,
    changePct: changePct,
    refClose: refClose,
    atr14: atr14,
    stopPrice: stopPrice,
    atrBand: hmAtrBand_(price, refClose, atr14),
    stopBroken: !!(price != null && stopPrice != null && stopPrice > 0 && price <= stopPrice),
    tradeTime: tradeDate || tradeTimeRaw || '',
    volumePace: volumePace,
    dataQuality: dataQuality,
    vwap9Dir: String(reg[5] || 'UNKNOWN').trim().toUpperCase(),
    vwap26Dir: String(reg[7] || 'UNKNOWN').trim().toUpperCase(),
    vwapRel: String(reg[8] || 'UNKNOWN').trim().toUpperCase(),
    vwapCross: String(reg[9] || 'NONE').trim().toUpperCase(),
    obvDir: String(reg[11] || 'UNKNOWN').trim().toUpperCase(),
    obv9Dir: String(reg[13] || 'UNKNOWN').trim().toUpperCase(),
    obvRel: String(reg[14] || 'UNKNOWN').trim().toUpperCase(),
    obvCross: String(reg[15] || 'NONE').trim().toUpperCase(),
    regime: String(reg[16] || 'UNKNOWN').trim().toUpperCase(),
    regimeText: String(reg[17] || '').trim(),
    regimeAsOf: hmAsDate_(reg[18]) || reg[18] || '',
    lastAlertKey: '',
    lastAlertAt: ''
  };
}

function hmAtrBand_(price, refClose, atr14) {
  if (price == null || refClose == null || atr14 == null || refClose <= 0 || atr14 <= 0) return 'ATR_DATA_MISSING';
  const move = (price - refClose) / atr14;
  if (move >= 1.0) return 'UP_1_0';
  if (move >= 0.5) return 'UP_0_5';
  if (move <= -1.0) return 'DOWN_1_0';
  if (move <= -0.5) return 'DOWN_0_5';
  return 'NORMAL';
}

function hmDetectEvents_(prev, cur) {
  if (!prev) return [];
  const events = [];

  if (prev.dataQuality !== cur.dataQuality) {
    if (cur.dataQuality === 'SUSPENDED') events.push(hmEvent_('DATA_SUSPENDED', 'RISK', '거래정지 상태', '장중 기술계산을 중지합니다.'));
    else if (cur.dataQuality !== 'VALID') events.push(hmEvent_('DATA_' + cur.dataQuality, 'WATCH', '장중 데이터 점검', prev.dataQuality + ' → ' + cur.dataQuality));
    else if (prev.dataQuality !== 'VALID') events.push(hmEvent_('DATA_RECOVERED', 'INFO', '장중 데이터 정상화', prev.dataQuality + ' → VALID'));
  }
  if (cur.dataQuality !== 'VALID') return events;

  if (!prev.stopBroken && cur.stopBroken) events.push(hmEvent_('STOP_BREACH', 'RISK', '손절선 최초 침범', '설정 손절선을 하향 이탈했습니다.'));
  else if (prev.stopBroken && !cur.stopBroken) events.push(hmEvent_('STOP_RECOVER', 'INFO', '손절선 재회복', '가격이 손절선 위로 회복했습니다.'));

  if (prev.atrBand !== cur.atrBand) {
    if (cur.atrBand === 'UP_1_0') events.push(hmEvent_('ATR_UP_1_0', 'STRONG', '+1.0 ATR 강한 상승', '가격 변화 강도가 한 단계 상승했습니다.'));
    if (cur.atrBand === 'UP_0_5') events.push(hmEvent_('ATR_UP_0_5', 'WATCH', '+0.5 ATR 상승', '의미 있는 상승 구간에 진입했습니다.'));
    if (cur.atrBand === 'DOWN_1_0') events.push(hmEvent_('ATR_DOWN_1_0', 'RISK', '-1.0 ATR 강한 하락', '가격 훼손 여부를 확인하십시오.'));
    if (cur.atrBand === 'DOWN_0_5') events.push(hmEvent_('ATR_DOWN_0_5', 'WATCH', '-0.5 ATR 하락', '의미 있는 하락 구간에 진입했습니다.'));
  }

  hmMaybeTurn_(events, prev.vwap26Dir, cur.vwap26Dir, 'VWAP26_TURN_UP', 'VWAP26_TURN_DOWN', 'VWAP26 상승 전환', 'VWAP26 하락 전환', 'STRONG', 'RISK');
  hmMaybeTurn_(events, prev.obv9Dir, cur.obv9Dir, 'OBV9_TURN_UP', 'OBV9_TURN_DOWN', 'OBV 기준선 상승 전환', 'OBV 기준선 하락 전환', 'STRONG', 'RISK');
  hmMaybeTurn_(events, prev.vwap9Dir, cur.vwap9Dir, 'VWAP9_TURN_UP', 'VWAP9_TURN_DOWN', 'VWAP9 머리 들기', 'VWAP9 머리 숙임', 'WATCH', 'WATCH');
  hmMaybeTurn_(events, prev.obvDir, cur.obvDir, 'OBV_TURN_UP', 'OBV_TURN_DOWN', 'OBV 상승 전환', 'OBV 하락 전환', 'WATCH', 'WATCH');

  if (cur.vwapCross === 'GOLD_CROSS' && prev.vwapCross !== 'GOLD_CROSS') {
    const aligned = cur.vwap26Dir === 'RISING';
    events.push(hmEvent_('VWAP_GOLD_CROSS', aligned ? 'WATCH' : 'INFO', 'VWAP 골드크로스', aligned ? 'VWAP26 상승과 정렬됨' : 'VWAP26이 상승하지 않아 반등 신호로만 봅니다.'));
  } else if (cur.vwapCross === 'DEAD_CROSS' && prev.vwapCross !== 'DEAD_CROSS') {
    const aligned = cur.vwap26Dir === 'FALLING';
    events.push(hmEvent_('VWAP_DEAD_CROSS', aligned ? 'RISK' : 'INFO', 'VWAP 데드크로스', aligned ? 'VWAP26 하락과 정렬됨' : '장기선 하락 확인 전에는 단기 약화로 봅니다.'));
  }

  if (cur.obvCross === 'GOLD_CROSS' && prev.obvCross !== 'GOLD_CROSS') {
    const aligned = cur.obv9Dir === 'RISING';
    events.push(hmEvent_('OBV_GOLD_CROSS', aligned ? 'WATCH' : 'INFO', 'OBV 골드크로스', aligned ? 'OBV9 기준선 상승과 정렬됨' : 'OBV9 기준선 상승 전환은 아직 확인되지 않았습니다.'));
  } else if (cur.obvCross === 'DEAD_CROSS' && prev.obvCross !== 'DEAD_CROSS') {
    const aligned = cur.obv9Dir === 'FALLING';
    events.push(hmEvent_('OBV_DEAD_CROSS', aligned ? 'RISK' : 'INFO', 'OBV 데드크로스', aligned ? 'OBV9 기준선 하락과 정렬됨' : '기준선 하락 확인 전에는 단기 수급 약화로 봅니다.'));
  }

  if (prev.regime !== cur.regime) {
    switch (cur.regime) {
      case 'BULL_CONFIRMED': events.push(hmEvent_('REGIME_BULL_CONFIRMED', 'STRONG', '상승국면 확인', 'VWAP 장단기선과 OBV/기준선이 모두 상승 정렬되었습니다.')); break;
      case 'BEAR_CONFIRMED': events.push(hmEvent_('REGIME_BEAR_CONFIRMED', 'RISK', '약세국면 확인', 'VWAP 장단기선과 OBV/기준선이 모두 하락 정렬되었습니다.')); break;
      case 'PULLBACK_IN_UPREGIME': events.push(hmEvent_('REGIME_PULLBACK', 'INFO', '상승국면 내 눌림', 'VWAP26 상승을 유지한 채 VWAP9가 조정 중입니다.')); break;
      case 'BOUNCE_IN_DOWNREGIME': events.push(hmEvent_('REGIME_BOUNCE_DOWN', 'INFO', '하락국면 내 반등', 'VWAP9는 상승하지만 VWAP26 하락이 계속됩니다.')); break;
      case 'EARLY_IMPROVEMENT': events.push(hmEvent_('REGIME_EARLY_IMPROVEMENT', 'WATCH', '초기 개선', '단기선/OBV 개선이 시작됐으나 장기·기준선 확인이 더 필요합니다.')); break;
      case 'EARLY_WEAKENING': events.push(hmEvent_('REGIME_EARLY_WEAKENING', 'WATCH', '초기 약화', '단기선/OBV 약화가 시작돼 장기선 훼손 여부를 확인해야 합니다.')); break;
    }
  }
  return events;
}

function hmMaybeTurn_(events, before, after, upKey, downKey, upTitle, downTitle, upLevel, downLevel) {
  if (before === after) return;
  if (after === 'RISING' && (before === 'FALLING' || before === 'FLAT')) events.push(hmEvent_(upKey, upLevel, upTitle, before + ' → RISING'));
  else if (after === 'FALLING' && (before === 'RISING' || before === 'FLAT')) events.push(hmEvent_(downKey, downLevel, downTitle, before + ' → FALLING'));
}

function hmEvent_(key, level, title, message) { return {key: key, level: level, title: title, message: message}; }

function hmStrongestLevel_(events) {
  const rank = {INFO: 1, WATCH: 2, STRONG: 3, RISK: 4};
  let best = 'INFO';
  events.forEach(function(e) { if ((rank[e.level] || 0) > (rank[best] || 0)) best = e.level; });
  return best;
}

function hmAlertKey_(events) { return events.map(function(e) { return e.key; }).sort().join('|'); }

function hmLoadState_(ss) {
  const sh = ss.getSheetByName(HM.STATE);
  if (!sh) throw new Error(HM.STATE + ' 시트를 찾을 수 없습니다.');
  const lastRow = sh.getLastRow();
  if (lastRow < 2) return {};
  const rows = sh.getRange(2, 1, lastRow - 1, HM.STATE_COLS).getValues();
  const map = {};
  rows.forEach(function(r) {
    const ticker = hmTicker_(r[0]);
    if (!ticker) return;
    map[ticker] = {
      ticker: ticker,
      stateDate: r[1],
      price: hmNum_(r[2]),
      atrBand: String(r[3] || ''),
      stopBroken: hmBool_(r[4]),
      tradeTime: r[6],
      lastAlertKey: String(r[17] || ''),
      dataQuality: String(r[18] || ''),
      vwap9Dir: String(r[9] || ''),
      vwap26Dir: String(r[10] || ''),
      vwapRel: String(r[11] || ''),
      obvDir: String(r[12] || ''),
      obv9Dir: String(r[13] || ''),
      obvRel: String(r[14] || ''),
      regime: String(r[15] || ''),
      regimeAsOf: r[16],
      vwapCross: String(r[19] || ''),
      obvCross: String(r[20] || ''),
      lastAlertAt: r[21] || ''
    };
  });
  return map;
}

function hmRewriteState_(ss, states) {
  const sh = ss.getSheetByName(HM.STATE);
  if (!sh) throw new Error(HM.STATE + ' 시트를 찾을 수 없습니다.');
  const now = new Date();
  const today = hmDateKey_(now);
  const rows = states.map(function(s) {
    return [
      s.ticker, today, s.price == null ? '' : s.price, s.atrBand || '', !!s.stopBroken, false,
      s.tradeTime || '', '{}', now, s.vwap9Dir || '', s.vwap26Dir || '', s.vwapRel || '',
      s.obvDir || '', s.obv9Dir || '', s.obvRel || '', s.regime || '', s.regimeAsOf || '',
      s.lastAlertKey || '', s.dataQuality || '', s.vwapCross || '', s.obvCross || '', s.lastAlertAt || ''
    ];
  });
  const lastRow = Math.max(sh.getLastRow(), 1);
  if (lastRow >= 2) sh.getRange(2, 1, lastRow - 1, HM.STATE_COLS).clearContent();
  if (rows.length) sh.getRange(2, 1, rows.length, HM.STATE_COLS).setValues(rows);
}

function hmFetchMarketContext_(cur, events, now) {
  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty(HM.PROP_API_URL) || '').trim().replace(/\/$/, '');
  const apiToken = String(props.getProperty(HM.PROP_API_TOKEN) || '').trim();

  if (!apiUrl || !apiToken) {
    return {
      status: 'NOT_CONFIGURED',
      catalyst: {label: '원인분석 미설정', confidence: 'NONE', reason: 'Cloud Run URL/토큰 미설정'},
      flow: {status: 'UNAVAILABLE', summary: '수급 확인 미설정'},
      disclosures: [], news: []
    };
  }

  const payload = {
    ticker: cur.ticker,
    name: cur.name,
    as_of_date: hmDateKey_(now),
    event_key: hmAlertKey_(events),
    event_level: hmStrongestLevel_(events),
    current_price: cur.price,
    change_pct: cur.changePct,
    atr_band: cur.atrBand,
    stop_broken: cur.stopBroken,
    vwap9_dir: cur.vwap9Dir,
    vwap26_dir: cur.vwap26Dir,
    vwap_rel: cur.vwapRel,
    obv_dir: cur.obvDir,
    obv9_dir: cur.obv9Dir,
    obv_rel: cur.obvRel,
    regime: cur.regime
  };

  try {
    const res = UrlFetchApp.fetch(apiUrl + '/held-context', {
      method: 'post', contentType: 'application/json',
      headers: {'X-StockBot-Token': apiToken},
      payload: JSON.stringify(payload), muteHttpExceptions: true, followRedirects: true
    });
    const http = res.getResponseCode();
    let body = {};
    try { body = JSON.parse(res.getContentText() || '{}'); } catch (e) { body = {}; }
    if (http >= 200 && http < 300 && body.ok === true && body.market_context) {
      body.market_context.status = 'OK';
      return body.market_context;
    }
    return {
      status: 'ERROR',
      catalyst: {label: '원인분석 일시 실패', confidence: 'NONE', reason: 'Cloud Run HTTP ' + http + (body.error ? ' · ' + body.error : '')},
      flow: {status: 'UNAVAILABLE', summary: '수급 확인 실패'}, disclosures: [], news: []
    };
  } catch (err) {
    return {
      status: 'ERROR',
      catalyst: {label: '원인분석 일시 실패', confidence: 'NONE', reason: String(err && err.message ? err.message : err)},
      flow: {status: 'UNAVAILABLE', summary: '수급 확인 실패'}, disclosures: [], news: []
    };
  }
}

function hmContextLines_(marketContext) {
  const c = marketContext || {};
  const catalyst = c.catalyst || {};
  const flow = c.flow || {};
  const disclosures = Array.isArray(c.disclosures) ? c.disclosures : [];
  const news = Array.isArray(c.news) ? c.news : [];

  const lines = [
    '[수급·공시·뉴스 원인확인]',
    '촉매판정: ' + String(catalyst.label || '확인된 촉매 없음'),
    String(catalyst.reason || ''),
    '수급: ' + String(flow.status || 'UNAVAILABLE') +
      ' / 외국인5일 ' + hmSignedNum_(flow.foreign_5d) +
      ' / 기관5일 ' + hmSignedNum_(flow.institution_5d)
  ];
  if (disclosures.length) {
    lines.push('DART:');
    disclosures.slice(0, 3).forEach(function(d) { lines.push('- ' + String(d.report_nm || '') + ' · ' + String(d.impact || d.summary || '')); });
  } else lines.push('DART: 당일 주요 신규 공시 없음');

  if (news.length) {
    lines.push('최근 뉴스:');
    news.slice(0, 3).forEach(function(n) { lines.push('- ' + String(n.title || '') + (n.source ? ' · ' + String(n.source) : '')); });
  } else lines.push('최근 뉴스: 뚜렷한 관련 기사 미확인');

  lines.push('※ 공시·뉴스는 동시성/관련성 보조근거이며 주가 움직임의 인과관계를 단정하지 않습니다.');
  return lines;
}

function hmSignedNum_(value) {
  if (value === null || value === '' || typeof value === 'undefined' || isNaN(Number(value))) return '-';
  const n = Number(value);
  return (n >= 0 ? '+' : '') + Math.round(n).toLocaleString('ko-KR');
}

function hmAlertRecipient_() {
  const props = PropertiesService.getScriptProperties();
  const configured = String(props.getProperty(HM.PROP_ALERT_EMAIL) || '').trim();
  if (configured) return configured;
  const effective = String(Session.getEffectiveUser().getEmail() || '').trim();
  if (!effective) throw new Error('경보 수신 이메일을 확인할 수 없습니다. hmSetAlertEmail("메일주소")를 한 번 실행하십시오.');
  return effective;
}

function hmSendAlertMail_(recipient, cur, events, marketContext) {
  const level = hmStrongestLevel_(events);
  const subject = '[보유종목 5분경보] ' + cur.name + ' (' + cur.ticker + ') | ' + level;
  const price = cur.price == null ? '-' : hmFmtNum_(cur.price) + '원';
  const change = cur.changePct == null ? '-' : (cur.changePct * 100).toFixed(2) + '%';
  const stop = cur.stopPrice == null ? '-' : hmFmtNum_(cur.stopPrice) + '원';

  const lines = [
    '종목: ' + cur.name + ' (' + cur.ticker + ')',
    '현재가: ' + price + ' / 등락률: ' + change,
    '손절선: ' + stop + ' / ATR 상태: ' + cur.atrBand,
    '', '[VWAP]',
    'VWAP9 방향: ' + cur.vwap9Dir,
    'VWAP26 방향: ' + cur.vwap26Dir,
    '관계/크로스: ' + cur.vwapRel + ' / ' + cur.vwapCross,
    '', '[OBV]',
    'OBV 방향: ' + cur.obvDir,
    'OBV9 기준선 방향: ' + cur.obv9Dir,
    '관계/크로스: ' + cur.obvRel + ' / ' + cur.obvCross,
    '', '[국면]',
    cur.regime + (cur.regimeText ? ' — ' + cur.regimeText : ''),
    '', '[이번 변화]'
  ];
  events.forEach(function(e) { lines.push('- [' + e.level + '] ' + e.title + ': ' + e.message); });
  lines.push('');
  hmContextLines_(marketContext).forEach(function(line) { lines.push(line); });
  lines.push('');
  lines.push('※ 자동주문 없음. 최종 매매 판단은 사용자가 합니다.');

  const body = lines.join('\n');
  const html = '<div style="font-family:Arial,sans-serif;white-space:pre-wrap">' + hmEscapeHtml_(body) + '</div>';
  MailApp.sendEmail({to: recipient, subject: subject, body: body, htmlBody: html, name: 'Held Position 5m Monitor'});
}

function hmAppendLog_(ss, rows) {
  const sh = ss.getSheetByName(HM.LOG);
  if (!sh) throw new Error(HM.LOG + ' 시트를 찾을 수 없습니다.');
  const start = Math.max(sh.getLastRow() + 1, 2);
  sh.getRange(start, 1, rows.length, HM.LOG_COLS).setValues(rows);
}

function hmMaybeCleanupLog_(ss, now) {
  const props = PropertiesService.getScriptProperties();
  const today = hmDateKey_(now);
  if (props.getProperty(HM.PROP_LAST_LOG_CLEANUP) === today) return;
  hmCleanupAlertLog_(ss, false);
  props.setProperty(HM.PROP_LAST_LOG_CLEANUP, today);
}

function hmCleanupAlertLog_(ss, force) {
  const sh = ss.getSheetByName(HM.LOG);
  if (!sh) throw new Error(HM.LOG + ' 시트를 찾을 수 없습니다.');
  const lastRow = sh.getLastRow();
  if (lastRow < 2) return {kept: 0, removed: 0};
  const rows = sh.getRange(2, 1, lastRow - 1, HM.LOG_COLS).getValues();
  const cutoff = new Date(Date.now() - HM.LOG_RETENTION_DAYS * 24 * 60 * 60 * 1000);
  let kept = rows.filter(function(r) { const d = hmAsDate_(r[0]); return d && d >= cutoff; });
  if (kept.length > HM.LOG_MAX_ROWS) kept = kept.slice(kept.length - HM.LOG_MAX_ROWS);
  const removed = rows.length - kept.length;
  if (force || removed > 0) {
    sh.getRange(2, 1, lastRow - 1, HM.LOG_COLS).clearContent();
    if (kept.length) sh.getRange(2, 1, kept.length, HM.LOG_COLS).setValues(kept);
  }
  return {kept: kept.length, removed: removed};
}

function hmIsMarketWindow_(now) {
  const dow = Utilities.formatDate(now, HM.TZ, 'EEE');
  if (dow === 'Sat' || dow === 'Sun') return false;
  const hhmm = Number(Utilities.formatDate(now, HM.TZ, 'HHmm'));
  return hhmm >= HM.MARKET_START && hhmm <= HM.MARKET_END;
}
function hmDateKey_(date) { return Utilities.formatDate(date, HM.TZ, 'yyyy-MM-dd'); }
function hmAsDate_(value) {
  if (value instanceof Date && !isNaN(value.getTime())) return value;
  if (typeof value === 'number' && isFinite(value)) return new Date(Math.round((value - 25569) * 86400000));
  if (typeof value === 'string' && value.trim()) { const parsed = new Date(value); if (!isNaN(parsed.getTime())) return parsed; }
  return null;
}
function hmTicker_(value) { const s = String(value == null ? '' : value).replace(/\D/g, ''); return s ? ('000000' + s).slice(-6) : ''; }
function hmNum_(value) { if (value === '' || value === null || typeof value === 'undefined') return null; const n = Number(value); return isFinite(n) ? n : null; }
function hmBool_(value) { if (value === true) return true; return String(value || '').trim().toUpperCase() === 'TRUE'; }
function hmFmtNum_(value) { return Number(value).toLocaleString('ko-KR', {maximumFractionDigits: 2}); }
function hmEscapeHtml_(text) { return String(text).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
