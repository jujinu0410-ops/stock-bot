/*
 * Runtime V6: self-healing candidate-mail polling.
 *
 * The proven 5-minute trigger also checks new or delayed Intelligence emails.
 * Gmail polling cadence: 5 min from 06:00-18:00 on weekdays, 30 min in the
 * evening, hourly overnight/weekends. Only new message IDs cause refresh.
 * Intraday buy-signal monitoring is still restricted to open KRX sessions.
 */

const YCV6 = Object.freeze({
  LAST_POLL_AT_KEY: 'YOUTUBE_MAIL_LAST_POLL_AT',
  LAST_POLL_STATUS_KEY: 'YOUTUBE_MAIL_LAST_POLL_STATUS',
  POLL_INTERVAL_MS: 4 * 60 * 1000,
});

function maybeScheduledCandidateRefreshV6() {
  const now = new Date();
  const parts = seoulNowPartsV5_(now);
  const weekday = isWeekdayV5_(parts);
  const fast = weekday && isWithinMinuteWindowV5_(parts, 6 * 60, 18 * 60);
  const evening = weekday && isWithinMinuteWindowV5_(parts, 18 * 60 + 1, 23 * 60 + 59);
  const intervalMs = fast ? 4 * 60 * 1000 : (evening ? 29 * 60 * 1000 : 59 * 60 * 1000);

  const props = PropertiesService.getScriptProperties();
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return 'SKIP_POLL_LOCKED';

  let attemptIso = '';
  try {
    const lastPoll = String(props.getProperty(YCV6.LAST_POLL_AT_KEY) || '');
    const lastMs = lastPoll ? Date.parse(lastPoll) : NaN;
    if (isFinite(lastMs) && now.getTime() - lastMs < intervalMs) {
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
 * Main scheduled tick. Candidate-mail collection piggybacks on the already
 * proven 5-minute trigger. Email ingestion can run after close/weekends;
 * buy-signal monitoring keeps the V5 market/time guards.
 */
function scheduledCombinedTickV6() {
  const refreshStatus = maybeScheduledCandidateRefreshV6();
  const monitorStatus = scheduledCandidateMonitorV5();
  return 'REFRESH=' + refreshStatus + '; MONITOR=' + monitorStatus;
}
