/**
 * Held Position 5-Minute Monitor v3.2 JEV ALERT GATE
 * 보유종목_INTRADAY_GF_MONITOR용
 * v3: 저소음 경보 게이트 + 방향 화살표(▲△▼▽) + 동일 tick 다종목 1통 묶음 + Cloud Run 원인분석
 *
 * - 평일 09:00~15:30, 5분 감시
 * - 메일: 매수조짐/매수확인 + 하락/손절 위험만. 관찰/일반 변화 제외
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
  JEV_LOG: 'JEV_GATE_LOG',

  CONFIG_COLS: 20,
  LIVE_COLS: 13,
  REGIME_COLS: 20,
  STATE_COLS: 22,
  LOG_COLS: 11,
  JEV_LOG_COLS: 18,

  MARKET_START: 900,
  MARKET_END: 1530,
  LOG_RETENTION_DAYS: 90,
  LOG_MAX_ROWS: 2000,

  PROP_ALERT_EMAIL: 'HELD_ALERT_EMAIL',
  PROP_LAST_LOG_CLEANUP: 'HELD_LAST_LOG_CLEANUP',
  PROP_API_URL: 'SIGNAL_API_URL',
  PROP_API_TOKEN: 'SIGNAL_API_TOKEN',

  GATE_VERSION: 3,
  CONFIRM_CYCLES: 2,
  STOP_COOLDOWN_MINUTES: 30
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
  const apiToken = String(props.getProperty(HM.PROP_API_TOKEN) || '').trim();

  if (!apiUrl) throw new Error('SIGNAL_API_URL이 없습니다.');
  if (!apiToken) throw new Error('SIGNAL_API_TOKEN이 없습니다.');

  const res = UrlFetchApp.fetch(apiUrl + '/health', {
    method: 'get',
    muteHttpExceptions: true,
    followRedirects: true
  });

  const text = res.getResponseCode() + ' ' + res.getContentText();
  console.log(text);

  if (res.getResponseCode() !== 200) {
    throw new Error('Cloud Run health check 실패: ' + text);
  }

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
    const pendingAlerts = [];
    const logRows = [];

    for (let i = 0; i < count; i++) {
      const cur = hmBuildSnapshot_(configs[i], live[i], regime[i], now);
      if (!cur.ticker) continue;

      const prev = prevMap[cur.ticker] || null;
      const rawEvents = hmDetectEvents_(prev, cur);

      // 첫 관측은 원칙적으로 조용히 seed한다.
      // 단, 이미 손절선 아래인 신규 감시 종목은 위험이므로 1회 후보 이벤트를 만든다.
      if (!prev && cur.dataQuality === 'VALID' && cur.stopBroken) {
        rawEvents.push(hmEvent_(
          'STOP_BREACH_INITIAL', 'RISK',
          '손절선 아래에서 감시 시작',
          '현재가가 설정 손절선 아래입니다. 보유전략을 즉시 확인하십시오.'
        ));
      }

      const gated = hmGateEvents_(prev, cur, rawEvents, now);
      cur.gateState = gated.gateState;
      cur.lastAlertKey = prev ? (prev.lastAlertKey || '') : '';
      cur.lastAlertAt = prev ? (prev.lastAlertAt || '') : '';

      if (gated.mailEvents.length > 0) {
        // 최종 메일 게이트를 통과한 사건에만 Kiwoom/DART/뉴스를 조회한다.
        const marketContext = hmFetchMarketContext_(cur, gated.mailEvents, now);
        const jevGate = hmJevEvaluateAlert_(cur, gated.mailEvents, marketContext, now, 'HELD');
        const alert = {
          cur: cur,
          events: gated.mailEvents,
          marketContext: marketContext,
          jevGate: jevGate
        };
        if (hmJevAllowsAlert_(gated.mailEvents, jevGate)) {
          pendingAlerts.push(alert);
        } else {
          // ACTIVE mode held this non-critical candidate.  Mark the state as
          // seen so the same 5-minute condition does not repeatedly call Jev;
          // ALERT_LOG remains mail-only and JEV_GATE_LOG preserves the audit.
          cur.gateState = hmMarkGateSent_(cur.gateState, cur, gated.mailEvents, now);
          hmAppendJevGateLog_(ss, [alert], now);
        }
      }

      nextStates.push(cur);
    }

    let emailsSent = 0;
    if (pendingAlerts.length > 0) {
      const recipient = hmAlertRecipient_();
      hmSendBatchAlertMail_(recipient, pendingAlerts, now);
      emailsSent = 1; // 동일 5분 tick의 여러 종목은 한 통으로 묶는다.

      pendingAlerts.forEach(function(a) {
        const key = hmAlertKey_(a.events);
        a.cur.lastAlertKey = key;
        a.cur.lastAlertAt = now;
        a.cur.gateState = hmMarkGateSent_(a.cur.gateState, a.cur, a.events, now);

        const contextLabel = a.marketContext && a.marketContext.catalyst
          ? String(a.marketContext.catalyst.label || '')
          : '';
        logRows.push([
          now,
          a.cur.ticker,
          a.cur.name,
          key,
          hmStrongestLevel_(a.events),
          a.cur.price == null ? '' : a.cur.price,
          a.cur.changePct == null ? '' : a.cur.changePct,
          a.cur.stopPrice == null ? '' : a.cur.stopPrice,
          a.cur.volumePace == null ? '' : a.cur.volumePace,
          a.cur.tradeTime || '',
          a.events.map(function(e) { return e.title + ': ' + e.message; }).join(' | ') +
            (contextLabel ? ' | 촉매: ' + contextLabel : '')
        ]);
      });
      hmAppendJevGateLog_(ss, pendingAlerts, now);
    }

    if (logRows.length) hmAppendLog_(ss, logRows);
    hmRewriteState_(ss, nextStates);
    hmMaybeCleanupLog_(ss, now);

    return {
      status: 'OK',
      holdings: nextStates.length,
      mailedStocks: pendingAlerts.length,
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

  const changed = existingNonBlank.length !== current.length ||
    !hmMatrixSame_(existingNonBlank.slice(0, current.length), current);

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
  if (status === 'SUSPENDED_HOLD') {
    dataQuality = 'SUSPENDED';
  } else if (!dataQuality) {
    dataQuality = (price != null && price > 0) ? 'VALID' : 'DATA_REVIEW';
  }

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
  if (price == null || refClose == null || atr14 == null || refClose <= 0 || atr14 <= 0) {
    return 'ATR_DATA_MISSING';
  }
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
    if (cur.dataQuality === 'SUSPENDED') {
      events.push(hmEvent_('DATA_SUSPENDED', 'RISK', '거래정지 상태', '장중 기술계산을 중지합니다.'));
    } else if (cur.dataQuality !== 'VALID') {
      events.push(hmEvent_('DATA_' + cur.dataQuality, 'WATCH', '장중 데이터 점검', prev.dataQuality + ' → ' + cur.dataQuality));
    } else if (prev.dataQuality !== 'VALID') {
      events.push(hmEvent_('DATA_RECOVERED', 'INFO', '장중 데이터 정상화', prev.dataQuality + ' → VALID'));
    }
  }

  if (cur.dataQuality !== 'VALID') return events;

  if (!prev.stopBroken && cur.stopBroken) {
    events.push(hmEvent_('STOP_BREACH', 'RISK', '손절선 최초 침범', '설정 손절선을 하향 이탈했습니다.'));
  } else if (prev.stopBroken && !cur.stopBroken) {
    events.push(hmEvent_('STOP_RECOVER', 'INFO', '손절선 재회복', '가격이 손절선 위로 회복했습니다.'));
  }

  if (prev.atrBand !== cur.atrBand) {
    if (cur.atrBand === 'UP_1_0') events.push(hmEvent_('ATR_UP_1_0', 'STRONG', '+1.0 ATR 강한 상승', '가격 변화 강도가 한 단계 상승했습니다.'));
    if (cur.atrBand === 'UP_0_5') events.push(hmEvent_('ATR_UP_0_5', 'WATCH', '+0.5 ATR 상승', '의미 있는 상승 구간에 진입했습니다.'));
    if (cur.atrBand === 'DOWN_1_0') events.push(hmEvent_('ATR_DOWN_1_0', 'RISK', '-1.0 ATR 강한 하락', '가격 훼손 여부를 확인하십시오.'));
    if (cur.atrBand === 'DOWN_0_5') events.push(hmEvent_('ATR_DOWN_0_5', 'WATCH', '-0.5 ATR 하락', '의미 있는 하락 구간에 진입했습니다.'));
  }

  hmMaybeTurn_(events, prev.vwap26Dir, cur.vwap26Dir,
    'VWAP26_TURN_UP', 'VWAP26_TURN_DOWN',
    'VWAP26 상승 전환', 'VWAP26 하락 전환', 'STRONG', 'RISK');

  hmMaybeTurn_(events, prev.obv9Dir, cur.obv9Dir,
    'OBV9_TURN_UP', 'OBV9_TURN_DOWN',
    'OBV 기준선 상승 전환', 'OBV 기준선 하락 전환', 'STRONG', 'RISK');

  hmMaybeTurn_(events, prev.vwap9Dir, cur.vwap9Dir,
    'VWAP9_TURN_UP', 'VWAP9_TURN_DOWN',
    'VWAP9 머리 들기', 'VWAP9 머리 숙임', 'WATCH', 'WATCH');

  hmMaybeTurn_(events, prev.obvDir, cur.obvDir,
    'OBV_TURN_UP', 'OBV_TURN_DOWN',
    'OBV 상승 전환', 'OBV 하락 전환', 'WATCH', 'WATCH');

  if (cur.vwapCross === 'GOLD_CROSS' && prev.vwapCross !== 'GOLD_CROSS') {
    const aligned = cur.vwap26Dir === 'RISING';
    events.push(hmEvent_(
      'VWAP_GOLD_CROSS', aligned ? 'WATCH' : 'INFO', 'VWAP 골드크로스',
      aligned ? 'VWAP26 상승과 정렬됨' : 'VWAP26이 상승하지 않아 반등 신호로만 봅니다.'
    ));
  } else if (cur.vwapCross === 'DEAD_CROSS' && prev.vwapCross !== 'DEAD_CROSS') {
    const aligned = cur.vwap26Dir === 'FALLING';
    events.push(hmEvent_(
      'VWAP_DEAD_CROSS', aligned ? 'RISK' : 'INFO', 'VWAP 데드크로스',
      aligned ? 'VWAP26 하락과 정렬됨' : '장기선 하락 확인 전에는 단기 약화로 봅니다.'
    ));
  }

  if (cur.obvCross === 'GOLD_CROSS' && prev.obvCross !== 'GOLD_CROSS') {
    const aligned = cur.obv9Dir === 'RISING';
    events.push(hmEvent_(
      'OBV_GOLD_CROSS', aligned ? 'WATCH' : 'INFO', 'OBV 골드크로스',
      aligned ? 'OBV9 기준선 상승과 정렬됨' : 'OBV9 기준선 상승 전환은 아직 확인되지 않았습니다.'
    ));
  } else if (cur.obvCross === 'DEAD_CROSS' && prev.obvCross !== 'DEAD_CROSS') {
    const aligned = cur.obv9Dir === 'FALLING';
    events.push(hmEvent_(
      'OBV_DEAD_CROSS', aligned ? 'RISK' : 'INFO', 'OBV 데드크로스',
      aligned ? 'OBV9 기준선 하락과 정렬됨' : '기준선 하락 확인 전에는 단기 수급 약화로 봅니다.'
    ));
  }

  if (prev.regime !== cur.regime) {
    switch (cur.regime) {
      case 'BULL_CONFIRMED':
        events.push(hmEvent_('REGIME_BULL_CONFIRMED', 'STRONG', '상승국면 확인', 'VWAP 장단기선과 OBV/기준선이 모두 상승 정렬되었습니다.'));
        break;
      case 'BEAR_CONFIRMED':
        events.push(hmEvent_('REGIME_BEAR_CONFIRMED', 'RISK', '약세국면 확인', 'VWAP 장단기선과 OBV/기준선이 모두 하락 정렬되었습니다.'));
        break;
      case 'PULLBACK_IN_UPREGIME':
        events.push(hmEvent_('REGIME_PULLBACK', 'INFO', '상승국면 내 눌림', 'VWAP26 상승을 유지한 채 VWAP9가 조정 중입니다.'));
        break;
      case 'BOUNCE_IN_DOWNREGIME':
        events.push(hmEvent_('REGIME_BOUNCE_DOWN', 'INFO', '하락국면 내 반등', 'VWAP9는 상승하지만 VWAP26 하락이 계속됩니다.'));
        break;
      case 'EARLY_IMPROVEMENT':
        events.push(hmEvent_('REGIME_EARLY_IMPROVEMENT', 'WATCH', '초기 개선', '단기선/OBV 개선이 시작됐으나 장기·기준선 확인이 더 필요합니다.'));
        break;
      case 'EARLY_WEAKENING':
        events.push(hmEvent_('REGIME_EARLY_WEAKENING', 'WATCH', '초기 약화', '단기선/OBV 약화가 시작돼 장기선 훼손 여부를 확인해야 합니다.'));
        break;
    }
  }

  return events;
}

function hmMaybeTurn_(events, before, after, upKey, downKey, upTitle, downTitle, upLevel, downLevel) {
  if (before === after) return;
  if (after === 'RISING' && (before === 'FALLING' || before === 'FLAT')) {
    events.push(hmEvent_(upKey, upLevel, upTitle, before + ' → RISING'));
  } else if (after === 'FALLING' && (before === 'RISING' || before === 'FLAT')) {
    events.push(hmEvent_(downKey, downLevel, downTitle, before + ' → FALLING'));
  }
}

function hmEvent_(key, level, title, message) {
  return {key: key, level: level, title: title, message: message};
}

function hmStrongestLevel_(events) {
  const rank = {INFO: 1, WATCH: 2, STRONG: 3, RISK: 4};
  let best = 'INFO';
  events.forEach(function(e) {
    if ((rank[e.level] || 0) > (rank[best] || 0)) best = e.level;
  });
  return best;
}

function hmAlertKey_(events) {
  return events.map(function(e) { return e.key; }).sort().join('|');
}


/**
 * v3 LOW-NOISE gate
 * - DATA/STALE/RECOVERED: mail 금지 (상태 저장만)
 * - INFO: mail 금지
 * - ±0.5 ATR 단독: mail 금지
 * - ±1.0 ATR: 방향별 하루 1회
 * - EARLY 상승/하락: 2회 연속 확인 + 방향별 하루 1회
 * - CONFIRMED 상승/하락: 2회 연속 확인 + 방향별 하루 1회
 * - VWAP26 + OBV9가 같은 방향으로 동시에 전환: 조기 신호로 즉시 1회
 * - 손절선 침범: 즉시, 단 30분 cooldown
 * - 동일 5분 tick 다종목: 메일 1통으로 묶음
 */
