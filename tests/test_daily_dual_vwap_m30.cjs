'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const file = path.join(__dirname, '../apps_script/paper_game/UnifiedWatchlists30.js');
const clock = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Seoul', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});
const Utilities = {formatDate(date, zone, pattern) {
  const stamp = clock.format(date);
  if (pattern === 'yyyy-MM-dd') return stamp.slice(0, 10);
  if (pattern === 'MM-dd HH:mm') return stamp.slice(5, 16);
  return stamp;
}};
const context = vm.createContext({Utilities, Date});
vm.runInContext(fs.readFileSync(file, 'utf8'), context, {filename: file});

const key = 'DAILY_DUAL_VWAP_BREAKOUT';
const item = {ticker: '000660', name: '테스트 종목', sources: ['HELD']};
function observe(time, price, old, options = {}) {
  const day = options.day || '2026-11-24';
  const now = new Date(day + 'T' + time + ':00+09:00');
  const stage = options.stage || 'BUY_CONFIRMED';
  const bar = options.bar || (time >= '10:30' ? '10:30' : time >= '10:00' ? '10:00' : '09:30');
  const result = {
    ok: true, session: 'KRX_SESSION', live_price_source: 'KIWOOM_KA10001',
    live_price: price, live_change_pct: 0.013, stage, send_allowed: stage !== 'WATCH',
    events: stage === 'WATCH' ? [] : [{key: stage, stage, level: 1}],
    reasons: [],
    technical: {
      basis: 'COMPLETED_DAILY', as_of: (day === '2026-11-24' ? '2026-11-23' : '2026-11-24') + ' 15:30:00',
      vwap9: 100, vwap26: 105, regime: 'BULL_CONFIRMED', ...options.technical,
    },
    m30: {
      available: true, gold: true, completed_bar: day + ' ' + bar + ':00',
      vwap9: 94, vwap26: 90, source: 'KIWOOM_15M_BAR_START', ...options.m30,
    },
    daily_dual_vwap: {observed_at: day + 'T' + time + ':00+09:00', eligible: true},
    ...options.result,
  };
  return context.umPlan_M30(options.item || item, result, old, now);
}
const enhanced = plan => plan.events.filter(event => event.dualVwapBreakout === true);
const eventKeys = plan => Array.from(plan.events, event => event.key);
const timeAt = time => new Date('2026-11-24T' + time + ':00+09:00');

test('two levels can be crossed on different 30m bars and merge into the existing event ID', () => {
  const low = observe('09:50', 99, null, {stage: 'WATCH'});
  const first = observe('09:55', 105, low.state);
  assert.equal(enhanced(first).length, 0);
  const sentBefore = context.umCommit_M30(first, timeAt('09:55'), 'earlier',
    context.umEventId_M30(first, timeAt('09:55')));
  const plan = observe('10:00', 106, sentBefore);
  assert.deepEqual(eventKeys(plan), ['BUY_CONFIRMED']);
  assert.equal(enhanced(plan).length, 1);
  const now = timeAt('10:00');
  const id = context.umEventId_M30(plan, now);
  assert.equal(id, 'U2_2026-11-24_KRX_SESSION_000660_BUY_CONFIRMED');
  const mail = context.umMessage_M30([plan], now);
  assert.match(mail.subject, /▲ .*강력 매수확인/);
  for (const text of [mail.body, mail.htmlBody]) {
    assert.match(text, /▲ 강력 매수확인 — 일봉 VWAP9·26 돌파 \+ 30분봉 정배열/);
  }
  assert.deepEqual(JSON.parse(mail.body.split('UNIFIED_EVENTS_JSON=')[1]), [{
    id, source: 'HELD', ticker: '000660', name: '테스트 종목',
    stage: '▲ 매수확인', price: 106, session: 'KRX_SESSION',
  }]);
  assert.doesNotMatch(mail.htmlBody, /UNIFIED_EVENTS_JSON/);
  const sent = context.umCommit_M30(plan, now, 'gmail-id', id);
  assert.deepEqual(sent.sent[key], sent.sent.BUY_CONFIRMED);
});

test('daily VWAP9 can be above VWAP26 and equality is a valid starting observation', () => {
  const technical = {vwap9: 105, vwap26: 100};
  const baseline = observe('09:55', 105, null, {stage: 'WATCH', technical});
  const cross = observe('10:00', 106, baseline.state, {technical});
  assert.equal(enhanced(cross).length, 0);
  assert.equal(cross.state.dailyDualVwap.pending, true);
  const confirmed = observe('10:05', 107, cross.state, {technical});
  assert.equal(enhanced(confirmed).length, 1);
});

test('the first observation above both establishes the baseline, including with legacy state', () => {
  for (const legacy of [null, {
    day: '2026-11-24', sent: {}, count: 2, stage: 'BUY_CONFIRMED', vwap30Price: 99,
  }]) {
    const initial = observe('09:55', 106, legacy);
    assert.equal(initial.state.dailyDualVwap.pending, false);
    assert.equal(enhanced(initial).length, 0);
    assert.equal(enhanced(observe('10:00', 107, initial.state)).length, 0);
  }
});

test('a corrected daily VWAP or a changed completed daily basis is re-baselined', () => {
  const low = observe('09:55', 104, null);
  for (const technical of [{vwap26: 103}, {as_of: '2026-11-20 15:30:00'}]) {
    const plan = observe('10:00', 106, low.state, {technical});
    assert.equal(enhanced(plan).length, 0);
    assert.equal(plan.state.dailyDualVwap.pending, false);
  }
});

