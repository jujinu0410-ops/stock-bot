/**
 * Daily Close Sync
 *
 * 15:35 StockBot closing report -> next-day source-of-truth sheets.
 *
 * Sources
 * - Gmail body: confirmed STOP / TARGET displayed to the user
 * - portfolio_monitoring_*.csv: qty / avg / close / ATR / P0 / mode
 *
 * Destinations
 * - PORTFOLIO_CONFIG: 07:00 morning briefing source
 * - MONITOR_CONFIG: next-day intraday GF monitor source
 *
 * Safety
 * - validate everything in memory before writes
 * - fail closed on missing/ambiguous data
 * - best-effort rollback if either spreadsheet write fails
 * - once-per-day success marker
 */
const DCS = Object.freeze({
  TZ: 'Asia/Seoul',
  PORTFOLIO_SPREADSHEET_ID: '1VZR4-khOQjcWvLvZWoHwjXeDFuTamuaXp7wk7JNbxbM',
  PORTFOLIO_SHEET: 'PORTFOLIO_CONFIG',
  MONITOR_SPREADSHEET_ID: '15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw',
  MONITOR_SHEET: 'MONITOR_CONFIG',
  LOG_SHEET: 'SYNC_LOG',
  PROP_LAST_SUCCESS: 'DAILY_CLOSE_SYNC_LAST_SUCCESS',
  REPORT_SUBJECT: '[장마감 리포트 (15:35)]',
  WINDOW_START_HHMM: 1538,
  WINDOW_END_HHMM: 1605,
  MIN_HOLDINGS: 1,
  MAX_HOLDINGS: 30,
  SOURCE_TAG: 'GMAIL_1535_V4',
  PORTFOLIO_HEADERS: [
    'Active','종목명','시장','종목코드','Ticker','보유수량',
    'ReferencePrice(직전종가)','ReferenceATR14','P0_Anchor(참고)','매매모드'
  ],
  MONITOR_HEADERS: [
    'TICKER','EXCHANGE','NAME','QTY','AVG_PRICE','REF_CLOSE','STOP_PRICE','STATUS',
    'BASE_DATE','ENABLED','TARGET1','TARGET2','TARGET_SOURCE','TARGET_BASE_DATE',
    'STRATEGY_STATUS','STRATEGY_WARNING','ATR14','BUY_TRAILING_DIST',
    'SELL_TRAILING_DIST','ATR_BASE_DATE'
  ]
});

function dcsAuthorizeGmailOnce() {
  const threads = GmailApp.search('subject:"' + DCS.REPORT_SUBJECT + '" newer_than:7d -in:trash -in:spam', 0, 1);
  SpreadsheetApp.getUi().alert(
    'Gmail 권한 승인 완료',
    '15:35 장마감 리포트 검색 권한이 연결되었습니다. 최근 일치 스레드: ' + threads.length,
    SpreadsheetApp.getUi().ButtonSet.OK
  );
  return threads.length;
}

function dcsRunNow() {
  return dcsRun_(new Date(), true);
}

function dcsScheduledTick_() {
  const now = new Date();
  const hhmm = Number(Utilities.formatDate(now, DCS.TZ, 'HHmm'));
  if (hhmm < DCS.WINDOW_START_HHMM || hhmm > DCS.WINDOW_END_HHMM) {
    return {status:'OUTSIDE_SYNC_WINDOW'};
  }
  return dcsRun_(now, false);
}

