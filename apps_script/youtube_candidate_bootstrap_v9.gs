/*
 * YouTube Candidate Watch - Apps Script bootstrap V9
 * Fixed-block Gmail parser: [YT_ALL_STOCKS_BEGIN] ... [YT_ALL_STOCKS_END]
 * Existing Script Properties are reused as-is.
 */

const YCB = Object.freeze({
  RAW_BASE: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/cb4bbe9c306c69f479c383324022b7d3055dfc2d/apps_script/',
  FILES: [
    'youtube_candidate_monitor.gs',
    'youtube_candidate_ingest.gs',
    'youtube_candidate_runtime_v2.gs',
    'youtube_candidate_runtime_v3.gs',
    'youtube_candidate_runtime_v4.gs',
    'youtube_candidate_runtime_v5.gs',
    'youtube_candidate_runtime_v6.gs',
    'youtube_candidate_runtime_v7.gs'
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
    '평일 08:00~15:30, 30분 간격 확인 후 당일 수집 성공 시 중단합니다.\n' +
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
