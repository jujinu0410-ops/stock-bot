/*
 * Additive expert-recommendation mail ingest V11.
 * Preserves the pinned PREMARKET_V10 collector and its one-month candidate TTL.
 * Adds 08:50 Spark and 10:50 specialist mails as ordinary CANDIDATES inputs.
 * No changes to trading logic, orders, notification policies or scheduling.
 */
const YCE11 = Object.freeze({
  TZ: 'Asia/Seoul',
  START_MINUTE: 8 * 60 + 40,
  END_MINUTE: 15 * 60 + 30,
  POLL_MS: 5 * 60 * 1000,
  MAX_THREADS_PER_QUERY: 30,
  MAX_STOCKS_PER_MAIL: 20,
  POLL_AT_KEY: 'EXPERT_MAIL_V11_POLL_AT',
  SEEN_DATE_KEY: 'EXPERT_MAIL_V11_SEEN_DATE',
  SEEN_IDS_KEY: 'EXPERT_MAIL_V11_SEEN_IDS',
  STATUS_KEY: 'EXPERT_MAIL_V11_STATUS'
});

let yce11TodayCache_ = null;

function yce11Parse_(subject, rawBody) {
  // Do not re-import the general Intelligence report's all-mentioned stocks.
  if (!/(전문가|추천주|종목수집)/.test(String(subject || ''))) {
    return {found: false, stocks: []};
  }
  const body = String(rawBody || '').replace(/\r\n?/g, '\n').replace(/\u00a0/g, ' ').replace(/\u200b/g, '');
  const block = body.match(/\[YT_ALL_STOCKS_BEGIN\]([\s\S]*?)\[YT_ALL_STOCKS_END\]/);
  if (!block) return {found: false, stocks: []};
  const stocks = [], seen = new Set();
  block[1].split('\n').forEach(function(raw) {
    const line = String(raw || '').trim();
    if (!line) return;
    const m = line.match(/^(\d{6})\|([^|\n]{2,60})$/);
    if (!m) throw new Error('EXPERT_V11_INVALID_STOCK_LINE');
    const code = normalizeCandidateCode_(m[1]);
    const name = String(m[2] || '').trim();
    if (!code || !name) throw new Error('EXPERT_V11_INVALID_IDENTITY');
    if (seen.has(code)) return; // Dedupe inside each recommendation mail.
    seen.add(code);
    stocks.push({code: code, name: name});
  });
  if (!stocks.length || stocks.length > YCE11.MAX_STOCKS_PER_MAIL) {
    throw new Error('EXPERT_V11_INVALID_STOCK_COUNT_' + stocks.length);
  }
  return {found: true, stocks: stocks};
}

function yce11TodayMails_() {
  if (yce11TodayCache_ !== null) return yce11TodayCache_;
  const today = todayKeyV8_(), all = new Map();
  const subjects = ['전문가', '추천주', '종목수집'];
  subjects.forEach(function(term) {
    const q = 'subject:"' + term + '" newer_than:3d -in:trash -in:spam';
    GmailApp.search(q, 0, YCE11.MAX_THREADS_PER_QUERY).forEach(function(th) {
      th.getMessages().forEach(function(msg) {
        const id = String(msg.getId() || '');
        if (!id || all.has(id)) return;
        if (Utilities.formatDate(msg.getDate(), YCE11.TZ, 'yyyy-MM-dd') !== today) return;
        const parsed = yce11Parse_(msg.getSubject(), msg.getPlainBody());
        if (!parsed.found) return;
        all.set(id, {id: id, date: today, subject: msg.getSubject(), stocks: parsed.stocks});
      });
    });
  });
  yce11TodayCache_ = Array.from(all.values()).sort(function(a,b){return a.id.localeCompare(b.id);});
  return yce11TodayCache_;
}