function hmGateEvents_(prev, cur, rawEvents, now) {
  const today = hmDateKey_(now);
  let g = hmCloneGateState_(prev && prev.gateState ? prev.gateState : {});

  // v3 첫 도입 시 기존 상태를 '이미 본 상태'로 seed하여 설치 직후 폭탄메일 방지.
  if (g.version !== HM.GATE_VERSION) {
    g = hmNewGateState_(today);
    const seedStage = hmStageFromCur_(cur);
    g.stage = seedStage;
    g.stageCount = seedStage === 'NONE' ? 0 : HM.CONFIRM_CYCLES;
    if (prev) {
      if (seedStage !== 'NONE') g.sent[today + ':' + seedStage] = true;
      if (cur.atrBand === 'UP_1_0') g.sent[today + ':ATR_UP_1_0'] = true;
      if (cur.atrBand === 'DOWN_1_0') g.sent[today + ':ATR_DOWN_1_0'] = true;
    }
  }

  if (g.date !== today) {
    const oldStage = g.stage || 'NONE';
    g = hmNewGateState_(today);
    g.stage = oldStage;
    g.stageCount = oldStage === 'NONE' ? 0 : 1;
  }

  const stage = hmStageFromCur_(cur);
  if (stage === g.stage) {
    g.stageCount = Math.min(99, Number(g.stageCount || 0) + 1);
  } else {
    g.stage = stage;
    g.stageCount = stage === 'NONE' ? 0 : 1;
  }

  // 손절선 회복은 2회 연속 위에서 버틴 뒤에만 알리기 위한 카운터.
  const stopRecoverEvent = hmFindEvent_(rawEvents, 'STOP_RECOVER');
  if (stopRecoverEvent) {
    g.stopRecoverCount = 1;
  } else if (!cur.stopBroken && Number(g.stopRecoverCount || 0) > 0) {
    g.stopRecoverCount = Math.min(2, Number(g.stopRecoverCount || 0) + 1);
  } else if (cur.stopBroken) {
    g.stopRecoverCount = 0;
  }

  const mailEvents = [];

  // 1) 손절: 가장 중요한 즉시 이벤트. 다만 같은 종목 30분 이내 재침범 반복은 억제.
  const stopEvent = hmFindEvent_(rawEvents, 'STOP_BREACH') || hmFindEvent_(rawEvents, 'STOP_BREACH_INITIAL');
  if (stopEvent && hmCooldownPassed_(g.lastStopAlertAt, now, HM.STOP_COOLDOWN_MINUTES)) {
    mailEvents.push(stopEvent);
  }
  if (Number(g.stopRecoverCount || 0) >= 2 && !g.sent[today + ':STOP_RECOVER']) {
    mailEvents.push(hmEvent_('STOP_RECOVER_CONFIRMED', 'WATCH', '손절선 회복 확인', '손절선 위에서 2회 연속 확인되었습니다.'));
  }

  // 2) 장기/기준선이 동시에 같은 방향으로 돌아설 때는 빠른 조기 경보.
  const upPair = hmFindEvent_(rawEvents, 'VWAP26_TURN_UP') && hmFindEvent_(rawEvents, 'OBV9_TURN_UP');
  const downPair = hmFindEvent_(rawEvents, 'VWAP26_TURN_DOWN') && hmFindEvent_(rawEvents, 'OBV9_TURN_DOWN');
  if (upPair && !g.sent[today + ':BASE_PAIR_UP']) {
    mailEvents.push(hmEvent_('BASE_PAIR_UP', 'STRONG', '장기선·OBV 기준선 동반 상승전환', 'VWAP26과 OBV9가 같은 시점에 상승으로 돌아섰습니다.'));
  }
  if (downPair && !g.sent[today + ':BASE_PAIR_DOWN']) {
    mailEvents.push(hmEvent_('BASE_PAIR_DOWN', 'RISK', '장기선·OBV 기준선 동반 하락전환', 'VWAP26과 OBV9가 같은 시점에 하락으로 돌아섰습니다.'));
  }

  // 3) Regime은 2회 연속 확인 후 메일. 속이 찬 ▲▼는 CONFIRMED에만 사용.
  if (stage !== 'NONE' && Number(g.stageCount || 0) >= HM.CONFIRM_CYCLES && !g.sent[today + ':' + stage]) {
    if (stage === 'CONF_UP') {
      mailEvents.push(hmEvent_('SIGNAL_CONF_UP', 'STRONG', '상승 확인', '상승국면이 2회 연속 확인되었습니다.'));
    } else if (stage === 'CONF_DOWN') {
      mailEvents.push(hmEvent_('SIGNAL_CONF_DOWN', 'RISK', '하락 확인', '하락국면이 2회 연속 확인되었습니다.'));
    } else if (stage === 'EARLY_UP') {
      mailEvents.push(hmEvent_('SIGNAL_EARLY_UP', 'WATCH', '상승 조짐', '초기 개선이 2회 연속 확인되었습니다.'));
    } else if (stage === 'EARLY_DOWN') {
      mailEvents.push(hmEvent_('SIGNAL_EARLY_DOWN', 'WATCH', '하락 조짐', '초기 약화가 2회 연속 확인되었습니다.'));
    }
  }

  // 4) ±1.0 ATR은 강한 가격 사건이라 하루 1회. ±0.5 ATR 단독메일은 완전 억제.
  const atrUp = hmFindEvent_(rawEvents, 'ATR_UP_1_0');
  const atrDown = hmFindEvent_(rawEvents, 'ATR_DOWN_1_0');
  if (atrUp && !g.sent[today + ':ATR_UP_1_0']) mailEvents.push(atrUp);
  if (atrDown && !g.sent[today + ':ATR_DOWN_1_0']) mailEvents.push(atrDown);

  // 5) 크로스·VWAP9/OBV 단기방향·데이터품질 이벤트는 단독 메일 금지.
  //    다만 이미 다른 중요 이벤트로 메일이 나갈 때는 설명용으로 같은 방향의 보조 이벤트를 최대 2개 포함.
  if (mailEvents.length > 0) {
    const aux = rawEvents.filter(function(e) {
      return /^(VWAP_GOLD_CROSS|VWAP_DEAD_CROSS|OBV_GOLD_CROSS|OBV_DEAD_CROSS|VWAP9_TURN_|OBV_TURN_)/.test(e.key);
    }).slice(0, 2);
    aux.forEach(function(e) {
      if (!hmFindEvent_(mailEvents, e.key)) mailEvents.push(e);
    });
  }

  return {mailEvents: hmMailEvents_(hmUniqueEvents_(mailEvents)), gateState: g};
}

