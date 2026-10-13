const CFG = {
  TIMEZONE: 'Asia/Seoul',

  SHEET_CONFIG: 'MONITOR_CONFIG',
  SHEET_QUOTES: 'LIVE_QUOTES',
  SHEET_STATE: 'ALERT_STATE',
  SHEET_LOG: 'ALERT_LOG',

  BAND_3: 3.0,
  BAND_5: 5.0,

  VOLUME_PACE_THRESHOLD: 2.0,
  VOLUME_MIN_MINUTES_FROM_OPEN: 30,

  MAX_STALE_MINUTES: 35,
  COOLDOWN_MINUTES: 30,

  MARKET_OPEN_HOUR: 9,
  MARKET_OPEN_MINUTE: 0,
  MARKET_CLOSE_HOUR: 15,
  MARKET_CLOSE_MINUTE: 30,

  // 시험운전: 메일 발송 안 함
  EMAIL_ENABLED: true,

  ALERT_EMAIL: 'jujinu0410@gmail.com'
};


function setupIntradayMonitor() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();

  PropertiesService
    .getScriptProperties()
    .setProperty('MONITOR_SPREADSHEET_ID', ss.getId());

  install10MinuteTrigger();

  Logger.log('Intraday monitor setup completed.');
}


function monitorIntraday() {
  if (typeof umEnabled_ === "function" && umEnabled_()) return umTick5m();
  const lock = LockService.getScriptLock();

  if (!lock.tryLock(5000)) return;

  try {
    const now = new Date();

    if (!isMonitoringWindow_(now)) return;

    const ss = getSpreadsheet_();

    const configSheet = ss.getSheetByName(CFG.SHEET_CONFIG);
    const quotesSheet = ss.getSheetByName(CFG.SHEET_QUOTES);
    const stateSheet = ss.getSheetByName(CFG.SHEET_STATE);
    const logSheet = ss.getSheetByName(CFG.SHEET_LOG);

    if (!configSheet || !quotesSheet || !stateSheet || !logSheet) {
      throw new Error('Required monitoring sheet is missing.');
    }

    const configs = readConfigs_(configSheet);
    const quotes = readQuotes_(quotesSheet);
    const states = readStates_(stateSheet);

    const today = formatDate_(now);

    for (const ticker of Object.keys(configs)) {
      const cfg = configs[ticker];

      if (!cfg.enabled) continue;

      if (
        cfg.status === 'SUSPENDED' ||
        cfg.status === 'SUSPENDED_HOLD' ||
        cfg.status === 'SOLD'
      ) {
        continue;
      }

      const q = quotes[ticker];

      if (!q) continue;

      if (
        !isFiniteNumber_(q.price) ||
        !isFiniteNumber_(q.prevClose) ||
        q.prevClose <= 0
      ) {
        continue;
      }

      if (!(q.tradeTime instanceof Date) || isNaN(q.tradeTime.getTime())) {
        continue;
      }

      const tradeDate = formatDate_(q.tradeTime);

      if (tradeDate !== today) continue;

      const staleMinutes =
        (now.getTime() - q.tradeTime.getTime()) / 60000;

      if (
        staleMinutes < -5 ||
        staleMinutes > CFG.MAX_STALE_MINUTES
      ) {
        continue;
      }

      let state = states[ticker];

      if (!state || state.stateDate !== today) {
        state = {
          ticker,
          stateDate: today,
          prevPrice: null,
          lastBand: 'NORMAL',
          stopBroken: false,
          volumeAlerted: false,
          lastTradeTime: null,
          cooldowns: {},
          updatedAt: null
        };
      }

      const changePct =
        ((q.price - q.prevClose) / q.prevClose) * 100;

      const volumePace =
        calculateVolumePace_(q.volume, q.volumeAvg, now);

      writeVolumePace_(quotesSheet, q.row, volumePace);

      const events = [];

      // 4) 목표가 감시
      const t1 = cfg.target1;
      const t2 = cfg.target2;

      let targetValid = false;
      if (t1 != null || t2 != null) {
        if (t1 != null && t2 != null && t2 <= t1) {
          targetValid = false;
        } else {
          targetValid = true;
        }
      }

      if (targetValid && isFiniteNumber_(state.prevPrice)) {
        const pPrice = state.prevPrice;
        const cPrice = q.price;

        if (t1 != null) {
          const t1Near = t1 * 0.95;
          if (pPrice < t1Near && cPrice >= t1Near) {
            const key = 'TARGET1_NEAR';
            if (canSendEvent_(state, key, now)) {
              events.push({
                key, level: 'WARNING', isTarget: true, targetType: 'TARGET1_NEAR',
                text: `목표1 접근: ${formatNumber_(cPrice)}원 (목표 ${formatNumber_(t1)}원의 95% 도달)`
              });
              markEventSent_(state, key, now);
            }
          }
          
          if (pPrice < t1 && cPrice >= t1) {
            const key = 'TARGET1_REACHED';
            if (canSendEvent_(state, key, now)) {
              events.push({
                key, level: 'WARNING', isTarget: true, targetType: 'TARGET1_REACHED',
                text: `목표1 도달: ${formatNumber_(cPrice)}원 (목표 ${formatNumber_(t1)}원)`
              });
              markEventSent_(state, key, now);
            }
          }
        }

        if (t2 != null) {
          if (pPrice < t2 && cPrice >= t2) {
            const key = 'TARGET2_REACHED';
            if (canSendEvent_(state, key, now)) {
              events.push({
                key, level: 'WARNING', isTarget: true, targetType: 'TARGET2_REACHED',
                text: `목표2 도달: ${formatNumber_(cPrice)}원 (목표 ${formatNumber_(t2)}원)`
              });
              markEventSent_(state, key, now);
            }
          }
        }
      }

      // 1) 손절선 신규 하향 돌파
      if (
        isFiniteNumber_(cfg.stopPrice) &&
        cfg.stopPrice > 0 &&
        isFiniteNumber_(state.prevPrice)
      ) {
        const newlyBroken =
          state.prevPrice > cfg.stopPrice &&
          q.price <= cfg.stopPrice;

        if (newlyBroken) {
          const key = 'STOP_BREAK';

          if (canSendEvent_(state, key, now)) {
            events.push({
              key,
              level: 'CRITICAL',
              text:
                `손절선 신규 하향 돌파: ${formatNumber_(q.price)}원 ≤ ${formatNumber_(cfg.stopPrice)}원`
            });

            markEventSent_(state, key, now);
          }
        }
      }

      if (
        isFiniteNumber_(cfg.stopPrice) &&
        q.price > cfg.stopPrice
      ) {
        state.stopBroken = false;
      }

      if (
        isFiniteNumber_(cfg.stopPrice) &&
        q.price <= cfg.stopPrice
      ) {
        state.stopBroken = true;
      }

      // 2) ±3%, ±5% 상태전환
      const newBand = getBand_(changePct);

      if (newBand !== state.lastBand) {
        if (shouldAlertBand_(state.lastBand, newBand)) {
          const key = `BAND:${newBand}`;

          if (canSendEvent_(state, key, now)) {
            const level =
              newBand === '+5' || newBand === '-5'
                ? 'CRITICAL'
                : 'WARNING';

            events.push({
              key,
              level,
              text:
                `등락률 ${newBand}% 구간 진입: ${formatPct_(changePct)}`
            });

            markEventSent_(state, key, now);
          }
        }

        state.lastBand = newBand;
      }

      // 3) 거래량 속도 2배
      if (
        volumePace !== null &&
        volumePace >= CFG.VOLUME_PACE_THRESHOLD &&
        !state.volumeAlerted
      ) {
        const key = 'VOLUME_PACE_2X';

        if (canSendEvent_(state, key, now)) {
          events.push({
            key,
            level: 'WARNING',
            text:
              `거래량 속도 이상: 정상 예상의 ${volumePace.toFixed(2)}배`
          });

          markEventSent_(state, key, now);
          state.volumeAlerted = true;
        }
      }

      if (events.length > 0) {
        // 시험운전: 메일은 보내지 않고 로그만 기록
        appendAlertLogs_(
          logSheet,
          cfg,
          q,
          changePct,
          volumePace,
          events,
          now
        );

        if (CFG.EMAIL_ENABLED) {
          sendAlertEmail_(
            cfg,
            q,
            changePct,
            volumePace,
            events
          );
        }
      }

      state.prevPrice = q.price;
      state.lastTradeTime = q.tradeTime;
      state.updatedAt = now;

      states[ticker] = state;
    }

    saveStates_(stateSheet, states);

  } finally {
    lock.releaseLock();
  }
}


