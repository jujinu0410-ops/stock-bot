/*
 * YouTube Candidate Watch - Apps Script bootstrap V12
 * P0-3 & P1-3 Remediation:
 * - SCAN_START expanded to 07:00 KST (7 * 60) for early morning Intelligence reports
 * - Regex updated to accept [경제 Intelligence] [재분석] subjects
 * - Redundant /confirm & /alert-outcome calls delegated to INTRADAY_GF_MONITOR
 * - Candidate collection from Gmail preserved and deduplicated across regular and [재분석]
 * - DISPATCH_LOG retention: 90 days / max 2,000 data rows
 * - YouTube candidate lifecycle: 10 active KRX sessions, D+10/D+20 evaluation through 20 sessions
 *
 * Existing Script Properties and installed trigger handler names are reused.
 */

const YCB = Object.freeze({
  RAW_BASE: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/93f00bf15261eb984321830d1ae691aabe83851c/apps_script/',
  LIFECYCLE_URL: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/074e185613162a5332795e803e27310574c95427/apps_script/youtube_candidate_lifecycle_v11.gs',
  TWO_MAIL_ADDON_URL: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/b77730b0db08dda5682a820f881866b8faac41c4/apps_script/youtube_candidate_two_mails_v12.gs',
  FILES: [
    'youtube_candidate_monitor.gs',
    'youtube_candidate_outcome.gs',
    'youtube_candidate_ingest.gs',
    'youtube_candidate_runtime_v2.gs',
    'youtube_candidate_runtime_v3.gs',
    'youtube_candidate_runtime_v4.gs',
    'youtube_candidate_runtime_v5.gs',
    'youtube_candidate_runtime_v6.gs',
    'youtube_candidate_runtime_v7.gs',
    'youtube_candidate_runtime_v8.gs',
    'youtube_candidate_runtime_v9.gs',
    'youtube_candidate_runtime_v10.gs'
  ]
});

