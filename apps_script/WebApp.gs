/**
 * 보유종목 장중 감시 모바일 웹앱
 * 기존 Code.gs와 별도로 사용하는 웹앱 서버 코드
 */
const WEBAPP_CFG = {
  TIMEZONE: 'Asia/Seoul',
  CONFIG_SHEET: 'MONITOR_CONFIG',
  QUOTES_SHEET: 'LIVE_QUOTES',
  STATE_SHEET: 'ALERT_STATE',
  LOG_SHEET: 'ALERT_LOG',
  MAX_ALERTS: 20,
  AUTO_REFRESH_SECONDS: 60,
};
/**
 * 웹앱 첫 화면
 */
function doGet() {
  return HtmlService
    .createTemplateFromFile('Index')
    .evaluate()
    .setTitle('보유종목 감시센터')
    .setXFrameOptionsMode(
      HtmlService.XFrameOptionsMode.ALLOWALL
    )
    .addMetaTag(
      'viewport',
      'width=device-width, initial-scale=1, viewport-fit=cover'
    );
}
/**
 * 웹 화면에서 호출하는 대시보드 데이터
 */
function getDashboardData() {
  const ss = wa_getMonitorSpreadsheet_();
  const cfgRows = wa_sheetObjects_(
    ss.getSheetByName(
      WEBAPP_CFG.CONFIG_SHEET
    )
  );
  const quoteRows = wa_sheetObjects_(
    ss.getSheetByName(
      WEBAPP_CFG.QUOTES_SHEET
    )
  );
  const stateRows = wa_sheetObjects_(
    ss.getSheetByName(
      WEBAPP_CFG.STATE_SHEET
    )
  );
  const logRows = wa_sheetObjects_(
    ss.getSheetByName(
      WEBAPP_CFG.LOG_SHEET
    )
  );
  const quoteMap = wa_keyed_(
    quoteRows,
    'TICKER'
  );
  const stateMap = wa_keyed_(
    stateRows,
    'TICKER'
  );
  const positions = cfgRows
    .filter(r =>
      wa_truthy_(r.ENABLED) &&
      String(r.TICKER || '').trim() &&
      String(r.STATUS || '').trim().toUpperCase() !== 'CLOSED' &&
      String(r.STATUS || '').trim().toUpperCase() !== 'SOLD' &&
      wa_num_(r.QTY) > 0
    )
    .map(r => {
      const ticker =
        wa_ticker_(r.TICKER);
      return wa_buildPosition_(
        r,
        quoteMap[ticker] || {},
        stateMap[ticker] || {}
      );
    });
  const alerts = logRows
    .filter(r => r.TIMESTAMP)
    .sort(
      (a, b) =>
        wa_toMillis_(b.TIMESTAMP) -
        wa_toMillis_(a.TIMESTAMP)
    )
    .slice(
      0,
      WEBAPP_CFG.MAX_ALERTS
    )
    .map(wa_buildAlert_);
  const counts = positions.reduce(
    (acc, p) => {
      acc.total++;
      acc[p.statusKey] =
        (acc[p.statusKey] || 0) + 1;
      return acc;
    },
    {
      total: 0,
      danger: 0,
      caution: 0,
      normal: 0,
      suspended: 0
    }
  );
  const market =
    wa_buildMarketSummary_(positions);
  return {
    generatedAt:
      wa_fmtDateTime_(new Date()),
    autoRefreshSeconds:
      WEBAPP_CFG.AUTO_REFRESH_SECONDS,
    counts,
    market,
    positions,
    alerts
  };
}
/**
 * 감시용 스프레드시트 열기
 */
function wa_getMonitorSpreadsheet_() {
  const propId =
    PropertiesService
      .getScriptProperties()
      .getProperty(
        'MONITOR_SPREADSHEET_ID'
      );
  if (propId) {
    return SpreadsheetApp.openById(
      propId
    );
  }
  const active =
    SpreadsheetApp
      .getActiveSpreadsheet();
  if (active) {
    return active;
  }
  throw new Error(
    'MONITOR_SPREADSHEET_ID가 없습니다. ' +
    'setupIntradayMonitor()를 먼저 1회 실행하세요.'
  );
}
/**
 * 시트를 객체 배열로 변환
 */
