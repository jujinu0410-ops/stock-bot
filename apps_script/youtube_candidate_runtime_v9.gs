/*
 * Runtime V9: bounded DISPATCH_LOG retention.
 *
 * Policy:
 * - Keep only the most recent 90 calendar days of dispatch logs.
 * - Hard-cap retained data rows at 2,000 even if 90 days produces more rows.
 * - Run cleanup at most once per Seoul calendar day during the normal 5-minute tick.
 * - Manual candidate refresh also performs the same once-per-day cleanup.
 * - Shrink excess blank grid rows after cleanup so the sheet itself does not grow forever.
 */

const YCV9 = Object.freeze({
  TIMEZONE: 'Asia/Seoul',
  RETENTION_DAYS: 90,
  MAX_DATA_ROWS: 2000,
  MIN_GRID_ROWS: 100,
  BLANK_BUFFER_ROWS: 50,
  LAST_CLEAN_DATE_KEY: 'DISPATCH_LOG_LAST_CLEAN_DATE',
});

function todayKeyV9_() {
  return Utilities.formatDate(new Date(), YCV9.TIMEZONE, 'yyyy-MM-dd');
}

function isValidLogDateV9_(value) {
  return value instanceof Date && !isNaN(value.getTime());
}

function cleanupDispatchLogV9_(ss) {
  const sheet = ss.getSheetByName(YC.LOG_SHEET);
  if (!sheet) return 'SKIP_LOG_SHEET_MISSING';

  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    trimDispatchLogGridV9_(sheet);
    return 'LOG_CLEAN_EMPTY';
  }

  const now = new Date();
  const cutoff = new Date(now.getTime());
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - YCV9.RETENTION_DAYS);

  const values = sheet.getRange(2, 1, lastRow - 1, 1).getValues();

  /* Logs are appended in chronological order. Delete only the contiguous old
   * prefix for the date rule. If an unexpected malformed timestamp appears,
   * stop there rather than risk deleting newer data behind it.
   */
  let firstKeepByDate = 2;
  for (let i = 0; i < values.length; i++) {
    const v = values[i][0];
    if (!isValidLogDateV9_(v)) break;
    if (v.getTime() >= cutoff.getTime()) {
      firstKeepByDate = i + 2;
      break;
    }
    firstKeepByDate = i + 3;
  }

  /* Hard cap: with lastRow including the header, keep at most the newest 2,000
   * data rows. This rule is allowed to override the malformed-row safety above.
   */
  const firstKeepByCount = Math.max(2, lastRow - YCV9.MAX_DATA_ROWS + 1);
  const firstKeepRow = Math.max(firstKeepByDate, firstKeepByCount);
  const deleteCount = Math.min(lastRow - 1, Math.max(0, firstKeepRow - 2));

  if (deleteCount > 0) {
    sheet.deleteRows(2, deleteCount);
  }

  trimDispatchLogGridV9_(sheet);
  return 'LOG_CLEANED_DELETED_' + deleteCount;
}

function trimDispatchLogGridV9_(sheet) {
  const lastRow = Math.max(1, sheet.getLastRow());
  const desired = Math.max(
    YCV9.MIN_GRID_ROWS,
    Math.min(YCV9.MAX_DATA_ROWS + 1 + YCV9.BLANK_BUFFER_ROWS, lastRow + YCV9.BLANK_BUFFER_ROWS)
  );
  const maxRows = sheet.getMaxRows();
  if (maxRows > desired) {
    sheet.deleteRows(desired + 1, maxRows - desired);
  }
}

function cleanupDispatchLogOncePerDayV9_() {
  const props = PropertiesService.getScriptProperties();
  const today = todayKeyV9_();
  if (String(props.getProperty(YCV9.LAST_CLEAN_DATE_KEY) || '') === today) {
    return 'SKIP_LOG_ALREADY_CLEANED_TODAY';
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return 'SKIP_LOG_CLEAN_LOCKED';

  try {
    if (String(props.getProperty(YCV9.LAST_CLEAN_DATE_KEY) || '') === today) {
      return 'SKIP_LOG_ALREADY_CLEANED_TODAY';
    }
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const status = cleanupDispatchLogV9_(ss);
    props.setProperty(YCV9.LAST_CLEAN_DATE_KEY, today);
    return status;
  } finally {
    lock.releaseLock();
  }
}

function manualDispatchLogCleanupV9() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return 'SKIP_LOG_CLEAN_LOCKED';
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const status = cleanupDispatchLogV9_(ss);
    PropertiesService.getScriptProperties().setProperty(YCV9.LAST_CLEAN_DATE_KEY, todayKeyV9_());
    return status;
  } finally {
    lock.releaseLock();
  }
}

/* Override V6 combined tick: daily log housekeeping first, then preserve the
 * existing candidate-mail refresh and market-guarded monitoring behavior.
 */
function scheduledCombinedTickV6() {
  const logStatus = cleanupDispatchLogOncePerDayV9_();
  const refreshStatus = maybeScheduledCandidateRefreshV6();
  const monitorStatus = scheduledCandidateMonitorV5();
  return 'LOG=' + logStatus + '; REFRESH=' + refreshStatus + '; MONITOR=' + monitorStatus;
}

/* Override V5 manual refresh so the user's normal refresh action also performs
 * housekeeping without needing a separate daily operation.
 */
function manualCandidateRefreshV5() {
  cleanupDispatchLogOncePerDayV9_();

  const props = PropertiesService.getScriptProperties();
  const before = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
  const mailInfo = findTodayIntelligenceMailV5_();
  refreshYouTubeCandidatePoolV2();
  const after = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
  if (mailInfo && after && after !== before) markTodayCollectionDoneV5_(mailInfo);
  return after && after !== before ? 'REFRESHED' : 'REFRESH_NOT_CONFIRMED';
}