const TWO_MAIL_V12_EMBEDDED = `
const YCV12 = Object.freeze({
  TZ: 'Asia/Seoul',
  SCAN_START: 7 * 60,
  SCAN_END: 15 * 60 + 30,
  BROKER_START: 8 * 60 + 30,
  BROKER_END_EXCLUSIVE: 9 * 60 + 31,
  POLL_INTERVAL_MS: 5 * 60 * 1000,
  POLL_KEY: 'TWO_MAIL_V12_LAST_POLL_MS',
  PROCESSED_DAY_KEY: 'TWO_MAIL_V12_PROCESSED_DAY',
  PROCESSED_IDS_KEY: 'TWO_MAIL_V12_PROCESSED_IDS',
  STATUS_KEY: 'TWO_MAIL_V12_STATUS',
  MAX_THREADS: 100,
  MAX_STOCKS_IN_MAIL: 100
});

let ycv12MailCache_ = null;

function ycv12Type_(subject) {
  const s = String(subject || '').trim();
  // 2026-10-28 morning mail still contains 49 over-inclusive codes: do not backfill it.
  // Accept this exact new subject only from the first corrected report, 2026-10-29.
  const morning = s.match(/^\\[경제 Intelligence\\]\\s*\\[아침 종합\\]\\s*(\\d{4}-\\d{2}-\\d{2})\\s*$/);
  if (morning && morning[1] >= '2026-10-29') return 'REGULAR';
  if (/^\\[경제 Intelligence\\]\\s*(?:\\[재분석\\]\\s*)?(?:\\d{1,2}월\\s*\\d{1,2}일|\\d{4}-\\d{2}-\\d{2})\\s*(?:\\||$)/.test(s)) return 'REGULAR';
  if (/^\\[경제 Intelligence\\]\\s*\\[(?:08:45\\s*)?증권사 리포트 종목수집\\]/.test(s)) return 'BROKER';
  return '';
}

function ycv12ParseBlock_(rawBody) {
  const body = String(rawBody || '').replace(/\\r\\n?/g, '\\n').replace(/\\u00a0/g, ' ').replace(/\\u200b/g, '');
  const m = body.match(/\\[YT_ALL_STOCKS_BEGIN\\]([\\s\\S]*?)\\[YT_ALL_STOCKS_END\\]/);
  if (!m) return {found: false, stocks: []};
  const result = [], seen = new Set();
  m[1].split('\\n').forEach(function(raw) {
    const line = String(raw || '').trim();
    if (!line) return;
    const match = line.match(/^(\\d{6})\Area*\\|([^|\\n]{2,60})$/);
    const m2 = line.match(/^(\\d{6})\\|([^|\\n]{2,60})$/);
    const validMatch = m2 || match;
    if (!validMatch) throw new Error('TWO_MAIL_V12_INVALID_STOCK_LINE');
    const code = normalizeCandidateCode_(validMatch[1]), name = String(validMatch[2] || '').trim();
    if (!code || !name) throw new Error('TWO_MAIL_V12_INVALID_CODE_OR_NAME');
    if (seen.has(code)) return;
    seen.add(code); result.push({code: code, name: name});
  });
  if (result.length > YCV12.MAX_STOCKS_IN_MAIL) throw new Error('TWO_MAIL_V12_EXCESS_STOCK_COUNT');
  return {found: true, stocks: result};
}

function ycv12TodayMails_() {
  if (ycv12MailCache_ !== null) return ycv12MailCache_;
  const today = todayKeyV8_(), found = new Map();
  const queries = [
    'subject:"경제 Intelligence" newer_than:2d -in:trash -in:spam',
    'subject:"증권사 리포트 종목수집" newer_than:2d -in:trash -in:spam'
  ];
  queries.forEach(function(q) {
    GmailApp.search(q, 0, YCV12.MAX_THREADS).forEach(function(thread) {
      thread.getMessages().forEach(function(msg) {
        const id = String(msg.getId() || ''), type = ycv12Type_(msg.getSubject());
        if (!id || !type || found.has(id)) return;
        const date = msg.getDate();
        if (Utilities.formatDate(date, YCV12.TZ, 'yyyy-MM-dd') !== today) return;
        const hour = Number(Utilities.formatDate(date, YCV12.TZ, 'HH'));
        const min = Number(Utilities.formatDate(date, YCV12.TZ, 'mm'));
        const minute = hour * 60 + min;
        if (type === 'REGULAR' && (minute < YCV12.SCAN_START || minute > YCV12.SCAN_END)) return;
        const morningDate = String(msg.getSubject()).match(/^\\[경제 Intelligence\\]\\s*\\[아침 종합\\]\\s*(\\d{4}-\\d{2}-\\d{2})\\s*$/);
        if (morningDate && morningDate[1] !== today) return;
        if (type === 'BROKER' && (minute < YCV12.BROKER_START || minute >= YCV12.BROKER_END_EXCLUSIVE)) return;
        if (type === 'BROKER') {
          const match = String(msg.getSubject()).match(/\\]\\s*(\\d{4}-\\d{2}-\\d{2})(?:\\s*[·|]|$)/);
          if (!match || match[1] !== today) return;
        }
        const parsed = ycv12ParseBlock_(msg.getPlainBody());
        if (!parsed.found) {
          Logger.log('TWO_MAIL_V12_SKIP_MISSING_BLOCK type=' + type + ' id=' + id);
          return;
        }
        found.set(id, {id: id, type: type, date: today, time: minute, stocks: parsed.stocks});
      });
    });
  });
  ycv12MailCache_ = Array.from(found.values()).sort(function(a, b) {
    return a.time - b.time || a.id.localeCompare(b.id);
  });
  return ycv12MailCache_;
}

function collectIntelligenceCandidates_() {
  const today = todayKeyV8_(), pool = {};
  const ss = SpreadsheetApp.getActiveSpreadsheet(), sh = ss.getSheetByName(YC.CANDIDATE_SHEET);
  if (sh && sh.getLastRow() >= YC.DATA_START_ROW) {
    sh.getRange(YC.DATA_START_ROW, 1, sh.getLastRow() - YC.DATA_START_ROW + 1, 7).getDisplayValues().forEach(function(r) {
      const name = String(r[1] || '').trim(), code = normalizeCandidateCode_(r[3]);
      const day = ymdFromAnyV8_(r[5]);
      if (String(r[0] || '').trim().toUpperCase() !== 'Y' || !name || !code || !day || isExpiredMentionV8_(day, today)) return;
      pool[code] = {name: name, nameTrusted: true, dates: new Set([day]), lastSeen: day, carryover: true, sourceTag: 'SHEET_TTL'};
    });
  }
  ycv12TodayMails_().forEach(function(mail) {
    mail.stocks.forEach(function(stock) {
      let item = pool[stock.code];
      if (!item) {
        item = {name: stock.name, nameTrusted: true, dates: new Set(), lastSeen: mail.date, sourceTag: mail.type};
        pool[stock.code] = item;
      }
      item.dates.add(mail.date);
      if (mail.date >= item.lastSeen) {
        item.name = stock.name;
        item.lastSeen = mail.date;
        item.nameTrusted = true;
        item.sourceTag = mail.type;
      }
      item.mentionDays = item.dates.size;
    });
  });
  Object.keys(pool).forEach(function(code) {
    if (isExpiredMentionV8_(pool[code].lastSeen, today)) delete pool[code];
    else pool[code].mentionDays = pool[code].dates.size;
  });
  return pool;
}

function ycv12ConfirmFresh_(fresh, today) {
  const required = new Set();
  fresh.forEach(function(mail) { mail.stocks.forEach(function(stock) { required.add(stock.code); }); });
  if (!required.size) return;
  const ss = SpreadsheetApp.getActiveSpreadsheet(), sh = ss.getSheetByName(YC.CANDIDATE_SHEET);
  if (!sh) throw new Error('TWO_MAIL_V12_CANDIDATES_MISSING');
  SpreadsheetApp.flush();
  const present = new Set();
  const last = sh.getLastRow();
  if (last >= YC.DATA_START_ROW) {
    sh.getRange(YC.DATA_START_ROW, 1, last - YC.DATA_START_ROW + 1, 7).getDisplayValues().forEach(function(row) {
      const code = normalizeCandidateCode_(row[3]);
      if (required.has(code) && String(row[0]).trim().toUpperCase() === 'Y' && ymdFromAnyV8_(row[5]) === today)
        present.add(code);
    });
  }
  const missing = Array.from(required).filter(function(code) { return !present.has(code); });
  if (missing.length) throw new Error('TWO_MAIL_V12_UNCONFIRMED_' + missing.join(','));
}

function ycv12MaybeCollect_() {
  const parts = seoulNowPartsV5_(new Date());
  if (!isWeekdayV5_(parts)) return 'SKIP_NON_WEEKDAY';
  if (!isWithinMinuteWindowV5_(parts, YCV12.SCAN_START, YCV12.SCAN_END)) return 'SKIP_COLLECTION_WINDOW';
  const props = PropertiesService.getScriptProperties(), now = Date.now();
  const last = Number(props.getProperty(YCV12.POLL_KEY) || '0');
  if (last > 0 && now - last < YCV12.POLL_INTERVAL_MS) return 'SKIP_POLL_THROTTLE';
  props.setProperty(YCV12.POLL_KEY, String(now));
  try {
    const mails = ycv12TodayMails_();
    const processed = props.getProperty(YCV12.PROCESSED_DAY_KEY) === parts.dateKey
      ? String(props.getProperty(YCV12.PROCESSED_IDS_KEY) || '').split(',').filter(Boolean) : [];
    const prior = new Set(processed), fresh = mails.filter(function(m) { return !prior.has(m.id); });
    if (!fresh.length) {
      const status = 'NO_NEW_SOURCE_MAIL source_count=' + mails.length;
      props.setProperty(YCV12.STATUS_KEY, status);
      return status;
    }
    const stockCount = fresh.reduce(function(n, m) { return n + m.stocks.length; }, 0);
    if (stockCount > 0) {
      const before = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
      refreshYouTubeCandidatePoolV2();
      const after = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
      if (!after || after === before) throw new Error('TWO_MAIL_V12_REFRESH_NOT_CONFIRMED');
      // Persist valid candidate events before whole-mail confirmation.
      // One quarantined name/code pair must not discard the other stocks'
      // D+10/D+20 histories. Confirmation still fails and retries the mail
      // until every candidate is verified; processed IDs are not advanced.
      ycv11RegisterFreshMentionsV11_(fresh);
      ycv12ConfirmFresh_(fresh, parts.dateKey);
    } else {
      const ss = SpreadsheetApp.getActiveSpreadsheet();
      const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
      if (sheet) reclaimExpiredRowsV8_(sheet, parts.dateKey);
    }

    const ids = Array.from(new Set(processed.concat(fresh.map(function(m) { return m.id; })))).slice(-40);
    props.setProperty(YCV12.PROCESSED_DAY_KEY, parts.dateKey);
    props.setProperty(YCV12.PROCESSED_IDS_KEY, ids.join(','));
    const regular = fresh.filter(function(m) { return m.type === 'REGULAR'; });
    const broker = fresh.filter(function(m) { return m.type === 'BROKER'; });
    const count = function(a) { return a.reduce(function(n, m) { return n + m.stocks.length; }, 0); };
    const status = 'COLLECTED REGULAR=' + regular.length + ' mails/' + count(regular) + ' stocks' +
      ' BROKER=' + broker.length + ' mails/' + count(broker) + ' stocks';
    props.setProperty(YCV12.STATUS_KEY, status);
    Logger.log('TWO_MAIL_V12 ' + status);
    return status;
  } catch (e) {
    props.deleteProperty(YCV12.POLL_KEY);
    props.setProperty(YCV12.STATUS_KEY, 'ERROR ' + String(e && e.message || e).slice(0, 160));
    throw e;
  }
}

maybeScheduledCandidateRefreshV6 = function() { return ycv12MaybeCollect_(); };
`;

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('YouTube 매수감시')
    .addItem('Gmail 권한 승인', 'ycAuthorizeGmailOnce')
    .addItem('장중 트리거 설치', 'ycInstallTriggers')
    .addSeparator()
    .addItem('후보 지금 동기화', 'ycRefreshNow')
    .addItem('신호 지금 검사', 'ycMonitorNow')
    .addItem('로그 지금 정리', 'ycCleanupLogsNow')
    .addItem('API 연결 확인', 'ycCheckApiHealth')
    .addToUi();
}

