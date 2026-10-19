/*
 * YouTube Candidate Watch - Apps Script bootstrap V10
 * Fixed-block Gmail parser + one-calendar-month TTL + expired-row reuse.
 * Existing Script Properties and trigger handlers are reused as-is.
 */

const YCB = Object.freeze({
  RAW_BASE: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/0e8076ecc6c0f9425edbaa543c85214eb0b1ac65/apps_script/',
  FILES: [
    'youtube_candidate_monitor.gs',
    'youtube_candidate_ingest.gs',
    'youtube_candidate_runtime_v2.gs',
    'youtube_candidate_runtime_v3.gs',
    'youtube_candidate_runtime_v4.gs',
    'youtube_candidate_runtime_v5.gs',
    'youtube_candidate_runtime_v6.gs',
    'youtube_candidate_runtime_v7.gs',
    'youtube_candidate_runtime_v8.gs'
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
    '후보메일: [YT_ALL_STOCKS_BEGIN]~[YT_ALL_STOCKS_END] 고정 블록만 수집합니다.\n' +
    '후보 유효기간: 마지막 직접언급일부터 한 달이며 재언급 시 다시 한 달 연장됩니다.\n' +
    '만료 행은 지우지 않고 같은 행을 신규 종목에 재사용합니다.\n' +
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
