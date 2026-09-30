/*
 * YouTube Candidate Watch - lightweight Apps Script bootstrap
 *
 * Paste ONLY this file into the spreadsheet-bound Code.gs.
 * It keeps the editor setup small and loads the tested runtime modules
 * from the feature branch only when a scheduled/manual action runs.
 *
 * No Kiwoom key is stored here or in sheet cells.
 */

const YCB = Object.freeze({
  RAW_BASE: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/feat/youtube-candidate-ingest-v0-20260930/apps_script/',
  FILES: [
    'youtube_candidate_monitor.gs',
    'youtube_candidate_ingest.gs',
    'youtube_candidate_runtime_v2.gs'
  ]
});

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('YouTube 매수감시')
    .addItem('1. Cloud Run API 설정', 'ycConfigureApi')
    .addItem('2. 5분/30분 트리거 설치', 'ycInstallTriggers')
    .addSeparator()
    .addItem('후보 지금 동기화', 'ycRefreshNow')
    .addItem('신호 지금 검사', 'ycMonitorNow')
    .addItem('API 연결 확인', 'ycCheckApiHealth')
    .addToUi();
}

function ycLoadRuntimeAndRun_(entryFunction) {
  const chunks = YCB.FILES.map(name => {
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

  // Keep invocation in the same eval scope as the loaded declarations.
  const source = chunks.join('\n\n') + '\n\n' + entryFunction + '();';
  eval(source);
}

function ycRefreshNow() {
  ycLoadRuntimeAndRun_('refreshYouTubeCandidatePoolV2');
}

function ycMonitorNow() {
  ycLoadRuntimeAndRun_('monitorYouTubeCandidatesV2');
}

function ycInstallTriggers() {
  const handlers = new Set([
    'ycMonitorNow', 'ycRefreshNow',
    'monitorYouTubeCandidates', 'refreshYouTubeCandidatePool',
    'monitorYouTubeCandidatesV2', 'refreshYouTubeCandidatePoolV2'
  ]);
  ScriptApp.getProjectTriggers().forEach(t => {
    if (handlers.has(t.getHandlerFunction())) ScriptApp.deleteTrigger(t);
  });

  ScriptApp.newTrigger('ycMonitorNow').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('ycRefreshNow').timeBased().everyMinutes(30).create();
  SpreadsheetApp.getUi().alert('설치 완료', '기술신호는 5분마다, YouTube 후보 동기화는 30분마다 확인합니다.', SpreadsheetApp.getUi().ButtonSet.OK);
}

function ycConfigureApi() {
  const ui = SpreadsheetApp.getUi();
  const urlPrompt = ui.prompt(
    'Cloud Run URL',
    '배포 완료 화면의 Service URL을 붙여넣으세요.',
    ui.ButtonSet.OK_CANCEL
  );
  if (urlPrompt.getSelectedButton() !== ui.Button.OK) return;

  const tokenPrompt = ui.prompt(
    '공유 토큰',
    '배포 스크립트가 클립보드에 복사한 API token을 붙여넣으세요. 토큰은 시트 셀에 저장되지 않습니다.',
    ui.ButtonSet.OK_CANCEL
  );
  if (tokenPrompt.getSelectedButton() !== ui.Button.OK) return;

  const apiUrl = String(urlPrompt.getResponseText() || '').trim().replace(/\/$/, '');
  const apiToken = String(tokenPrompt.getResponseText() || '').trim();
  if (!apiUrl || !apiToken) throw new Error('API URL/token required');

  PropertiesService.getScriptProperties().setProperties({
    SIGNAL_API_URL: apiUrl,
    SIGNAL_API_TOKEN: apiToken
  }, false);
  ui.alert('API 설정 완료');
}

function ycCheckApiHealth() {
  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty('SIGNAL_API_URL') || '').trim().replace(/\/$/, '');
  if (!apiUrl) throw new Error('SIGNAL_API_URL missing');
  const res = UrlFetchApp.fetch(apiUrl + '/health', {muteHttpExceptions: true});
  SpreadsheetApp.getUi().alert('API 상태', res.getResponseCode() + ' ' + res.getContentText(), SpreadsheetApp.getUi().ButtonSet.OK);
}
