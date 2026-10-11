/*
 * Runtime V6: self-healing candidate-mail polling.
 *
 * The proven 5-minute monitor trigger also checks whether today's Intelligence
 * mail has changed. Gmail is checked every 5 minutes, but the expensive full
 * technical refresh runs only for a new Intelligence message.
 * The legacy 30-minute trigger uses the same throttle/message-ID state.
 */

const YCV6 = Object.freeze({
  LAST_POLL_AT_KEY: 'YOUTUBE_MAIL_LAST_POLL_AT',
  LAST_POLL_STATUS_KEY: 'YOUTUBE_MAIL_LAST_POLL_STATUS',
  POLL_INTERVAL_MS: 4 * 60 * 1000,
});

function maybeScheduledCandidateRefreshV6() {
  const now = new Date();
  const parts = seoulNowPartsV5_(now);
  if (!isWeekdayV5_(parts)) return 'SKIP_NON_WEEKDAY';
  if (!isWithinMinuteWindowV5_(parts, YCV5.COLLECTION_START_MINUTE, YCV5.COLLECTION_END_MINUTE)) {
    return 'SKIP_OUTSIDE_COLLECTION_WINDOW';
  }

  const props = PropertiesService.getScriptProperties();
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
 * Main scheduled tick. Candidate-mail collection piggybacks on the already
 * proven 5-minute monitor trigger, while actual Gmail scans run every ~5 minutes.
 * Buy-signal monitoring keeps the V5 market/time guards.
 */
function scheduledCombinedTickV6() {
  const refreshStatus = maybeScheduledCandidateRefreshV6();
  const monitorStatus = scheduledCandidateMonitorV5();
  return 'REFRESH=' + refreshStatus + '; MONITOR=' + monitorStatus;
}