function dcsRun_(now, force) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) return {status:'LOCKED_SKIP'};

  try {
    const dayKey = Utilities.formatDate(now, DCS.TZ, 'yyyy-MM-dd');
    const props = PropertiesService.getScriptProperties();
    if (!force && props.getProperty(DCS.PROP_LAST_SUCCESS) === dayKey) {
      return {status:'ALREADY_SYNCED', day:dayKey};
    }

    const report = dcsFindReportForDate_(dayKey);
    if (!report) {
      dcsAppendLog_(now, 'SKIP_NO_REPORT', '', 0, '', '당일 15:35 장마감 리포트 없음');
      return {status:'NO_REPORT', day:dayKey};
    }

    const parsed = dcsParseReport_(report.message, dayKey);
    dcsValidateParsed_(parsed, report.message, dayKey);

    const portfolioSS = SpreadsheetApp.openById(DCS.PORTFOLIO_SPREADSHEET_ID);
    const monitorSS = SpreadsheetApp.openById(DCS.MONITOR_SPREADSHEET_ID);
    const portfolioSheet = portfolioSS.getSheetByName(DCS.PORTFOLIO_SHEET);
    const monitorSheet = monitorSS.getSheetByName(DCS.MONITOR_SHEET);
    if (!portfolioSheet || !monitorSheet) {
      throw new Error('필수 시트 PORTFOLIO_CONFIG / MONITOR_CONFIG 누락');
    }

    dcsValidateHeaders_(portfolioSheet, DCS.PORTFOLIO_HEADERS);
    dcsValidateHeaders_(monitorSheet, DCS.MONITOR_HEADERS);

    const oldPortfolio = dcsReadDataBlock_(portfolioSheet, DCS.PORTFOLIO_HEADERS.length);
    const oldMonitor = dcsReadDataBlock_(monitorSheet, DCS.MONITOR_HEADERS.length);
    const oldPortfolioMap = dcsRowsByCode_(oldPortfolio.values, 3);
    const oldMonitorMap = dcsRowsByCode_(oldMonitor.values, 0);

    const output = dcsBuildOutput_(parsed.holdings, dayKey, oldPortfolioMap, oldMonitorMap);

    try {
      dcsReplaceDataBlock_(portfolioSheet, DCS.PORTFOLIO_HEADERS.length, output.portfolioRows);
      dcsReplaceDataBlock_(monitorSheet, DCS.MONITOR_HEADERS.length, output.monitorRows);
      SpreadsheetApp.flush();
    } catch (writeErr) {
      try {
        dcsRestoreDataBlock_(portfolioSheet, DCS.PORTFOLIO_HEADERS.length, oldPortfolio);
        dcsRestoreDataBlock_(monitorSheet, DCS.MONITOR_HEADERS.length, oldMonitor);
        SpreadsheetApp.flush();
      } catch (rollbackErr) {
        throw new Error('WRITE_FAILED_AND_ROLLBACK_FAILED: ' + writeErr.message + ' / ' + rollbackErr.message);
      }
      throw writeErr;
    }

    props.setProperty(DCS.PROP_LAST_SUCCESS, dayKey);
    const detail = [
      'holdings=' + output.monitorRows.length,
      'added=' + output.added.join('|'),
      'removed=' + output.removed.join('|'),
      'updated=' + output.updated.join('|')
    ].join('; ');
    dcsAppendLog_(now, 'SUCCESS', report.message.getId(), output.monitorRows.length, report.csvName, detail);

    return {
      status:'SUCCESS',
      day:dayKey,
      holdings:output.monitorRows.length,
      added:output.added,
      removed:output.removed,
      updated:output.updated,
      messageId:report.message.getId()
    };
  } catch (err) {
    dcsAppendLog_(now, 'ERROR', '', 0, '', String(err && err.message ? err.message : err));
    if (force) throw err;
    return {status:'ERROR', error:String(err && err.message ? err.message : err)};
  } finally {
    lock.releaseLock();
  }
}

function dcsFindReportForDate_(dayKey) {
  const query = 'subject:"' + DCS.REPORT_SUBJECT + '" newer_than:2d -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, 20);
  const messages = [];
  threads.forEach(function(t) {
    t.getMessages().forEach(function(m) { messages.push(m); });
  });
  messages.sort(function(a,b) { return b.getDate().getTime() - a.getDate().getTime(); });

  for (let i=0; i<messages.length; i++) {
    const m = messages[i];
    if (String(m.getSubject() || '').indexOf(DCS.REPORT_SUBJECT) < 0) continue;
    const body = String(m.getPlainBody() || '');
    const match = body.match(/기준일시:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})/);
    if (!match || match[1] !== dayKey) continue;

    const attachments = m.getAttachments({includeInlineImages:false, includeAttachments:true});
    const portfolioCsv = attachments.find(function(a) {
      return /^portfolio_monitoring_\d{8}_\d{6}\.csv$/i.test(String(a.getName() || ''));
    });
    if (!portfolioCsv) continue;

    return {
      message:m,
      csv:portfolioCsv,
      csvName:portfolioCsv.getName()
    };
  }
  return null;
}

