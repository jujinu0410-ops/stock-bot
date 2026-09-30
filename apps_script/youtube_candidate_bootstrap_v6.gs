/*
 * YouTube Candidate Watch - Apps Script bootstrap V6
 * Runtime is pinned to a tested feature-branch commit.
 * Existing Script Properties are reused as-is.
 */

const YCB = Object.freeze({
  RAW_BASE: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/7daa60bd3aaad53e4c9bd103e315a6373ec3e03d/apps_script/',
  FILES: [
    'youtube_candidate_monitor.gs',
    'youtube_candidate_ingest.gs',
    'youtube_candidate_runtime_v2.gs',
    'youtube_candidate_runtime_v3.gs',
    'youtube_candidate_runtime_v4.gs',
    'youtube_candidate_runtime_v5.gs'
  ]
});

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('YouTube 매수감시')
    .addItem('장중 트리거 설치', 'ycInstallTriggers')
    .addSeparator()
    .addItem('후보 지금 동기화', 'ycRefreshNow')
    .addItem('신호 지금 검사', 'ycMonitorNow')
    .addItem('API 연결 확인', 'ycCheckApiHealth')
    .addToUi();
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
  return ycLoadRuntimeAndRun_('scheduledCandidateRefreshV5');
}

function ycScheduledMonitor() {
  return ycLoadRuntimeAndRun_('scheduledCandidateMonitorV5');
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
    '후보메일: 평일 08:00~15:30에 30분마다 확인하고, 오늘 메일 수집 성공 후 그날은 중단합니다.\n' +
    '매수신호: 평일 09:00~15:30에 5분마다 계속 감시합니다.',
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