// 메일 허용 목록: WATCH 등급 전체를 제거하면 매수조짐까지 사라지므로 사건 키로 분류.
// 일반 관찰/손절선 회복/상승 ATR 단독/보조 크로스는 메일에서 제외. 상태 계산은 유지.
function hmMailEvents_(events) {
  const allowed = [
    'SIGNAL_EARLY_UP', 'SIGNAL_CONF_UP', 'BASE_PAIR_UP',
    'SIGNAL_EARLY_DOWN', 'SIGNAL_CONF_DOWN', 'BASE_PAIR_DOWN',
    'ATR_DOWN_1_0', 'STOP_BREACH', 'STOP_BREACH_INITIAL'
  ];
  return (Array.isArray(events) ? events : []).filter(function(e) {
    return e && allowed.indexOf(e.key) >= 0;
  });
}
// 무발송/상태변경 없는 운영 검증용.
function hmPreviewMailPolicy() {
  const checks = [
    ['관찰·일반 변화', ['INFO', 'STOP_RECOVER_CONFIRMED', 'ATR_UP_1_0', 'VWAP_GOLD_CROSS'], 0],
    ['매수조짐', ['SIGNAL_EARLY_UP'], 1],
    ['매수확인', ['SIGNAL_CONF_UP'], 1],
    ['하락 위험', ['SIGNAL_EARLY_DOWN', 'SIGNAL_CONF_DOWN', 'BASE_PAIR_DOWN', 'ATR_DOWN_1_0'], 4],
    ['손절 위험', ['STOP_BREACH', 'STOP_BREACH_INITIAL'], 2]
  ];
  checks.forEach(function(c) {
    const count=hmMailEvents_(c[1].map(function(key) {return {key:key};})).length;
    if (count!==c[2]) throw new Error('메일 정책 검증 실패: '+c[0]);
    console.log(c[0]+': '+(count ? '메일 유지' : '메일 제외')+' ('+count+')');
  });
  console.log('PASS: 관찰 제외 · 매수조짐/확인 및 하락/손절 유지 · 무발송');
}