function ycAuthorizeGmailOnce() {
  const threads = GmailApp.search('subject:"경제 Intelligence" newer_than:2d -in:trash -in:spam', 0, 1);
  SpreadsheetApp.getUi().alert(
    'Gmail 권한 승인 완료',
    'Gmail 검색 권한이 연결되어 있습니다. 최근 일치 스레드 수: ' + threads.length,
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

function ycLoadRuntimeAndRun_(entryFunction) {
  const chunks = YCB.FILES.map(function(name) {
    const res = UrlFetchApp.fetch(YCB.RAW_BASE + name, {
      method: 'get',
      muteHttpExceptions: true,
      headers: {'User-Agent': 'StockBot-AppsScript/1.0'}
    });
    if (res.getResponseCode() !== 200) {
      throw new Error('RUNTIME_FETCH_FAILED ' + name + ' HTTP_' + res.getResponseCode());
    }
    return res.getContentText();
  });

  // P0-3 & P1-3: Embed updated V12 collector and delegate monitoring to INTRADAY_GF_MONITOR
  const delegationStubs = [
    'scheduledCandidateMonitorV5 = function() { return "DELEGATED_TO_UNIFIED_WATCHLISTS"; };',
    'manualCandidateMonitorV5 = function() { return "DELEGATED_TO_UNIFIED_WATCHLISTS"; };',
    'monitorYouTubeCandidatesV2 = function() { return "DELEGATED_TO_UNIFIED_WATCHLISTS"; };',
    'monitorYouTubeCandidates = function() { return "DELEGATED_TO_UNIFIED_WATCHLISTS"; };'
  ].join('\n');

  const lifecycleCache = CacheService.getScriptCache();
  const lifecycleCacheKey = 'YCV11_LIFECYCLE_V11_074e185';
  let lifecycleCode = lifecycleCache.get(lifecycleCacheKey);
  if (!lifecycleCode) {
    const lifecycleRes = UrlFetchApp.fetch(YCB.LIFECYCLE_URL, {
      method: 'get',
      muteHttpExceptions: true,
      headers: {'User-Agent': 'StockBot-AppsScript/1.0'}
    });
    if (lifecycleRes.getResponseCode() !== 200) {
      throw new Error('RUNTIME_FETCH_FAILED youtube_candidate_lifecycle_v11.gs HTTP_' + lifecycleRes.getResponseCode());
    }
    lifecycleCode = lifecycleRes.getContentText();
    lifecycleCache.put(lifecycleCacheKey,lifecycleCode,21600);
  }
  const source = chunks.join('\n\n') + '\n\n' + lifecycleCode +
    '\n\n' + TWO_MAIL_V12_EMBEDDED + '\n\n' + delegationStubs + '\n\n' + entryFunction + '();';
  return eval(source);
}

function ycRefreshNow() {
  return ycLoadRuntimeAndRun_('manualCandidateRefreshWithLifecycleV11');
}

function ycMonitorNow() {
  SpreadsheetApp.getUi().alert(
    '신호 감시 안내',
    '장중 신호 감시는 INTRADAY_GF_MONITOR(UnifiedWatchlists)로 완전히 위임되어 Cloud Run 부하와 중복 알림이 방지됩니다.\n(DELEGATED_TO_UNIFIED_WATCHLISTS)',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
  return 'DELEGATED_TO_UNIFIED_WATCHLISTS';
}

function ycCleanupLogsNow() {
  const status = ycLoadRuntimeAndRun_('manualDispatchLogCleanupV9');
  SpreadsheetApp.getUi().alert('로그 정리', String(status), SpreadsheetApp.getUi().ButtonSet.OK);
  return status;
}

function ycScheduledRefresh() {
  return 'SKIP_LEGACY_REFRESH_5MIN_COMBINED_ACTIVE';
}

function ycScheduledMonitor() {
  const props = PropertiesService.getScriptProperties();
  const lifecycleMigrationKey = 'YCV11_LIFECYCLE_MIGRATION_DONE';
  if (props.getProperty(lifecycleMigrationKey) !== 'Y') {
    const lifecycleMigration = ycLoadRuntimeAndRun_('migrateCandidateLifecycleV11');
    if (props.getProperty(lifecycleMigrationKey) !== 'Y') {
      return 'YCV11_MIGRATION_PENDING; ' + lifecycleMigration;
    }
  }
  const migrationKey = 'PREMARKET_V10_MIGRATION_DONE_20261007';
  if (props.getProperty(migrationKey) !== 'Y') {
    ycLoadRuntimeAndRun_('manualCandidateRefreshV5');
    props.setProperty(migrationKey, 'Y');
  }
  return ycLoadRuntimeAndRun_('scheduledCandidateLifecycleAndTickV11');
}

function ycInstallTriggers() {
  const handlers = new Set([
    'ycScheduledMonitor', 'ycScheduledRefresh',
    'ycMonitorNow', 'ycRefreshNow',
    'monitorYouTubeCandidates', 'refreshYouTubeCandidatePool',
    'monitorYouTubeCandidatesV2', 'refreshYouTubeCandidatePoolV2'
  ]);

  ScriptApp.getProjectTriggers().forEach(function(t) {
    if (handlers.has(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('ycScheduledMonitor').timeBased().everyMinutes(5).create();

  SpreadsheetApp.getUi().alert(
    '설치 완료',
    '수집원은 당일 07:00 이후 정규/재분석 경제 Intelligence와 08:30~09:30 증권사 리포트 메일 두 종류만 허용합니다.\n' +
    '후보 유효기간: 마지막 직접언급일부터 한 달이며 재언급 시 다시 한 달 연장됩니다.\n' +
    '만료 후보 행은 신규 종목에 재사용합니다.\n' +
    'DISPATCH_LOG: 최근 90일, 최대 2,000건만 유지하며 하루 한 번 자동 정리합니다.\n' +
    '장중 신호 감시는 통합 감시(INTRADAY_GF_MONITOR)로 일원화되어 중복 API 호출이 제거되었습니다.',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

function ycCheckApiHealth() {
  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty('SIGNAL_API_URL') || '').trim().replace(/\/$/, '');
  if (!apiUrl) throw new Error('SIGNAL_API_URL missing');
  const res = UrlFetchApp.fetch(apiUrl + '/health', {muteHttpExceptions: true});
  SpreadsheetApp.getUi().alert(
    'API 상태',
    res.getResponseCode() + ' ' + res.getContentText(),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}