function dcsParseReport_(message, dayKey) {
  const body = String(message.getPlainBody() || '');
  const attachments = message.getAttachments({includeInlineImages:false, includeAttachments:true});
  const portfolioCsv = attachments.find(function(a) {
    return /^portfolio_monitoring_\d{8}_\d{6}\.csv$/i.test(String(a.getName() || ''));
  });
  if (!portfolioCsv) throw new Error('portfolio_monitoring CSV 첨부 누락');

  const csvText = portfolioCsv.getDataAsString('UTF-8').replace(/^\uFEFF/, '');
  const table = Utilities.parseCsv(csvText);
  if (!table || table.length < 2) throw new Error('portfolio_monitoring CSV 데이터 없음');

  const headers = table[0].map(dcsNormalizeHeader_);
  const idx = {
    code:dcsHeaderIndex_(headers,['종목코드']),
    name:dcsHeaderIndex_(headers,['종목명']),
    mode:dcsHeaderIndex_(headers,['매매모드']),
    p0:dcsHeaderIndex_(headers,['기준가격P0감시개시가원','기준가격P0']),
    avg:dcsHeaderIndex_(headers,['매입평단가원','매입평단가']),
    close:dcsHeaderIndex_(headers,['현재가원','현재가']),
    atr:dcsHeaderIndex_(headers,['현재완료봉ATRAt원','현재완료봉ATRAt','14일ATR원']),
    qty:dcsHeaderIndex_(headers,['현재보유수량','보유수량'])
  };
  Object.keys(idx).forEach(function(k) {
    if (idx[k] < 0) throw new Error('CSV 필수 열 누락: ' + k);
  });

  const bodyMap = dcsParseBodyStopsTargets_(body);
  const holdings = [];
  for (let r=1; r<table.length; r++) {
    const row = table[r];
    const code = dcsCode_(row[idx.code]);
    const qty = dcsNum_(row[idx.qty]);
    if (!code || !(qty > 0)) continue;

    const h = {
      code:code,
      name:String(row[idx.name] || '').trim(),
      mode:String(row[idx.mode] || '').trim().toUpperCase(),
      p0:dcsNum_(row[idx.p0]),
      avg:dcsNum_(row[idx.avg]),
      close:dcsNum_(row[idx.close]),
      atr:dcsNum_(row[idx.atr]),
      qty:qty,
      stop:bodyMap[code] ? bodyMap[code].stop : null,
      target:bodyMap[code] ? bodyMap[code].target : null
    };
    holdings.push(h);
  }

  return {holdings:holdings, body:body, csvName:portfolioCsv.getName(), dayKey:dayKey};
}

function dcsParseBodyStopsTargets_(body) {
  const out = {};
  let currentCode = '';
  String(body || '').split(/\r?\n/).forEach(function(raw) {
    const line = String(raw || '').trim();
    const head = line.match(/^(.+?)\s*\((\d{6})\)/);
    if (head) {
      currentCode = head[2];
      if (!out[currentCode]) out[currentCode] = {name:head[1].trim(), stop:null, target:null};
      return;
    }
    if (!currentCode || line.indexOf('손절가') < 0 || line.indexOf('익절 목표가') < 0) return;
    const stopMatch = line.match(/손절가\s+([^|]+)/);
    const targetMatch = line.match(/익절 목표가\s+(.+)$/);
    if (stopMatch) out[currentCode].stop = dcsNum_(stopMatch[1]);
    if (targetMatch) out[currentCode].target = dcsNum_(targetMatch[1]);
  });
  return out;
}

function dcsValidateParsed_(parsed, message, dayKey) {
  const rows = parsed.holdings || [];
  if (rows.length < DCS.MIN_HOLDINGS || rows.length > DCS.MAX_HOLDINGS) {
    throw new Error('보유종목 수 비정상: ' + rows.length);
  }

  const seen = {};
  rows.forEach(function(h) {
    if (!/^\d{6}$/.test(h.code)) throw new Error('종목코드 비정상: ' + h.code);
    if (seen[h.code]) throw new Error('중복 종목코드: ' + h.code);
    seen[h.code] = true;
    if (!h.name) throw new Error('종목명 누락: ' + h.code);
    if (!(h.qty > 0)) throw new Error('수량 비정상: ' + h.code);
    if (!(h.avg > 0)) throw new Error('평단 비정상: ' + h.code);
    if (!(h.close > 0)) throw new Error('종가 비정상: ' + h.code);
    if (!(h.atr >= 0)) throw new Error('ATR 비정상: ' + h.code);
    if (!(h.p0 > 0)) throw new Error('P0 비정상: ' + h.code);
  });

  const body = String(message.getPlainBody() || '');
  const countMatch = body.match(/전체\s*보유종목\s*현황\s*\((\d+)종목\)/);
  if (countMatch && Number(countMatch[1]) !== rows.length) {
    throw new Error('메일 종목수(' + countMatch[1] + ')와 CSV 종목수(' + rows.length + ') 불일치');
  }
  if (body.indexOf('기준일시: ' + dayKey) < 0) {
    throw new Error('리포트 기준일 불일치: ' + dayKey);
  }
}