function wa_sheetObjects_(sheet) {
  if (!sheet) {
    return [];
  }
  const values =
    sheet
      .getDataRange()
      .getValues();
  if (values.length < 2) {
    return [];
  }
  const headers =
    values[0].map(
      v => String(v).trim()
    );
  return values
    .slice(1)
    .map(row => {
      const obj = {};
      headers.forEach(
        (header, i) => {
          if (header) {
            obj[header] = row[i];
          }
        }
      );
      return obj;
    });
}
/**
 * 특정 컬럼을 키로 객체화
 */
function wa_keyed_(rows, key) {
  const out = {};
  rows.forEach(r => {
    const k =
      key === 'TICKER' ? wa_ticker_(r[key]) : String(r[key] || '').trim();
    if (k) {
      out[k] = r;
    }
  });
  return out;
}
/**
 * 한 종목의 웹 표시 데이터 생성
 */
function wa_buildPosition_(
  cfg,
  quote,
  state
) {
  const ticker =
    wa_ticker_(cfg.TICKER);
  const name =
    String(
      cfg.NAME || ticker
    ).trim();
  const exchange =
    String(
      cfg.EXCHANGE || ''
    ).trim();
  const statusRaw =
    String(
      cfg.STATUS || ''
    )
      .trim()
      .toUpperCase();
  const price =
    wa_num_(quote.PRICE);
  const refClose =
    wa_num_(cfg.REF_CLOSE) ??
    wa_num_(quote.PREV_CLOSE);
  /*
   * Google Sheets의 퍼센트 셀은
   * -1.70% → 내부값 -0.017 로 전달됨.
   * 웹에서는 다시 100을 곱해
   * -1.70 으로 사용한다.
   */
  const rawChangePct =
    wa_num_(quote.CHANGE_PCT);
  const changePct =
    rawChangePct != null
      ? rawChangePct * 100
      : (
          price != null &&
          refClose != null &&
          refClose !== 0
            ? (
                (price / refClose) - 1
              ) * 100
            : null
        );
  const stop =
    wa_num_(cfg.STOP_PRICE);
  const volume =
    wa_num_(quote.VOLUME);
  const volumePace =
    wa_num_(quote.VOLUME_PACE);
  const tradeTime =
    quote.TRADE_TIME;
  const avg =
    wa_num_(cfg.AVG_PRICE);
  const qty =
    wa_num_(cfg.QTY);
  const stopBroken =
    wa_truthy_(state.STOP_BROKEN) ||
    (
      price != null &&
      stop != null &&
      price <= stop
    );
  const lastBand =
    String(
      state.LAST_BAND || ''
    ).toUpperCase();
  const isSuspended = statusRaw.includes('SUSPENDED');
  const valPrice = (isSuspended && (price == null || price === 0)) ? refClose : (price ?? refClose);
  const stratStatus = String(cfg.STRATEGY_STATUS || '').trim().toUpperCase();
  const stratWarning = String(cfg.STRATEGY_WARNING || '').trim();
  let strategyWarning = stratWarning;
  let strategyStatus = stratStatus || 'NORMAL';
  let statusKey = 'normal';
  let statusLabel = '정상';
  if (isSuspended) {
    statusKey = 'suspended';
    statusLabel = '거래정지';
    strategyStatus = 'SUSPENDED';
  } else if (
    stratStatus === 'REVIEW_REQUIRED' ||
    (stop != null && stop > 0 && valPrice != null && stop >= valPrice && ((avg != null && stop > avg) || stratStatus === 'REVIEW_REQUIRED'))
  ) {
    statusKey = 'caution';
    statusLabel = '전략 재검토';
    strategyStatus = 'STRATEGY_REVIEW';
    if (!strategyWarning) {
      strategyWarning = '현재가가 기존 손절선 아래';
    }
  } else if (
    stopBroken ||
    (stop != null && stop > 0 && valPrice != null && stop >= valPrice)
  ) {
    statusKey = 'danger';
    statusLabel = '손절 경고';
    strategyStatus = 'STOP_BREACHED';
    if (!strategyWarning) {
      strategyWarning = '현재가가 손절선 아래';
    }
  } else if (
    String(cfg.TARGET_SOURCE || '').toUpperCase() === 'UNCONFIGURED' ||
    (stop == null && wa_num_(cfg.TARGET1) == null)
  ) {
    statusKey = 'caution';
    statusLabel = '전략 미설정';
    strategyStatus = 'UNCONFIGURED';
    if (!strategyWarning) {
      strategyWarning = '전략 목표/손절 미지정';
    }
  } else if (
    wa_num_(cfg.TARGET1) != null &&
    valPrice != null &&
    wa_num_(cfg.TARGET1) <= valPrice
  ) {
    statusKey = 'caution';
    statusLabel = '목표가 도달';
    strategyStatus = 'TARGET_REACHED';
    if (!strategyWarning) {
      strategyWarning = '1차 목표가 도달';
    }
  } else if (
    Math.abs(
      changePct || 0
    ) >= 5 ||
    lastBand.includes('5')
  ) {
    statusKey = 'danger';
    statusLabel = '경고';
  } else if (
    Math.abs(
      changePct || 0
    ) >= 3 ||
    (
      volumePace != null &&
      volumePace >= 2
    ) ||
    lastBand.includes('3')
  ) {
    statusKey = 'caution';
    statusLabel = '주의';
  }
  const stopGapPct =
    (
      price != null &&
      stop != null &&
      stop !== 0
    )
      ? (
          (price - stop) /
          stop
        ) * 100
      : null;
  const costAmt = (qty != null && avg != null) ? qty * avg : null;
  const mktVal = (qty != null && valPrice != null) ? qty * valPrice : null;
  const pl = (mktVal != null && costAmt != null) ? mktVal - costAmt : null;
  const plPct = (valPrice != null && avg != null && avg !== 0) ? ((valPrice / avg) - 1) * 100 : null;
  const dayPl = isSuspended ? null : ((price != null && wa_num_(quote.PREV_CLOSE) != null && qty != null) ? (price - wa_num_(quote.PREV_CLOSE)) * qty : null);
  const dayPlPct = isSuspended ? null : ((price != null && wa_num_(quote.PREV_CLOSE) != null && wa_num_(quote.PREV_CLOSE) !== 0) ? ((price / wa_num_(quote.PREV_CLOSE)) - 1) * 100 : null);
  const t1 = wa_num_(cfg.TARGET1);
  const t2 = wa_num_(cfg.TARGET2);
  const tSource = String(cfg.TARGET_SOURCE || '').trim();
  const tBaseDate = wa_fmtMaybeDate_(cfg.TARGET_BASE_DATE) || String(cfg.TARGET_BASE_DATE || '').trim();
  const t1Gap = (t1 != null && valPrice != null && valPrice !== 0) ? ((t1 / valPrice) - 1) * 100 : null;
  const t2Gap = (t2 != null && valPrice != null && valPrice !== 0) ? ((t2 / valPrice) - 1) * 100 : null;
  const atr14Raw = cfg.ATR14;
  const atr14 = wa_num_(atr14Raw);
  const buyTrailingDist = wa_num_(cfg.BUY_TRAILING_DIST);
  const sellTrailingDist = wa_num_(cfg.SELL_TRAILING_DIST);
  const atrBaseDate = wa_fmtMaybeDate_(cfg.ATR_BASE_DATE).replace(/\s+\d{2}:\d{2}$/, '');
  let atrText = 'ATR -';
  let atrDetailText = '-';
  if (isSuspended) {
    if (atr14 != null && atr14 > 0) {
      let datePart = '';
      if (atrBaseDate) {
        const cleanDate = atrBaseDate.replace(/-/g, '').trim();
        if (cleanDate.length >= 8) {
          datePart = ' · 기준일 ' + cleanDate.slice(4, 6) + '/' + cleanDate.slice(6, 8);
        } else {
          datePart = ' · 기준일 ' + atrBaseDate;
        }
      }
      atrText = 'ATR14 ' + Math.round(atr14).toLocaleString() + '원' + datePart;
    } else {
      atrText = 'ATR - (거래정지)';
    }
    atrDetailText = '- (거래정지)';
  } else if (atr14 != null && atr14 > 0) {
    const atrRound = Math.round(atr14);
    atrText = 'ATR14 ' + atrRound.toLocaleString() + '원';
    const buyDist = buyTrailingDist != null ? Math.round(buyTrailingDist) : Math.round(atr14 * 0.6);
    const sellDist = sellTrailingDist != null ? Math.round(sellTrailingDist) : Math.round(atr14 * 0.4);
    atrDetailText = '0.6ATR ' + buyDist.toLocaleString() + '원 · 0.4ATR ' + sellDist.toLocaleString() + '원';
  } else {
    atrText = 'ATR -';
    atrDetailText = '-';
  }
  return {
    ticker,
    name,
    exchange,
    qty,
    avgPrice: avg,
    refClose,
    stopPrice: stop,
    price,
    changePct,
    volume,
    volumePace,
    tradeTime:
      wa_fmtMaybeDate_(tradeTime),
    dataDelay:
      quote.DATA_DELAY == null
        ? ''
        : String(
            quote.DATA_DELAY
          ),
    statusKey,
    statusLabel,
    stopGapPct,
    high:
      wa_num_(quote.HIGH),
    low:
      wa_num_(quote.LOW),
    valuationPrice: valPrice,
    costAmount: costAmt,
    marketValue: mktVal,
    profitLoss: pl,
    profitLossPct: plPct,
    dayProfitLoss: dayPl,
    dayProfitLossPct: dayPlPct,
    target1: t1,
    target2: t2,
    targetSource: tSource,
    targetBaseDate: tBaseDate,
    target1GapPct: t1Gap,
    target2GapPct: t2Gap,
    prevClose: wa_num_(quote.PREV_CLOSE),
    strategyStatus,
    strategyWarning,
    atr14,
    buyTrailingDist,
    sellTrailingDist,
    atrBaseDate,
    atrText,
    atrDetailText
  };
}
/**
 * 알림 로그 한 줄 가공
 */