function hmMarkGateSent_(gateState, cur, events, now) {
  const g = hmCloneGateState_(gateState || hmNewGateState_(hmDateKey_(now)));
  const today = hmDateKey_(now);
  if (!g.sent) g.sent = {};

  events.forEach(function(e) {
    if (e.key === 'SIGNAL_CONF_UP') g.sent[today + ':CONF_UP'] = true;
    if (e.key === 'SIGNAL_CONF_DOWN') g.sent[today + ':CONF_DOWN'] = true;
    if (e.key === 'SIGNAL_EARLY_UP') g.sent[today + ':EARLY_UP'] = true;
    if (e.key === 'SIGNAL_EARLY_DOWN') g.sent[today + ':EARLY_DOWN'] = true;
    if (e.key === 'BASE_PAIR_UP') g.sent[today + ':BASE_PAIR_UP'] = true;
    if (e.key === 'BASE_PAIR_DOWN') g.sent[today + ':BASE_PAIR_DOWN'] = true;
    if (e.key === 'ATR_UP_1_0') g.sent[today + ':ATR_UP_1_0'] = true;
    if (e.key === 'ATR_DOWN_1_0') g.sent[today + ':ATR_DOWN_1_0'] = true;
    if (e.key === 'STOP_RECOVER_CONFIRMED') g.sent[today + ':STOP_RECOVER'] = true;
    if (e.key === 'STOP_BREACH' || e.key === 'STOP_BREACH_INITIAL') g.lastStopAlertAt = now.toISOString();
  });
  return g;
}