function dcsBuildOutput_(holdings, dayKey, oldPortfolioMap, oldMonitorMap) {
  const portfolioRows = [];
  const monitorRows = [];
  const added = [], updated = [], removed = [];

  const currentCodes = {};
  holdings.forEach(function(h) {
    currentCodes[h.code] = true;
    const oldP = oldPortfolioMap[h.code] || null;
    const oldM = oldMonitorMap[h.code] || null;
    const exchange = dcsResolveExchange_(h, oldP, oldM);
    const market = exchange === 'KOSDAQ' ? 'KOSDAQ' : 'KOSPI';
    const ticker = (exchange === 'KOSDAQ' ? 'KOSDAQ:' : 'KRX:') + h.code;

    const stop = dcsFirstNum_(
      h.stop,
      oldM ? dcsNum_(oldM[6]) : null
    );
    const target = dcsFirstNum_(
      h.target,
      oldM ? dcsNum_(oldM[10]) : null
    );

    const suspended = /SUSPENDED/.test(h.mode);
    const status = suspended ? 'SUSPENDED_HOLD' : 'ACTIVE';
    const strategy = dcsStrategyStatus_(h, stop, target, suspended);
    const buyDist = h.atr > 0 ? Math.round(h.atr * 0.6) : '';
    const sellDist = h.atr > 0 ? Math.round(h.atr * 0.4) : '';

    portfolioRows.push([
      'Y',h.name,market,h.code,ticker,h.qty,h.close,h.atr,h.p0,h.mode || 'NORMAL'
    ]);

    monitorRows.push([
      h.code,exchange,h.name,h.qty,h.avg,h.close,
      suspended ? 'HOLD' : (stop != null ? stop : ''),
      status,dayKey,true,
      target != null ? target : '',
      '',
      'StockBot V4',
      dayKey,
      strategy.status,
      strategy.warning,
      h.atr,
      buyDist,
      sellDist,
      dayKey
    ]);

    if (!oldM && !oldP) added.push(h.code);
    else updated.push(h.code);
  });

  Object.keys(oldMonitorMap).forEach(function(code) {
    if (code && !currentCodes[code]) removed.push(code);
  });
  Object.keys(oldPortfolioMap).forEach(function(code) {
    if (code && !currentCodes[code] && removed.indexOf(code) < 0) removed.push(code);
  });

  return {portfolioRows:portfolioRows, monitorRows:monitorRows, added:added, updated:updated, removed:removed};
}

function dcsStrategyStatus_(h, stop, target, suspended) {
  if (suspended) return {status:'SUSPENDED', warning:'거래정지 보류'};
  if (stop != null && h.close < stop) {
    return {status:'REVIEW_REQUIRED', warning:'현재가가 기존 손절선 아래 (전략 재검토 필요)'};
  }
  if (target != null && target <= h.avg) {
    return {status:'REVIEW_REQUIRED', warning:'목표가가 평단 이하 (전략 재설정 필요)'};
  }
  if (stop == null || target == null) {
    return {status:'UNCONFIGURED', warning:'전략 기준 일부 미확정 (기존값 확인 필요)'};
  }
  return {status:'NORMAL', warning:''};
}

function dcsResolveExchange_(h, oldP, oldM) {
  if (oldM && /^(KRX|KOSDAQ)$/.test(String(oldM[1] || '').toUpperCase())) {
    return String(oldM[1]).toUpperCase();
  }
  if (oldP) {
    const market = String(oldP[2] || '').toUpperCase();
    if (market === 'KOSDAQ') return 'KOSDAQ';
    if (market === 'KOSPI') return 'KRX';
  }
  if (/^(KODEX|TIGER|RISE|ACE|SOL|HANARO|KBSTAR|KOSEF|TIMEFOLIO|PLUS|WON|히어로즈)/i.test(h.name)) {
    return 'KRX';
  }

  try {
    const res = UrlFetchApp.fetch('https://m.stock.naver.com/api/stock/' + h.code + '/basic', {
      method:'get', muteHttpExceptions:true, followRedirects:true,
      headers:{'User-Agent':'Mozilla/5.0'}
    });
    if (res.getResponseCode() === 200) {
      const obj = JSON.parse(res.getContentText() || '{}');
      const ex = String(
        (obj.stockExchangeType && (obj.stockExchangeType.code || obj.stockExchangeType.name)) ||
        obj.marketType || obj.market || ''
      ).toUpperCase();
      if (ex.indexOf('KOSDAQ') >= 0) return 'KOSDAQ';
      if (ex.indexOf('KOSPI') >= 0 || ex.indexOf('KRX') >= 0) return 'KRX';
    }
  } catch (e) {}

  throw new Error('신규 종목 시장구분 확인 실패: ' + h.code + ' ' + h.name);
}