/* Decorate, never replace, the established PREMARKET_V10 stock universe. */
const yce11BaseCollector_ = collectIntelligenceCandidates_;
collectIntelligenceCandidates_ = function() {
  const pool = yce11BaseCollector_();
  yce11TodayMails_().forEach(function(mail) {
    mail.stocks.forEach(function(stock) {
      const code = stock.code;
      if (!pool[code]) {
        pool[code] = {
          name: stock.name, nameTrusted: true, dates: new Set(),
          lastSeen: mail.date, sourceTag: 'EXPERT_MAIL_V11'
        };
      }
      const existing = pool[code];
      existing.dates.add(mail.date);
      if (mail.date >= existing.lastSeen) {
        existing.lastSeen = mail.date;
        existing.name = stock.name;
        existing.nameTrusted = true;
      }
      existing.mentionDays = existing.dates.size;
    });
  });
  return pool;
};

function yce11MaybeSupplementalRefresh_() {
  const parts = seoulNowPartsV5_(new Date());
  if (!isWeekdayV5_(parts)) return 'SKIP_WEEKEND';
  if (!isWithinMinuteWindowV5_(parts, YCE11.START_MINUTE, YCE11.END_MINUTE)) return 'SKIP_WINDOW';

  const props = PropertiesService.getScriptProperties();
  const nowMs = Date.now();
  const last = Number(props.getProperty(YCE11.POLL_AT_KEY) || '0');
  if (last > 0 && nowMs - last < YCE11.POLL_MS) return 'SKIP_POLL_THROTTLE';
  props.setProperty(YCE11.POLL_AT_KEY, String(nowMs));

  try {
    const mails = yce11TodayMails_();
    const already = props.getProperty(YCE11.SEEN_DATE_KEY) === parts.dateKey
      ? String(props.getProperty(YCE11.SEEN_IDS_KEY) || '').split(',').filter(Boolean)
      : [];
    const seen = new Set(already), fresh = mails.filter(function(m){return !seen.has(m.id);});
    if (!fresh.length) {
      props.setProperty(YCE11.STATUS_KEY, 'NO_NEW_MAIL ' + parts.dateKey + ' found=' + mails.length);
      return 'NO_NEW_EXPERT_MAIL';
    }

    // Existing V8 refresh enforces 6-digit identity, Naver name/market, TTL and same Sheet format.
    const before = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
    refreshYouTubeCandidatePoolV2();
    const after = String(props.getProperty('LAST_CANDIDATE_REFRESH_AT') || '');
    if (!after || after === before) throw new Error('EXPERT_V11_REFRESH_NOT_CONFIRMED');

    const ids = Array.from(new Set(already.concat(fresh.map(function(m){return m.id;})))).slice(-30);
    props.setProperty(YCE11.SEEN_DATE_KEY, parts.dateKey);
    props.setProperty(YCE11.SEEN_IDS_KEY, ids.join(','));
    const count = fresh.reduce(function(n,m){return n + m.stocks.length;},0);
    const status = 'INGESTED mails=' + fresh.length + ' mentions=' + count + ' distinct_codes=' +
      new Set(fresh.reduce(function(a,m){return a.concat(m.stocks.map(function(s){return s.code;}));},[])).size;
    props.setProperty(YCE11.STATUS_KEY, status);
    Logger.log('EXPERT_MAIL_V11 ' + status);
    return status;
  } catch (e) {
    props.deleteProperty(YCE11.POLL_AT_KEY);
    props.setProperty(YCE11.STATUS_KEY, 'ERROR ' + String(e && e.message || e).slice(0,160));
    throw e;
  }
}

/* Existing V9 tick keeps its original log cleanup + market-guarded 45-minute watch. */
const yce11BaseMaybeRefresh_ = maybeScheduledCandidateRefreshV6;
maybeScheduledCandidateRefreshV6 = function() {
  const base = yce11BaseMaybeRefresh_();
  const extra = yce11MaybeSupplementalRefresh_();
  return base + '; EXPERT=' + extra;
};