function hmNewGateState_(today) {
  return {
    version: HM.GATE_VERSION,
    date: today,
    stage: 'NONE',
    stageCount: 0,
    stopRecoverCount: 0,
    lastStopAlertAt: '',
    sent: {}
  };
}

function hmCloneGateState_(g) {
  try { return JSON.parse(JSON.stringify(g || {})); } catch (e) { return {}; }
}

function hmStageFromCur_(cur) {
  if (cur.dataQuality !== 'VALID') return 'NONE';
  if (cur.regime === 'BULL_CONFIRMED') return 'CONF_UP';
  if (cur.regime === 'BEAR_CONFIRMED') return 'CONF_DOWN';
  if (cur.regime === 'EARLY_IMPROVEMENT') return 'EARLY_UP';
  if (cur.regime === 'EARLY_WEAKENING') return 'EARLY_DOWN';
  return 'NONE';
}

function hmFindEvent_(events, key) {
  const list = Array.isArray(events) ? events : [];
  for (let i = 0; i < list.length; i++) if (list[i].key === key) return list[i];
  return null;
}

function hmUniqueEvents_(events) {
  const seen = {};
  return (events || []).filter(function(e) {
    if (seen[e.key]) return false;
    seen[e.key] = true;
    return true;
  });
}

function hmCooldownPassed_(iso, now, minutes) {
  if (!iso) return true;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return true;
  return (now.getTime() - d.getTime()) >= minutes * 60000;
}

function hmSignalVisual_(cur, events) {
  const keys = (events || []).map(function(e) { return e.key; });
  if (keys.indexOf('STOP_BREACH') >= 0 || keys.indexOf('STOP_BREACH_INITIAL') >= 0 || keys.indexOf('SIGNAL_CONF_DOWN') >= 0) {
    return {symbol: '▼', label: keys.indexOf('SIGNAL_CONF_DOWN') >= 0 ? '하락확인' : '손절위험', direction: 'DOWN', confirmed: true, color: '#1565c0'};
  }
  if (keys.indexOf('SIGNAL_CONF_UP') >= 0) {
    return {symbol: '▲', label: '상승확인', direction: 'UP', confirmed: true, color: '#d32f2f'};
  }
  if (keys.indexOf('SIGNAL_EARLY_DOWN') >= 0 || keys.indexOf('BASE_PAIR_DOWN') >= 0 || keys.indexOf('ATR_DOWN_1_0') >= 0) {
    return {symbol: '▽', label: '하락조짐', direction: 'DOWN', confirmed: false, color: '#1565c0'};
  }
  if (keys.indexOf('SIGNAL_EARLY_UP') >= 0 || keys.indexOf('BASE_PAIR_UP') >= 0 || keys.indexOf('ATR_UP_1_0') >= 0 || keys.indexOf('STOP_RECOVER_CONFIRMED') >= 0) {
    return {symbol: '△', label: keys.indexOf('STOP_RECOVER_CONFIRMED') >= 0 ? '회복조짐' : '상승조짐', direction: 'UP', confirmed: false, color: '#d32f2f'};
  }
  return {symbol: '•', label: '변화', direction: 'NEUTRAL', confirmed: false, color: '#475569'};
}