function dcsValidateHeaders_(sheet, expected) {
  const actual = sheet.getRange(1,1,1,expected.length).getDisplayValues()[0];
  for (let i=0; i<expected.length; i++) {
    if (String(actual[i] || '').trim() !== expected[i]) {
      throw new Error(sheet.getName() + ' 헤더 불일치 col ' + (i+1) + ': ' + actual[i] + ' != ' + expected[i]);
    }
  }
}

function dcsReadDataBlock_(sheet, cols) {
  const lastRow = Math.max(1, sheet.getLastRow());
  const count = Math.max(0, lastRow - 1);
  const values = count ? sheet.getRange(2,1,count,cols).getValues() : [];
  return {count:count, values:values};
}

function dcsReplaceDataBlock_(sheet, cols, rows) {
  const existing = Math.max(0, sheet.getLastRow() - 1);
  const clearRows = Math.max(existing, rows.length);
  if (clearRows > 0) sheet.getRange(2,1,clearRows,cols).clearContent();
  if (rows.length > 0) sheet.getRange(2,1,rows.length,cols).setValues(rows);
}

function dcsRestoreDataBlock_(sheet, cols, snapshot) {
  const existing = Math.max(0, sheet.getLastRow() - 1);
  const clearRows = Math.max(existing, snapshot.count || 0);
  if (clearRows > 0) sheet.getRange(2,1,clearRows,cols).clearContent();
  if (snapshot.values && snapshot.values.length) {
    sheet.getRange(2,1,snapshot.values.length,cols).setValues(snapshot.values);
  }
}

function dcsRowsByCode_(rows, codeIndex) {
  const out = {};
  (rows || []).forEach(function(r) {
    const code = dcsCode_(r[codeIndex]);
    if (code) out[code] = r;
  });
  return out;
}

function dcsAppendLog_(now, status, messageId, holdings, csvName, detail) {
  try {
    const ss = SpreadsheetApp.openById(DCS.MONITOR_SPREADSHEET_ID);
    let sh = ss.getSheetByName(DCS.LOG_SHEET);
    if (!sh) {
      sh = ss.insertSheet(DCS.LOG_SHEET);
      sh.getRange(1,1,1,7).setValues([[
        'TIMESTAMP','STATUS','REPORT_DATE','MESSAGE_ID','HOLDINGS','SOURCE_FILE','DETAIL'
      ]]);
      sh.setFrozenRows(1);
    }
    sh.appendRow([
      now,
      status,
      Utilities.formatDate(now, DCS.TZ, 'yyyy-MM-dd'),
      messageId || '',
      holdings || 0,
      csvName || '',
      detail || ''
    ]);
  } catch (e) {
    console.log('SYNC_LOG write failed: ' + e);
  }
}

function dcsHeaderIndex_(normalizedHeaders, candidates) {
  for (let i=0; i<candidates.length; i++) {
    const c = dcsNormalizeHeader_(candidates[i]);
    const exact = normalizedHeaders.indexOf(c);
    if (exact >= 0) return exact;
  }
  for (let i=0; i<candidates.length; i++) {
    const c = dcsNormalizeHeader_(candidates[i]);
    for (let j=0; j<normalizedHeaders.length; j++) {
      if (normalizedHeaders[j].indexOf(c) >= 0 || c.indexOf(normalizedHeaders[j]) >= 0) return j;
    }
  }
  return -1;
}

function dcsNormalizeHeader_(v) {
  return String(v == null ? '' : v)
    .replace(/^\uFEFF/,'')
    .replace(/[\s,()]/g,'')
    .replace(/[·]/g,'')
    .trim();
}

function dcsCode_(v) {
  const s = String(v == null ? '' : v).replace(/\D/g,'');
  return s ? ('000000' + s).slice(-6) : '';
}

function dcsNum_(v) {
  if (typeof v === 'number' && isFinite(v)) return v;
  const s = String(v == null ? '' : v).trim();
  if (!s || /^(HOLD|N\/A|NONE|-|OFF)$/i.test(s)) return null;
  const m = s.replace(/,/g,'').match(/-?\d+(?:\.\d+)?/);
  if (!m) return null;
  const n = Number(m[0]);
  return isFinite(n) ? n : null;
}

function dcsFirstNum_() {
  for (let i=0; i<arguments.length; i++) {
    const n = arguments[i];
    if (typeof n === 'number' && isFinite(n) && n > 0) return n;
  }
  return null;
}