function getBand_(pct) {
  if (pct >= CFG.BAND_5) return '+5';
  if (pct >= CFG.BAND_3) return '+3';
  if (pct <= -CFG.BAND_5) return '-5';
  if (pct <= -CFG.BAND_3) return '-3';
  return 'NORMAL';
}


function shouldAlertBand_(oldBand, newBand) {
  if (newBand === 'NORMAL') return false;
  if (oldBand === newBand) return false;

  const severity = {
    NORMAL: 0,
    '+3': 1,
    '-3': 1,
    '+5': 2,
    '-5': 2
  };

  if (
    oldBand !== 'NORMAL' &&
    oldBand.charAt(0) !== newBand.charAt(0)
  ) {
    return true;
  }

  return severity[newBand] > severity[oldBand];
}


function calculateVolumePace_(volume, volumeAvg, now) {
  if (
    !isFiniteNumber_(volume) ||
    !isFiniteNumber_(volumeAvg) ||
    volumeAvg <= 0
  ) {
    return null;
  }

  const minutes = minutesFromMarketOpen_(now);

  if (minutes < CFG.VOLUME_MIN_MINUTES_FROM_OPEN) {
    return null;
  }

  const totalMinutes = 390;

  const elapsedRatio =
    Math.min(Math.max(minutes / totalMinutes, 0), 1);

  if (elapsedRatio <= 0) return null;

  const expectedVolume =
    volumeAvg * elapsedRatio;

  if (expectedVolume <= 0) return null;

  return volume / expectedVolume;
}