function hmRegimeDisplay_(cur) {
  if (cur.regime === 'BULL_CONFIRMED') return '▲ 상승확인';
  if (cur.regime === 'BEAR_CONFIRMED') return '▼ 하락확인';
  if (cur.regime === 'EARLY_IMPROVEMENT') return '△ 상승조짐';
  if (cur.regime === 'EARLY_WEAKENING') return '▽ 하락조짐';
  if (cur.regime === 'PULLBACK_IN_UPREGIME') return '상승국면 내 눌림';
  if (cur.regime === 'BOUNCE_IN_DOWNREGIME') return '하락국면 내 반등';
  if (cur.regime === 'OBV_REBOUND_BASE_DOWN') return 'OBV 반등 · 기준선 하락';
  if (cur.regime === 'SUSPENDED') return '거래정지';
  return cur.regimeText || cur.regime || '중립';
}

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

    let gateState = {};
    try {
      gateState = r[7] ? JSON.parse(String(r[7])) : {};
    } catch (e) {
      gateState = {};
    }

    map[ticker] = {
      ticker: ticker,
      stateDate: r[1],
      price: hmNum_(r[2]),
      atrBand: String(r[3] || ''),
      stopBroken: hmBool_(r[4]),
      tradeTime: r[6],
      gateState: gateState,
      vwap9Dir: String(r[9] || ''),
      vwap26Dir: String(r[10] || ''),
      vwapRel: String(r[11] || ''),
      obvDir: String(r[12] || ''),
      obv9Dir: String(r[13] || ''),
      obvRel: String(r[14] || ''),
      regime: String(r[15] || ''),
      regimeAsOf: r[16],
      lastAlertKey: String(r[17] || ''),
      dataQuality: String(r[18] || ''),
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
      s.ticker,
      today,
      s.price == null ? '' : s.price,
      s.atrBand || '',
      !!s.stopBroken,
      false,
      s.tradeTime || '',
      JSON.stringify(s.gateState || {}),
      now,
      s.vwap9Dir || '',
      s.vwap26Dir || '',
      s.vwapRel || '',
      s.obvDir || '',
      s.obv9Dir || '',
      s.obvRel || '',
      s.regime || '',
      s.regimeAsOf || '',
      s.lastAlertKey || '',
      s.dataQuality || '',
      s.vwapCross || '',
      s.obvCross || '',
      s.lastAlertAt || ''
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
      disclosures: [],
      news: []
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
      method: 'post',
      contentType: 'application/json',
      headers: {'X-StockBot-Token': apiToken},
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
      followRedirects: true
    });

    const http = res.getResponseCode();
    let body = {};
    try {
      body = JSON.parse(res.getContentText() || '{}');
    } catch (e) {
      body = {};
    }

    if (http >= 200 && http < 300 && body.ok === true && body.market_context) {
      body.market_context.status = 'OK';
      return body.market_context;
    }

    return {
      status: 'ERROR',
      catalyst: {
        label: '원인분석 일시 실패',
        confidence: 'NONE',
        reason: 'Cloud Run HTTP ' + http + (body.error ? ' · ' + body.error : '')
      },
      flow: {status: 'UNAVAILABLE', summary: '수급 확인 실패'},
      disclosures: [],
      news: []
    };
  } catch (err) {
    return {
      status: 'ERROR',
      catalyst: {
        label: '원인분석 일시 실패',
        confidence: 'NONE',
        reason: String(err && err.message ? err.message : err)
      },
      flow: {status: 'UNAVAILABLE', summary: '수급 확인 실패'},
      disclosures: [],
      news: []
    };
  }
}

/**
 * Jev is the final *delivery* gate, not a signal generator and never an order
 * engine.  It is deliberately fail-open: the existing technical rule alert is
 * delivered when the gateway is off, unconfigured, slow, or returns an error.
 *
 * Cloud Run owns JEV_API_KEY.  Apps Script continues to use only its existing
 * SIGNAL_API_URL / SIGNAL_API_TOKEN properties, so the Jev key never reaches a
 * spreadsheet, email body, or Apps Script source.
 */
function hmJevEvaluateAlert_(cur, events, marketContext, now, source) {
  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty(HM.PROP_API_URL) || '').trim().replace(/\/$/, '');
  const apiToken = String(props.getProperty(HM.PROP_API_TOKEN) || '').trim();
  const base = {
    ok: true,
    status: 'NOT_CONFIGURED',
    mode: 'SHADOW',
    decision: 'SEND',
    reason: 'SIGNAL_API_NOT_CONFIGURED',
    source: source || 'HELD',
    ticker: cur.ticker,
    event_keys: (events || []).map(function(e) { return e.key; })
  };
  if (!apiUrl || !apiToken) return base;

  const context = marketContext || {};
  const flow = context.flow || {};
  const catalyst = context.catalyst || {};
  const payload = {
    source: source || 'HELD',
    ticker: cur.ticker,
    name: cur.name,
    as_of_date: hmDateKey_(now),
    event_keys: base.event_keys,
    event_level: hmStrongestLevel_(events || []),
    current_price: cur.price,
    change_pct: cur.changePct,
    atr_band: cur.atrBand,
    volume_pace: cur.volumePace,
    stop_broken: cur.stopBroken,
    vwap9_dir: cur.vwap9Dir,
    vwap26_dir: cur.vwap26Dir,
    vwap_rel: cur.vwapRel,
    obv_dir: cur.obvDir,
    obv9_dir: cur.obv9Dir,
    obv_rel: cur.obvRel,
    regime: cur.regime,
    context: {
      catalyst: {label: catalyst.label || '', confidence: catalyst.confidence || '', reason: catalyst.reason || ''},
      flow: {status: flow.status || '', foreign_5d: flow.foreign_5d, institution_5d: flow.institution_5d, combined_5d: flow.combined_5d, combined_20d: flow.combined_20d},
      disclosures: (context.disclosures || []).slice(0, 3).map(function(d) { return {report_nm: d.report_nm || '', impact: d.impact || d.summary || ''}; }),
      news: (context.news || []).slice(0, 3).map(function(n) { return {title: n.title || '', impact: n.impact || n.summary || ''}; })
    }
  };

  try {
    const res = UrlFetchApp.fetch(apiUrl + '/jev-gate', {
      method: 'post', contentType: 'application/json',
      headers: {'X-StockBot-Token': apiToken}, payload: JSON.stringify(payload),
      muteHttpExceptions: true, followRedirects: true
    });
    const http = res.getResponseCode();
    let body = {};
    try { body = JSON.parse(res.getContentText() || '{}'); } catch (e) { body = {}; }
    if (http >= 200 && http < 300 && body && body.ok === true && body.decision) return body;
    return Object.assign({}, base, {status: 'ERROR', reason: 'JEV_GATE_HTTP_' + http + (body.error ? ':' + body.error : '')});
  } catch (err) {
    return Object.assign({}, base, {status: 'ERROR', reason: 'JEV_GATE_TRANSPORT:' + String(err && err.message ? err.message : err)});
  }
}

function hmJevAllowsAlert_(events, gate) {
  const keys = (events || []).map(function(e) { return e.key; });
  // Stop-loss warnings cannot be suppressed even in ACTIVE mode.
  if (keys.indexOf('STOP_BREACH') >= 0 || keys.indexOf('STOP_BREACH_INITIAL') >= 0) return true;
  // Fail open. A temporary provider/API problem must never silence a legacy alert.
  if (!gate || gate.status !== 'OK') return true;
  return String(gate.decision || 'SEND').toUpperCase() !== 'HOLD';
}

