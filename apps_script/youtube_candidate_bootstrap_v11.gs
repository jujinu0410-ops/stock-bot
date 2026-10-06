/*
 * YouTube Candidate Watch - Apps Script bootstrap V11
 * Final housekeeping bundle:
 * - morning premarket-only three-column candidate block ingestion
 * - one-calendar-month candidate TTL
 * - expired candidate-row reuse
 * - DISPATCH_LOG retention: 90 days / max 2,000 data rows
 *
 * Existing Script Properties and installed trigger handler names are reused.
 */

const YCB = Object.freeze({
  RAW_BASE: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/66bdac94569d9a36f58e7610b7db73702ff064dc/apps_script/',
  FILES: [
    'youtube_candidate_monitor.gs',
    'youtube_candidate_ingest.gs',
    'youtube_candidate_runtime_v2.gs',
    'youtube_candidate_runtime_v3.gs',
    'youtube_candidate_runtime_v4.gs',
    'youtube_candidate_runtime_v5.gs',
    'youtube_candidate_runtime_v6.gs',
    'youtube_candidate_runtime_v7.gs',
    'youtube_candidate_runtime_v8.gs',
    'youtube_candidate_runtime_v9.gs'
  ]
});

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

/* Keep GmailApp directly in Code.gs so Apps Script keeps Gmail scope. */
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

  const source = chunks.join('\n\n') + '\n\n' + entryFunction + '();';
  return eval(source);
}

function ycRefreshNow() {
  return ycLoadRuntimeAndRun_('manualCandidateRefreshV5');
}

function ycMonitorNow() {
  return ycLoadRuntimeAndRun_('manualCandidateMonitorV5');
}

function ycCleanupLogsNow() {
  const status = ycLoadRuntimeAndRun_('manualDispatchLogCleanupV9');
  SpreadsheetApp.getUi().alert('로그 정리', String(status), SpreadsheetApp.getUi().ButtonSet.OK);
  return status;
}

function ycScheduledRefresh() {
  return ycLoadRuntimeAndRun_('maybeScheduledCandidateRefreshV6');
}

function ycScheduledMonitor() {
  return ycLoadRuntimeAndRun_('scheduledCombinedTickV6');
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
  ScriptApp.newTrigger('ycScheduledRefresh').timeBased().everyMinutes(30).create();

  SpreadsheetApp.getUi().alert(
    '설치 완료',
    '후보메일: 상단 장전 시황 블록(code|name|reason)만 수집하며 전체 87종목 블록은 무시합니다.\n' +
    '후보 유효기간: 마지막 직접언급일부터 한 달이며 재언급 시 다시 한 달 연장됩니다.\n' +
    '만료 후보 행은 신규 종목에 재사용합니다.\n' +
    'DISPATCH_LOG: 최근 90일, 최대 2,000건만 유지하며 하루 한 번 자동 정리합니다.\n' +
    '매수신호: 평일 장중 5분마다 계속 감시합니다.',
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