function appendAlertLogs_(
  sheet,
  cfg,
  q,
  changePct,
  volumePace,
  events,
  now
) {
  const rows = events.map(e => [
    now,
    cfg.ticker,
    cfg.name,
    e.key,
    e.level,
    q.price,
    changePct,
    cfg.stopPrice || '',
    volumePace === null ? '' : volumePace,
    q.tradeTime,
    e.text
  ]);

  if (rows.length > 0) {
    sheet
      .getRange(
        sheet.getLastRow() + 1,
        1,
        rows.length,
        rows[0].length
      )
      .setValues(rows);
  }
}


function sendAlertEmail_(
  cfg,
  q,
  changePct,
  volumePace,
  events
) {
  const critical =
    events.some(e => e.level === 'CRITICAL');

  const icon = critical ? '🔴' : '🟠';

  const eventSummary =
    events.map(e => e.text).join(' / ');

  const subject =
    `${icon} [장중감시] ${cfg.name} - ${eventSummary}`;

  const stopText =
    isFiniteNumber_(cfg.stopPrice)
      ? `${formatNumber_(cfg.stopPrice)}원`
      : '-';

  const volumeText =
    volumePace === null
      ? '-'
      : `${volumePace.toFixed(2)}배`;

  const targetEvents = events.filter(e => e.isTarget);
  let finalSubject = subject;
  let finalBody = '';

  if (targetEvents.length > 0) {
    // 우선순위: TARGET2_REACHED > TARGET1_REACHED > TARGET1_NEAR
    const priority = {
      'TARGET2_REACHED': 3,
      'TARGET1_REACHED': 2,
      'TARGET1_NEAR': 1
    };
    targetEvents.sort((a, b) => (priority[b.targetType] || 0) - (priority[a.targetType] || 0));
    const highestTarget = targetEvents[0];

    const isT2 = highestTarget.targetType === 'TARGET2_REACHED';
    const isT1Near = highestTarget.targetType === 'TARGET1_NEAR';

    const tLabel = isT2 ? '목표2' : '목표1';
    const tState = isT1Near ? '접근' : '도달';
    finalSubject = `[${tLabel} ${tState}] ${cfg.name}`;

    const targetPrice = isT2 ? cfg.target2 : cfg.target1;
    const currentPrice = q.price;

    let diffLine = '';
    if (isT1Near) {
      const gapPct = ((targetPrice / currentPrice) - 1) * 100;
      diffLine = `${tLabel}까지 ${formatPct_(gapPct)}`;
    } else {
      const overPct = ((currentPrice / targetPrice) - 1) * 100;
      diffLine = `목표 초과 ${formatPct_(overPct)}`;
    }

    const qty = cfg.qty;
    const avg = cfg.avgPrice;
    const pl = (qty != null && avg != null) ? (currentPrice * qty) - (avg * qty) : null;
    const plPct = (avg != null && avg !== 0) ? (currentPrice / avg - 1) * 100 : null;

    let multiSummary = '';
    if (events.length > 1) {
      multiSummary = events.map(e => `• ${e.text}`).join('\n') + '\n\n';
    }

    finalBody = `[${tLabel} ${tState}] ${cfg.name}

${multiSummary}현재가 ${formatNumber_(currentPrice)}원
${tLabel} ${formatNumber_(targetPrice)}원
${diffLine}

평균단가 ${formatNumber_(avg)}원
평가손익 ${pl != null ? (pl > 0 ? '+' : '') + formatNumber_(pl) : '-'}원
수익률 ${plPct != null ? formatPct_(plPct) : '-'}

출처 ${cfg.targetSource || '-'}
기준일 ${cfg.targetBaseDate instanceof Date ? formatDate_(cfg.targetBaseDate) : (cfg.targetBaseDate || '-')}
`;
  } else {
    finalBody = `${cfg.name} (${cfg.ticker})

${events.map(e => `• ${e.text}`).join('\n')}

현재가: ${formatNumber_(q.price)}원
전일종가: ${formatNumber_(q.prevClose)}원
등락률: ${formatPct_(changePct)}
손절선: ${stopText}
거래량 Pace: ${volumeText}
마지막 거래시각: ${formatDateTime_(q.tradeTime)}

※ GOOGLEFINANCE 지연시세 기반 보조 감시입니다.
※ 자동매매 신호가 아닙니다.`;
  }

  MailApp.sendEmail({
    to: CFG.ALERT_EMAIL,
    subject: finalSubject,
    body: finalBody
  });
}


