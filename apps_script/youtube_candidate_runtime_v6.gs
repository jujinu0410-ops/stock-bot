/*
 * Runtime V6: self-healing candidate-mail polling.
 *
 * The proven 5-minute monitor trigger also checks whether today's Intelligence
 * mail still needs to be collected. Gmail polling is throttled to once every
 * 30 minutes, so candidate ingestion no longer depends on a separate 30-minute
 * trigger. The legacy 30-minute trigger remains safe because both paths share
 * the same throttle/collected-date properties.
 */

const YCV6 = Object.freeze({
  LAST_POLL_AT_KEY: 'YOUTUBE_MAIL_LAST_POLL_AT',
  LAST_POLL_STATUS_KEY: 'YOUTUBE_MAIL_LAST_POLL_STATUS',
  POLL_INTERVAL_MS: 30 * 60 * 1000,
  DATA_HOLD_REPAIR_AT_KEY: 'YOUTUBE_DATA_HOLD_REPAIR_AT',
  DATA_HOLD_REPAIR_INTERVAL_MS: 10 * 60 * 1000,
});

function maybeScheduledCandidateRefreshV6() {
  const now = new Date();
  const parts = seoulNowPartsV5_(now);
  if (!isWeekdayV5_(parts)) return 'SKIP_NON_WEEKDAY';
  if (!isWithinMinuteWindowV5_(parts, YCV5.COLLECTION_START_MINUTE, YCV5.COLLECTION_END_MINUTE)) {
    return 'SKIP_OUTSIDE_COLLECTION_WINDOW';
  }

  const props = PropertiesService.getScriptProperties();
  if (String(props.getProperty(YCV5.COLLECTED_DATE_KEY) || '') === parts.dateKey) {
    return 'SKIP_ALREADY_COLLECTED_TODAY';
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return 'SKIP_POLL_LOCKED';

  let attemptIso = '';
  try {
    const lastPoll = String(props.getProperty(YCV6.LAST_POLL_AT_KEY) || '');
    const lastMs = lastPoll ? Date.parse(lastPoll) : NaN;
    if (isFinite(lastMs) && now.getTime() - lastMs < YCV6.POLL_INTERVAL_MS) {
      return 'SKIP_REFRESH_THROTTLED';
    }
    attemptIso = now.toISOString();
    props.setProperty(YCV6.LAST_POLL_AT_KEY, attemptIso);
  } finally {
    lock.releaseLock();
  }

  try {
    const status = scheduledCandidateRefreshV5();
    props.setProperty(YCV6.LAST_POLL_STATUS_KEY, status);
    if (status === 'RETRY_REFRESH_NOT_CONFIRMED') {
      if (String(props.getProperty(YCV6.LAST_POLL_AT_KEY) || '') === attemptIso) {
        props.deleteProperty(YCV6.LAST_POLL_AT_KEY);
      }
    }
    return status;
  } catch (err) {
    props.setProperty(YCV6.LAST_POLL_STATUS_KEY, 'ERROR: ' + String(err && err.message ? err.message : err));
    if (String(props.getProperty(YCV6.LAST_POLL_AT_KEY) || '') === attemptIso) {
      props.deleteProperty(YCV6.LAST_POLL_AT_KEY);
    }
    throw err;
  }
}

/*
 * Repair technical inputs independently from the once-per-day mail collection
 * state. This prevents a successful/failed morning ingest from leaving newly
 * added candidates stuck in DATA_HOLD until the next day.
 *
 * The 5-minute combined trigger checks for active DATA_HOLD rows. A repair is
 * throttled to once every 10 minutes to avoid repeatedly hammering market-data
 * sources when one ticker is temporarily unavailable.
 */
function maybeRepairDataHoldV10_() {
  const parts = seoulNowPartsV5_(new Date());
  if (!isWeekdayV5_(parts)) return 'SKIP_TECH_REPAIR_NON_WEEKDAY';
  if (!isWithinMinuteWindowV5_(parts, YCV5.MONITOR_START_MINUTE, YCV5.MONITOR_END_MINUTE)) {
    return 'SKIP_TECH_REPAIR_OUTSIDE_MARKET_WINDOW';
  }

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
  if (!sheet || sheet.getLastRow() < YC.DATA_START_ROW) return 'SKIP_TECH_REPAIR_NO_ROWS';

  const rowCount = sheet.getLastRow() - YC.DATA_START_ROW + 1;
  const values = sheet.getRange(YC.DATA_START_ROW, 1, rowCount, 19).getDisplayValues();
  let holdCount = 0;
  values.forEach(function(row) {
    if (
      String(row[0] || '').trim().toUpperCase() === 'Y' &&
      String(row[18] || '').trim().toUpperCase() === 'DATA_HOLD'
    ) {
      holdCount += 1;
    }
  });
  if (!holdCount) return 'SKIP_TECH_REPAIR_NO_DATA_HOLD';

  const props = PropertiesService.getScriptProperties();
  const now = new Date();
  const last = String(props.getProperty(YCV6.DATA_HOLD_REPAIR_AT_KEY) || '');
  const lastMs = last ? Date.parse(last) : NaN;
  if (isFinite(lastMs) && now.getTime() - lastMs < YCV6.DATA_HOLD_REPAIR_INTERVAL_MS) {
    return 'SKIP_TECH_REPAIR_THROTTLED_' + holdCount;
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return 'SKIP_TECH_REPAIR_LOCKED_' + holdCount;
  try {
    const lastInsideLock = String(props.getProperty(YCV6.DATA_HOLD_REPAIR_AT_KEY) || '');
    const lastInsideMs = lastInsideLock ? Date.parse(lastInsideLock) : NaN;
    if (isFinite(lastInsideMs) && now.getTime() - lastInsideMs < YCV6.DATA_HOLD_REPAIR_INTERVAL_MS) {
      return 'SKIP_TECH_REPAIR_THROTTLED_' + holdCount;
    }
    props.setProperty(YCV6.DATA_HOLD_REPAIR_AT_KEY, now.toISOString());
  } finally {
    lock.releaseLock();
  }

  refreshYouTubeCandidatePoolV2();
  return 'TECH_REPAIR_REFRESHED_' + holdCount;
}

/*
 * Main scheduled tick. Candidate-mail collection piggybacks on the already
 * proven 5-minute monitor trigger, while actual Gmail scans remain 30-minute
 * throttled. DATA_HOLD technical repair is independent of the mail-collected
 * flag. Buy-signal monitoring keeps the V5 market/time guards.
 */
function scheduledCombinedTickV6() {
  const refreshStatus = maybeScheduledCandidateRefreshV6();
  const repairStatus = maybeRepairDataHoldV10_();
  const monitorStatus = scheduledCandidateMonitorV5();
  return 'REFRESH=' + refreshStatus + '; REPAIR=' + repairStatus + '; MONITOR=' + monitorStatus;
}
