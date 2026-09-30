/*
 * Runtime V5: trading-day schedule guards.
 *
 * Scheduled candidate-mail collection:
 * - weekdays only
 * - 08:00~15:30 Asia/Seoul
 * - every 30 minutes via bootstrap trigger
 * - once today's [경제 Intelligence] mail is found and refresh succeeds,
 *   stop collecting for the rest of that Seoul calendar day
 *
 * Scheduled buy-signal monitoring:
 * - weekdays only
 * - 09:00~15:30 Asia/Seoul
 * - every 5 minutes via bootstrap trigger
 *
 * Manual refresh/monitor entry points remain available separately and are not
 * blocked by these schedule guards.
 */

const YCV5 = Object.freeze({
  TIMEZONE: 'Asia/Seoul',
  COLLECTION_START_MINUTE: 8 * 60,
  COLLECTION_END_MINUTE: 15 * 60 + 30,
  MONITOR_START_MINUTE: 9 * 60,
  MONITOR_END_MINUTE: 15 * 60 + 30,
  COLLECTED_DATE_KEY: 'YOUTUBE_MAIL_COLLECTED_DATE',
  COLLECTED_AT_KEY: 'YOUTUBE_MAIL_COLLECTED_AT',
  COLLECTED_SUBJECT_KEY: 'YOUTUBE_MAIL_COLLECTED_SUBJECT',
});

function seoulNowPartsV5_(now) {
  const d = now || new Date();
  const dateKey = Utilities.formatDate(d, YCV5.TIMEZONE, 'yyyy-MM-dd');
  const hh = Number(Utilities.formatDate(d, YCV5.TIMEZONE, 'HH'));
  const mm = Number(Utilities.formatDate(d, YCV5.TIMEZONE, 'mm'));
  const parts = dateKey.split('-').map(Number);
  const weekday = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2])).getUTCDay();
  return {
    dateKey: dateKey,
    weekday: weekday,
    minuteOfDay: hh * 60 + mm,
  };
}

function isWeekdayV5_(parts) {
  return parts.weekday >= 1 && parts.weekday <= 5;
}

function isWithinMinuteWindowV5_(parts, startMinute, endMinute) {
  return parts.minuteOfDay >= startMinute && parts.minuteOfDay <= endMinute;
}

function findTodayIntelligenceMailV5_() {
  const parts = seoulNowPartsV5_(new Date());
  const query = 'subject:"경제 Intelligence" newer_than:2d -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, 20);
  let newest = null;

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(message) {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;
      const messageDateKey = Utilities.formatDate(message.getDate(), YCV5.TIMEZONE, 'yyyy-MM-dd');
      if (messageDateKey !== parts.dateKey) return;
      if (!newest || message.getDate().getTime() > newest.date.getTime()) {
        newest = {
          subject: subject,
          date: message.getDate(),
        };
      }
    });
  });

  return newest;
}

function markTodayCollectionDoneV5_(mailInfo) {
  const parts = seoulNowPartsV5_(new Date());
  const props = PropertiesService.getScriptProperties();
  props.setProperties({
    [YCV5.COLLECTED_DATE_KEY]: parts.dateKey,
    [YCV5.COLLECTED_AT_KEY]: new Date().toISOString(),
    [YCV5.COLLECTED_SUBJECT_KEY]: String(mailInfo && mailInfo.subject ? mailInfo.subject : ''),
  }, false);
}

/*
 * Scheduled 30-minute candidate-mail collector.
 * Returns a short status string for execution logs/debugging.
 */
function scheduledCandidateRefreshV5() {
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

  const mailInfo = findTodayIntelligenceMailV5_();
  if (!mailInfo) return 'WAIT_TODAY_MAIL';

  const before = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
  refreshYouTubeCandidatePoolV2();
  const after = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');

  // refreshYouTubeCandidatePoolV2 can legitimately return early if another
  // runtime holds the script lock.  Do not mark the day complete unless the
  // successful-refresh timestamp actually advanced.
  if (!after || after === before) return 'RETRY_REFRESH_NOT_CONFIRMED';

  markTodayCollectionDoneV5_(mailInfo);
  return 'COLLECTED_TODAY';
}

/* Manual refresh: always run immediately. If today's mail is present and the
 * refresh succeeds, count it as today's completed collection so the scheduled
 * collector stops for the day.
 */
function manualCandidateRefreshV5() {
  const props = PropertiesService.getScriptProperties();
  const before = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
  const mailInfo = findTodayIntelligenceMailV5_();
  refreshYouTubeCandidatePoolV2();
  const after = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
  if (mailInfo && after && after !== before) markTodayCollectionDoneV5_(mailInfo);
  return after && after !== before ? 'REFRESHED' : 'REFRESH_NOT_CONFIRMED';
}

/* Scheduled 5-minute technical monitor. */
function scheduledCandidateMonitorV5() {
  const parts = seoulNowPartsV5_(new Date());
  if (!isWeekdayV5_(parts)) return 'SKIP_NON_WEEKDAY';
  if (!isWithinMinuteWindowV5_(parts, YCV5.MONITOR_START_MINUTE, YCV5.MONITOR_END_MINUTE)) {
    return 'SKIP_OUTSIDE_MARKET_WINDOW';
  }
  monitorYouTubeCandidatesV2();
  return 'MONITORED';
}

/* Manual monitor: immediate test/inspection entry point, no schedule guard. */
function manualCandidateMonitorV5() {
  monitorYouTubeCandidatesV2();
  return 'MONITORED_MANUAL';
}