function canSendEvent_(state, eventKey, now) {
  const last = state.cooldowns[eventKey];

  if (!last) return true;

  const lastDate = new Date(last);

  if (isNaN(lastDate.getTime())) return true;

  const minutes =
    (now.getTime() - lastDate.getTime()) / 60000;

  return minutes >= CFG.COOLDOWN_MINUTES;
}


function markEventSent_(state, eventKey, now) {
  state.cooldowns[eventKey] =
    now.toISOString();
}


function readConfigs_(sheet) {
  const values = sheet.getDataRange().getValues();
  const result = {};

  for (let i = 1; i < values.length; i++) {
    const r = values[i];

    const ticker =
      String(r[0] || '').trim();

    if (!ticker) continue;

    result[ticker] = {
      ticker,
      exchange: String(r[1] || '').trim(),
      name: String(r[2] || '').trim(),
      qty: toNumber_(r[3]),
      avgPrice: toNumber_(r[4]),
      refClose: toNumber_(r[5]),
      stopPrice: toNumber_(r[6]),
      status: String(r[7] || '').trim().toUpperCase(),
      baseDate: r[8],
      enabled:
        r[9] === true ||
        String(r[9]).toUpperCase() === 'TRUE',
      target1: toNumber_(r[10]),
      target2: toNumber_(r[11]),
      targetSource: String(r[12] || '').trim(),
      targetBaseDate: r[13]
    };
  }

  return result;
}


function readQuotes_(sheet) {
  const values = sheet.getDataRange().getValues();
  const result = {};

  for (let i = 1; i < values.length; i++) {
    const r = values[i];

    const ticker =
      String(r[0] || '').trim();

    if (!ticker) continue;

    result[ticker] = {
      row: i + 1,
      ticker,
      exchange: r[1],
      name: r[2],
      price: toNumber_(r[3]),
      prevClose: toNumber_(r[4]),
      changePct: toNumber_(r[5]),
      high: toNumber_(r[6]),
      low: toNumber_(r[7]),
      volume: toNumber_(r[8]),
      volumeAvg: toNumber_(r[9]),
      tradeTime: r[10],
      dataDelay: toNumber_(r[11])
    };
  }

  return result;
}