test('an existing setup stage stays setup and the daily receipt prevents a later strong re-send', () => {
  const setup = {stage: 'BUY_SETUP', technical: {regime: 'EARLY_IMPROVEMENT'}};
  const base = observe('09:55', 104, null, setup);
  const plan = observe('10:00', 106, base.state, setup);
  const now = timeAt('10:00');
  assert.deepEqual(eventKeys(plan), ['BUY_SETUP']);
  const mail = context.umMessage_M30([plan], now);
  assert.match(mail.body, /△ 강화된 매수 관심 — 일봉 VWAP9·26 돌파 \+ 30분봉 정배열/);
  assert.doesNotMatch(mail.body, /강력 매수확인/);
  const committed = context.umCommit_M30(plan, now, 'gmail-id', context.umEventId_M30(plan, now));
  const dip = observe('10:05', 103, committed);
  const recross = observe('10:10', 110, dip.state);
  assert.deepEqual(eventKeys(recross), ['BUY_CONFIRMED']);
  assert.equal(enhanced(recross).length, 0);
});

test('a separate new breakout may follow an ordinary alert and is sent once per day', () => {
  const low = observe('09:50', 102, null);
  const ordinary = observe('09:55', 104, low.state);
  const before = timeAt('09:55');
  const afterOrdinary = context.umCommit_M30(ordinary, before, 'first', context.umEventId_M30(ordinary, before));
  const plan = observe('10:00', 106, afterOrdinary);
  assert.deepEqual(eventKeys(plan), [key]);
  const now = timeAt('10:00');
  const id = context.umEventId_M30(plan, now);
  assert.equal(id, 'U2_2026-11-24_KRX_SESSION_000660_' + key);
  const sent = context.umCommit_M30(plan, now, 'second', id);
  assert.equal(sent.sent.BUY_CONFIRMED.messageId, 'first');
  assert.equal(sent.sent[key].messageId, 'second');
  assert.equal(enhanced(observe('10:00', 106, sent)).length, 0);
  const dip = observe('10:05', 104, sent);
  assert.equal(enhanced(observe('10:10', 110, dip.state)).length, 0);
  const next = observe('09:55', 104, sent, {day: '2026-11-25'});
  assert.equal(enhanced(observe('10:00', 110, next.state, {day: '2026-11-25'})).length, 1);
});

test('invalid, stale, partial and non-stacked inputs cannot create a breakout', () => {
  const baseline = observe('09:55', 104, null);
  const cases = [
    {technical: {vwap26: null}},
    {technical: {basis: 'INCOMPLETE_DAILY_NOT_CONFIRMED'}},
    {technical: {as_of: '2026-11-24 15:30:00'}},
    {result: {daily_dual_vwap: null}}, // Missing or stale one-minute verification from Cloud Run.
    {result: {daily_dual_vwap: {observed_at: '2026-11-24T09:58:00+09:00', eligible: true}}},
    {m30: {available: false}},
    {bar: '09:30'},
    {bar: '10:30'},
    {m30: {vwap9: 106}},
    {m30: {vwap9: 94, vwap26: 94}},
  ];
  for (const options of cases) {
    const plan = observe('10:00', 106, baseline.state, options);
    assert.equal(enhanced(plan).length, 0, JSON.stringify(options));
  }
});

test('valid pre-09:30 quote can be the baseline but a partial 30m bar cannot generate the alert', () => {
  const base = observe('09:25', 104, null, {stage: 'WATCH', m30: {available: false}});
  assert.equal(base.state.dailyDualVwap.price, 104);
  const crossing = observe('09:30', 106, base.state);
  assert.equal(crossing.state.dailyDualVwap.pending, true);
  const confirmed = observe('09:35', 107, crossing.state);
  assert.equal(enhanced(confirmed).length, 1);
});

test('downside, WATCH, denied signals, overheated quotes and inverse information stay unchanged', () => {
  const base = observe('09:55', 104, null);
  for (const options of [
    {stage: 'WATCH'}, {stage: 'DOWN_EARLY'}, {stage: 'DOWN_CONFIRMED'},
    {technical: {regime: 'BOUNCE_IN_DOWNREGIME'}},
    {result: {send_allowed: false}},
    {result: {reasons: ['ABOVE_OVERHEAT_LIMIT']}},
    {result: {inverse_market_info: true}},
  ]) {
    assert.equal(enhanced(observe('10:00', 106, base.state, options)).length, 0);
  }
});

test('stop, market crash and risk warnings take precedence including at the stop price', () => {
  const base = observe('09:55', 104, null);
  for (const risk of ['STOP_BREACH', 'MARKET_CRASH', 'OBV15_WARNING', 'SELL_PREP_WARNING']) {
    const plan = observe('10:00', 106, base.state, {
      stage: 'RISK',
      item: {...item, stop_price: risk === 'STOP_BREACH' ? 106 : 0},
      result: {events: [{key: risk, stage: 'RISK', level: 3}]},
    });
    assert.deepEqual(eventKeys(plan), [risk]);
    assert.equal(enhanced(plan).length, 0);
    assert.doesNotMatch(context.umMessage_M30([plan], timeAt('10:00')).body, /강력 매수확인/);
  }
});

test('pending crossover expires on an invalid observation, a lost confirmation, or a cadence gap', () => {
  const low = observe('09:55', 99, null, {stage: 'WATCH'});
  const crossing = observe('10:00', 106, low.state);
  assert.equal(crossing.state.dailyDualVwap.pending, true);
  for (const options of [{result: {daily_dual_vwap: null}}, {stage: 'WATCH'}]) {
    const interruption = observe('10:05', 107, crossing.state, options);
    assert.equal(interruption.state.dailyDualVwap.pending, false);
    const later = observe('10:10', 110, interruption.state);
    assert.equal(enhanced(observe('10:15', 111, later.state)).length, 0);
  }
  const delayed = observe('10:10', 107, crossing.state);
  assert.equal(delayed.state.dailyDualVwap.pending, false);
});