function wa_buildAlert_(r) {
  return {
    timestamp:
      wa_fmtMaybeDate_(
        r.TIMESTAMP
      ),
    ticker:
      wa_ticker_(r.TICKER),
    name:
      String(
        r.NAME || ''
      ),
    eventKey:
      String(
        r.EVENT_KEY || ''
      ),
    level:
      String(
        r.LEVEL || ''
      ),
    price:
      wa_num_(r.PRICE),
    changePct:
      wa_normalizePct_(
        r.CHANGE_PCT
      ),
    stopPrice:
      wa_num_(r.STOP_PRICE),
    volumePace:
      wa_num_(r.VOLUME_PACE),
    tradeTime:
      wa_fmtMaybeDate_(
        r.TRADE_TIME
      ),
    message:
      String(
        r.MESSAGE || ''
      )
  };
}
/**
 * 보유종목 평균 등락
 */
function wa_buildMarketSummary_(
  positions
) {
  const active =
    positions.filter(
      p =>
        p.statusKey !==
          'suspended' &&
        p.changePct != null
    );
  const avg =
    active.length
      ? active.reduce(
          (sum, p) =>
            sum +
            p.changePct,
          0
        ) /
        active.length
      : null;
  const up =
    active.filter(
      p =>
        p.changePct > 0
    ).length;
  const down =
    active.filter(
      p =>
        p.changePct < 0
    ).length;
  let totalCostAmount = 0;
  let totalMarketValue = 0;
  let hasValuation = false;
  let totalDayProfitLoss = 0;
  let dayBaseValue = 0;
  let hasDayValuation = false;
  positions.forEach(p => {
    if (p.costAmount != null && p.marketValue != null) {
      totalCostAmount += p.costAmount;
      totalMarketValue += p.marketValue;
      hasValuation = true;
    }
    if (p.statusKey !== 'suspended' && p.dayProfitLoss != null && p.qty != null && p.prevClose != null) {
      totalDayProfitLoss += p.dayProfitLoss;
      dayBaseValue += (p.prevClose * p.qty);
      hasDayValuation = true;
    }
  });
  const totalProfitLoss = hasValuation ? totalMarketValue - totalCostAmount : null;
  const totalProfitLossPct = (hasValuation && totalCostAmount !== 0) ? (totalMarketValue / totalCostAmount - 1) * 100 : null;
  const totalDayProfitLossPct = (hasDayValuation && dayBaseValue !== 0) ? (totalDayProfitLoss / dayBaseValue) * 100 : null;
  return {
    avgChangePct: avg,
    up,
    down,
    totalCostAmount,
    totalMarketValue,
    totalProfitLoss,
    totalProfitLossPct,
    totalDayProfitLoss,
    totalDayProfitLossPct
  };
}
/**
 * 퍼센트 값 정규화
 */