function readStates_(sheet) {
  const values = sheet.getDataRange().getValues();
  const result = {};

  for (let i = 1; i < values.length; i++) {
    const r = values[i];

    const ticker =
      String(r[0] || '').trim();

    if (!ticker) continue;

    let cooldowns = {};

    try {
      cooldowns = r[7]
        ? JSON.parse(r[7])
        : {};
    } catch (e) {
      cooldowns = {};
    }

    result[ticker] = {
      ticker,
      stateDate: String(r[1] || ''),
      prevPrice: toNumber_(r[2]),
      lastBand: String(r[3] || 'NORMAL'),
      stopBroken: r[4] === true,
      volumeAlerted: r[5] === true,
      lastTradeTime: r[6],
      cooldowns,
      updatedAt: r[8]
    };
  }

  return result;
}


function saveStates_(sheet, states) {
  const tickers =
    Object.keys(states).sort();

  const rows = tickers.map(ticker => {
    const s = states[ticker];

    return [
      ticker,
      s.stateDate,
      s.prevPrice === null ? '' : s.prevPrice,
      s.lastBand,
      s.stopBroken,
      s.volumeAlerted,
      s.lastTradeTime || '',
      JSON.stringify(s.cooldowns || {}),
      s.updatedAt || ''
    ];
  });

  if (sheet.getLastRow() > 1) {
    sheet
      .getRange(
        2,
        1,
        sheet.getLastRow() - 1,
        9
      )
      .clearContent();
  }

  if (rows.length > 0) {
    sheet
      .getRange(2, 1, rows.length, 9)
      .setValues(rows);
  }
}


function writeVolumePace_(sheet, row, value) {
  if (value === null) {
    sheet.getRange(row, 13).clearContent();
  } else {
    sheet.getRange(row, 13).setValue(value);
  }
}


function install10MinuteTrigger() {
  const triggers =
    ScriptApp.getProjectTriggers();

  for (const trigger of triggers) {
    if (
      trigger.getHandlerFunction() ===
      'monitorIntraday'
    ) {
      ScriptApp.deleteTrigger(trigger);
    }
  }

  ScriptApp
    .newTrigger('monitorIntraday')
    .timeBased()
    .everyMinutes(10)
    .create();
}


function isMonitoringWindow_(date) {
  const parts =
    Utilities.formatDate(
      date,
      CFG.TIMEZONE,
      'u,H,m'
    ).split(',');

  const weekday = Number(parts[0]);
  const hour = Number(parts[1]);
  const minute = Number(parts[2]);

  if (weekday >= 6) return false;

  const current = hour * 60 + minute;

  const open =
    CFG.MARKET_OPEN_HOUR * 60 +
    CFG.MARKET_OPEN_MINUTE;

  const close =
    CFG.MARKET_CLOSE_HOUR * 60 +
    CFG.MARKET_CLOSE_MINUTE;

  return current >= open && current <= close;
}


function minutesFromMarketOpen_(date) {
  const hm =
    Utilities.formatDate(
      date,
      CFG.TIMEZONE,
      'H,m'
    ).split(',');

  const hour = Number(hm[0]);
  const minute = Number(hm[1]);

  return (
    hour * 60 +
    minute -
    (CFG.MARKET_OPEN_HOUR * 60 +
      CFG.MARKET_OPEN_MINUTE)
  );
}


function getSpreadsheet_() {
  const id =
    PropertiesService
      .getScriptProperties()
      .getProperty('MONITOR_SPREADSHEET_ID');

  if (!id) {
    throw new Error(
      'Run setupIntradayMonitor() once first.'
    );
  }

  return SpreadsheetApp.openById(id);
}


function formatDate_(date) {
  return Utilities.formatDate(
    date,
    CFG.TIMEZONE,
    'yyyy-MM-dd'
  );
}


function formatDateTime_(date) {
  return Utilities.formatDate(
    date,
    CFG.TIMEZONE,
    'yyyy-MM-dd HH:mm:ss'
  );
}


function formatNumber_(value) {
  if (!isFiniteNumber_(value)) return '-';

  return Math.round(value)
    .toLocaleString('ko-KR');
}


function formatPct_(value) {
  if (!isFiniteNumber_(value)) return '-';

  const sign =
    value > 0 ? '+' : '';

  return `${sign}${value.toFixed(2)}%`;
}


function toNumber_(value) {
  if (
    value === '' ||
    value === null ||
    value === undefined
  ) {
    return null;
  }

  const n = Number(value);

  return Number.isFinite(n)
    ? n
    : null;
}


function isFiniteNumber_(value) {
  return (
    typeof value === 'number' &&
    Number.isFinite(value)
  );
}
  