function hmJevAnswer_(gate, name, field, fallback) {
  const answer = gate && gate.answers && gate.answers[name] ? gate.answers[name] : {};
  const value = answer[field];
  return value == null || value === '' ? (fallback == null ? '' : fallback) : value;
}

function hmJevDisplayLines_(gate) {
  if (!gate) return ['Jev: 기록 없음'];
  if (gate.status !== 'OK') return ['Jev: ' + String(gate.status || 'ERROR') + ' · 기존 규칙으로 발송'];
  const pct = Math.round(Number(hmJevAnswer_(gate, 'send_alert_now', 'noul', 0)) * 100);
  const stage = hmJevAnswer_(gate, 'signal_stage', 'choice', 'UNKNOWN');
  const direction = hmJevAnswer_(gate, 'signal_direction', 'choice', 'UNKNOWN');
  const urgency = Number(hmJevAnswer_(gate, 'alert_urgency', 'score', 0)).toFixed(1);
  return ['Jev ' + String(gate.mode || 'SHADOW') + ': ' + direction + ' / ' + stage + ' / 긴급도 ' + urgency + ' / 발송적합 ' + pct + '% / ' + String(gate.decision || 'SEND')];
}

function hmAppendJevGateLog_(ss, alerts, now) {
  const list = Array.isArray(alerts) ? alerts : [];
  if (!list.length) return;
  let sh = ss.getSheetByName(HM.JEV_LOG);
  if (!sh) {
    sh = ss.insertSheet(HM.JEV_LOG);
    sh.getRange(1, 1, 1, HM.JEV_LOG_COLS).setValues([[
      'Timestamp', 'Source', 'Ticker', 'Name', 'EventKeys', 'RuleLevel', 'Price', 'ChangePct', 'Regime',
      'GateStatus', 'Mode', 'Decision', 'Reason', 'Direction', 'Stage', 'Urgency', 'SendProbability', 'InputTokens'
    ]]);
    sh.setFrozenRows(1);
  }
  const rows = list.map(function(a) {
    const g = a.jevGate || {};
    const p = hmJevAnswer_(g, 'send_alert_now', 'noul', '');
    return [
      now, String(g.source || 'HELD'), a.cur.ticker, a.cur.name,
      (a.events || []).map(function(e) { return e.key; }).join('|'), hmStrongestLevel_(a.events || []),
      a.cur.price == null ? '' : a.cur.price, a.cur.changePct == null ? '' : a.cur.changePct, a.cur.regime,
      g.status || '', g.mode || '', g.decision || '', g.reason || '',
      hmJevAnswer_(g, 'signal_direction', 'choice', ''), hmJevAnswer_(g, 'signal_stage', 'choice', ''),
      hmJevAnswer_(g, 'alert_urgency', 'score', ''), p === '' ? '' : p,
      g.usage && g.usage.input_tokens != null ? g.usage.input_tokens : ''
    ];
  });
  const start = Math.max(sh.getLastRow() + 1, 2);
  sh.getRange(start, 1, rows.length, HM.JEV_LOG_COLS).setValues(rows);
  if (sh.getLastRow() > HM.LOG_MAX_ROWS + 1) sh.deleteRows(2, sh.getLastRow() - HM.LOG_MAX_ROWS - 1);
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
    disclosures.slice(0, 3).forEach(function(d) {
      lines.push('- ' + String(d.report_nm || '') + ' · ' + String(d.impact || d.summary || ''));
    });
  } else {
    lines.push('DART: 당일 주요 신규 공시 없음');
  }

  if (news.length) {
    lines.push('최근 뉴스:');
    news.slice(0, 3).forEach(function(n) {
      lines.push('- ' + String(n.title || '') + (n.source ? ' · ' + String(n.source) : ''));
    });
  } else {
    lines.push('최근 뉴스: 뚜렷한 관련 기사 미확인');
  }

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
  if (!effective) {
    throw new Error('경보 수신 이메일을 확인할 수 없습니다. hmSetAlertEmail("메일주소")를 한 번 실행하십시오.');
  }
  return effective;
}