function wa_normalizePct_(v) {
  // ALERT_LOG.CHANGE_PCT uses the same fractional unit as LIVE_QUOTES.
  const n = wa_num_(v);
  return n == null ? null : n * 100;
}
/**
 * 숫자 변환
 */
function wa_num_(v) {
  if (
    v === '' ||
    v == null
  ) {
    return null;
  }
  const n =
    Number(v);
  return Number.isFinite(n)
    ? n
    : null;
}
/**
 * TRUE/FALSE 계열 값 판정
 */
function wa_truthy_(v) {
  if (v === true) {
    return true;
  }
  const s =
    String(v || '')
      .trim()
      .toLowerCase();
  return [
    'true',
    '1',
    'y',
    'yes',
    'on'
  ].includes(s);
}
/**
 * 정렬용 timestamp
 */
function wa_toMillis_(v) {
  if (v instanceof Date) {
    return v.getTime();
  }
  /*
   * Google Sheets 날짜 일련번호
   */
  if (
    typeof v === 'number' &&
    v > 20000 &&
    v < 80000
  ) {
    return (
      v - 25569
    ) *
    24 *
    60 *
    60 *
    1000;
  }
  const d =
    new Date(v);
  return isNaN(
    d.getTime()
  )
    ? 0
    : d.getTime();
}
/**
 * 현재 생성시각
 */
