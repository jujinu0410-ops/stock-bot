/*
 * YouTube Candidate Watch - Apps Script bootstrap V11
 * Final housekeeping bundle:
 * - fixed Gmail stock block ingestion
 * - one-calendar-month candidate TTL
 * - expired candidate-row reuse
 * - DISPATCH_LOG retention: 90 days / max 2,000 data rows
 *
 * Existing Script Properties and installed trigger handler names are reused.
 */

const YCB = Object.freeze({
  RAW_BASE: 'https://raw.githubusercontent.com/jujinu0410-ops/stock-bot/3af7c6fec7abbaa0cff817ddeac82470c26faff3/apps_script/',
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
    'youtube_candidate_runtime_v9.gs',
    'youtube_candidate_runtime_v10.gs'
  ]
});

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('YouTube 留ㅼ닔媛먯떆')
    .addItem('Gmail 沅뚰븳 ?뱀씤', 'ycAuthorizeGmailOnce')
    .addItem('?μ쨷 ?몃━嫄??ㅼ튂', 'ycInstallTriggers')
    .addSeparator()
    .addItem('?꾨낫 吏湲??숆린??, 'ycRefreshNow')
    .addItem('?좏샇 吏湲?寃??, 'ycMonitorNow')
    .addItem('濡쒓렇 吏湲??뺣━', 'ycCleanupLogsNow')
    .addItem('API ?곌껐 ?뺤씤', 'ycCheckApiHealth')
    .addToUi();
}

/* Keep GmailApp directly in Code.gs so Apps Script keeps Gmail scope. */
function ycAuthorizeGmailOnce() {
  const threads = GmailApp.search('subject:"寃쎌젣 Intelligence" newer_than:2d -in:trash -in:spam', 0, 1);
  SpreadsheetApp.getUi().alert(
    'Gmail 沅뚰븳 ?뱀씤 ?꾨즺',
    'Gmail 寃??沅뚰븳???곌껐?섏뼱 ?덉뒿?덈떎. 理쒓렐 ?쇱튂 ?ㅻ젅???? ' + threads.length,
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
  SpreadsheetApp.getUi().alert('濡쒓렇 ?뺣━', String(status), SpreadsheetApp.getUi().ButtonSet.OK);
  return status;
}

function ycScheduledRefresh() {
  return ycLoadRuntimeAndRun_('maybeScheduledCandidateRefreshV6');
}

function ycScheduledMonitor() {
  const props = PropertiesService.getScriptProperties();
  const migrationKey = 'PREMARKET_V10_MIGRATION_DONE_20261007';
  if (props.getProperty(migrationKey) !== 'Y') {
    ycLoadRuntimeAndRun_('manualCandidateRefreshV5');
    props.setProperty(migrationKey, 'Y');
  }
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
    '?ㅼ튂 ?꾨즺',
    '?꾨낫硫붿씪: [寃쎌젣 Intelligence]??"?μ쟾 ?쒗솴" CODE|NAME|reason 釉붾줉留??섏쭛?⑸땲??\n' +
    '?꾨낫 ?좏슚湲곌컙: 留덉?留?吏곸젒?멸툒?쇰??????ъ씠硫??ъ뼵湲????ㅼ떆 ?????곗옣?⑸땲??\n' +
    '留뚮즺 ?꾨낫 ?됱? ?좉퇋 醫낅ぉ???ъ궗?⑺빀?덈떎.\n' +
    'DISPATCH_LOG: 理쒓렐 90?? 理쒕? 2,000嫄대쭔 ?좎??섎ŉ ?섎（ ??踰??먮룞 ?뺣━?⑸땲??\n' +
    '留ㅼ닔?좏샇: ?됱씪 ?μ쨷 5遺꾨쭏??怨꾩냽 媛먯떆?⑸땲??',
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

function ycCheckApiHealth() {
  const props = PropertiesService.getScriptProperties();
  const apiUrl = String(props.getProperty('SIGNAL_API_URL') || '').trim().replace(/\/$/, '');
  if (!apiUrl) throw new Error('SIGNAL_API_URL missing');
  const res = UrlFetchApp.fetch(apiUrl + '/health', {muteHttpExceptions: true});
  SpreadsheetApp.getUi().alert(
    'API ?곹깭',
    res.getResponseCode() + ' ' + res.getContentText(),
    SpreadsheetApp.getUi().ButtonSet.OK
  );
}