function hmSendBatchAlertMail_(recipient, alerts, now) {
  const list = (Array.isArray(alerts) ? alerts : []).map(function(a) {
    return Object.assign({}, a, {events: hmMailEvents_(a.events)});
  }).filter(function(a) { return a.events.length > 0; });
  if (!list.length) return;

  const visuals = list.map(function(a) { return hmSignalVisual_(a.cur, a.events); });
  const counts = {UP_CONF: 0, UP_EARLY: 0, DOWN_CONF: 0, DOWN_EARLY: 0};
  visuals.forEach(function(v) {
    if (v.direction === 'UP' && v.confirmed) counts.UP_CONF++;
    if (v.direction === 'UP' && !v.confirmed) counts.UP_EARLY++;
    if (v.direction === 'DOWN' && v.confirmed) counts.DOWN_CONF++;
    if (v.direction === 'DOWN' && !v.confirmed) counts.DOWN_EARLY++;
  });

  let subject;
  if (list.length === 1) {
    subject = '[보유종목 5분경보] ' + visuals[0].symbol + ' ' + list[0].cur.name + ' | ' + visuals[0].label;
  } else {
    const bits = [];
    if (counts.UP_CONF) bits.push('▲' + counts.UP_CONF);
    if (counts.UP_EARLY) bits.push('△' + counts.UP_EARLY);
    if (counts.DOWN_CONF) bits.push('▼' + counts.DOWN_CONF);
    if (counts.DOWN_EARLY) bits.push('▽' + counts.DOWN_EARLY);
    subject = '[보유종목 5분경보] ' + list.length + '종목 변화 | ' + bits.join(' ');
  }

  const textParts = [];
  const htmlParts = [];

  list.forEach(function(a, idx) {
    const cur = a.cur;
    const events = a.events;
    const v = visuals[idx];
    const price = cur.price == null ? '-' : hmFmtNum_(cur.price) + '원';
    const change = cur.changePct == null ? '-' : (cur.changePct * 100).toFixed(2) + '%';
    const stop = cur.stopPrice == null ? '-' : hmFmtNum_(cur.stopPrice) + '원';

    const block = [
      v.symbol + ' ' + v.label + '  ' + cur.name + ' (' + cur.ticker + ')',
      '현재가 ' + price + ' / 등락률 ' + change + ' / 손절선 ' + stop + ' / ATR ' + cur.atrBand,
      'VWAP9 ' + cur.vwap9Dir + ' / VWAP26 ' + cur.vwap26Dir + ' / ' + cur.vwapRel,
      'OBV ' + cur.obvDir + ' / OBV9 ' + cur.obv9Dir + ' / ' + cur.obvRel,
      '국면: ' + hmRegimeDisplay_(cur),
      '이번 변화: ' + events.map(function(e) { return e.title; }).join(' · ')
    ];
    hmContextLines_(a.marketContext).forEach(function(line) { block.push(line); });
    hmJevDisplayLines_(a.jevGate).forEach(function(line) { block.push(line); });
    textParts.push(block.join('\n'));

    const eventHtml = events.map(function(e) {
      return '<li><b>' + hmEscapeHtml_(e.title) + '</b> — ' + hmEscapeHtml_(e.message) + '</li>';
    }).join('');

    const ctxLines = hmContextLines_(a.marketContext).concat(hmJevDisplayLines_(a.jevGate));
    const ctxHtml = ctxLines.map(function(line) { return hmEscapeHtml_(line); }).join('<br>');

    htmlParts.push(
      '<div style="border:1px solid #e2e8f0;border-radius:10px;padding:14px;margin:0 0 12px 0;background:#fff">' +
        '<div style="font-size:22px;font-weight:800;color:' + v.color + ';margin-bottom:8px">' +
          v.symbol + ' ' + hmEscapeHtml_(v.label) +
        '</div>' +
        '<div style="font-size:15px;font-weight:700;margin-bottom:6px">' + hmEscapeHtml_(cur.name + ' (' + cur.ticker + ')') + '</div>' +
        '<div style="font-size:13px;line-height:1.7;color:#334155">' +
          '현재가 <b>' + hmEscapeHtml_(price) + '</b> / 등락률 ' + hmEscapeHtml_(change) + '<br>' +
          '손절선 ' + hmEscapeHtml_(stop) + ' / ATR ' + hmEscapeHtml_(cur.atrBand) + '<br>' +
          'VWAP9 <b>' + hmEscapeHtml_(cur.vwap9Dir) + '</b> / VWAP26 <b>' + hmEscapeHtml_(cur.vwap26Dir) + '</b> / ' + hmEscapeHtml_(cur.vwapRel) + '<br>' +
          'OBV <b>' + hmEscapeHtml_(cur.obvDir) + '</b> / OBV9 <b>' + hmEscapeHtml_(cur.obv9Dir) + '</b> / ' + hmEscapeHtml_(cur.obvRel) + '<br>' +
          '국면: <b style="color:' + v.color + '">' + hmEscapeHtml_(hmRegimeDisplay_(cur)) + '</b>' +
        '</div>' +
        '<ul style="font-size:13px;line-height:1.6;margin:8px 0 8px 20px;padding:0">' + eventHtml + '</ul>' +
        '<div style="border-top:1px solid #e2e8f0;padding-top:8px;font-size:12px;line-height:1.6;color:#475569">' + ctxHtml + '</div>' +
      '</div>'
    );
  });

  const body = textParts.join('\n\n------------------------------\n\n') +
    '\n\n※ 5분마다 계산하며 매수조짐·매수확인과 하락·손절 위험만 발송합니다. 관찰·일반 변화는 제외합니다. 자동주문 없음. 최종 판단은 사용자가 합니다.';

  const htmlBody =
    '<div style="font-family:-apple-system,BlinkMacSystemFont,\'Apple SD Gothic Neo\',\'Malgun Gothic\',Arial,sans-serif;background:#f8fafc;padding:16px;color:#0f172a">' +
      '<div style="max-width:720px;margin:0 auto">' +
        '<div style="font-size:12px;color:#64748b;margin-bottom:10px">' + hmEscapeHtml_(Utilities.formatDate(now, HM.TZ, 'yyyy-MM-dd HH:mm')) + ' · 저소음 5분 감시</div>' +
        htmlParts.join('') +
        '<div style="font-size:11px;color:#64748b">※ ▲/△는 빨간 상승, ▼/▽는 파란 하락. 속이 찬 화살표는 확인(confirmed), 빈 화살표는 조짐/미확인입니다. 자동주문 없음.</div>' +
      '</div>' +
    '</div>';

  MailApp.sendEmail({
    to: recipient,
    subject: subject,
    body: body,
    htmlBody: htmlBody,
    name: 'Held Position 5m Monitor'
  });
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

  let kept = rows.filter(function(r) {
    const d = hmAsDate_(r[0]);
    return d && d >= cutoff;
  });

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

function hmDateKey_(date) {
  return Utilities.formatDate(date, HM.TZ, 'yyyy-MM-dd');
}

function hmAsDate_(value) {
  if (value instanceof Date && !isNaN(value.getTime())) return value;

  if (typeof value === 'number' && isFinite(value)) {
    return new Date(Math.round((value - 25569) * 86400000));
  }

  if (typeof value === 'string' && value.trim()) {
    const parsed = new Date(value);
    if (!isNaN(parsed.getTime())) return parsed;
  }

  return null;
}

function hmTicker_(value) {
  const s = String(value == null ? '' : value).replace(/\D/g, '');
  if (!s) return '';
  return ('000000' + s).slice(-6);
}

function hmNum_(value) {
  if (value === '' || value === null || typeof value === 'undefined') return null;
  const n = Number(value);
  return isFinite(n) ? n : null;
}

function hmBool_(value) {
  if (value === true) return true;
  return String(value || '').trim().toUpperCase() === 'TRUE';
}

function hmFmtNum_(value) {
  return Number(value).toLocaleString('ko-KR', {maximumFractionDigits: 2});
}

function hmEscapeHtml_(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