function wa_fmtDateTime_(d) {
  return Utilities.formatDate(
    d,
    WEBAPP_CFG.TIMEZONE,
    'yyyy.MM.dd HH:mm:ss'
  );
}
/**
 * 시트의 날짜/시간 값을 웹 표시용으로 변환
 */
function wa_fmtMaybeDate_(v) {
  if (
    v === '' ||
    v == null
  ) {
    return '';
  }
  /*
   * Google Sheets 날짜 일련번호
   *
   * 예:
   * 46288.64589
   * → 실제 시트의 날짜 + 시간
   */
  if (
    typeof v === 'number' &&
    v > 20000 &&
    v < 80000
  ) {
    const ms =
      (
        v - 25569
      ) *
      24 *
      60 *
      60 *
      1000;
    return Utilities.formatDate(
      new Date(ms),
      'UTC',
      'MM.dd HH:mm'
    );
  }
  const d =
    v instanceof Date
      ? v
      : new Date(v);
  if (
    isNaN(
      d.getTime()
    )
  ) {
    return String(v);
  }
  return Utilities.formatDate(
    d,
    WEBAPP_CFG.TIMEZONE,
    'MM.dd HH:mm'
  );
}
/** A ticker may have been stored as a number by an older log writer. */
function wa_ticker_(value) {
  const text = String(value == null ? '' : value).trim();
  return /^\d{1,6}$/.test(text) ? text.padStart(6, '0') : text;
}
